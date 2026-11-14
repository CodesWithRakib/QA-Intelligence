<#
.SYNOPSIS
  Installs the Phase 2 toolchain (web + Android testing) into a project-local directory.
.DESCRIPTION
  Everything goes under <repo>\.tools (override with QAI_TOOLS_DIR). Nothing is installed
  system-wide, no administrator rights are needed and PATH is not modified: the backend resolves
  these tools from the tools directory.

  Idempotent: each component is skipped when its verification already succeeds.
  Storage safety: refuses to start below 40 GB free, stops before a component below 20 GB free.

  Components
    web      Playwright Firefox + WebKit, Lighthouse (npm), gitleaks / k6 / OWASP ZAP images
    android  Temurin JDK 17 (portable), Android command-line tools, platform-tools, build-tools,
             emulator, ONE system image, Appium + UiAutomator2 driver, Appium's ApiDemos sample APK

  Android Studio is intentionally not installed: the command-line SDK provides everything the
  automated engine needs and saves about 3 GB. Installing the SDK accepts the Android SDK licences.
.PARAMETER Only
  web | android | all (default)
.PARAMETER SkipZap
  Skip the 2 GB OWASP ZAP image.
#>
[CmdletBinding()]
param([ValidateSet('all', 'web', 'android')][string]$Only = 'all', [switch]$SkipZap)
. "$PSScriptRoot\_common.ps1"

$Tools = if ($env:QAI_TOOLS_DIR) { $env:QAI_TOOLS_DIR } else { Join-Path $RepoRoot '.tools' }
$Downloads = Join-Path $Tools 'downloads'
New-Item -ItemType Directory -Force $Tools, $Downloads | Out-Null
$ProgressPreference = 'SilentlyContinue'
$AndroidApi = '34'
$SystemImage = "system-images;android-$AndroidApi;google_apis;x86_64"

function Get-FreeGb { [math]::Round((Get-PSDrive -Name (Split-Path -Qualifier $Tools).TrimEnd(':')).Free / 1GB, 1) }
function Assert-Space([string]$What) {
    $free = Get-FreeGb
    if ($free -lt 20) { throw "Only $free GB free: stopping before '$What' (20 GB floor). Nothing was deleted." }
}
function Get-File([string]$Url, [string]$Target) {
    if (Test-Path $Target) { return }
    Write-Step "Downloading $(Split-Path -Leaf $Target)"
    Invoke-WebRequest -Uri $Url -OutFile "$Target.part" -UseBasicParsing
    Move-Item "$Target.part" $Target
}

if ((Get-FreeGb) -lt 40) { throw "Only $(Get-FreeGb) GB free: heavyweight installation needs at least 40 GB." }
Write-Ok "Tools directory: $Tools ($(Get-FreeGb) GB free)"

# ---------------------------------------------------------------- web
if ($Only -in 'all', 'web') {
    Write-Step 'Playwright browsers (Firefox, WebKit)'
    Invoke-Checked 'playwright install' { & $VenvPython -m playwright install firefox webkit }

    $lighthouseDir = Join-Path $Tools 'lighthouse'
    if (Test-Path (Join-Path $lighthouseDir 'node_modules\lighthouse\package.json')) { Write-Ok 'Lighthouse already installed' }
    else {
        Assert-Space 'Lighthouse'
        New-Item -ItemType Directory -Force $lighthouseDir | Out-Null
        Push-Location $lighthouseDir
        try {
            if (-not (Test-Path 'package.json')) { Set-Content package.json '{"name":"qai-lighthouse","private":true}' }
            Invoke-Checked 'npm install lighthouse' { npm install --no-audit --no-fund --loglevel=error lighthouse@12 }
        } finally { Pop-Location }
        Write-Ok 'Lighthouse installed'
    }

    $images = @('zricethezav/gitleaks:latest', 'grafana/k6:latest')
    if (-not $SkipZap) { $images += 'ghcr.io/zaproxy/zaproxy:stable' }
    foreach ($image in $images) {
        docker image inspect $image --format '{{.Id}}' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Ok "$image already present"; continue }
        Assert-Space $image
        Write-Step "Pulling $image"
        docker pull --quiet $image | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Ok "$image pulled" } else { Write-Bad "could not pull $image (checks that need it will report 'blocked')" }
    }
}

# ---------------------------------------------------------------- android
if ($Only -in 'all', 'android') {
    $jdk = Join-Path $Tools 'jdk-17'
    if (Test-Path (Join-Path $jdk 'bin\java.exe')) { Write-Ok 'JDK 17 already installed' }
    else {
        Assert-Space 'JDK'
        $zip = Join-Path $Downloads 'temurin-jdk17.zip'
        Get-File 'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse' $zip
        $staging = Join-Path $Tools 'jdk-staging'
        Expand-Archive $zip $staging -Force
        Move-Item (Get-ChildItem $staging -Directory | Select-Object -First 1).FullName $jdk
        Remove-Item $staging -Recurse -Force -Confirm:$false
        Write-Ok 'JDK 17 extracted'
    }
    $env:JAVA_HOME = $jdk
    $env:PATH = "$jdk\bin;$env:PATH"

    $sdk = Join-Path $Tools 'android-sdk'
    $sdkmanager = Join-Path $sdk 'cmdline-tools\latest\bin\sdkmanager.bat'
    if (Test-Path $sdkmanager) { Write-Ok 'Android command-line tools already installed' }
    else {
        Assert-Space 'Android command-line tools'
        $zip = Join-Path $Downloads 'cmdline-tools.zip'
        $downloaded = $false
        foreach ($build in '13114758', '11076708') {
            try { Get-File "https://dl.google.com/android/repository/commandlinetools-win-${build}_latest.zip" $zip; $downloaded = $true; break }
            catch { Write-Note "command-line tools build $build not available: $($_.Exception.Message)" }
        }
        if (-not $downloaded) { throw 'Could not download the Android command-line tools from dl.google.com.' }
        $staging = Join-Path $Tools 'cmdline-staging'
        Expand-Archive $zip $staging -Force
        New-Item -ItemType Directory -Force (Join-Path $sdk 'cmdline-tools') | Out-Null
        Move-Item (Join-Path $staging 'cmdline-tools') (Join-Path $sdk 'cmdline-tools\latest')
        Remove-Item $staging -Recurse -Force -Confirm:$false
        Write-Ok 'Android command-line tools extracted'
    }
    $env:ANDROID_HOME = $sdk
    $env:ANDROID_SDK_ROOT = $sdk

    Write-Step 'Accepting Android SDK licences'
    (1..40 | ForEach-Object { 'y' }) | & $sdkmanager --sdk_root=$sdk --licenses 2>&1 | Out-Null

    foreach ($package in 'platform-tools', "platforms;android-$AndroidApi", "build-tools;$AndroidApi.0.0", 'emulator', $SystemImage) {
        $folder = Join-Path $sdk ($package -replace ';', '\')
        if (Test-Path $folder) { Write-Ok "$package already installed"; continue }
        Assert-Space $package
        Write-Step "Installing SDK package $package"
        & $sdkmanager --sdk_root=$sdk --install $package 2>&1 | Select-Object -Last 2
        if (-not (Test-Path $folder)) { throw "sdkmanager did not install $package" }
        Write-Ok "$package installed"
    }

    $avdHome = Join-Path $Tools 'avd'
    $env:ANDROID_AVD_HOME = $avdHome
    New-Item -ItemType Directory -Force $avdHome | Out-Null
    if (Test-Path (Join-Path $avdHome 'qai_api34.avd')) { Write-Ok 'AVD qai_api34 already exists' }
    else {
        Write-Step 'Creating one emulator profile (qai_api34, Pixel 5)'
        'no' | & (Join-Path $sdk 'cmdline-tools\latest\bin\avdmanager.bat') create avd --name qai_api34 --package $SystemImage --device pixel_5 2>&1 | Select-Object -Last 2
        # Conservative resources for a 16 GB host that also runs Docker.
        Add-Content (Join-Path $avdHome 'qai_api34.avd\config.ini') "hw.ramSize=2048`nhw.cpu.ncore=2`nvm.heapSize=256`ndisk.dataPartition.size=2G`nhw.keyboard=yes`nshowDeviceFrame=no"
        Write-Ok 'AVD created'
    }

    $appium = Join-Path $Tools 'appium'
    if (Test-Path (Join-Path $appium 'node_modules\appium\package.json')) { Write-Ok 'Appium already installed' }
    else {
        Assert-Space 'Appium'
        New-Item -ItemType Directory -Force $appium | Out-Null
        Push-Location $appium
        try {
            if (-not (Test-Path 'package.json')) { Set-Content package.json '{"name":"qai-appium","private":true}' }
            Invoke-Checked 'npm install appium' { npm install --no-audit --no-fund --loglevel=error appium@latest }
        } finally { Pop-Location }
        Write-Ok 'Appium installed'
    }
    $env:APPIUM_HOME = Join-Path $appium 'home'
    $appiumCli = Join-Path $appium 'node_modules\.bin\appium.cmd'
    if ((& $appiumCli driver list --installed 2>&1 | Out-String) -match 'uiautomator2') { Write-Ok 'UiAutomator2 driver already installed' }
    else {
        Write-Step 'Installing the UiAutomator2 driver'
        Invoke-Checked 'appium driver install' { & $appiumCli driver install uiautomator2 }
    }

    $sample = Join-Path $Tools 'samples\ApiDemos-debug.apk'
    New-Item -ItemType Directory -Force (Split-Path $sample) | Out-Null
    Get-File 'https://github.com/appium/appium/raw/master/packages/appium/sample-code/apps/ApiDemos-debug.apk' $sample

    Write-Step 'Verification'
    & (Join-Path $jdk 'bin\java.exe') -version 2>&1 | Select-Object -First 1
    & (Join-Path $sdk 'platform-tools\adb.exe') version | Select-Object -First 1
    & (Join-Path $sdk 'emulator\emulator.exe') -accel-check 2>&1 | Select-Object -Last 3
    & $appiumCli --version
}
Write-Host "`nPhase 2 tools ready under $Tools ($(Get-FreeGb) GB free)." -ForegroundColor Green
