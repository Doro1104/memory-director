$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'work/server.pid.json'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'No instance PID file; no process was stopped.'; exit 0 }
$record = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
$config = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'local-config.json') | ConvertFrom-Json
if ($record.instance -ne $config.install_id) { throw 'Instance identity mismatch; no process was stopped.' }
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)"
if (-not $process) { Write-Host 'Recorded process is no longer running.'; exit 0 }
$entry = Join-Path $PSScriptRoot 'server.py'
if (-not $process.CommandLine -or $process.CommandLine.IndexOf($entry, [StringComparison]::OrdinalIgnoreCase) -lt 0) { throw 'Process entry mismatch; no process was stopped.' }
Stop-Process -Id $record.pid -ErrorAction Stop
Write-Host 'This Memory Director service was stopped. User data was retained.'
