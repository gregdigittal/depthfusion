# =============================================================================
# DepthFusion — Connect to Team VPS (Windows)
# =============================================================================
# Configures Claude Desktop (and Claude Code CLI if installed) to use the
# team's shared DepthFusion memory hub on the VPS via Tailscale.
#
# Prerequisites:
#   - Tailscale installed and connected to Greg's tailnet (see instructions)
#   - Greg has approved your device in admin.tailscale.com
#
# Distribute privately — do NOT commit to GitHub.
#
# Usage:
#   PowerShell -ExecutionPolicy Bypass -File connect-vps.ps1
# =============================================================================
[CmdletBinding(SupportsShouldProcess)]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─── FILL IN AFTER RUNNING setup-tailscale-vps.sh ON THE VPS ───────────────
$VpsTailscaleIp = "100.112.109.51"
# ────────────────────────────────────────────────────────────────────────────
$VpsPort  = "7301"
$McpToken = "3cea56481975dc53587e8d99cfa989c3ab8b1c3e5e44792443832f4cf8c1f317"
$McpUrl   = "http://${VpsTailscaleIp}:${VpsPort}/sse"

function Write-Step   { param([string]$M) Write-Host "`n-- $M --" -ForegroundColor Cyan }
function Write-Ok     { param([string]$M) Write-Host "v  $M" -ForegroundColor Green }
function Write-Warn   { param([string]$M) Write-Host "!  $M" -ForegroundColor Yellow }
function Write-Fail   { param([string]$M) Write-Host "x  $M" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "   DepthFusion - Connect to Team VPS               " -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

# Sanity check: placeholder not filled in
if ($VpsTailscaleIp -eq "VPS_TAILSCALE_IP_HERE") {
    Write-Fail "VPS Tailscale IP not set. Open this script in Notepad and replace VPS_TAILSCALE_IP_HERE with the actual IP Greg gave you."
}

# =============================================================================
# 1. Tailscale — install if missing
# =============================================================================
Write-Step "Checking Tailscale"

$TailscalePath = @(
    "$env:LOCALAPPDATA\Programs\Tailscale\tailscale.exe",
    "C:\Program Files\Tailscale\tailscale.exe",
    (Get-Command tailscale -ErrorAction SilentlyContinue)?.Source
) | Where-Object { $_ -and (Test-Path $_ -ErrorAction SilentlyContinue) } | Select-Object -First 1

if (-not $TailscalePath) {
    Write-Warn "Tailscale not found. Attempting to install via winget..."
    try {
        winget install Tailscale.Tailscale --silent --accept-package-agreements --accept-source-agreements
        Write-Ok "Tailscale installed"
        Write-Host ""
        Write-Host "  Tailscale is now installed. To connect:" -ForegroundColor Yellow
        Write-Host "  1. Look for the Tailscale icon in your system tray (bottom right, near the clock)."
        Write-Host "  2. Right-click it and choose 'Log in'."
        Write-Host "  3. Sign in with Google or create a free account."
        Write-Host "  4. Message Greg with your Tailscale email to get approved."
        Write-Host "  5. Wait until the icon turns connected (not grey)."
        Write-Host "  6. Re-run this script."
        Write-Host ""
        exit 0
    } catch {
        Write-Host ""
        Write-Host "  Could not auto-install. Install Tailscale manually:" -ForegroundColor Yellow
        Write-Host "  1. Open your browser and go to: https://tailscale.com/download"
        Write-Host "  2. Download and run the Windows installer."
        Write-Host "  3. Sign in and create a free account."
        Write-Host "  4. Message Greg with your email to get approved."
        Write-Host "  5. Re-run this script once the Tailscale icon shows connected."
        Write-Host ""
        exit 1
    }
} else {
    Write-Ok "Tailscale is installed"
}

# =============================================================================
# 2. Tailscale — check it's running and connected
# =============================================================================
try {
    $StatusJson = & $TailscalePath status --json 2>$null
    $Status = ($StatusJson | python -c "import json,sys; print(json.load(sys.stdin).get('BackendState',''))" 2>$null) -replace '\r','' -replace '\n',''
} catch {
    $Status = ""
}

if ($Status -ne "Running") {
    Write-Warn "Tailscale is installed but not connected."
    Write-Host ""
    Write-Host "  To connect:" -ForegroundColor Yellow
    Write-Host "  1. Find the Tailscale icon in your system tray (bottom right, near the clock)."
    Write-Host "  2. Right-click and choose 'Log in'."
    Write-Host "  3. Message Greg with your email to get approved (if you haven't already)."
    Write-Host "  4. Wait until the icon shows connected (not grey)."
    Write-Host "  5. Re-run this script."
    Write-Host ""
    exit 1
}

Write-Ok "Tailscale is running and connected"

# =============================================================================
# 3. Verify VPS reachability over Tailscale
# =============================================================================
Write-Step "Checking VPS connectivity ($VpsTailscaleIp`:$VpsPort)"
try {
    $Response = Invoke-WebRequest -Uri "http://${VpsTailscaleIp}:${VpsPort}/health" `
        -Headers @{ Authorization = "Bearer $McpToken" } `
        -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
    Write-Ok "VPS is reachable over Tailscale"
} catch {
    Write-Warn "Cannot reach ${VpsTailscaleIp}:${VpsPort}"
    Write-Host ""
    Write-Host "  This usually means one of:" -ForegroundColor Yellow
    Write-Host "  a) Greg hasn't approved your device yet (message him your Tailscale email)"
    Write-Host "  b) The VPS is temporarily offline (ask Greg)"
    Write-Host "  c) The IP in this script is wrong"
    Write-Host ""
    Write-Host "  Try running in PowerShell: tailscale ping $VpsTailscaleIp"
    Write-Host "  If that times out, your device isn't approved yet."
    Write-Host ""
    exit 1
}

# =============================================================================
# 4. Claude Desktop
# =============================================================================
Write-Step "Registering with Claude Desktop"

$DesktopConfig = "$env:APPDATA\Claude\claude_desktop_config.json"
$DesktopDir    = Split-Path $DesktopConfig

if (-not (Test-Path $DesktopDir)) {
    New-Item -ItemType Directory -Path $DesktopDir -Force | Out-Null
}

if (Test-Path $DesktopConfig) {
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Copy-Item $DesktopConfig "$DesktopConfig.bak-$Stamp"
}

$PyScript = @"
import json, os, sys, tempfile
config_path, mcp_url = sys.argv[1], sys.argv[2]
config = {}
if os.path.exists(config_path):
    try:
        with open(config_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        sys.exit(f'Error: {config_path} is not valid JSON ({exc}). Fix or remove it, then re-run.')
config.setdefault('mcpServers', {})
config['mcpServers']['depthfusion'] = {'url': mcp_url}
d = os.path.dirname(config_path) or '.'
os.makedirs(d, exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')
try:
    with os.fdopen(fd, 'w') as tf:
        json.dump(config, tf, indent=2)
    os.replace(tmp, config_path)
except Exception:
    try: os.unlink(tmp)
    except OSError: pass
    raise
"@

$PyFile = [System.IO.Path]::GetTempFileName() + ".py"
Set-Content -Path $PyFile -Value $PyScript -Encoding UTF8
try {
    python $PyFile $DesktopConfig $McpUrl
    if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to update Claude Desktop config." }
    Write-Ok "Claude Desktop configured -> $McpUrl"
} finally {
    Remove-Item $PyFile -Force -ErrorAction SilentlyContinue
}

# =============================================================================
# 5. Claude Code CLI (optional)
# =============================================================================
$ClaudeBin = (Get-Command claude -ErrorAction SilentlyContinue).Source
if ($ClaudeBin) {
    Write-Step "Registering with Claude Code CLI"
    & $ClaudeBin mcp remove depthfusion -s user 2>$null | Out-Null
    & $ClaudeBin mcp add depthfusion --scope user $McpUrl
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Claude Code CLI registered (user-scoped)"
    } else {
        Write-Warn "Claude Code CLI registration failed — Claude Desktop is still configured."
    }
} else {
    Write-Warn "Claude Code CLI not found — only Claude Desktop configured."
}

# =============================================================================
# Done
# =============================================================================
Write-Host ""
Write-Host "===================================================" -ForegroundColor Green
Write-Host "  Connected!                                        " -ForegroundColor Green
Write-Host "===================================================" -ForegroundColor Green
Write-Host "  1. Quit and restart Claude Desktop."
Write-Host "  2. Open a new chat and type:  depthfusion_status"
Write-Host "  3. You should see the team memory hub respond."
Write-Host ""
Write-Host "  ! Keep Tailscale running in your system tray." -ForegroundColor Yellow
Write-Host "    If you quit Tailscale, the connection will stop until you restart it."
Write-Host ""
