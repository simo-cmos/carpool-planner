param()

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $repoRoot "logs\app-server.pid"
$notifierPidFile = Join-Path $repoRoot "logs\app-notifier.pid"

if (Test-Path $notifierPidFile) {
    $notifierPid = (Get-Content $notifierPidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($notifierPid) {
        $notifierProcess = Get-Process -Id $notifierPid -ErrorAction SilentlyContinue
        if ($notifierProcess) {
            Stop-Process -Id $notifierPid -Force
            Write-Host "Notifier stopped (PID $notifierPid)."
        }
    }
    Remove-Item -LiteralPath $notifierPidFile -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $pidFile)) {
    Write-Host "Drivers Manager is not running (no PID file found)."
    exit 0
}

$rawPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
if (-not $rawPid) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Drivers Manager is not running (empty PID file removed)."
    exit 0
}

$runningProcess = Get-Process -Id $rawPid -ErrorAction SilentlyContinue
if (-not $runningProcess) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Drivers Manager was already stopped."
    exit 0
}

Stop-Process -Id $rawPid -Force
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "Drivers Manager stopped (PID $rawPid)."
