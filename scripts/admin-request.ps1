param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('ping','list_vms','agent_status','restart_agent','shutdown','create_vm','destroy_vm','start_vm','stop_vm','checkpoint_vm','restore_vm','copy_file','get_vm_ip')]
    [string]$Operation,
    [string]$PayloadJson=''
)
$ErrorActionPreference='Stop'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$pipe=[IO.Pipes.NamedPipeClientStream]::new('.','HyperVAgent-Broker-'+$sid,[IO.Pipes.PipeDirection]::InOut,[IO.Pipes.PipeOptions]::None)
try {
    $pipe.Connect(3000)
    $writer=[IO.StreamWriter]::new($pipe,[Text.UTF8Encoding]::new($false),1024,$true);$writer.AutoFlush=$true
    $reader=[IO.StreamReader]::new($pipe,[Text.UTF8Encoding]::new($false),$false,1024,$true)
    $body=@{operation=$Operation}
    $tokFile=Join-Path (Split-Path -Parent $PSScriptRoot) 'runtime\broker-token.txt'
    if(Test-Path -LiteralPath $tokFile){$body.token=(Get-Content -LiteralPath $tokFile -Raw).Trim()}
    if($PayloadJson){$body.payload=($PayloadJson|ConvertFrom-Json)}
    $writer.WriteLine(($body|ConvertTo-Json -Depth 6 -Compress))
    $pending=$reader.ReadLineAsync()
    if(-not $pending.Wait(240000)){throw 'Management request timed out.'}
    $response=$pending.Result|ConvertFrom-Json
    if(-not $response.ok){throw $response.error}
    $response|ConvertTo-Json -Depth 6
} finally {if($reader){$reader.Dispose()};if($writer){$writer.Dispose()};$pipe.Dispose()}
