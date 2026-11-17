#Requires -RunAsAdministrator
# guest-setup.ps1 — run once inside Agent-Base-Builder VM

$ErrorActionPreference = 'Stop'

# 1. enable ethernet
netsh interface set interface "Ethernet" enable
Start-Sleep -Seconds 5

# wait for network
$timeout = 60
$elapsed = 0
while ($elapsed -lt $timeout) {
    if (Test-Connection -ComputerName 8.8.8.8 -Count 1 -Quiet) { break }
    Start-Sleep -Seconds 3
    $elapsed += 3
}

# 2. download python 3.12.8
$pyInstaller = "$env:TEMP\py.exe"
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" -OutFile $pyInstaller

# 3. install python silently
Start-Process -FilePath $pyInstaller -ArgumentList '/quiet InstallAllUsers=1 PrependPath=1' -Wait

# refresh PATH
$env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH', 'User')

# 4. install pip packages
python -m pip install --upgrade pip
python -m pip install fastmcp mcp psutil pywin32 pillow numpy

# 5. create dirs
$dir = "C:\Users\agent\WindowsAgent\app\windows_agent"
New-Item -ItemType Directory -Path $dir -Force

# 6. generate token
[guid]::NewGuid().ToString('N') | Set-Content "$dir\guest-agent-token.txt" -Encoding UTF8

# 7. register scheduled task
$action = New-ScheduledTaskAction -Execute "pythonw.exe" -Argument "C:\Users\agent\WindowsAgent\app\windows_agent\vm-agent-server.py" -WorkingDirectory "C:\Users\agent\WindowsAgent\app\windows_agent"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "agent"
$principal = New-ScheduledTaskPrincipal -UserId "agent" -RunLevel Highest -LogonType Interactive
Register-ScheduledTask -TaskName "HyperV-VM-Agent" -Action $action -Trigger $trigger -Principal $principal -Force

Write-Host "setup complete — shutting down in 5s"
Start-Sleep -Seconds 5
Stop-Computer -Force
