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
$outLog = Join-Path $logsDir "cloudflare-tunnel-$safeMode.out.log"
$errLog = Join-Path $logsDir "cloudflare-tunnel-$safeMode.err.log"

function Get-TunnelUrlFromLogs {
    param(
        [string[]]$CandidatePaths
    )

    foreach ($candidatePath in $CandidatePaths) {
        if (-not (Test-Path $candidatePath)) {
            continue
        }
        $content = Get-Content $candidatePath -ErrorAction SilentlyContinue
        foreach ($line in $content) {
            if ($line -match "https://[-a-zA-Z0-9]+\.trycloudflare\.com") {
                return $matches[0].Trim().TrimEnd("/")
            }
        }
    }
    return $null
}

$tunnelUrl = if (Test-Path $urlFile) { (Get-Content $urlFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim() } else { "" }
if (-not $tunnelUrl) {
    $tunnelUrl = Get-TunnelUrlFromLogs -CandidatePaths @($outLog, $errLog)
}

$appStatus = "unreachable"
try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $LocalAppUrl -Method Get -TimeoutSec 5
    if ($response.StatusCode) {
        $appStatus = "reachable"
    }
} catch {
}

if (-not (Test-Path $pidFile)) {
    Write-Host "Cloudflare $safeMode tunnel is stopped."
    Write-Host "Local app: $appStatus"
    if ($tunnelUrl) {
        Write-Host "Last known URL (stale): $tunnelUrl"
    }
    exit 1
}

$rawPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
if (-not $rawPid) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Cloudflare $safeMode tunnel is stopped."
    Write-Host "Local app: $appStatus"
    exit 1
}

$runningProcess = Get-Process -Id $rawPid -ErrorAction SilentlyContinue
if (-not $runningProcess) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Cloudflare $safeMode tunnel is stopped."
    Write-Host "Local app: $appStatus"
    if ($tunnelUrl) {
        Write-Host "Last known URL (stale): $tunnelUrl"
    }
    exit 1
}

Write-Host "Cloudflare $safeMode tunnel is running."
Write-Host "PID: $rawPid"
Write-Host "Local app: $appStatus"
if ($tunnelUrl) {
    Write-Host "URL: $tunnelUrl"
} else {
    Write-Host "URL: not captured yet"
}
