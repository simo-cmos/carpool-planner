param(
    [ValidateSet("guest", "admin")]
    [string]$Mode = "guest",
    [string]$LocalAppUrl = "http://127.0.0.1:8000"
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logsDir = Join-Path $repoRoot "logs"
$safeMode = $Mode.ToLowerInvariant()
$pidFile = Join-Path $logsDir "cloudflare-tunnel-$safeMode.pid"
$urlFile = Join-Path $logsDir "cloudflare-tunnel-$safeMode.url"

if (-not (Test-Path $pidFile)) {
    Write-Host "Cloudflare $safeMode tunnel is not running (no PID file found)."
} else {
    $rawPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($rawPid) {
        $runningProcess = Get-Process -Id $rawPid -ErrorAction SilentlyContinue
        if ($runningProcess) {
            Stop-Process -Id $rawPid -Force
            Write-Host "Cloudflare $safeMode tunnel stopped (PID $rawPid)."
        } else {
            Write-Host "Cloudflare $safeMode tunnel was already stopped."
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

Remove-Item -LiteralPath $urlFile -Force -ErrorAction SilentlyContinue

try {
    & (Join-Path $PSScriptRoot "clear_quick_tunnel_url.ps1") -Mode $safeMode -LocalAppUrl $LocalAppUrl | Out-Null
    Write-Host "Cleared saved $safeMode tunnel URL from the app."
} catch {
    Write-Host "Stopped the tunnel, but could not clear the saved $safeMode tunnel URL from the app."
}
