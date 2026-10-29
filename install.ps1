param(
    [string]$DataDir,
    [string]$PythonExe,
    [switch]$EnableLan,
    [switch]$Interactive,
    [switch]$SkipModels,
    [switch]$IncludeMusic
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$logDir = Join-Path $PSScriptRoot 'work'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$installLog = Join-Path $logDir 'install.log'
function Invoke-Logged([string]$Executable, [string[]]$Arguments) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Executable @Arguments *>> $installLog
    $result = $LASTEXITCODE
    $ErrorActionPreference = $previousPreference
    if ($result -ne 0) { throw 'Installation step failed; inspect work/install.log locally.' }
}
try {
    if ($Interactive -and -not $DataDir) {
        $DataDir = Read-Host '输入新建的数据目录（回车使用安装目录下的 data；不要选择已有相册目录）'
        $answer = Read-Host '是否启用 iPhone 安全局域网连接？输入 y 或直接回车保持关闭'
        $EnableLan = $answer -eq 'y'
    }
    $configPath = Join-Path $PSScriptRoot 'local-config.json'
    if (Test-Path -LiteralPath $configPath) {
        # Retain existing settings on retries/upgrades; never silently switch libraries.
        $savedConfig = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
        if ($DataDir -and [IO.Path]::GetFullPath($DataDir) -ne $savedConfig.data_dir) { throw 'Existing data directory differs; edit local-config.json locally instead.' }
        $DataDir = $savedConfig.data_dir
    }
    if (-not $DataDir) { $DataDir = Join-Path $PSScriptRoot 'data' }
    $DataDir = [IO.Path]::GetFullPath($DataDir)
    $marker = Join-Path $DataDir '.memory-director-data'
    if ((Test-Path -LiteralPath $DataDir) -and -not (Test-Path -LiteralPath $marker)) {
        if (@(Get-ChildItem -LiteralPath $DataDir -Force).Count -gt 0) { throw 'Choose a new empty data directory; existing personal folders are not accepted.' }
    }
    $freshData = -not (Test-Path -LiteralPath $marker)
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    if ($freshData) {
        # Protect a new application-owned directory only. Never alter a photo folder ACL.
        $security = New-Object System.Security.AccessControl.DirectorySecurity
        $security.SetAccessRuleProtection($true, $false)
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $security.SetOwner($sid)
        foreach ($identity in @($sid, (New-Object Security.Principal.SecurityIdentifier('S-1-5-18')), (New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))) {
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
            $security.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $DataDir -AclObject $security
        Set-Content -LiteralPath $marker -Value 'Memory Director application data' -Encoding UTF8
    }
    if (-not (Test-Path -LiteralPath $configPath)) {
        @{data_dir=$DataDir;web_port=7860;lan_port=7861;enable_lan=[bool]$EnableLan;install_id=[guid]::NewGuid().ToString()} | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
    } elseif ($EnableLan -and -not $savedConfig.enable_lan) {
        $savedConfig.enable_lan = $true
        $savedConfig | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
    }
    if ($savedConfig -and -not $savedConfig.PSObject.Properties['install_id']) {
        $savedConfig | Add-Member -NotePropertyName install_id -NotePropertyValue ([guid]::NewGuid().ToString())
        $savedConfig | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
    }
    if (-not $PythonExe) {
        foreach ($version in @('3.12','3.11','3.10')) {
            if (Get-Command py.exe -ErrorAction SilentlyContinue) {
                $previousPreference = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                $candidate = & py.exe "-$version" -c 'import sys; print(sys.executable)' 2>> $installLog
                $pythonStatus = $LASTEXITCODE
                $ErrorActionPreference = $previousPreference
                if ($pythonStatus -eq 0) { $PythonExe = [string]$candidate; break }
            }
        }
    }
    if (-not $PythonExe) {
        Write-Host '正在下载 Python 官方安装程序，仅安装到此项目 .runtime 目录，不修改 PATH。'
        $runtimeDir = Join-Path $PSScriptRoot '.runtime'
        New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
        $installer = Join-Path $runtimeDir 'python-installer.exe'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $installer
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'Python installer publisher verification failed.' }
        $targetPython = Join-Path $runtimeDir 'python'
        $process = Start-Process -FilePath $installer -ArgumentList @('/quiet','InstallAllUsers=0','PrependPath=0','AppendPath=0','Include_launcher=0','Include_test=0','Include_doc=0','Include_tcltk=0','AssociateFiles=0','Shortcuts=0',('TargetDir="'+$targetPython+'"')) -WindowStyle Hidden -PassThru -Wait
        if ($process.ExitCode -notin @(0,3010)) { throw 'Python setup failed.' }
        $PythonExe = Join-Path $targetPython 'python.exe'
    }
    Invoke-Logged $PythonExe @('-c','import sys,struct; assert (3,10)<=sys.version_info[:2]<=(3,12) and struct.calcsize(''P'')==8')
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) { Invoke-Logged $PythonExe @('-m','venv',(Join-Path $PSScriptRoot '.venv')) }
    Write-Host '正在安装独立运行环境，详细输出仅保存在本机 work/install.log。'
    Invoke-Logged $venvPython @('-m','pip','install','--disable-pip-version-check','-r','requirements.txt')
    Invoke-Logged $venvPython @('-m','pip','check')
    if (-not $SkipModels) {
        Write-Host '正在下载并校验公开模型；此阶段不读取任何个人素材。'
        $modelArgs = @('setup_models.py')
        if ($IncludeMusic) { $modelArgs += '--music' }
        Invoke-Logged $venvPython $modelArgs
    }
    & $venvPython doctor.py
    Write-Host '安装完成。双击 启动记忆导演.cmd，在本机网页创建账号。'
    if ($EnableLan) { Write-Host '局域网连接使用 HTTPS。若防火墙阻止，请按 README 仅放行专用网络、本地子网的连接端口。' }
} catch {
    $_ | Out-String | Add-Content -LiteralPath $installLog
    Write-Host '安装未完成。请在电脑本地检查 work/install.log；不要将该日志原样发送给 Agent 或 GitHub。'
    exit 1
}
