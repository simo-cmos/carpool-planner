param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8000,
    [string]$DataDir = ""
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logsDir = Join-Path $repoRoot "logs"
$pidFile = Join-Path $logsDir "app-server.pid"
$outLog = Join-Path $logsDir "app-server.out.log"
$errLog = Join-Path $logsDir "app-server.err.log"
$notifierPidFile = Join-Path $logsDir "app-notifier.pid"
$notifierOutLog = Join-Path $logsDir "app-notifier.out.log"
$notifierErrLog = Join-Path $logsDir "app-notifier.err.log"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

function Test-UvicornAvailable {
    param(
        [string]$PythonCommand
    )

    try {
        & $PythonCommand -c "import uvicorn" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-ListeningProcessId {
    param(
        [int]$ListeningPort
    )

    $lines = netstat -ano -p tcp | Select-String ":$ListeningPort\s+.*LISTENING\s+\d+\s*$"
    foreach ($line in $lines) {
        if ($line.Line -match "LISTENING\s+(\d+)\s*$") {
            return $matches[1]
        }
    }
    return $null
}

function Get-TrackedRunningProcessId {
    param(
        [string]$TrackedPidFile,
        [string]$ExpectedProcessName = ""
    )

    if (-not (Test-Path $TrackedPidFile)) {
        return $null
    }
    $rawPid = (Get-Content $TrackedPidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if (-not $rawPid) {
        Remove-Item -LiteralPath $TrackedPidFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    $runningProcess = Get-Process -Id $rawPid -ErrorAction SilentlyContinue
    if (-not $runningProcess) {
        Remove-Item -LiteralPath $TrackedPidFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    if ($ExpectedProcessName -and $runningProcess.ProcessName -ne $ExpectedProcessName) {
        Remove-Item -LiteralPath $TrackedPidFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    return $rawPid
}

if (Test-Path $pidFile) {
    $existingPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($existingPid) {
        $runningProcess = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($runningProcess) {
            Write-Host "Drivers Manager is already running on http://$BindHost`:$Port (PID $existingPid)."
            exit 0
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

$existingNotifierPid = Get-TrackedRunningProcessId -TrackedPidFile $notifierPidFile -ExpectedProcessName "python"

$existingListenerPid = Get-ListeningProcessId -ListeningPort $Port
if ($existingListenerPid) {
    Write-Host "Port $Port is already in use by PID $existingListenerPid."
    Write-Host "If that is Drivers Manager, stop it first or use a different port."
    exit 1
}

# The project venv always wins: a globally installed uvicorn must never shadow it.
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment in .venv ..."
    & python -m venv (Join-Path $repoRoot ".venv")
}

if ((Test-Path $venvPython) -and -not (Test-UvicornAvailable -PythonCommand $venvPython)) {
    Write-Host "Installing dependencies (first run only, this can take a minute) ..."
    & $venvPython -m pip install --disable-pip-version-check --quiet -r (Join-Path $repoRoot "requirements.txt")
}

if ((Test-Path $venvPython) -and (Test-UvicornAvailable -PythonCommand $venvPython)) {
    $pythonExe = $venvPython
} elseif (Test-UvicornAvailable -PythonCommand "python") {
    Write-Host "Using the system Python: .venv could not be prepared."
    $pythonExe = "python"
} else {
    Write-Host "Could not prepare a Python environment with uvicorn."
    Write-Host "Check that 'python' is on PATH, then retry, or set it up manually:"
    Write-Host "  python -m venv .venv"
    Write-Host "  .venv\Scripts\python -m pip install -r requirements.txt"
    exit 1
}

$arguments = @(
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    $BindHost,
    "--port",
    "$Port"
)

if ($DataDir) {
    $resolvedDataDir = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $DataDir))
    $env:DMPROJECT_DATA_DIR = $resolvedDataDir
}

$process = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList $arguments `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

$managedPid = $null
for ($attempt = 0; $attempt -lt 10; $attempt++) {
    Start-Sleep -Seconds 1
    $managedPid = Get-ListeningProcessId -ListeningPort $Port
    if ($managedPid) {
        break
    }
    $runningProcess = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    if (-not $runningProcess) {
        break
    }
}

if (-not $managedPid) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Drivers Manager failed to start. Check logs\app-server.err.log for details."
    exit 1
}

Set-Content -LiteralPath $pidFile -Value $managedPid

if (-not $existingNotifierPid) {
    $notifierProcess = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @("-m", "app.notifier_watcher") `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $notifierOutLog `
        -RedirectStandardError $notifierErrLog `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $notifierPidFile -Value $notifierProcess.Id
} else {
    $notifierProcess = $null
}

Write-Host "Drivers Manager started in background."
Write-Host "URL: http://$BindHost`:$Port"
Write-Host "PID: $managedPid"
Write-Host "Logs: $outLog"
if ($existingNotifierPid) {
    Write-Host "Notifier already running (PID $existingNotifierPid)."
} elseif ($notifierProcess) {
    Write-Host "Notifier PID: $($notifierProcess.Id)"
}
if ($DataDir) {
    Write-Host "Data directory: $resolvedDataDir"
}
