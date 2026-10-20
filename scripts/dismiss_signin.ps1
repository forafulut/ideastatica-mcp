# Closes the IDEA StatiCa licence "Sign in" window whenever it appears (same as pressing Cancel),
# so the REST service continues its start-up with local design sets instead of hanging.
# Usage: powershell -NoProfile -WindowStyle Hidden -File dismiss_signin.ps1 [-IntervalSeconds 3]
param([int]$IntervalSeconds = 3)

$log = Join-Path $env:LOCALAPPDATA "ideastatica-mcp\logs\dismiss_signin.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null

while ($true) {
    Get-Process License -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowTitle -like "Sign in*IDEA StatiCa*" } |
        ForEach-Object {
            if ($_.CloseMainWindow()) {
                Add-Content $log "$(Get-Date -Format s) closed sign-in window (pid $($_.Id))"
            }
        }
    Start-Sleep -Seconds $IntervalSeconds
}
