param(
    [ValidateSet("guest", "admin")]
    [string]$Mode = "guest",
    [string]$LocalAppUrl = "http://127.0.0.1:8000"
)

$body = @{
    public_base_url = ""
}

Invoke-WebRequest `
    -Uri "$LocalAppUrl/settings/public-share-url/$Mode" `
    -Method Post `
    -Body $body `
    -ContentType "application/x-www-form-urlencoded" | Out-Null

Write-Host "Cleared $Mode public share URL."
