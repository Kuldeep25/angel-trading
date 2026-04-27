# ============================================================
# test-webhook.ps1  –  Test the Level Strategy webhook locally
# Usage: powershell -ExecutionPolicy Bypass -File .\test-webhook.ps1
# ============================================================

$baseUrl = "http://localhost:8000"
$endpoint = "$baseUrl/level-strategy/alert"

Write-Host "`n=== Level Strategy Webhook Test ===" -ForegroundColor Cyan
Write-Host "Endpoint: $endpoint`n"

# ── Test 1: Auto-detect type (no type field sent) ──────────
Write-Host "Test 1: Auto-detect SUPPORT/RESISTANCE (no type sent)" -ForegroundColor Yellow
$body1 = '{"symbol":"NIFTY","level":23500}'
try {
    $r1 = Invoke-RestMethod -Uri $endpoint -Method POST `
          -ContentType "application/json" -Body $body1
    Write-Host "  PASS  Response: $($r1 | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch {
    Write-Host "  FAIL  $($_.Exception.Message)" -ForegroundColor Red
}

# ── Test 2: Explicit RESISTANCE ───────────────────────────
Write-Host "`nTest 2: Explicit RESISTANCE level" -ForegroundColor Yellow
$body2 = '{"symbol":"BANKNIFTY","level":48000,"type":"RESISTANCE"}'
try {
    $r2 = Invoke-RestMethod -Uri $endpoint -Method POST `
          -ContentType "application/json" -Body $body2
    Write-Host "  PASS  Response: $($r2 | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch {
    Write-Host "  FAIL  $($_.Exception.Message)" -ForegroundColor Red
}

# ── Test 3: Explicit SUPPORT ──────────────────────────────
Write-Host "`nTest 3: Explicit SUPPORT level" -ForegroundColor Yellow
$body3 = '{"symbol":"RELIANCE","level":2900,"type":"SUPPORT"}'
try {
    $r3 = Invoke-RestMethod -Uri $endpoint -Method POST `
          -ContentType "application/json" -Body $body3
    Write-Host "  PASS  Response: $($r3 | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch {
    Write-Host "  FAIL  $($_.Exception.Message)" -ForegroundColor Red
}

# ── Test 4: Invalid type (should return 422) ──────────────
Write-Host "`nTest 4: Invalid type field (expect 422 validation error)" -ForegroundColor Yellow
$body4 = '{"symbol":"NIFTY","level":23000,"type":"BREAKOUT"}'
try {
    $r4 = Invoke-RestMethod -Uri $endpoint -Method POST `
          -ContentType "application/json" -Body $body4
    Write-Host "  UNEXPECTED PASS (should have failed): $($r4 | ConvertTo-Json -Compress)" -ForegroundColor Magenta
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 422) {
        Write-Host "  PASS  Got expected 422 Unprocessable Entity" -ForegroundColor Green
    } else {
        Write-Host "  INFO  Status $code : $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

Write-Host "`n=== Tests complete ===" -ForegroundColor Cyan
