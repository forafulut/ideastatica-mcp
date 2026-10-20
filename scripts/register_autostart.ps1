# Registers a Windows scheduled task that starts the IDEA StatiCa Connection REST service at logon
# on a fixed port (default 5055), so the MCP server finds it ready immediately (attach mode).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\register_autostart.ps1 [-Port 5055] [-Remove]
param([int]$Port = 5055, [switch]$Remove)
$ErrorActionPreference = "Stop"
$taskName = "IdeaStatiCa Connection REST API ($Port)"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    [Environment]::SetEnvironmentVariable("IDEASTATICA_API_URL", $null, "User")
    Write-Host "Removed task and IDEASTATICA_API_URL."
    exit 0
}
$dir = Get-ChildItem "C:\Program Files\IDEA StatiCa" -Directory | Where-Object { Test-Path (Join-Path $_.FullName "IdeaStatiCa.ConnectionRestApi.exe") } | Sort-Object Name -Descending | Select-Object -First 1
if (-not $dir) { throw "IDEA StatiCa installation with IdeaStatiCa.ConnectionRestApi.exe not found" }
$exe = Join-Path $dir.FullName "IdeaStatiCa.ConnectionRestApi.exe"
$action = New-ScheduledTaskAction -Execute $exe -Argument "-port=$Port" -WorkingDirectory $dir.FullName
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -Hidden
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
[Environment]::SetEnvironmentVariable("IDEASTATICA_API_URL", "http://127.0.0.1:$Port", "User")
Start-ScheduledTask -TaskName $taskName
Write-Host "Task '$taskName' registered and started. IDEASTATICA_API_URL=http://127.0.0.1:$Port set for the user."
Write-Host "Restart Claude Desktop / Claude Code so the MCP server picks up the variable (or add it to the server's env)."
