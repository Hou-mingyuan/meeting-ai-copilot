param(
    [string]$OutputPath = "docs/media/mock-demo-60s.mp4",
    [int]$DurationSeconds = 60
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue)) {
    throw "ffmpeg.exe is required to record the real console window"
}
$output = [System.IO.Path]::GetFullPath((Join-Path $root $OutputPath))
New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($output)) -Force | Out-Null
if (Test-Path -LiteralPath $output) { Remove-Item -LiteralPath $output -Force }

$title = "Meeting AI Copilot Mock Demo"
$demoDuration = $DurationSeconds + 3
$command = "mode con: cols=120 lines=14 >nul & title $title & python src\cloud_asr_volcengine.py --mock-tui-demo --fixture tests\fixtures\meeting_question.wav --demo-duration $demoDuration"
$cmdArguments = "/d /c `"$command`""
$demo = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdArguments -WorkingDirectory $root -PassThru -WindowStyle Normal
try {
    $windowHandle = 0
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 100
        $process = Get-Process -Id $demo.Id -ErrorAction SilentlyContinue
        if ($process -and $process.MainWindowHandle -ne 0) {
            $windowHandle = $process.MainWindowHandle.ToInt64()
            break
        }
    }
    if ($windowHandle -eq 0) { throw "Mock TUI window was not created" }
    $windowInput = "hwnd=0x{0:X}" -f $windowHandle
    & ffmpeg.exe -hide_banner -loglevel error -y -f gdigrab -framerate 15 -i $windowInput -t $DurationSeconds -c:v libx264 -preset veryfast -crf 24 -pix_fmt yuv420p $output
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg recording failed" }
    $demo.WaitForExit(10000) | Out-Null
    if (-not $demo.HasExited) { throw "Mock TUI demo did not exit" }
    if ($demo.ExitCode -ne 0) { throw "Mock TUI demo exited with $($demo.ExitCode)" }
} finally {
    if ($demo -and -not $demo.HasExited) { Stop-Process -Id $demo.Id -Force }
}
Write-Host "MOCK VIDEO RECORDED: $output"
