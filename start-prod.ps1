param(
  [int]$Port = 8000,
  [switch]$Rebuild = $false,
  [switch]$NoBrowser = $false,
  [switch]$NoLogFile = $false
)

# Runs the packaged app in single-port "prod" mode: one uvicorn process serves
# both the API and the pre-built frontend (frontend/dist) on a free port. Logs
# stream to this console (and, by default, to logs/prod-<timestamp>.log). The
# dev flow (start-dev.cmd) is separate and unaffected.

$ErrorActionPreference = 'Stop'

function Test-PortFree {
  param([int]$Port)
  $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($conn) { return $false }
  try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    $listener.Start()
    $listener.Stop()
    return $true
  } catch {
    return $false
  }
}

function Get-FreePort {
  param([int]$StartPort, [int]$MaxTries = 50)
  for ($i = 0; $i -lt $MaxTries; $i++) {
    $candidate = $StartPort + $i
    if (Test-PortFree -Port $candidate) { return $candidate }
  }
  throw "No free port found in range $StartPort..$($StartPort + $MaxTries - 1)"
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $scriptRoot 'frontend'
$distDir = Join-Path $frontendDir 'dist'
$venvPython = Join-Path $scriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path (Join-Path $scriptRoot 'server.py'))) {
  throw "Backend entry point server.py not found in: $scriptRoot"
}

# ── Load .env (same convention as start-dev) ─────────────────────────────────
$envFile = Join-Path $scriptRoot ".env"
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
      $key = $matches[1].Trim()
      $value = $matches[2].Trim()
      [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
  }
  Write-Host "[env] Loaded .env file"
} else {
  Write-Host "[env] .env not found, using defaults."
}

# ── Build artifacts if missing or -Rebuild ───────────────────────────────────
if ($Rebuild -or -not (Test-Path $distDir) -or -not (Test-Path $venvPython)) {
  Write-Host "Building production artifacts (first run or -Rebuild)..."
  & (Join-Path $scriptRoot 'build-prod.ps1')
  if ($LASTEXITCODE -ne 0) { throw "build-prod failed" }
}
if (-not (Test-Path $distDir)) { throw "Frontend dist not found: $distDir. Run build-prod.cmd first." }

# Prefer the project-local venv; fall back to system python.
if (Test-Path $venvPython) {
  $pythonExe = $venvPython
  Write-Host "Using virtual environment: $venvPython"
} else {
  Write-Host "WARNING: .venv not found. Falling back to system python..."
  $pythonExe = 'python'
}

# ── Resolve a free port ──────────────────────────────────────────────────────
$requestedPort = $Port
$Port = Get-FreePort -StartPort $Port
if ($Port -ne $requestedPort) {
  Write-Host "Port $requestedPort is in use, falling back to $Port"
}

# Persist the resolved port so it can be reused / stopped later.
$stateFile = Join-Path $scriptRoot '.vt-prod-state.json'
@{
  port      = $Port
  startedAt = (Get-Date).ToString('o')
} | ConvertTo-Json | Set-Content -Path $stateFile -Encoding UTF8

# ── Optional log file (console always streams regardless) ────────────────────
$logFile = $null
if (-not $NoLogFile) {
  $logsDir = Join-Path $scriptRoot 'logs'
  if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $logFile = Join-Path $logsDir "prod-$stamp.log"
}

# ── Open the browser once the port is listening (background) ─────────────────
if (-not $NoBrowser) {
  Start-Job -Name 'vt-prod-open' -ScriptBlock {
    param([int]$Port)
    for ($i = 0; $i -lt 180; $i++) {
      $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
      if ($conn) { Start-Process "http://localhost:$Port"; break }
      Start-Sleep -Seconds 1
    }
  } -ArgumentList $Port | Out-Null
}

Write-Host ""
Write-Host "==============================================="
Write-Host " Video Transcript (prod) starting"
Write-Host "   URL     : http://localhost:$Port"
Write-Host "   Backend : $pythonExe"
Write-Host "   Frontend: $distDir"
if ($logFile) { Write-Host "   Log     : $logFile" }
Write-Host " Press Ctrl+C to stop."
Write-Host "==============================================="
Write-Host ""

# Run uvicorn in the foreground with cwd = scriptRoot so data/, logs/ and the
# frontend/dist lookup in app/main.py all resolve relative to the repo root.
# No --reload in prod.
Push-Location $scriptRoot
# uvicorn/dependencies write normal warnings to stderr; with $ErrorActionPreference='Stop'
# a merged stderr line would surface as a terminating NativeCommandError and abort the
# launch, so relax it to 'Continue' while the server runs in the foreground.
$ErrorActionPreference = 'Continue'
try {
  $uvicornArgs = @(
    '-m', 'uvicorn', 'server:app',
    '--host', '0.0.0.0',
    '--timeout-graceful-shutdown', '5',
    '--port', "$Port"
  )
  if ($logFile) {
    & $pythonExe @uvicornArgs 2>&1 | Tee-Object -FilePath $logFile
  } else {
    & $pythonExe @uvicornArgs 2>&1
  }
} finally {
  Pop-Location
  Get-Job -Name 'vt-prod-open' -ErrorAction SilentlyContinue | Remove-Job -Force -ErrorAction SilentlyContinue
  if (Test-Path $stateFile) { Remove-Item $stateFile -ErrorAction SilentlyContinue }
}
