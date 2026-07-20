param(
    [string]$OutputRoot = "dist",
    [switch]$SkipTests,
    [switch]$SkipPyInstaller
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

if (-not $IsWindows -and $PSVersionTable.PSVersion.Major -ge 6) {
    throw "Windows portable package must be built on Windows"
}

$version = (Get-Content -LiteralPath "VERSION" -Raw).Trim()
$safeVersion = $version -replace "[^0-9A-Za-z.-]", "-"
$outputPath = Join-Path $root $OutputRoot
$workPath = Join-Path $root "build/pyinstaller"
$stagingPath = Join-Path $outputPath "meeting-ai-copilot-portable"
$zipPath = Join-Path $outputPath "meeting-ai-copilot-$safeVersion-win-x64.zip"

if (-not $SkipTests) {
    python -m py_compile src/app_contracts.py src/audio_pipeline.py src/asr_resilience.py src/cloud_runtime.py src/cloud_asr_volcengine.py
    if ($LASTEXITCODE -ne 0) { throw "py_compile failed" }
    python -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
}

if (-not $SkipPyInstaller) {
    python -m PyInstaller --noconfirm --clean --workpath $workPath --distpath $outputPath meeting-ai-copilot.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    if (Test-Path -LiteralPath $stagingPath) { Remove-Item -LiteralPath $stagingPath -Recurse -Force }
    New-Item -ItemType Directory -Path $stagingPath | Out-Null
    Move-Item -LiteralPath (Join-Path $outputPath "MeetingAICopilot") -Destination (Join-Path $stagingPath "MeetingAICopilot")
    Copy-Item -LiteralPath "启动云端实时转写和AI答案.bat" -Destination $stagingPath
    Copy-Item -LiteralPath "一键Mock演示.bat" -Destination $stagingPath
    Copy-Item -LiteralPath "config.example.json" -Destination $stagingPath
    Copy-Item -LiteralPath "config.mock-offline.json" -Destination $stagingPath
    Copy-Item -LiteralPath "使用说明.md" -Destination $stagingPath
    Copy-Item -LiteralPath "便携版说明.md" -Destination $stagingPath
    Copy-Item -LiteralPath "SECURITY.md" -Destination $stagingPath
    Copy-Item -LiteralPath "LICENSE" -Destination $stagingPath
    Copy-Item -LiteralPath "VERSION" -Destination $stagingPath
    New-Item -ItemType Directory -Path (Join-Path $stagingPath "tests/fixtures") -Force | Out-Null
    Copy-Item -LiteralPath "tests/fixtures/meeting_question.wav" -Destination (Join-Path $stagingPath "tests/fixtures")
    Copy-Item -LiteralPath "tests/fixtures/meeting_question.json" -Destination (Join-Path $stagingPath "tests/fixtures")
} elseif (-not (Test-Path -LiteralPath $stagingPath)) {
    throw "SkipPyInstaller requires an existing staging directory: $stagingPath"
}

if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $stagingPath,
    $zipPath,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $true
)
if (-not (Test-Path -LiteralPath $zipPath) -or (Get-Item -LiteralPath $zipPath).Length -le 0) {
    throw "zip output is missing or empty"
}
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "PORTABLE BUILD OK"
Write-Host "zip=$zipPath"
Write-Host "sha256=$hash"
