param(
    [Parameter(Mandatory = $true)]
    [string]$ZipPath,
    [switch]$IncludeWindowsAudio
)

function Invoke-BatchLauncher {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string[]]$Arguments = @()
    )

    if (-not (Test-Path -LiteralPath $Path)) { throw "portable launcher is missing: $Path" }
    $commandLine = 'call "' + $Path.Replace('"', '""') + '"'
    foreach ($argument in $Arguments) {
        $commandLine += ' "' + $argument.Replace('"', '""') + '"'
    }
    & $env:ComSpec /d /s /c $commandLine
    if ($LASTEXITCODE -ne 0) { throw "portable launcher failed with exit code $LASTEXITCODE`: $Path" }
}

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = (Resolve-Path -LiteralPath $ZipPath).Path
$sandbox = Join-Path $env:TEMP "Meeting AI Copilot Clean User Smoke"
if (Test-Path -LiteralPath $sandbox) { Remove-Item -LiteralPath $sandbox -Recurse -Force }
New-Item -ItemType Directory -Path $sandbox | Out-Null
[System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $sandbox)
$package = Join-Path $sandbox "meeting-ai-copilot-portable"
$exe = Join-Path $package "MeetingAICopilot/MeetingAICopilot.exe"
$profile = Join-Path $sandbox "Clean User Profile"
$output = Join-Path $profile "Desktop/Meeting Smoke Output"
New-Item -ItemType Directory -Path $output -Force | Out-Null

$originalProfile = $env:USERPROFILE
try {
    $env:USERPROFILE = $profile
    $version = & $exe --version
    if ($LASTEXITCODE -ne 0) { throw "portable --version failed" }
    & $exe --config (Join-Path $package "config.example.json") --smoke-test
    if ($LASTEXITCODE -ne 0) { throw "portable smoke-test failed" }
    & $exe --config (Join-Path $package "config.example.json") --list-devices
    if ($LASTEXITCODE -ne 0) { throw "portable device enumeration failed" }
    & $exe --mock-demo --fixture (Join-Path $package "tests/fixtures/meeting_question.wav") --output-directory $output
    if ($LASTEXITCODE -ne 0) { throw "portable Mock demo failed" }
    & $exe --mock-tui-demo --fixture (Join-Path $package "tests/fixtures/meeting_question.wav") --demo-duration 1
    if ($LASTEXITCODE -ne 0) { throw "portable TUI smoke failed" }
    if ($IncludeWindowsAudio) {
        & $exe --windows-audio-acceptance `
            --fixture (Join-Path $package "tests/fixtures/meeting_question.wav") `
            --report (Join-Path $output "windows-audio.json")
        if ($LASTEXITCODE -ne 0) { throw "portable Windows audio acceptance failed" }
    }
    Invoke-BatchLauncher -Path (Join-Path $package "启动云端实时转写和AI答案.bat") -Arguments @("--smoke-test")
    Invoke-BatchLauncher -Path (Join-Path $package "一键Mock演示.bat")
} finally {
    $env:USERPROFILE = $originalProfile
}

$sessions = @(Get-ChildItem -LiteralPath $output -Filter "session-*.json")
$launcherOutput = Join-Path $profile "Desktop/实时监听/Mock演示"
$launcherSessions = @(
    if (Test-Path -LiteralPath $launcherOutput) {
        Get-ChildItem -LiteralPath $launcherOutput -Filter "session-*.json"
    }
)
if ($launcherSessions.Count -lt 1) { throw "portable one-click Mock session was not saved" }
$report = [ordered]@{
    status = "passed"
    zip = $zip
    zip_sha256 = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    version = ($version | Select-Object -Last 1).Trim()
    executable = $exe
    python_required = $false
    clean_profile = $profile
    path_with_spaces = $true
    session_json_count = $sessions.Count
    one_click_launcher = $true
    one_click_mock_session_count = $launcherSessions.Count
    tui_smoke = $true
    windows_audio_acceptance = [bool]$IncludeWindowsAudio
}
$json = $report | ConvertTo-Json -Depth 5
Write-Host $json
Write-Host "PORTABLE SMOKE PASSED"
