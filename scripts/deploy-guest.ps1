$ErrorActionPreference='Stop'
$rootDir=Split-Path -Parent $PSScriptRoot
. (Join-Path $rootDir 'scripts\guest-env.ps1')
$settings=Get-HyperVSettings -RootDir $rootDir
$vmId=[string]$settings['vm_id']
$guestPort=[int]$settings['guest_port']
$runtimeDir=Join-Path $rootDir 'runtime'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$status=Join-Path $runtimeDir 'hyperv-tools-deploy.json'
$result=[ordered]@{started=[DateTimeOffset]::Now.ToString('o');state='starting'}
$session=$null
try {
    $vm=Get-VM -Id $vmId
    if($vm.State -ne 'Running'){throw 'Hyper-V VM is not running.'}
    $guestUser=if($settings['guest_user']){[string]$settings['guest_user']}else{'Agent'}
    $remote=if($settings['guest_root']){[string]$settings['guest_root']}else{'C:\Users\Agent\WindowsAgent'}
    $guestTask=if($settings['guest_task_name']){[string]$settings['guest_task_name']}else{'HyperV-VM-Agent'}
    $pw=Get-HyperVGuestPassword -RootDir $rootDir
    $cred=New-Object System.Management.Automation.PSCredential($guestUser,$pw)
    $session=New-PSSession -VMId $vm.Id -Credential $cred -ErrorAction Stop
    $files=@(
        @{src='guest\agent_server.py'; dst='vm-agent-server.py'}
        @{src='guest\universal.py';    dst='app\windows_agent\vm_universal.py'}
        @{src='guest\frame.py';        dst='app\windows_agent\hyperv_frame.py'}
        @{src='guest\game_capture.py'; dst='app\windows_agent\hyperv_game_capture.py'}
    )
    $manifest=@()
    foreach($f in $files){
        $local=Join-Path $rootDir $f.src
        $cand=$f.dst -replace '\.py$','.candidate.py'
        Copy-Item -LiteralPath $local -Destination (Join-Path $remote $cand) -ToSession $session
        $manifest+=@{dst=$f.dst; cand=$cand; bak=($f.dst -replace '\.py$','.pre-tools.bak.py'); hash=(Get-FileHash $local -Algorithm SHA256).Hash}
    }
    $reply=Invoke-Command -Session $session -ScriptBlock {
        param($r,$items,$port,$taskName)
        foreach($m in $items){
            $cand=Join-Path $r $m.cand
            if((Get-FileHash $cand -Algorithm SHA256).Hash -ne $m.hash){throw "Guest transfer hash mismatch: $($m.dst)"}
        }
        foreach($m in $items){
            $dst=Join-Path $r $m.dst
            if(Test-Path $dst){Copy-Item $dst (Join-Path $r $m.bak) -Force}
        }
        try {
            Stop-ScheduledTask -TaskName $taskName
            foreach($m in $items){Move-Item (Join-Path $r $m.cand) (Join-Path $r $m.dst) -Force}
            Start-ScheduledTask -TaskName $taskName
            for($i=0;$i -lt 60;$i++){
                Start-Sleep -Seconds 2
                if(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue){return 'listening'}
            }
            throw 'Guest agent did not resume listening.'
        } catch {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            foreach($m in $items){
                $bak=Join-Path $r $m.bak
                if(Test-Path $bak){Copy-Item $bak (Join-Path $r $m.dst) -Force}
            }
            Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            throw
        }
    } -ArgumentList $remote,$manifest,$guestPort,$guestTask -ErrorAction Stop
    if($reply -ne 'listening'){throw 'Guest readiness was not established.'}
    $result.state='deployed'
} catch {
    $result.state='failed'
    $result.error=$_.Exception.Message
} finally {
    if($session){Remove-PSSession $session -ErrorAction SilentlyContinue}
    $result.completed=[DateTimeOffset]::Now.ToString('o')
    $result|ConvertTo-Json -Depth 4|Set-Content -LiteralPath $status -Encoding UTF8
}
$result|ConvertTo-Json -Depth 4
