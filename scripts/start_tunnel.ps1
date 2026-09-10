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

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

function Get-TrackedRunningProcessId {
    param(
        [string]$TrackedPidFile
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
    return $rawPid
}

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

$existingPid = Get-TrackedRunningProcessId -TrackedPidFile $pidFile
if ($existingPid) {
    $existingUrl = if (Test-Path $urlFile) { (Get-Content $urlFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim() } else { "" }
    Write-Host "Cloudflare $safeMode tunnel is already running (PID $existingPid)."
    if ($existingUrl) {
        Write-Host "URL: $existingUrl"
    }
    exit 0
}

try {
    $appResponse = Invoke-WebRequest -UseBasicParsing -Uri $LocalAppUrl -Method Get -TimeoutSec 10
    if (-not $appResponse.StatusCode) {
        throw "App did not respond."
    }
} catch {
    Write-Host "Local app is not reachable at $LocalAppUrl."
    Write-Host "Start the app first with start-app.cmd or pass a different -LocalAppUrl."
    exit 1
}

$cloudflaredCommand = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflaredCommand) {
    Write-Host "cloudflared was not found in PATH."
    Write-Host "Install Cloudflare Tunnel first, then try again."
    exit 1
}

Remove-Item -LiteralPath $urlFile -Force -ErrorAction SilentlyContinue
Clear-Content -LiteralPath $outLog -ErrorAction SilentlyContinue
Clear-Content -LiteralPath $errLog -ErrorAction SilentlyContinue

$process = Start-Process `
    -FilePath $cloudflaredCommand.Source `
    -ArgumentList @("tunnel", "--url", $LocalAppUrl) `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

Set-Content -LiteralPath $pidFile -Value $process.Id

$tunnelUrl = $null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    $tunnelUrl = Get-TunnelUrlFromLogs -CandidatePaths @($outLog, $errLog)
    if ($tunnelUrl) {
        break
    }
    $runningProcess = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    if (-not $runningProcess) {
        break
    }
}

if (-not $tunnelUrl) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Cloudflare $safeMode tunnel failed to produce a public URL."
    Write-Host "Check $outLog and $errLog for details."
    exit 1
}

Set-Content -LiteralPath $urlFile -Value $tunnelUrl

try {
    & (Join-Path $PSScriptRoot "save_quick_tunnel_url.ps1") -TunnelUrl $tunnelUrl -Mode $safeMode -LocalAppUrl $LocalAppUrl | Out-Null
} catch {
    Write-Host "Tunnel started, but the app setting could not be updated automatically."
}

Write-Host "Cloudflare $safeMode tunnel started in background."
Write-Host "PID: $($process.Id)"
Write-Host "URL: $tunnelUrl"
Write-Host "Logs: $outLog"
