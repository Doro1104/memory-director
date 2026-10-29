# Run explicitly as administrator only when the user wants phone connections.
$ErrorActionPreference = 'Stop'
$config = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'local-config.json') | ConvertFrom-Json
if (-not $config.enable_lan) { throw 'Enable LAN in installation settings first.' }
$ruleName = 'Memory Director HTTPS '+$config.lan_port
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $config.lan_port -Profile Private -RemoteAddress LocalSubnet | Out-Null
}
Write-Host 'Only the selected HTTPS port is allowed on Private networks from LocalSubnet.'
