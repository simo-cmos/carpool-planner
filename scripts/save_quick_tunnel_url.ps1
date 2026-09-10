param(
    [Parameter(Mandatory = $true)]
    [string]$TunnelUrl,
    [ValidateSet("guest", "admin")]
    [string]$Mode = "guest",
    [string]$LocalAppUrl = "http://127.0.0.1:8000"
)

$normalizedUrl = $TunnelUrl.Trim().TrimEnd("/")
if (-not $normalizedUrl) {
    throw "TunnelUrl cannot be empty."
}

$body = @{
    public_base_url = $normalizedUrl
}

Invoke-WebRequest `
    -Uri "$LocalAppUrl/settings/public-share-url/$Mode" `
    -Method Post `
    -Body $body `
    -ContentType "application/x-www-form-urlencoded" | Out-Null

Write-Host "Saved $Mode public share URL: $normalizedUrl"
