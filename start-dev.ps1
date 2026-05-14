param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 5173,
  [switch]$StopExisting = $false
)

$ErrorActionPreference = 'Stop'

function Stop-ListeningProcessByPort {
  param([int]$Port)

  $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if (-not $connections) {
    Write-Host "No listening process on port $Port"
    return
  }

  $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($processId in $processIds) {
    try {
      Stop-Process -Id $processId -Force -ErrorAction Stop
      Write-Host "Stopped process $processId on port $Port"
    } catch {
      Write-Host "Failed to stop process $processId on port ${Port}: $($_.Exception.Message)"
    }
  }
}

function Test-PortListening {
  param([int]$Port)

  $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  return $null -ne $connections
}

function Get-NextAvailablePort {
  param(
    [int]$StartPort,
    [int[]]$ReservedPorts = @(),
    [int]$MaxSteps = 100
  )

  $candidate = $StartPort
  for ($i = 0; $i -le $MaxSteps; $i++) {
    if (($ReservedPorts -contains $candidate) -or (Test-PortListening -Port $candidate)) {
      $candidate++
      continue
    }

    return $candidate
  }

  throw "Could not find an available port after checking $MaxSteps ports from $StartPort"
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $scriptRoot 'frontend'

if (-not (Test-Path $frontendDir)) {
  throw "Frontend directory not found: $frontendDir"
}

if (-not (Test-Path (Join-Path $scriptRoot 'server.py'))) {
  throw "Backend entry point server.py not found in: $scriptRoot"
}

# Prefer the project-local virtual environment; fall back to system python
$venvPython = Join-Path $scriptRoot '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
  $pythonExe = $venvPython
  Write-Host "Using virtual environment: $venvPython"
} else {
  Write-Host "WARNING: .venv not found. Run 'npm run setup' first to create it."
  Write-Host "Falling back to system python..."
  $pythonExe = 'python'
}

if ($StopExisting) {
  Stop-ListeningProcessByPort -Port $BackendPort
  Stop-ListeningProcessByPort -Port $FrontendPort
}

$requestedBackendPort = $BackendPort
$requestedFrontendPort = $FrontendPort

$BackendPort = Get-NextAvailablePort -StartPort $BackendPort
$FrontendPort = Get-NextAvailablePort -StartPort $FrontendPort -ReservedPorts @($BackendPort)

if ($BackendPort -ne $requestedBackendPort) {
  Write-Host "Backend port $requestedBackendPort is occupied. Switched to $BackendPort"
}

if ($FrontendPort -ne $requestedFrontendPort) {
  Write-Host "Frontend port $requestedFrontendPort is occupied. Switched to $FrontendPort"
}

Write-Host "Starting backend and frontend in this single window..."

$backendJob = Start-Job -Name 'backend-dev' -ScriptBlock {
  param([string]$Dir, [int]$Port, [string]$Python)

  Set-Location $Dir
  & $Python -m uvicorn server:app --reload --reload-exclude ".venv" --host 0.0.0.0 --port $Port 2>&1 | ForEach-Object { $_.ToString() }
} -ArgumentList $scriptRoot, $BackendPort, $pythonExe

Write-Host "Waiting for backend to be ready on port $BackendPort..."
$maxWait = 30
$waited = 0
$ready = $false
while ($waited -lt $maxWait) {
  Receive-Job -Job $backendJob -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "[backend] $_"
  }

  $conn = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue
  if ($conn) { $ready = $true; break }
  Start-Sleep -Seconds 1
  $waited++
  if ($waited % 10 -eq 0) { Write-Host "  still waiting... ($waited s)" }
}

if (-not $ready) {
  Write-Host "Backend did not start within $maxWait seconds. Starting frontend anyway."
} else {
  Write-Host "Backend is ready after ${waited}s."
}

if (-not $ready -and $backendJob.State -in @('Completed', 'Failed', 'Stopped')) {
  Write-Host "Backend process exited before it became ready."
}

$frontendJob = Start-Job -Name 'frontend-dev' -ScriptBlock {
  param([string]$Dir, [int]$ApiPort, [int]$Port)

  Set-Location $Dir
  $env:BACKEND_PORT = "$ApiPort"

  & npm run dev -- --host 0.0.0.0 --port $Port 2>&1 | ForEach-Object { $_.ToString() }
} -ArgumentList $frontendDir, $BackendPort, $FrontendPort

Write-Host "Started backend on http://localhost:$BackendPort"
Write-Host "Started frontend on http://localhost:$FrontendPort"
Write-Host 'Press Ctrl+C to stop log streaming. Services can be stopped with .\stop-dev.ps1'

try {
  while ($true) {
    $hadOutput = $false

    Receive-Job -Job $backendJob -ErrorAction SilentlyContinue | ForEach-Object {
      $hadOutput = $true
      Write-Host "[backend] $_"
    }

    Receive-Job -Job $frontendJob -ErrorAction SilentlyContinue | ForEach-Object {
      $hadOutput = $true
      Write-Host "[frontend] $_"
    }

    $backendDone = $backendJob.State -in @('Completed', 'Failed', 'Stopped')
    $frontendDone = $frontendJob.State -in @('Completed', 'Failed', 'Stopped')

    if ($backendDone -and $frontendDone) {
      break
    }

    if (-not $hadOutput) {
      Start-Sleep -Milliseconds 250
    }
  }
}
finally {
  Write-Host "`nProcess states: backend=$($backendJob.State), frontend=$($frontendJob.State)"

  if ($backendJob.State -notin @('Completed', 'Failed', 'Stopped')) {
    Stop-Job -Job $backendJob -Force -ErrorAction SilentlyContinue
  }

  if ($frontendJob.State -notin @('Completed', 'Failed', 'Stopped')) {
    Stop-Job -Job $frontendJob -Force -ErrorAction SilentlyContinue
  }

  Remove-Job -Job $backendJob, $frontendJob -Force -ErrorAction SilentlyContinue
}
