# ============================================================
# start-with-ngrok.ps1  –  Start backend + ngrok together
# Usage:  powershell -ExecutionPolicy Bypass -File .\start-with-ngrok.ps1
# ============================================================

$ngrok    = "C:\ngrok\ngrok.exe"
$backend  = "F:\angle-tarding\backend"
$venv     = "$backend\.venv\Scripts\Activate.ps1"

Write-Host "`n=== Starting FastAPI backend ===" -ForegroundColor Cyan
# Launch backend in a new window so it keeps running
Start-Process powershell -ArgumentList `
  "-NoExit", "-Command", `
  "cd '$backend'; & '$venv'; uvicorn main:app --reload --port 8000"

Write-Host "Waiting for backend to start..." -ForegroundColor Yellow
Start-Sleep -Seconds 4

Write-Host "`n=== Starting ngrok tunnel ===" -ForegroundColor Cyan
# Start ngrok in background (port 8000)
$ngrokJob = Start-Process $ngrok -ArgumentList "http 8000" -PassThru

Write-Host "Waiting for ngrok to establish tunnel..." -ForegroundColor Yellow
Start-Sleep -Seconds 3

# Fetch the public URL from ngrok local API
try {
    $tunnels = Invoke-RestMethod -Uri "http://localhost:4040/api/tunnels" -Method GET
    $publicUrl = ($tunnels.tunnels | Where-Object { $_.proto -eq "https" }).public_url
    if (-not $publicUrl) {
        $publicUrl = $tunnels.tunnels[0].public_url
    }

    Write-Host "`n======================================================" -ForegroundColor Green
    Write-Host " ngrok tunnel is LIVE!" -ForegroundColor Green
    Write-Host "======================================================" -ForegroundColor Green
    Write-Host " Public URL : $publicUrl" -ForegroundColor White
    Write-Host " Webhook URL: $publicUrl/level-strategy/alert" -ForegroundColor Yellow
    Write-Host "======================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host " Paste this in TradingView Alert → Webhook URL:" -ForegroundColor Cyan
    Write-Host " $publicUrl/level-strategy/alert" -ForegroundColor White
    Write-Host ""
    Write-Host " TradingView Alert Message JSON:" -ForegroundColor Cyan
    Write-Host ' {"symbol": "{{ticker}}", "level": {{plot_0}}}' -ForegroundColor White
    Write-Host "======================================================`n" -ForegroundColor Green
} catch {
    Write-Host "`nCould not fetch ngrok URL automatically." -ForegroundColor Yellow
    Write-Host "Open http://localhost:4040 in your browser to see the public URL." -ForegroundColor Yellow
    Write-Host "Then append /level-strategy/alert to get your webhook URL.`n" -ForegroundColor Yellow
}

Write-Host "Press Ctrl+C to stop ngrok. The backend window stays open." -ForegroundColor Gray
Wait-Process -Id $ngrokJob.Id
