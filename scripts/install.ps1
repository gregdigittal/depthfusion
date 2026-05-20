#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# DepthFusion installer — Windows
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install.ps1

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = if ($Env:DEPTHFUSION_VENV_PATH) { $Env:DEPTHFUSION_VENV_PATH } else { Join-Path $HOME ".depthfusion-venv" }
$ConfigDir = if ($Env:CLAUDE_CONFIG_DIR) { $Env:CLAUDE_CONFIG_DIR } else { Join-Path $Env:APPDATA "Claude" }
$EnvFile = Join-Path $ConfigDir "depthfusion.env"
$DesktopConfig = Join-Path $ConfigDir "claude_desktop_config.json"

Write-Host "DepthFusion Installer"
Write-Host "====================="

# 1. Python version check
try { $PyOut = & python --version 2>&1 } catch { Write-Error "python not found. Install Python 3.10+ from python.org."; exit 1 }
if ($PyOut -match "Python (\d+)\.(\d+)") {
    $Major = [int]$Matches[1]; $Minor = [int]$Matches[2]
    if ($Major -lt 3 -or ($Major -eq 3 -and $Minor -lt 10)) { Write-Error "Python 3.10+ required (found $PyOut)"; exit 1 }
}
Write-Host "✓ $PyOut"

# 2. Create venv
Write-Host "Creating virtual environment at $VenvPath ..."
python -m venv $VenvPath
Write-Host "✓ Virtual environment created"

# 3. Install DepthFusion
Write-Host "Installing DepthFusion (this may take a minute) ..."
& "$VenvPath\Scripts\pip.exe" install --quiet -e "$RepoRoot[local]"
Write-Host "✓ DepthFusion installed"

# 4. Get API key
Write-Host ""
Write-Host "Get your DepthFusion API key from: claude.ai/settings -> API Keys"
Write-Host "(This is NOT the same as your Claude Code subscription key)"
Write-Host ""
$SecureKey = Read-Host "DEPTHFUSION_API_KEY" -AsSecureString
$ApiKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey))

# Guard: refuse Claude Code's own billing key
if ($ApiKey -match '^sk-ant-api03-') {
    Write-Error "That looks like a Claude Code API key (used for subscription billing). Your DepthFusion API key is different — get it from claude.ai/settings -> API Keys."
    exit 1
}
if ([string]::IsNullOrWhiteSpace($ApiKey)) { Write-Error "API key cannot be empty."; exit 1 }

# 5. Write env file (restrict to current user)
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
Set-Content -Path $EnvFile -Value "DEPTHFUSION_API_KEY=$ApiKey"
$Acl = Get-Acl $EnvFile
$Acl.SetAccessRuleProtection($true, $false)
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Rule = New-Object System.Security.AccessControl.FileSystemAccessRule($CurrentUser, "FullControl", "Allow")
$Acl.AddAccessRule($Rule)
Set-Acl $EnvFile $Acl
Write-Host "✓ API key saved to $EnvFile"

# 6. Register MCP server
$PythonBin = Join-Path $VenvPath "Scripts\python.exe"
$McpEntry = @{ command = $PythonBin; args = @("-m", "depthfusion.mcp"); env = @{ DEPTHFUSION_ENV_FILE = $EnvFile } }
if (-not (Test-Path $DesktopConfig)) {
    @{ mcpServers = @{ depthfusion = $McpEntry } } | ConvertTo-Json -Depth 5 | Set-Content $DesktopConfig
} else {
    $Config = Get-Content $DesktopConfig -Raw | ConvertFrom-Json
    if (-not $Config.PSObject.Properties['mcpServers']) { $Config | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([PSCustomObject]@{}) }
    $Config.mcpServers | Add-Member -NotePropertyName depthfusion -NotePropertyValue $McpEntry -Force
    $Config | ConvertTo-Json -Depth 5 | Set-Content $DesktopConfig
}
Write-Host "✓ MCP server registered in $DesktopConfig"

Write-Host ""
Write-Host "Installation complete. Restart Claude Desktop to activate DepthFusion."
