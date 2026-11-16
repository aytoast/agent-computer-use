param([string]$TunnelId=$env:HYPERV_TUNNEL_ID)
$ErrorActionPreference='Stop'
$rootDir=Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'guest-env.ps1')
$settings=Get-HyperVSettings -RootDir $rootDir
$client=[string]$settings['tunnel_client_exe']
$profileDir=Join-Path $rootDir 'tunnel'
$profile='hyperv'
$serverPath=(Join-Path $rootDir 'src\hyperv_acu\server.py') -replace '\\','/'
$pythonExe=([string]$settings['python_exe']) -replace '\\','/'
$command="$pythonExe -B -u $serverPath"
if (-not (Test-Path -LiteralPath $client)) { throw 'OpenAI tunnel client is missing.' }
if (-not $TunnelId) { $TunnelId=Read-Host 'OpenAI tunnel ID' }
if ($TunnelId -notmatch '^tunnel_[A-Za-z0-9]+$') { throw 'Invalid tunnel ID.' }
New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
$profileFile=Join-Path $profileDir "$profile.yaml"
if (Test-Path -LiteralPath $profileFile) {
    $existing=Get-Content -LiteralPath $profileFile -Raw
    if (-not $existing.Contains("tunnel_id: `"$TunnelId`"") -or
        -not $existing.Contains("command: `"$command`"") -or
        -not $existing.Contains('api_key: "env:CONTROL_PLANE_API_KEY"')) {
        throw 'The existing profile does not match this tunnel and service. Inspect it before proceeding.'
    }
} else {
    & $client init --sample sample_mcp_stdio_local --profile $profile --profile-dir $profileDir `
        --tunnel-id $TunnelId --health-listen-addr '127.0.0.1:0' --mcp-command $command
    if ($LASTEXITCODE -ne 0) { throw 'Tunnel profile creation failed.' }
}
$previousKey=[Environment]::GetEnvironmentVariable('CONTROL_PLANE_API_KEY','Process')
$keyDialog=$null
try {
    if (-not $previousKey) {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $keyDialog=New-Object System.Windows.Forms.Form
        $keyDialog.Text='Connect Hyper-V to ChatGPT'
        $keyDialog.StartPosition='CenterScreen'
        $keyDialog.ClientSize=New-Object System.Drawing.Size(480,145)
        $keyDialog.FormBorderStyle=[System.Windows.Forms.FormBorderStyle]::FixedDialog
        $keyDialog.MaximizeBox=$false
        $keyDialog.MinimizeBox=$false
        $label=New-Object System.Windows.Forms.Label
        $label.Text='Paste your OpenAI Platform API key:'
        $label.SetBounds(18,18,440,24)
        $keyField=New-Object System.Windows.Forms.TextBox
        $keyField.UseSystemPasswordChar=$true
        $keyField.SetBounds(18,49,440,25)
        $button=New-Object System.Windows.Forms.Button
        $button.Text='Connect'
        $button.DialogResult=[System.Windows.Forms.DialogResult]::OK
        $button.SetBounds(358,95,100,30)
        $keyDialog.Controls.AddRange(@($label,$keyField,$button))
        $keyDialog.AcceptButton=$button
        if ($keyDialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw 'Key entry cancelled.' }
        $env:CONTROL_PLANE_API_KEY=$keyField.Text.Trim()
        $keyField.Clear()
        if ([string]::IsNullOrEmpty($env:CONTROL_PLANE_API_KEY) -or -not $env:CONTROL_PLANE_API_KEY.StartsWith('sk-')) {
            throw 'The value does not look like a Platform API key.'
        }
    }
    & $client doctor --profile $profile --profile-dir $profileDir --explain
    if ($LASTEXITCODE -ne 0) { throw 'Tunnel doctor failed; the connection was not started.' }
    $running=Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" |
        Where-Object { $_.CommandLine -like '*--profile hyperv*' -and $_.CommandLine -like '*hyperv*tunnel*' }
    if ($running) { throw 'A Hyper-V tunnel client appears to be running. Inspect it before starting another.' }
    $health=Join-Path $profileDir ("health-"+[guid]::NewGuid().ToString('N')+".url")
    $process=Start-Process -FilePath $client -WindowStyle Hidden -PassThru `
        -ArgumentList @('run','--profile',$profile,'--profile-dir',$profileDir,'--health.url-file',$health)
    $ready=$false
    for ($i=0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        $process.Refresh()
        if ($process.HasExited) { throw 'Tunnel client exited before becoming ready.' }
        if (Test-Path -LiteralPath $health) {
            $url=(Get-Content -LiteralPath $health -Raw).Trim().TrimEnd('/')
            if ($url -notmatch '^http://127\.0\.0\.1:[0-9]+$') { throw 'Unexpected tunnel health address.' }
            try {
                $response=Invoke-WebRequest -Uri "$url/readyz" -TimeoutSec 2 -UseBasicParsing
                if ($response.StatusCode -eq 200) { $ready=$true; break }
            } catch { }
        }
    }
    if (-not $ready) { throw "Tunnel client started (PID $($process.Id)) but readiness was not confirmed." }
    Write-Output "Tunnel client is ready (PID $($process.Id)). Use the existing ChatGPT app in ChatGPT using this tunnel."
} finally {
    if ($keyDialog) { $keyDialog.Dispose() }
    if ($null -eq $previousKey) { Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue }
    else { $env:CONTROL_PLANE_API_KEY=$previousKey }
}
