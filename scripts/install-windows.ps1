# =============================================================================
# DepthFusion — Windows Standalone Installer
# =============================================================================
# Designed to run via:
#   iwr https://raw.githubusercontent.com/gregdigittal/depthfusion/main/scripts/install-windows.ps1 | iex
# or:
#   PowerShell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
#
# What this does:
#   1. Verifies Windows 10/11 (64-bit) and PowerShell 5.1+
#   2. Sets ExecutionPolicy to RemoteSigned for this user
#   3. Installs Git via winget if missing
#   4. Installs Python 3.12 via winget if Python 3.11+ not present
#   5. Clones the DepthFusion repo (or updates it if already present)
#   6. Creates a dedicated venv at %USERPROFILE%\.depthfusion-venv
#   7. Detects NVIDIA GPU — installs CUDA-enabled PyTorch if found
#   8. Installs DepthFusion with the appropriate extras
#   9. Prompts for API key and writes %USERPROFILE%\.claude\depthfusion.env
#  10. Registers DepthFusion with Claude Desktop
#  11. Optionally registers with Claude Code CLI (if installed)
#  12. Registers as a Windows startup task so the server auto-starts at login
#  13. Starts the DepthFusion server and runs a smoke test
#
# Safe to re-run — idempotent throughout.
# =============================================================================
#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepoUrl    = "https://github.com/gregdigittal/depthfusion.git",
    [string]$RepoDir    = "$env:USERPROFILE\depthfusion",
    [string]$VenvDir    = "$env:USERPROFILE\.depthfusion-venv",
    [string]$ConfigDir  = "$env:USERPROFILE\.claude",
    [int]   $RestPort   = 7300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Colour helpers ────────────────────────────────────────────────────────────
function Write-Step   { param([string]$Msg) Write-Host "-> $Msg" -ForegroundColor Cyan }
function Write-Ok     { param([string]$Msg) Write-Host "v  $Msg" -ForegroundColor Green }
function Write-Warn   { param([string]$Msg) Write-Host "!  $Msg" -ForegroundColor Yellow }
function Write-Fail   { param([string]$Msg) Write-Host "x  $Msg" -ForegroundColor Red }

function Assert-Ok {
    param([bool]$Cond, [string]$Msg)
    if (-not $Cond) { Write-Fail $Msg; exit 1 }
}

function Invoke-SafeCmd {
    param([string]$Cmd, [string[]]$Args, [string]$Desc)
    Write-Step $Desc
    & $Cmd @Args
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Command failed ($LASTEXITCODE): $Cmd $Args"
        exit 1
    }
}

# ── Header ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "   DepthFusion - Windows Installer                 " -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

# =============================================================================
# STEP 1 — OS and architecture checks
# =============================================================================
Write-Step "Checking OS and architecture..."

$OSVersion = [System.Environment]::OSVersion.Version
Assert-Ok ($OSVersion.Major -ge 10) "Windows 10 or later required."

$Arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
Assert-Ok ($Arch -eq [System.Runtime.InteropServices.Architecture]::X64) "64-bit Windows required."

Write-Ok "Windows $($OSVersion.Major).$($OSVersion.Minor) x64"

# =============================================================================
# STEP 2 — ExecutionPolicy (allows running downloaded scripts)
# =============================================================================
$Policy = Get-ExecutionPolicy -Scope CurrentUser
if ($Policy -eq "Restricted" -or $Policy -eq "AllSigned") {
    Write-Step "Setting PowerShell execution policy to RemoteSigned for current user..."
    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
    Write-Ok "ExecutionPolicy set to RemoteSigned"
} else {
    Write-Ok "ExecutionPolicy already permits scripts ($Policy)"
}

# =============================================================================
# STEP 3 — winget availability
# =============================================================================
Write-Step "Checking winget (Windows Package Manager)..."
try {
    $wingetVersion = (winget --version 2>&1)
    Write-Ok "winget $wingetVersion"
} catch {
    Write-Warn "winget not found. Install 'App Installer' from the Microsoft Store,"
    Write-Warn "then re-run this script."
    Write-Warn "  ms-windows-store://pdp/?ProductId=9NBLGGH4NNS1"
    exit 1
}

# =============================================================================
# STEP 4 — Git
# =============================================================================
Write-Step "Checking Git..."
$GitBin = ""
foreach ($candidate in @("git")) {
    try {
        $GitBin = (Get-Command $candidate -ErrorAction SilentlyContinue).Source
        if ($GitBin) { break }
    } catch {}
}

if (-not $GitBin) {
    Write-Step "Installing Git via winget..."
    winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $GitBin = (Get-Command git -ErrorAction SilentlyContinue).Source
    Assert-Ok ($null -ne $GitBin) "Git not found after install — open a new PowerShell window and re-run."
}
Write-Ok "Git: $GitBin"

# =============================================================================
# STEP 5 — Python 3.11+
# =============================================================================
Write-Step "Checking Python 3.11+..."
$PythonBin = ""
$PythonVer  = ""

foreach ($candidate in @("python3.12", "python3.11", "python3", "python")) {
    try {
        $bin = (Get-Command $candidate -ErrorAction SilentlyContinue).Source
        if (-not $bin) { continue }
        $verOut = & $bin -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
        if ($verOut -match "^(\d+)\.(\d+)$") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                $PythonBin = $bin; $PythonVer = $verOut; break
            }
        }
    } catch {}
}

if (-not $PythonBin) {
    Write-Step "Python 3.11+ not found — installing Python 3.12 via winget..."
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" +
                "$env:LOCALAPPDATA\Programs\Python\Python312;$env:LOCALAPPDATA\Programs\Python\Python312\Scripts"
    foreach ($candidate in @("python3.12", "python3", "python")) {
        try {
            $bin = (Get-Command $candidate -ErrorAction SilentlyContinue).Source
            if (-not $bin) { continue }
            $verOut = & $bin -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
            if ($verOut -match "^\d+\.\d+$") { $PythonBin = $bin; $PythonVer = $verOut; break }
        } catch {}
    }
    Assert-Ok ($null -ne $PythonBin) "Python not found after install — open a new PowerShell window and re-run."
}
Write-Ok "Python $PythonVer ($PythonBin)"

# =============================================================================
# STEP 6 — Clone or update repo
# =============================================================================
Write-Step "Setting up DepthFusion repo at $RepoDir..."
if (Test-Path "$RepoDir\.git") {
    Write-Step "Updating existing repo..."
    & git -C $RepoDir pull --ff-only 2>&1 | Out-Null
    Write-Ok "Repo updated"
} else {
    & git clone $RepoUrl $RepoDir
    Assert-Ok ($LASTEXITCODE -eq 0) "git clone failed — check your internet connection."
    Write-Ok "Repo cloned to $RepoDir"
}

# =============================================================================
# STEP 7 — Virtual environment
# =============================================================================
Write-Step "Setting up virtual environment at $VenvDir..."
$VenvPython = "$VenvDir\Scripts\python.exe"
$VenvPip    = "$VenvDir\Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    & $PythonBin -m venv $VenvDir
    Assert-Ok ($LASTEXITCODE -eq 0) "Failed to create virtual environment."
    Write-Ok "Virtual environment created"
} else {
    Write-Ok "Re-using existing virtual environment"
}

# =============================================================================
# STEP 8 — GPU detection and extras selection
# =============================================================================
Write-Step "Detecting GPU..."

$HasNvidiaGPU = $false
$NvidiaGPUName = ""

try {
    $gpuInfo = nvidia-smi --query-gpu=name --format=csv,noheader 2>&1
    if ($LASTEXITCODE -eq 0 -and $gpuInfo -notmatch "FAILED|error") {
        $HasNvidiaGPU = $true
        $NvidiaGPUName = $gpuInfo.Trim()
    }
} catch {}

if ($HasNvidiaGPU) {
    Write-Ok "NVIDIA GPU detected: $NvidiaGPUName"
    $InstallExtras = "local"   # base install first, then add PyTorch CUDA separately
    $InstallCuda   = $true
} else {
    Write-Ok "No NVIDIA GPU detected — CPU-only mode"
    $InstallExtras = "local"
    $InstallCuda   = $false
}

# =============================================================================
# STEP 9 — Install DepthFusion
# =============================================================================
Write-Step "Upgrading pip..."
& $VenvPip install --quiet --upgrade pip
Assert-Ok ($LASTEXITCODE -eq 0) "pip upgrade failed."

if ($InstallCuda) {
    Write-Step "Installing PyTorch with CUDA support (this may take 5-10 minutes)..."
    & $VenvPip install --quiet torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "PyTorch CUDA install failed — falling back to CPU-only."
        $InstallCuda = $false
    } else {
        Write-Ok "PyTorch with CUDA installed"
    }
    Write-Step "Installing sentence-transformers for GPU-accelerated embeddings..."
    & $VenvPip install --quiet sentence-transformers
}

Write-Step "Installing DepthFusion[$InstallExtras] (may take 2-5 minutes)..."
& $VenvPip install --quiet -e "$RepoDir[$InstallExtras]"
Assert-Ok ($LASTEXITCODE -eq 0) "DepthFusion install failed."

# HNSW optional extra for faster vector search
Write-Step "Installing hnswlib for fast vector search..."
& $VenvPip install --quiet hnswlib
if ($LASTEXITCODE -ne 0) {
    Write-Warn "hnswlib install failed (optional) — using fallback linear search."
}

Write-Ok "DepthFusion installed"

# =============================================================================
# STEP 10 — API key
# =============================================================================
Write-Host ""
Write-Host "  Your DepthFusion API key comes from:" -ForegroundColor Cyan
Write-Host "    https://console.anthropic.com/settings/keys" -ForegroundColor White
Write-Host ""
Write-Host "  IMPORTANT: This is NOT your Claude Pro/Max subscription." -ForegroundColor Yellow
Write-Host "  It is a separate API key for DepthFusion's reranking calls." -ForegroundColor Yellow
Write-Host ""

$EnvFile = "$ConfigDir\depthfusion.env"

# Check for existing valid key
$ExistingKey = ""
if (Test-Path $EnvFile) {
    $ExistingKey = (Select-String -Path $EnvFile -Pattern "^DEPTHFUSION_API_KEY=.+" 2>$null |
                    ForEach-Object { $_.Line.Substring("DEPTHFUSION_API_KEY=".Length) } |
                    Select-Object -First 1)
}

if ($ExistingKey) {
    Write-Warn "Found existing API key in $EnvFile — skipping prompt."
    Write-Warn "Delete that line and re-run to change it."
    $ApiKey = $ExistingKey
} else {
    $ApiKey = ""
    while ($true) {
        $SecureKey = Read-Host "  Paste your API key" -AsSecureString
        $ApiKey = [System.Net.NetworkCredential]::new("", $SecureKey).Password
        if (-not $ApiKey) { Write-Warn "Key cannot be empty — try again."; continue }
        # Refuse Claude Code billing key
        if ($ApiKey -match "^sk-ant-api03-") {
            Write-Warn "That looks like a Claude Code billing key (starts with sk-ant-api03-)."
            Write-Warn "DepthFusion needs a SEPARATE key from console.anthropic.com -> API keys."
            continue
        }
        break
    }
}

# Write env file
if (-not (Test-Path $ConfigDir)) { New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null }

# Preserve existing entries, replace DEPTHFUSION_API_KEY
$EnvLines = @()
if (Test-Path $EnvFile) {
    $EnvLines = Get-Content $EnvFile | Where-Object { $_ -notmatch "^DEPTHFUSION_API_KEY=" }
}

$NewLines = @(
    "DEPTHFUSION_API_KEY=$ApiKey",
    "DEPTHFUSION_MODE=local",
    "DEPTHFUSION_HNSW_ENABLED=true",
    "DEPTHFUSION_GRAPH_ENABLED=true",
    "DEPTHFUSION_VECTOR_SEARCH_ENABLED=true",
    "DEPTHFUSION_TIER_AUTOPROMOTE=true",
    "DEPTHFUSION_RERANKER_ENABLED=true",
    "DEPTHFUSION_EMBEDDING_BACKEND=local",
    "DEPTHFUSION_TIER_THRESHOLD=500",
    "DEPTHFUSION_HAIKU_ENABLED=true",
    "DEPTHFUSION_REST_API=true"
)

# Remove keys we're about to set, then append
$AllKeys = $NewLines | ForEach-Object { ($_ -split "=")[0] }
$Kept = $EnvLines | Where-Object {
    $key = ($_ -split "=")[0]
    $AllKeys -notcontains $key
}
$Final = ($Kept + $NewLines) | Select-Object -Unique

$TempEnv = "$EnvFile.tmp"
$Final | Set-Content -Path $TempEnv -Encoding UTF8
Move-Item -Force $TempEnv $EnvFile

# Restrict permissions: current user only
$Acl = Get-Acl $EnvFile
$Acl.SetAccessRuleProtection($true, $false)
$Rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $env:USERNAME, "FullControl", "Allow")
$Acl.AddAccessRule($Rule)
Set-Acl -Path $EnvFile -AclObject $Acl 2>$null

Write-Ok "API key and config written to $EnvFile"

# =============================================================================
# STEP 11 — Windows startup task (Task Scheduler)
# =============================================================================
Write-Step "Registering DepthFusion as a Windows startup task..."

$TaskName    = "DepthFusion MCP Server"
$StartupCmd  = "$VenvDir\Scripts\python.exe"
$StartupArgs = "-m depthfusion.mcp.server"
$LogFile     = "$env:USERPROFILE\AppData\Local\DepthFusion\depthfusion-rest.log"

New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null

# Remove stale task if present
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false | Out-Null
}

$Action    = New-ScheduledTaskAction -Execute $StartupCmd -Argument $StartupArgs -WorkingDirectory $RepoDir
$Trigger   = New-ScheduledTaskTrigger -AtLogOn
$Settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartOnIdle -StartWhenAvailable `
             -MultipleInstances IgnoreNew -RunOnlyIfNetworkAvailable:$false
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Principal -Description "DepthFusion memory intelligence MCP server" |
    Out-Null

Write-Ok "Startup task registered: '$TaskName' (runs at login)"

# =============================================================================
# STEP 12 — Register with Claude Desktop
# =============================================================================
Write-Step "Registering with Claude Desktop..."

$DesktopConfig = "$env:APPDATA\Claude\claude_desktop_config.json"
$DesktopDir    = Split-Path $DesktopConfig

if (-not (Test-Path $DesktopDir)) {
    New-Item -ItemType Directory -Path $DesktopDir -Force | Out-Null
}

# Backup before mutation
if (Test-Path $DesktopConfig) {
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Copy-Item $DesktopConfig "$DesktopConfig.bak-$Stamp"
}

$PyScript = @"
import json, os, sys, tempfile

config_path  = sys.argv[1]
python_bin   = sys.argv[2]
env_file_arg = sys.argv[3]

config = {}
if os.path.exists(config_path):
    try:
        with open(config_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        sys.exit(f'Error: {config_path} is not valid JSON ({exc}). Fix or remove it, then re-run.')

config.setdefault('mcpServers', {})
config['mcpServers']['depthfusion'] = {
    'command': python_bin,
    'args': ['-m', 'depthfusion.mcp.server'],
    'env': {'DEPTHFUSION_ENV_FILE': env_file_arg},
}

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

$PyTempFile = [System.IO.Path]::GetTempFileName() + ".py"
Set-Content -Path $PyTempFile -Value $PyScript -Encoding UTF8

try {
    & $VenvPython $PyTempFile $DesktopConfig $VenvPython $EnvFile
    Assert-Ok ($LASTEXITCODE -eq 0) "Failed to register with Claude Desktop."
    Write-Ok "Claude Desktop registered ($DesktopConfig)"
} finally {
    Remove-Item $PyTempFile -Force -ErrorAction SilentlyContinue
}

# =============================================================================
# STEP 13 — Claude Code CLI (optional)
# =============================================================================
$ClaudeBin = (Get-Command claude -ErrorAction SilentlyContinue).Source
if ($ClaudeBin) {
    Write-Step "Registering with Claude Code CLI..."
    & $ClaudeBin mcp remove depthfusion -s user 2>$null | Out-Null
    & $ClaudeBin mcp add depthfusion --scope user $VenvPython -m depthfusion.mcp.server
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Claude Code CLI registered (user-scoped MCP)"
    } else {
        Write-Warn "Claude Code CLI registration failed — Claude Desktop is still registered."
    }
} else {
    Write-Warn "Claude Code CLI not found — skipping (Claude Desktop is registered)."
    Write-Step "If you install Claude Code CLI later, run:"
    Write-Host "  claude mcp add depthfusion --scope user $VenvPython -m depthfusion.mcp.server" -ForegroundColor White
}

# =============================================================================
# STEP 14 — Start the server and smoke test
# =============================================================================
Write-Step "Starting DepthFusion server..."

# Start the server in a background job
$ServerJob = Start-Job -ScriptBlock {
    param($python, $repoDir, $envFile)
    $env:DEPTHFUSION_ENV_FILE = $envFile
    & $python -m depthfusion.mcp.server 2>&1
} -ArgumentList $VenvPython, $RepoDir, $EnvFile

# Wait for the REST API to become healthy (up to 30 seconds)
Write-Step "Waiting for server to become ready..."
$Ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $Response = Invoke-WebRequest -Uri "http://127.0.0.1:$RestPort/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($Response.StatusCode -eq 200) { $Ready = $true; break }
    } catch {}
    Write-Host "." -NoNewline
}
Write-Host ""

if ($Ready) {
    Write-Ok "Server is healthy at http://127.0.0.1:$RestPort"

    # Quick recall smoke test
    $SmokeScript = @"
from depthfusion.mcp.server import _tool_recall
import json
result = json.loads(_tool_recall({'query': 'install verification test', 'top_k': 1}))
print(f'blocks={len(result.get(\"blocks\", []))} error={result.get(\"error\", \"none\")}')
"@
    $SmokeFile = [System.IO.Path]::GetTempFileName() + ".py"
    Set-Content -Path $SmokeFile -Value $SmokeScript -Encoding UTF8
    try {
        $SmokeResult = & $VenvPython $SmokeFile 2>&1
        Write-Ok "Smoke test passed: $SmokeResult"
    } catch {
        Write-Warn "Smoke test skipped ($($_.Exception.Message))"
    } finally {
        Remove-Item $SmokeFile -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Warn "Server not yet responding after 30 seconds."
    Write-Warn "Check: Get-Content '$env:USERPROFILE\AppData\Local\DepthFusion\depthfusion-rest.log'"
    Write-Warn "The startup task will start the server automatically at your next login."
}

# The background job stays running — it is the server. Don't stop it.
Write-Ok "Server running as background job (Job ID: $($ServerJob.Id))"

# =============================================================================
# Done
# =============================================================================
Write-Host ""
Write-Host "===================================================================" -ForegroundColor Green
Write-Host "  Installation complete!                                            " -ForegroundColor Green
Write-Host "===================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Cyan
Write-Host "  1. Restart Claude Desktop to load DepthFusion." -ForegroundColor White
Write-Host "  2. Open a new chat and type:  depthfusion_status" -ForegroundColor White
Write-Host "  3. You should see version info confirming the connection." -ForegroundColor White
Write-Host ""
Write-Host "  The server starts automatically at login via Task Scheduler." -ForegroundColor Cyan
Write-Host "  Log: $env:USERPROFILE\AppData\Local\DepthFusion\depthfusion-rest.log" -ForegroundColor White
if ($HasNvidiaGPU) {
    Write-Host ""
    Write-Host "  GPU acceleration: ENABLED ($NvidiaGPUName)" -ForegroundColor Green
    Write-Host "  Embeddings run on your GPU for faster semantic search." -ForegroundColor White
}
Write-Host ""
