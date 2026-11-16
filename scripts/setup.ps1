param(
    [string]$VmName = '',
    [string]$VmId = '',
    [string]$VmMac = '',
    [string]$PythonExe = '',
    [string]$GuestUser = 'Agent',
    [string]$GuestRoot = 'C:\Users\Agent\WindowsAgent',
    [string]$GuestTaskName = 'HyperV-VM-Agent',
    [int]$GuestPort = 18777,
    [string]$InstallClients = '',
    [switch]$CreateVm,
    [string]$BaseVhdx = '',
    [ValidateSet('ephemeral', 'persistent')][string]$Mode = 'persistent',
    [int]$MemoryGB = 4,
    [int]$CpuCount = 2,
    [switch]$NoThumbnail
)

$ErrorActionPreference = 'Stop'
$rootDir = Split-Path -Parent $PSScriptRoot

if (-not $PythonExe) {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    $PythonExe = if ($pyCmd) { $pyCmd.Source } else { 'python' }
}

if ($CreateVm -or $BaseVhdx) {
    if (-not $VmName) { $VmName = 'Agent-VM-' + (Get-Date -Format 'yyyyMMdd-HHmmss') }
    if (-not $BaseVhdx) {
        $cfgPath = Join-Path $rootDir 'config\settings.local.json'
        if (-not (Test-Path -LiteralPath $cfgPath)) { $cfgPath = Join-Path $rootDir 'config\settings.json' }
        if (Test-Path -LiteralPath $cfgPath) {
            $cfg = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
            if ($cfg.base_vhdx) { $BaseVhdx = [string]$cfg.base_vhdx }
        }
    }
    if (-not $BaseVhdx) { throw 'Pass -BaseVhdx <path> or set base_vhdx in config\settings.local.json to create a VM.' }

    $payload = @{
        name      = $VmName
        mode      = $Mode
        base_vhdx = $BaseVhdx
        memory_gb = $MemoryGB
        cpu_count = $CpuCount
    } | ConvertTo-Json -Compress

    Write-Host "Creating and starting Hyper-V VM '$VmName'..."
    $rawReply = & (Join-Path $PSScriptRoot 'admin-request.ps1') -Operation create_vm -PayloadJson $payload
    $reply = $rawReply | ConvertFrom-Json
    $VmId = [string]$reply.vm.id
    $VmMac = [string]$reply.vm.mac
    $VmName = [string]$reply.vm.name
    Write-Host "Started VM $VmName ($VmId)"

    if (-not $NoThumbnail) {
        if (Get-Command vmconnect.exe -ErrorAction SilentlyContinue) {
            Start-Process vmconnect.exe -ArgumentList @('localhost', $VmName)
            Start-Sleep -Seconds 2
        }
        $env:PYTHONPATH = Join-Path $rootDir 'src'
        Start-Process $PythonExe -ArgumentList @('-m', 'hyperv_acu.pip_thumbnail', '--title', $VmName, '--notch') -WindowStyle Hidden
        Write-Host "Launched live host thumbnail for $VmName"
    }
}

if ((-not $VmId -or -not $VmMac) -and (Get-Command Get-VM -ErrorAction SilentlyContinue)) {
    try {
        $vms = @(Get-VM -ErrorAction Stop)
        if ($vms.Count -gt 0) {
            $selected = $null
            if ($VmName) {
                $selected = $vms | Where-Object { $_.Name -eq $VmName } | Select-Object -First 1
            } elseif ($vms.Count -eq 1) {
                $selected = $vms[0]
            } else {
                Write-Host 'Available Hyper-V VMs:'
                for ($i = 0; $i -lt $vms.Count; $i++) {
                    Write-Host "  [$i] $($vms[$i].Name) ($($vms[$i].Id)) [$($vms[$i].State)]"
                }
                $idx = Read-Host 'Select VM index (leave blank to skip)'
                if ($idx -match '^\d+$' -and [int]$idx -lt $vms.Count) {
                    $selected = $vms[[int]$idx]
                }
            }
            if ($selected) {
                if (-not $VmName) { $VmName = $selected.Name }
                if (-not $VmId) { $VmId = $selected.Id.ToString() }
                if (-not $VmMac) {
                    $nic = Get-VMNetworkAdapter -VM $selected -ErrorAction SilentlyContinue | Select-Object -First 1
                    if ($nic -and $nic.MacAddress) { $VmMac = $nic.MacAddress }
                }
            }
        }
    } catch {
        Write-Warning "Hyper-V VM auto-discovery skipped: $($_.Exception.Message)"
    }
}

if (-not $VmName) { $VmName = 'Agent-VM' }

$localSettings = [ordered]@{
    vm_id           = $VmId
    vm_mac          = $VmMac
    vm_name         = $VmName
    guest_port      = $GuestPort
    guest_user      = $GuestUser
    guest_root      = $GuestRoot
    guest_task_name = $GuestTaskName
    python_exe      = $PythonExe
    base_vhdx       = $BaseVhdx
}

$settingsPath = Join-Path $rootDir 'config\settings.local.json'
$localSettings | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $settingsPath -Encoding UTF8
Write-Host "Wrote $settingsPath"

$envPath = Join-Path $rootDir '.env'
$envExample = Join-Path $rootDir '.env.example'
if (-not (Test-Path -LiteralPath $envPath) -and (Test-Path -LiteralPath $envExample)) {
    Copy-Item -LiteralPath $envExample -Destination $envPath
    Write-Host "Created $envPath from .env.example"
}

$serverScript = Join-Path $rootDir 'src\hyperv_acu\server.py'
$mcpConfig = [ordered]@{
    mcpServers = [ordered]@{
        'hyperv-computer-use' = [ordered]@{
            command = $PythonExe
            args    = @('-B', '-u', $serverScript)
        }
    }
}
$mcpPath = Join-Path $rootDir '.mcp.json'
$mcpConfig | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $mcpPath -Encoding UTF8
Write-Host "Wrote $mcpPath"

Write-Host 'Running unit tests...'
& $PythonExe -m unittest discover -s (Join-Path $rootDir 'tests') -t $rootDir
if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed.' }

if ($InstallClients) {
    & (Join-Path $PSScriptRoot 'install-mcp.ps1') -Clients $InstallClients -PythonExe $PythonExe
}

Write-Host 'Setup complete.'
