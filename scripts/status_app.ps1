param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8000
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $repoRoot "logs\app-server.pid"
$notifierPidFile = Join-Path $repoRoot "logs\app-notifier.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "Drivers Manager is stopped."
    exit 1
}

$rawPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
if (-not $rawPid) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Drivers Manager is stopped."
    exit 1
}

$runningProcess = Get-Process -Id $rawPid -ErrorAction SilentlyContinue
if (-not $runningProcess) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Drivers Manager is stopped."
    exit 1
}

Write-Host "Drivers Manager is running."
Write-Host "URL: http://$BindHost`:$Port"
Write-Host "PID: $rawPid"

if (Test-Path $notifierPidFile) {
    $notifierPid = (Get-Content $notifierPidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    $notifierProcess = $null
    if ($notifierPid) {
        $notifierProcess = Get-Process -Id $notifierPid -ErrorAction SilentlyContinue
    }
    if ($notifierProcess -and $notifierProcess.ProcessName -eq "python") {
        Write-Host "Notifier: running (PID $notifierPid)"
    } else {
        Remove-Item -LiteralPath $notifierPidFile -Force -ErrorAction SilentlyContinue
        Write-Host "Notifier: stopped"
    }
} else {
    Write-Host "Notifier: stopped"
}
