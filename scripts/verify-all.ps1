param(
    [switch]$SkipWindowsAudio,
    [switch]$SkipDocker,
    [switch]$SkipPackage
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
$python = "python"
if (Test-Path -LiteralPath ".venv/Scripts/python.exe") {
    $venvPython = (Resolve-Path -LiteralPath ".venv/Scripts/python.exe").Path
    $previousErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & $venvPython -c "import pytest, ruff, pip_audit" 2>$null
    $venvReady = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $previousErrorPreference
    if ($venvReady) { $python = $venvPython }
}
& $python -c "import pytest, ruff, pip_audit"
if ($LASTEXITCODE -ne 0) { throw "Install requirements-dev.txt before running full acceptance" }

Write-Host "[1/11] Compile"
& $python -m compileall -q src scripts loadtest tests
if ($LASTEXITCODE -ne 0) { throw "compile failed" }

Write-Host "[2/11] Ruff"
& $python -m ruff check src tests scripts loadtest
if ($LASTEXITCODE -ne 0) { throw "ruff failed" }

Write-Host "[3/11] Unit and integration tests"
& $python -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw "pytest failed" }

Write-Host "[4/11] CLI smoke and offline fixture acceptance"
& $python src/cloud_asr_volcengine.py --config config.example.json --smoke-test
if ($LASTEXITCODE -ne 0) { throw "CLI smoke failed" }
$offlineOutput = Join-Path $env:TEMP "meeting-ai-copilot-verify-offline"
& $python src/cloud_asr_volcengine.py --mock-demo --fixture tests/fixtures/meeting_question.wav --output-directory $offlineOutput
if ($LASTEXITCODE -ne 0) { throw "offline fixture acceptance failed" }

Write-Host "[5/11] HTTP Mock ASR/AI and performance on port 19060"
$mock = Start-Process -FilePath $python -ArgumentList @("loadtest/mock_server.py", "--port", "19060") -PassThru -WindowStyle Hidden
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:19060/health" -UseBasicParsing -TimeoutSec 1 | Out-Null
            $ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 100
        }
    }
    if (-not $ready) { throw "Mock server did not become healthy" }
    & $python scripts/demo_mock_loop.py --base-url http://127.0.0.1:19060 --fixture tests/fixtures/meeting_question.wav
    if ($LASTEXITCODE -ne 0) { throw "HTTP Mock demo failed" }
    & $python loadtest/dry_run.py --base-url http://127.0.0.1:19060 --iterations 20 --concurrency 4
    if ($LASTEXITCODE -ne 0) { throw "performance dry-run failed" }
} finally {
    if ($mock -and -not $mock.HasExited) {
        Stop-Process -Id $mock.Id -Force
        $mock.WaitForExit()
    }
}

Write-Host "[6/11] Windows host audio"
if (-not $SkipWindowsAudio) {
    & $python src/cloud_asr_volcengine.py --windows-audio-acceptance --fixture tests/fixtures/meeting_question.wav
    if ($LASTEXITCODE -ne 0) { throw "Windows audio acceptance failed" }
} else {
    Write-Host "SKIPPED by -SkipWindowsAudio"
}

Write-Host "[7/11] Docker dependency smoke"
if (-not $SkipDocker) {
    docker compose up --build --abort-on-container-exit --exit-code-from meeting-ai-copilot
    if ($LASTEXITCODE -ne 0) { throw "Docker smoke failed" }
} else {
    Write-Host "SKIPPED by -SkipDocker"
}

Write-Host "[8/11] Portable build and clean-profile smoke"
if (-not $SkipPackage) {
    & "$PSScriptRoot/build-portable.ps1" -SkipTests
    if ($LASTEXITCODE -ne 0) { throw "portable build failed" }
    $version = (Get-Content -LiteralPath "VERSION" -Raw).Trim()
    & "$PSScriptRoot/test-portable.ps1" -ZipPath "dist/meeting-ai-copilot-$version-win-x64.zip"
    if ($LASTEXITCODE -ne 0) { throw "portable smoke failed" }
} else {
    Write-Host "SKIPPED by -SkipPackage"
}

Write-Host "[9/11] Dependency audit"
& $python -m pip_audit -r requirements.txt --desc
if ($LASTEXITCODE -ne 0) { throw "dependency audit failed" }

Write-Host "[10/11] Secrets and reserved ports"
& $python scripts/repo_checks.py
if ($LASTEXITCODE -ne 0) { throw "repository checks failed" }

Write-Host "[11/11] Git whitespace"
git diff --check
if ($LASTEXITCODE -ne 0) { throw "git diff --check failed" }

Write-Host "ALL ACCEPTANCE CHECKS PASSED"
