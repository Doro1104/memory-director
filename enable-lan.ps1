# Run explicitly as administrator only when the user wants phone connections.
$ErrorActionPreference = 'Stop'
$config = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'local-config.json') | ConvertFrom-Json
$rules = @()
if ($config.enable_lan) { $rules += @{Name='Memory Director HTTPS ';Port=$config.lan_port} }
if ($config.enable_web_lan) { $rules += @{Name='Memory Director Web HTTP ';Port=$config.web_port} }
if (-not $rules.Count) { throw 'Enable phone connections in installation settings first.' }
foreach ($rule in $rules) {
    $ruleName = $rule.Name+$rule.Port
    if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $rule.Port -Profile Private -RemoteAddress LocalSubnet | Out-Null
    }
}
Write-Host 'Only configured phone ports are allowed on Private networks from LocalSubnet. Browser HTTP is not encrypted.'
