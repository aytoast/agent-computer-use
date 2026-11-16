function Get-HyperVSettings {
    param([string]$RootDir)
    $merged = @{}
    foreach ($name in @('settings.json', 'settings.local.json')) {
        $path = Join-Path (Join-Path $RootDir 'config') $name
        if (Test-Path -LiteralPath $path) {
            $obj = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($prop in $obj.PSObject.Properties) {
                $merged[$prop.Name] = $prop.Value
            }
        }
    }
    return $merged
}

function Get-HyperVEnvValue {
    param([string]$RootDir, [string]$Key)
    $val = [Environment]::GetEnvironmentVariable($Key)
    if (-not [string]::IsNullOrWhiteSpace($val)) { return $val.Trim() }
    $envPath = Join-Path $RootDir '.env'
    if (Test-Path -LiteralPath $envPath) {
        foreach ($raw in [IO.File]::ReadAllLines($envPath, [Text.UTF8Encoding]::new($false))) {
            $line = $raw.Trim()
            if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) { continue }
            $parts = $line.Split('=', 2)
            if ($parts[0].Trim() -eq $Key) {
                return $parts[1].Trim().Trim('"').Trim("'")
            }
        }
    }
    return $null
}

function Get-HyperVGuestPassword {
    param([string]$RootDir)
    $pw = Get-HyperVEnvValue -RootDir $RootDir -Key 'HYPERV_GUEST_PASSWORD'
    if (-not [string]::IsNullOrWhiteSpace($pw)) {
        return (ConvertTo-SecureString $pw -AsPlainText -Force)
    }
    $pwPath = Get-HyperVEnvValue -RootDir $RootDir -Key 'HYPERV_GUEST_PASSWORD_PATH'
    if ([string]::IsNullOrWhiteSpace($pwPath)) {
        throw 'HYPERV_GUEST_PASSWORD or HYPERV_GUEST_PASSWORD_PATH is not configured.'
    }
    if (-not [IO.Path]::IsPathRooted($pwPath)) { $pwPath = Join-Path $RootDir $pwPath }
    if (-not (Test-Path -LiteralPath $pwPath)) {
        throw "Guest password file not found: $pwPath"
    }
    $text = [IO.File]::ReadAllText($pwPath, [Text.UTF8Encoding]::new($false)).Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        throw "Guest password file is empty: $pwPath"
    }
    return (ConvertTo-SecureString $text -AsPlainText -Force)
}
