param(
    [string]$Clients = 'all',
    [string]$ServerName = 'hyperv-computer-use',
    [string]$PythonExe = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$rootDir = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'guest-env.ps1')
$settings = Get-HyperVSettings -RootDir $rootDir

if (-not $PythonExe) {
    $configured = [string]$settings['python_exe']
    if ($configured -and ($configured -ne 'python' -or (Get-Command python -ErrorAction SilentlyContinue))) {
        $PythonExe = $configured
    } else {
        $pyCmd = Get-Command python -ErrorAction SilentlyContinue
        $PythonExe = if ($pyCmd) { $pyCmd.Source } else { 'python' }
    }
}

$serverScript = Join-Path $rootDir 'src\hyperv_acu\server.py'
$allTargets = @('claude-desktop', 'cursor', 'codex', 'antigravity', 'vscode', 'claude-code')
$selected = if ($Clients -eq 'all') {
    $allTargets
} else {
    @($Clients.Split(',') | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
}

function Set-JsonMcpEntry {
    param(
        [string]$Path,
        [string]$RootKey,
        [string]$Name,
        [hashtable]$Entry,
        [bool]$CreateIfParentMissing
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        if (-not $CreateIfParentMissing) {
            Write-Host "Skipped $Path (client directory not found)"
            return
        }
        if (-not $DryRun) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    }
    $data = [PSCustomObject]@{}
    if (Test-Path -LiteralPath $Path) {
        $raw = Get-Content -LiteralPath $Path -Raw
        if ($raw.Trim()) {
            $parsed = $raw | ConvertFrom-Json
            if ($null -ne $parsed) { $data = $parsed }
        }
    }
    if (-not ($data.PSObject.Properties.Name -contains $RootKey) -or $null -eq $data.$RootKey) {
        $data | Add-Member -NotePropertyName $RootKey -NotePropertyValue ([PSCustomObject]@{}) -Force
    }
    $data.$RootKey | Add-Member -NotePropertyName $Name -NotePropertyValue ([PSCustomObject]$Entry) -Force
    if ($DryRun) {
        Write-Host "[DryRun] Would update $Path ($RootKey.$Name)"
    } else {
        $data | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Path -Encoding UTF8
        Write-Host "Registered $Name in $Path"
    }
}

$explicit = ($Clients -ne 'all')
$stdEntry = @{
    command = $PythonExe
    args    = @('-B', '-u', $serverScript)
}

foreach ($target in $selected) {
    switch ($target) {
        'claude-desktop' {
            $p = Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'
            Set-JsonMcpEntry -Path $p -RootKey 'mcpServers' -Name $ServerName -Entry $stdEntry -CreateIfParentMissing $explicit
        }
        'cursor' {
            $p = Join-Path $env:USERPROFILE '.cursor\mcp.json'
            Set-JsonMcpEntry -Path $p -RootKey 'mcpServers' -Name $ServerName -Entry $stdEntry -CreateIfParentMissing $explicit
        }
        'antigravity' {
            $p = Join-Path $env:USERPROFILE '.gemini\config\mcp_config.json'
            Set-JsonMcpEntry -Path $p -RootKey 'mcpServers' -Name $ServerName -Entry $stdEntry -CreateIfParentMissing $explicit
        }
        'vscode' {
            $p = Join-Path $rootDir '.vscode\mcp.json'
            $vsEntry = @{
                type    = 'stdio'
                command = $PythonExe
                args    = @('-B', '-u', $serverScript)
            }
            Set-JsonMcpEntry -Path $p -RootKey 'servers' -Name $ServerName -Entry $vsEntry -CreateIfParentMissing $true
        }
        'codex' {
            $codexDir = Join-Path $env:USERPROFILE '.codex'
            $tomlPath = Join-Path $codexDir 'config.toml'
            if ((Test-Path -LiteralPath $codexDir) -or $explicit) {
                if (-not $DryRun -and -not (Test-Path -LiteralPath $codexDir)) {
                    New-Item -ItemType Directory -Path $codexDir -Force | Out-Null
                }
                $existing = if (Test-Path -LiteralPath $tomlPath) { Get-Content -LiteralPath $tomlPath -Raw } else { '' }
                $sectionHeader = "[mcp_servers.$ServerName]"
                if ($existing -match [regex]::Escape($sectionHeader)) {
                    Write-Host "Already registered in $tomlPath ($sectionHeader)"
                } else {
                    $pyToml = $PythonExe.Replace('\', '\\')
                    $srvToml = $serverScript.Replace('\', '\\')
                    $block = "`r`n$sectionHeader`r`ncommand = `"$pyToml`"`r`nargs = [`"-B`", `"-u`", `"$srvToml`"]`r`n"
                    if ($DryRun) {
                        Write-Host "[DryRun] Would append $sectionHeader to $tomlPath"
                    } else {
                        Add-Content -LiteralPath $tomlPath -Value $block -Encoding UTF8
                        Write-Host "Registered $ServerName in $tomlPath"
                    }
                }
            } else {
                Write-Host "Skipped $tomlPath (client directory not found)"
            }
        }
        'claude-code' {
            $claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
            if ($claudeCmd) {
                if ($DryRun) {
                    Write-Host "[DryRun] Would run: claude mcp add -s user $ServerName -- $PythonExe -B -u $serverScript"
                } else {
                    & $claudeCmd.Source mcp add -s user $ServerName -- $PythonExe -B -u $serverScript
                    if ($LASTEXITCODE -eq 0) {
                        Write-Host "Registered $ServerName via claude CLI"
                    }
                }
            } else {
                Write-Host 'Skipped claude-code (claude CLI not found on PATH)'
            }
        }
        default {
            Write-Warning "Unknown client target: $target"
        }
    }
}
