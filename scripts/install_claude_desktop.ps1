# Adds (or updates) the "ideastatica" MCP server entry in Claude Desktop's configuration.
# A timestamped backup of the previous config is written next to it. Restart Claude Desktop afterwards.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment not found. Run scripts\setup.ps1 first."
}

$configDir = Join-Path $env:APPDATA "Claude"
$configPath = Join-Path $configDir "claude_desktop_config.json"
New-Item -ItemType Directory -Force $configDir | Out-Null

if (Test-Path $configPath) {
    $raw = Get-Content $configPath -Raw
    $backup = "$configPath.$(Get-Date -Format 'yyyyMMdd-HHmmss').bak"
    Copy-Item $configPath $backup
    Write-Host "Backup written to $backup"
    if ($raw.Trim().Length -gt 0) {
        $config = $raw | ConvertFrom-Json
    } else {
        $config = [pscustomobject]@{}
    }
} else {
    $config = [pscustomobject]@{}
}

if (-not ($config.PSObject.Properties.Name -contains "mcpServers")) {
    $config | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value ([pscustomobject]@{})
}

$entry = [pscustomobject]@{
    command = $python
    args    = @("-m", "ideastatica_mcp")
    env     = [pscustomobject]@{ IDEASTATICA_LOG_LEVEL = "INFO" }
}

if ($config.mcpServers.PSObject.Properties.Name -contains "ideastatica") {
    $config.mcpServers.ideastatica = $entry
} else {
    $config.mcpServers | Add-Member -MemberType NoteProperty -Name "ideastatica" -Value $entry
}

$json = $config | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($configPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Registered 'ideastatica' in $configPath"
Write-Host "Restart Claude Desktop, then ask: 'IDEA StatiCa servis durumunu goster' (service_status)."
