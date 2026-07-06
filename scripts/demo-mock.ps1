# 零密钥 Mock 演示（Windows PowerShell）
# 启动 mock ASR/AI 服务并跑通 会议→转写→AI 流式答案 闭环
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot\..

$port = 8765
$base = "http://127.0.0.1:$port"

Write-Host "=== meeting-ai-copilot Mock 演示（零密钥）===" -ForegroundColor Cyan

$mockJob = Start-Job -ScriptBlock {
    param($root, $p)
    Set-Location $root
    python loadtest/mock_server.py --port $p
} -ArgumentList (Get-Location).Path, $port

try {
    for ($i = 1; $i -le 30; $i++) {
        try {
            Invoke-WebRequest -Uri "$base/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
            break
        } catch {
            Start-Sleep -Seconds 1
        }
        if ($i -eq 30) { throw "Mock 服务启动超时" }
    }

    python scripts/demo_mock_loop.py --base-url $base
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "`n提示: 真实会议转写请使用 config.json + 启动云端实时转写和AI答案.bat" -ForegroundColor Gray
} finally {
    Stop-Job $mockJob -ErrorAction SilentlyContinue
    Remove-Job $mockJob -Force -ErrorAction SilentlyContinue
}
