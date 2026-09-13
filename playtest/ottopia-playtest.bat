<# : batch part. PowerShell reads everything below the closing #> as its script.
@echo off
rem Ottopia playtest: installs the current playtest builds of the Otto mods into Gale.
rem Double-click to run. Set OTTOPIA_PROFILE to use a Gale profile other than Ottopia.
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ([IO.File]::ReadAllText('%~f0'))"
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
#>

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Owner = 'potto007'
# Public repositories only: this script does not log in to GitHub.
# Keep this list equal to MODS in OttoModTools/tools/playtest.py.
$Mods = @('OttoAura', 'OttoBifrost', 'OttoFuel', 'OttoLens', 'OttoPay', 'OttoStash')
$ProfileName = if ($env:OTTOPIA_PROFILE) { $env:OTTOPIA_PROFILE } else { 'Ottopia' }
$Headers = @{ 'User-Agent' = 'ottopia-playtest'; 'Accept' = 'application/vnd.github+json' }

Write-Host "Ottopia playtest: Gale profile '$ProfileName'"
Write-Host ''

$Gale = @(
    (Join-Path $env:ProgramFiles 'Gale\gale.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Gale\gale.exe'),
    (Join-Path $env:LOCALAPPDATA 'Gale\gale.exe')
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Gale) {
    Write-Host 'FAIL  Gale is not installed in Program Files or LocalAppData.'
    exit 1
}

$Plugins = Join-Path $env:APPDATA "com.kesomannen.gale\valheim\profiles\$ProfileName\BepInEx\plugins"
if (-not (Test-Path $Plugins)) {
    Write-Host "FAIL  Gale has no Valheim profile named '$ProfileName'."
    Write-Host '      Import the Ottopia profile code in Gale first (Import > ...profile from code).'
    exit 1
}

$Work = Join-Path $env:TEMP 'ottopia-playtest'
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$Builds = @()
$Failed = $false
foreach ($Mod in $Mods) {
    try {
        $Releases = Invoke-RestMethod -Headers $Headers -UseBasicParsing `
            -Uri "https://api.github.com/repos/$Owner/$Mod/releases?per_page=30"
    } catch {
        Write-Host ("{0,-12} FAIL  could not read releases: {1}" -f $Mod, $_.Exception.Message)
        $Failed = $true
        continue
    }
    $Release = @($Releases | Where-Object {
        $_.prerelease -and -not $_.draft -and $_.tag_name -like 'playtest-*'
    }) | Select-Object -First 1
    if (-not $Release) {
        Write-Host ("{0,-12} no playtest build" -f $Mod)
        continue
    }
    $Asset = @($Release.assets | Where-Object { $_.name -like '*.zip' }) | Select-Object -First 1
    $Want = [regex]::Match([string]$Release.body, 'sha256: ([0-9a-f]{64})').Groups[1].Value
    if (-not $Asset -or -not $Want) {
        Write-Host ("{0,-12} FAIL  {1} has no zip or no sha256 in its notes" -f $Mod, $Release.tag_name)
        $Failed = $true
        continue
    }
    $Zip = Join-Path $Work $Asset.name
    Invoke-WebRequest -Headers $Headers -UseBasicParsing -Uri $Asset.browser_download_url -OutFile $Zip
    $Got = (Get-FileHash -Algorithm SHA256 -Path $Zip).Hash.ToLower()
    if ($Got -ne $Want) {
        Write-Host ("{0,-12} FAIL  {1} download does not match its sha256, not installed" -f $Mod, $Asset.name)
        $Failed = $true
        continue
    }
    Write-Host ("{0,-12} {1} downloaded and checked" -f $Mod, $Release.tag_name)
    $Builds += [pscustomobject]@{ Mod = $Mod; Tag = $Release.tag_name; Zip = $Zip }
}

Write-Host ''
if ($Builds.Count -eq 0) {
    Write-Host 'Nothing to install.'
    if ($Failed) { exit 1 } else { exit 0 }
}

# A second gale.exe hands its arguments to the running Gale, so start it once and wait.
if (-not (Get-Process -Name gale -ErrorAction SilentlyContinue)) {
    Write-Host 'Starting Gale...'
    Start-Process -FilePath $Gale -ArgumentList @('--game', 'valheim', '--profile', "`"$ProfileName`"")
    Start-Sleep -Seconds 20
}
foreach ($Build in $Builds) {
    Write-Host "Installing $($Build.Mod) $($Build.Tag)"
    Start-Process -FilePath $Gale -Wait -ArgumentList @(
        '--game', 'valheim', '--profile', "`"$ProfileName`"", '--install', "`"$($Build.Zip)`"")
    Start-Sleep -Seconds 5
}
Start-Sleep -Seconds 5

# Gale keeps a Thunderstore copy beside a local one. Two live DLLs means both load.
Write-Host ''
foreach ($Build in $Builds) {
    $Dll = "$($Build.Mod).dll"
    $Live = @(Get-ChildItem -Path $Plugins -Recurse -File |
        Where-Object { $_.Name -eq $Dll } | ForEach-Object { $_.Directory.Name })
    if ($Live.Count -eq 1) {
        Write-Host ("{0,-12} OK    installed in plugins\{1}" -f $Build.Mod, $Live[0])
    } elseif ($Live.Count -eq 0) {
        Write-Host ("{0,-12} FAIL  not found in the profile. Check Gale for an error." -f $Build.Mod)
        $Failed = $true
    } else {
        Write-Host ("{0,-12} FAIL  {1} live copies: {2}" -f $Build.Mod, $Live.Count, ($Live -join ', '))
        Write-Host '             In Gale, click Pull update on the Ottopia profile, which disables'
        Write-Host '             the Thunderstore copy, or disable it by hand.'
        $Failed = $true
    }
}

Write-Host ''
if ($Failed) {
    Write-Host 'PLAYTEST INSTALL HAD PROBLEMS, see above.'
    exit 1
}
Write-Host 'PLAYTEST INSTALL DONE. Launch Valheim from Gale.'
exit 0
