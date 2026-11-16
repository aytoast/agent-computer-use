param([string]$RepoRoot='')
$ErrorActionPreference='Stop'
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Elevated execution is required.'}
$rootDir=if($RepoRoot){$RepoRoot}else{Split-Path -Parent $PSScriptRoot}
. (Join-Path $PSScriptRoot 'guest-env.ps1')
$settings=Get-HyperVSettings -RootDir $rootDir
$vmId=[guid]$settings['vm_id']
$guestPort=[int]$settings['guest_port']
$guestUser=if($settings['guest_user']){[string]$settings['guest_user']}else{'Agent'}
$guestTask=if($settings['guest_task_name']){[string]$settings['guest_task_name']}else{'HyperV-VM-Agent'}
$pipePrefix=if($settings['pipe_prefix']){[string]$settings['pipe_prefix']}else{'HyperVAgent-Broker-'}
$userSid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$pipeName=$pipePrefix+$userSid.Value
$security=[IO.Pipes.PipeSecurity]::new()
$security.SetAccessRuleProtection($true,$false)
$security.AddAccessRule([IO.Pipes.PipeAccessRule]::new($userSid,[IO.Pipes.PipeAccessRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow))

function Invoke-Guest([string]$operation) {
    $vm=Get-VM -Id $vmId -ErrorAction Stop
    if($vm.State -ne 'Running'){throw 'Configured VM is not running.'}
    $rootDir=Split-Path -Parent $PSScriptRoot
    . (Join-Path $PSScriptRoot 'guest-env.ps1')
    $pw=Get-HyperVGuestPassword -RootDir $rootDir
    $cred=[Management.Automation.PSCredential]::new($guestUser,$pw)
    $session=New-PSSession -VMId $vm.Id -Credential $cred -ErrorAction Stop
    try {
        if($operation -eq 'agent_status') {
            return Invoke-Command -Session $session -ScriptBlock {
                param($port,$taskName)
                $task=Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
                $procs=@(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object {$_.CommandLine -like '*WindowsAgent*vm-agent-server.py*'})
                [ordered]@{task_state=$task.State.ToString();run_level=$task.Principal.RunLevel.ToString();processes=$procs.Count;port_listening=(@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count -gt 0)}
            } -ArgumentList $guestPort,$guestTask -ErrorAction Stop
        }
        return Invoke-Command -Session $session -ScriptBlock {
            param($taskName)
            Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop
            Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
            @{state='restart_requested'}
        } -ArgumentList $guestTask -ErrorAction Stop
    } finally {Remove-PSSession $session -ErrorAction SilentlyContinue}
}

while($true) {
    $pipe=$null;$reader=$null;$writer=$null
    try {
        $pipe=[IO.Pipes.NamedPipeServerStream]::new($pipeName,[IO.Pipes.PipeDirection]::InOut,1,[IO.Pipes.PipeTransmissionMode]::Byte,[IO.Pipes.PipeOptions]::Asynchronous,4096,4096,$security)
        $pipe.WaitForConnection()
        $reader=[IO.StreamReader]::new($pipe,[Text.UTF8Encoding]::new($false),$false,1024,$true)
        $writer=[IO.StreamWriter]::new($pipe,[Text.UTF8Encoding]::new($false),1024,$true);$writer.AutoFlush=$true
        $pending=$reader.ReadLineAsync()
        if(-not $pending.Wait(5000)){throw 'Request timed out.'}
        $line=$pending.Result
        if(!$line -or $line.Length -gt 4096){throw 'Invalid request.'}
        $request=$line|ConvertFrom-Json -ErrorAction Stop
        $properties=@($request.PSObject.Properties.Name)
        if(($properties -notcontains 'operation') -or ($properties|Where-Object{$_ -notin @('operation','payload','token')}).Count -gt 0){throw 'Unsupported request fields.'}
        $tokFile=Join-Path $rootDir 'runtime\broker-token.txt'
        if(Test-Path -LiteralPath $tokFile){
            $expectedTok=(Get-Content -LiteralPath $tokFile -Raw).Trim()
            if(-not $expectedTok -or [string]$request.token -ne $expectedTok){throw 'Invalid broker token.'}
        }
        $operation=[string]$request.operation
        $payload=$request.payload
        $reply=[ordered]@{ok=$true;operation=$operation}
        switch -CaseSensitive ($operation) {
            'ping' {$reply.pid=$PID;$reply.vm_id=$vmId.ToString();$reply.elevated=$true}
            'list_vms' {$reply.vms=@(Get-VM -ErrorAction Stop|ForEach-Object{[ordered]@{name=$_.Name;id=$_.Id.ToString();state=$_.State.ToString()}})}
            'agent_status' {$reply.result=Invoke-Guest 'agent_status'}
            'restart_agent' {
                $rootDir=Split-Path -Parent $PSScriptRoot
                $pendingInput=Join-Path $rootDir 'runtime\hyperv-input-block.json'
                if((Test-Path -LiteralPath $pendingInput) -or (Get-Process agy -ErrorAction SilentlyContinue)){throw 'Active executor or pending input; restart refused.'}
                $reply.result=Invoke-Guest 'restart_agent'
            }
            'create_vm' {
                $name=[string]$payload.name
                if($name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$'){throw 'Invalid VM name.'}
                $mode=if($payload.mode){[string]$payload.mode}else{'ephemeral'}
                if($mode -notin @('ephemeral','persistent')){throw 'Invalid VM mode.'}
                $baseVhdx=[string]$payload.base_vhdx
                if(-not $baseVhdx -or -not (Test-Path -LiteralPath $baseVhdx)){throw 'Base VHDX not found.'}
                $resolvedBase=[IO.Path]::GetFullPath($baseVhdx)
                $allowedBaseDir=if($settings['base_vhdx']){Split-Path -Parent ([IO.Path]::GetFullPath([string]$settings['base_vhdx']))}else{'C:\HyperV\Base'}
                if(-not $resolvedBase.StartsWith($allowedBaseDir,[StringComparison]::OrdinalIgnoreCase)){throw 'Base VHDX path outside allowed directory.'}
                $storageDir=if($settings['vm_storage_dir']){[string]$settings['vm_storage_dir']}else{Join-Path $rootDir 'runtime\vms'}
                New-Item -ItemType Directory -Path $storageDir -Force | Out-Null
                $diffVhdx=Join-Path $storageDir "$name.vhdx"
                if(Test-Path -LiteralPath $diffVhdx){throw "Disk already exists: $diffVhdx"}
                $switchName=if($settings['default_switch']){[string]$settings['default_switch']}else{'Default Switch'}
                $memBytes=[int64]([Math]::Max(2,[int]$payload.memory_gb)*1GB)
                $cpus=[Math]::Max(1,[int]$payload.cpu_count)
                New-VHD -Path $diffVhdx -ParentPath $resolvedBase -Differencing -ErrorAction Stop | Out-Null
                $newVm=New-VM -Name $name -MemoryStartupBytes $memBytes -VHDPath $diffVhdx -SwitchName $switchName -Generation 2 -ErrorAction Stop
                Set-VMProcessor -VM $newVm -Count $cpus -ErrorAction Stop
                $randBytes=New-Object byte[] 3
                [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($randBytes)
                $mac='00155D'+(($randBytes|ForEach-Object{$_.ToString('X2')}) -join '')
                Get-VMNetworkAdapter -VM $newVm | Set-VMNetworkAdapter -StaticMacAddress $mac -ErrorAction Stop
                Start-VM -VM $newVm -ErrorAction Stop
                $reply.vm=[ordered]@{id=$newVm.Id.ToString().ToLowerInvariant();name=$newVm.Name;mac=$mac;mode=$mode;base_vhdx=$resolvedBase;diff_vhdx=$diffVhdx;port=$guestPort}
            }
            'destroy_vm' {
                $targetVm=Get-VM -Id ([guid]$payload.vm_id) -ErrorAction Stop
                if($targetVm.State -ne 'Off'){Stop-VM -VM $targetVm -TurnOff -Force -ErrorAction Stop}
                Remove-VM -VM $targetVm -Force -ErrorAction Stop
                $diff=[string]$payload.diff_vhdx
                $storageDir=[IO.Path]::GetFullPath((if($settings['vm_storage_dir']){[string]$settings['vm_storage_dir']}else{Join-Path $rootDir 'runtime\vms'}))
                if($diff -and (Test-Path -LiteralPath $diff)){
                    $resolvedDiff=[IO.Path]::GetFullPath($diff)
                    if(-not $resolvedDiff.StartsWith($storageDir,[StringComparison]::OrdinalIgnoreCase)){throw 'Refusing to delete disk outside vm_storage_dir.'}
                    Remove-Item -LiteralPath $resolvedDiff -Force
                }
                $reply.destroyed=$targetVm.Id.ToString().ToLowerInvariant()
            }
            'start_vm' {
                $targetVm=Get-VM -Id ([guid]$payload.vm_id) -ErrorAction Stop
                Start-VM -VM $targetVm -ErrorAction Stop
                $reply.state='Running'
            }
            'stop_vm' {
                $targetVm=Get-VM -Id ([guid]$payload.vm_id) -ErrorAction Stop
                if($payload.force){Stop-VM -VM $targetVm -TurnOff -Force -ErrorAction Stop}else{Stop-VM -VM $targetVm -Force -ErrorAction Stop}
                $reply.state='Off'
            }
            'checkpoint_vm' {
                $targetVm=Get-VM -Id ([guid]$payload.vm_id) -ErrorAction Stop
                $snapName=if($payload.name){[string]$payload.name}else{'ckpt-'+(Get-Date -Format 'yyyyMMdd-HHmmss')}
                Checkpoint-VM -VM $targetVm -SnapshotName $snapName -ErrorAction Stop
                $reply.checkpoint=$snapName
            }
            'restore_vm' {
                $targetVm=Get-VM -Id ([guid]$payload.vm_id) -ErrorAction Stop
                $snap=Get-VMSnapshot -VM $targetVm -Name ([string]$payload.checkpoint_name) -ErrorAction Stop
                Restore-VMSnapshot -VMSnapshot $snap -Confirm:$false -ErrorAction Stop
                $reply.restored=$snap.Name
            }
            'copy_file' {
                $src=[string]$payload.src
                $dst=[string]$payload.dst
                if(-not $src -or -not (Test-Path -LiteralPath $src)){throw "Source file not found: $src"}
                $resolvedSrc=[IO.Path]::GetFullPath($src)
                $guestSrcDir=[IO.Path]::GetFullPath((Join-Path $rootDir 'guest'))
                $scriptSrcDir=[IO.Path]::GetFullPath((Join-Path $rootDir 'scripts'))
                if(-not ($resolvedSrc.StartsWith($guestSrcDir,[StringComparison]::OrdinalIgnoreCase) -or $resolvedSrc.StartsWith($scriptSrcDir,[StringComparison]::OrdinalIgnoreCase))){throw 'copy_file restricted to guest/ or scripts/'}
                if(-not $dst){throw 'Destination path required.'}
                $targetVm=if($payload.vm_id){Get-VM -Id ([guid]$payload.vm_id) -ErrorAction Stop}elseif($payload.vm_name){Get-VM -Name ([string]$payload.vm_name) -ErrorAction Stop}else{Get-VM -Id $vmId -ErrorAction Stop}
                if($targetVm.State -ne 'Running'){throw 'Target VM is not running.'}
                $u=if($payload.guest_user){[string]$payload.guest_user}else{$guestUser}
                $pw=if($payload.guest_password){ConvertTo-SecureString ([string]$payload.guest_password) -AsPlainText -Force}else{Get-HyperVGuestPassword -RootDir $rootDir}
                $cred=[Management.Automation.PSCredential]::new($u,$pw)
                $session=New-PSSession -VMId $targetVm.Id -Credential $cred -ErrorAction Stop
                try {
                    Copy-Item -LiteralPath $src -Destination $dst -ToSession $session -Force -ErrorAction Stop
                    $reply.copied=$dst
                } finally {Remove-PSSession $session -ErrorAction SilentlyContinue}
            }
            'get_vm_ip' {
                $mac=if($payload.mac){([string]$payload.mac -replace '[^0-9A-Fa-f]','').ToUpperInvariant()}else{''}
                $adapters=if($payload.vm_id){Get-VM -Id ([guid]$payload.vm_id) -ErrorAction Stop | Get-VMNetworkAdapter}else{Get-VM | Where-Object {$_.State -eq 'Running'} | Get-VMNetworkAdapter}
                if($mac){$adapters=$adapters | Where-Object {($_.MacAddress -replace '[^0-9A-Fa-f]','').ToUpperInvariant() -eq $mac}}
                $ips=@($adapters | ForEach-Object {$_.IPAddresses} | Where-Object {$_ -match '^\d{1,3}(\.\d{1,3}){3}$' -and $_ -notlike '169.254.*'})
                if($ips.Count -lt 1){throw 'No guest IPv4 reported by Hyper-V.'}
                $reply.ip=$ips[0]
            }
            'shutdown' {$reply.state='stopping'}
            default {throw 'Unsupported operation.'}
        }
        $writer.WriteLine(($reply|ConvertTo-Json -Depth 6 -Compress))
        if($operation -eq 'shutdown'){break}
    } catch {
        if($writer){try{$writer.WriteLine((@{ok=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress))}catch{}}
    } finally {
        if($writer){$writer.Dispose()};if($reader){$reader.Dispose()};if($pipe){$pipe.Dispose()}
    }
}
