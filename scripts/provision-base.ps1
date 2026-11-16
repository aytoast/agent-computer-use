param(
    [string]$IsoPath = '',
    [string]$OutputVhdx = 'C:\HyperV\Base\win11-agent-base.vhdx',
    [string]$VmName = 'Agent-Base-Builder',
    [string]$GuestUser = 'agent',
    [string]$GuestPassword = '',
    [int]$DiskSizeGB = 32,
    [int]$MemoryGB = 4,
    [int]$CpuCount = 4
)

$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run scripts\provision-base.ps1 in an Administrator PowerShell window.'
}

$rootDir = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $IsoPath)) { throw "Windows ISO not found: $IsoPath" }

if (-not $GuestPassword) {
    $secPw = Read-Host -AsSecureString "Enter password for guest user '$GuestUser'"
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secPw)
    try { $GuestPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
if (-not $GuestPassword) { throw 'Guest password is required.' }

$baseDir = Split-Path -Parent $OutputVhdx
New-Item -ItemType Directory -Path $baseDir -Force | Out-Null
if (Test-Path -LiteralPath $OutputVhdx) { throw "Output VHDX already exists: $OutputVhdx" }

$answerVhdx = Join-Path $baseDir "$VmName-unattend.vhdx"
if (Test-Path -LiteralPath $answerVhdx) { Remove-Item -LiteralPath $answerVhdx -Force }

$guestRoot = "C:\Users\$GuestUser\WindowsAgent"
$setupCmd = @"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "
  `$ErrorActionPreference='Stop';
  New-Item -ItemType Directory -Path '$guestRoot\app\windows_agent' -Force | Out-Null;
  `$srcDrive = (Get-Volume -FileSystemLabel 'UNATTEND' -ErrorAction SilentlyContinue | Select-Object -First 1).DriveLetter + ':';
  if (`$srcDrive -ne ':') {
    Copy-Item "`$srcDrive\guest\agent_server.py" '$guestRoot\vm-agent-server.py' -Force;
    Copy-Item "`$srcDrive\guest\universal.py" '$guestRoot\app\windows_agent\vm_universal.py' -Force;
    Copy-Item "`$srcDrive\guest\frame.py" '$guestRoot\app\windows_agent\hyperv_frame.py' -Force;
    Copy-Item "`$srcDrive\guest\game_capture.py" '$guestRoot\app\windows_agent\hyperv_game_capture.py' -Force;
  };
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements;
  } else {
    `$pyInstaller = Join-Path `$env:TEMP 'python-3.12.8-amd64.exe';
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile `$pyInstaller;
    Start-Process -FilePath `$pyInstaller -ArgumentList '/quiet','InstallAllUsers=1','PrependPath=1' -Wait;
  };
  `$py = (Get-ChildItem '$env:LocalAppData\Programs\Python\Python312\python.exe','$env:ProgramFiles\Python312\python.exe' -ErrorAction SilentlyContinue | Select-Object -First 1).FullName;
  if (-not `$py) { `$py = (Get-Command python).Source };
  & `$py -m pip install --upgrade pip fastmcp mcp psutil pywin32 pillow;
  [guid]::NewGuid().ToString('N') | Set-Content -LiteralPath '$guestRoot\guest-agent-token.txt' -Encoding UTF8;
  `$action = New-ScheduledTaskAction -Execute (`$py -replace 'python\.exe$','pythonw.exe') -Argument '$guestRoot\vm-agent-server.py';
  `$trigger = New-ScheduledTaskTrigger -AtLogOn -User '$GuestUser';
  Register-ScheduledTask -TaskName 'HyperV-VM-Agent' -Action `$action -Trigger `$trigger -RunLevel Highest -Force;
  Stop-Computer -Force
"
"@

$unattendXml = @"
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <SetupUILanguage><UILanguage>en-US</UILanguage></SetupUILanguage>
      <InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale><UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale>
    </component>
    <component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <DiskConfiguration>
        <Disk wcm:action="add" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
          <DiskID>0</DiskID><WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add"><Order>1</Order><Type>EFI</Type><Size>260</Size></CreatePartition>
            <CreatePartition wcm:action="add"><Order>2</Order><Type>MSR</Type><Size>16</Size></CreatePartition>
            <CreatePartition wcm:action="add"><Order>3</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Format>FAT32</Format><Label>System</Label></ModifyPartition>
            <ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>3</PartitionID><Format>NTFS</Format><Label>Windows</Label><Letter>C</Letter></ModifyPartition>
          </ModifyPartitions>
        </Disk>
      </DiskConfiguration>
      <ImageInstall>
        <OSImage>
          <InstallFrom><MetaData wcm:action="add" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State"><Key>/IMAGE/INDEX</Key><Value>1</Value></MetaData></InstallFrom>
          <InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>
        </OSImage>
      </ImageInstall>
      <UserData><AcceptEula>true</AcceptEula><ProductKey><WillShowUI>Never</WillShowUI></ProductKey></UserData>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <OOBE>
        <HideEULAPage>true</HideEULAPage><HideOEMRegistrationScreen>true</HideOEMRegistrationScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens><HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <ProtectYourPC>3</ProtectYourPC>
      </OOBE>
      <UserAccounts>
        <LocalAccounts>
          <LocalAccount wcm:action="add" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
            <Password><Value>$GuestPassword</Value><PlainText>true</PlainText></Password>
            <Group>Administrators</Group><Name>$GuestUser</Name><DisplayName>$GuestUser</DisplayName>
          </LocalAccount>
        </LocalAccounts>
      </UserAccounts>
      <AutoLogon>
        <Password><Value>$GuestPassword</Value><PlainText>true</PlainText></Password>
        <Enabled>true</Enabled><LogonCount>1</LogonCount><Username>$GuestUser</Username>
      </AutoLogon>
      <FirstLogonCommands>
        <SynchronousCommand wcm:action="add" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
          <Order>1</Order><CommandLine>cmd.exe /c for %d in (D E F G) do if exist %d:\bootstrap.cmd call %d:\bootstrap.cmd</CommandLine>
        </SynchronousCommand>
      </FirstLogonCommands>
    </component>
  </settings>
</unattend>
"@

Write-Host 'Creating unattend helper VHDX...'
New-VHD -Path $answerVhdx -SizeBytes 64MB -Dynamic | Out-Null
$mounted = Mount-VHD -Path $answerVhdx -Passthru
try {
    Initialize-Disk -Number $mounted.DiskNumber -PartitionStyle MBR -PassThru |
        New-Partition -UseMaximumSize -AssignDriveLetter |
        Format-Volume -FileSystem FAT32 -NewFileSystemLabel 'UNATTEND' -Confirm:$false | Out-Null
    $drive = (Get-Partition -DiskNumber $mounted.DiskNumber | Where-Object { $_.DriveLetter }).DriveLetter + ':'
    $unattendXml | Set-Content -LiteralPath "$drive\autounattend.xml" -Encoding UTF8
    $setupCmd | Set-Content -LiteralPath "$drive\bootstrap.cmd" -Encoding ASCII
    New-Item -ItemType Directory -Path "$drive\guest" -Force | Out-Null
    Copy-Item (Join-Path $rootDir 'guest\*.py') "$drive\guest\" -Force
} finally {
    Dismount-VHD -Path $answerVhdx
}

Write-Host "Creating base VHDX ($DiskSizeGB GB dynamic) and builder VM '$VmName'..."
New-VHD -Path $OutputVhdx -SizeBytes ($DiskSizeGB * 1GB) -Dynamic | Out-Null
$vm = New-VM -Name $VmName -MemoryStartupBytes ($MemoryGB * 1GB) -VHDPath $OutputVhdx -SwitchName 'Default Switch' -Generation 2
Set-VMProcessor -VM $vm -Count $CpuCount
Set-VMKeyProtector -VM $vm -NewLocalKeyProtector
Enable-VMTPM -VM $vm
Add-VMHardDiskDrive -VM $vm -Path $answerVhdx
$dvd = Add-VMDvdDrive -VM $vm -Path $IsoPath -Passthru
$vhdDrive = Get-VMHardDiskDrive -VM $vm | Select-Object -First 1
Set-VMFirmware -VM $vm -BootOrder $dvd,$vhdDrive

Write-Host 'Starting unattended Windows 11 + Python + guest agent installation...'
Start-VM -VM $vm
Write-Host "Waiting for '$VmName' to finish setup and shut down (10-20 min)..."
while ((Get-VM -Id $vm.Id).State -ne 'Off') {
    Start-Sleep -Seconds 15
}

Write-Host 'Cleaning up builder VM and helper disk...'
Remove-VM -VM $vm -Force
Remove-Item -LiteralPath $answerVhdx -Force -ErrorAction SilentlyContinue

$localCfgPath = Join-Path $rootDir 'config\settings.local.json'
$cfg = if (Test-Path -LiteralPath $localCfgPath) { Get-Content -LiteralPath $localCfgPath -Raw | ConvertFrom-Json } else { [PSCustomObject]@{} }
$cfg | Add-Member -NotePropertyName 'base_vhdx' -NotePropertyValue $OutputVhdx -Force
$cfg | Add-Member -NotePropertyName 'guest_user' -NotePropertyValue $GuestUser -Force
$cfg | Add-Member -NotePropertyName 'guest_root' -NotePropertyValue $guestRoot -Force
$cfg | Add-Member -NotePropertyName 'guest_task_name' -NotePropertyValue 'HyperV-VM-Agent' -Force
$cfg | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $localCfgPath -Encoding UTF8

Write-Host "Base image ready: $OutputVhdx"
Write-Host 'Spawn VMs anytime with: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -CreateVm -Mode ephemeral'
