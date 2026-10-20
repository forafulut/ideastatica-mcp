# Creates the virtual environment and installs the MCP server (run from any folder).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -e ".[dev]"
Write-Host ""
Write-Host "Installed. Quick check:"
& $py -m pytest -q tests/test_sdk_contract.py
Write-Host ""
Write-Host "Register the server in Claude Desktop with:  powershell -ExecutionPolicy Bypass -File scripts\install_claude_desktop.ps1"
