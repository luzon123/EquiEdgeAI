<#
.SYNOPSIS
  Full verification pass for the Android 16 KB page-size fix.

.DESCRIPTION
  Builds a fresh debug APK, verifies native libraries and ELF alignment,
  verifies APK zip alignment, installs the APK on a connected Android device,
  launches the app, and checks logcat for crashes.

  Manual overlay / MediaProjection permission steps are printed at the end.

.PARAMETER AndroidHome
  Android SDK path. Defaults to ANDROID_HOME / ANDROID_SDK_ROOT.

.PARAMETER Serial
  Optional adb device serial.

.EXAMPLE
  .\verify-16kb.ps1

.EXAMPLE
  .\verify-16kb.ps1 -Serial R5CY346XPJH
#>

param(
    [string]$AndroidHome = $(if ($env:ANDROID_HOME) {
        $env:ANDROID_HOME
    }
    elseif ($env:ANDROID_SDK_ROOT) {
        $env:ANDROID_SDK_ROOT
    }
    else {
        ""
    }),

    [string]$Serial
)

# ============================================================
# GLOBAL CONFIG
# ============================================================

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AndroidDir = Split-Path -Parent $ScriptDir
$MobileDir = Split-Path -Parent $AndroidDir
$RepoRoot = Split-Path -Parent $MobileDir

$RequiredNdk = "27.1.12297006"
$RequiredBuildTools = "35.0.0"
$PackageName = "ai.equiedge.mobile"

$results = [ordered]@{}

# ============================================================
# HELPERS
# ============================================================

function Report {
    param(
        [string]$Name,
        [bool]$Pass,
        [string]$Detail = ""
    )

    $results[$Name] = $Pass

    if ($Pass) {
        Write-Host "[PASS] $Name" -ForegroundColor Green
    }
    else {
        Write-Host "[FAIL] $Name" -ForegroundColor Red
    }

    if ($Detail) {
        Write-Host "       $Detail" -ForegroundColor Gray
    }
}

function Invoke-NativeSafe {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @()
    )

    # PowerShell 5.1 can convert native stderr into a terminating
    # NativeCommandError when ErrorActionPreference=Stop.
    # Temporarily allow stderr so the real exit code can be evaluated.
    $previousPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "Continue"

        $output = & $FilePath @Arguments 2>&1
        $exitCode = $LASTEXITCODE

        return [PSCustomObject]@{
            Output   = @($output)
            ExitCode = $exitCode
        }
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Write-CommandOutput {
    param(
        [object[]]$Output,
        [int]$LastLines = 10
    )

    if (-not $Output) {
        return
    }

    $lines = @($Output | ForEach-Object {
        $_.ToString()
    })

    if ($lines.Count -le $LastLines) {
        $lines | ForEach-Object {
            Write-Host "       $_" -ForegroundColor Gray
        }
    }
    else {
        $lines | Select-Object -Last $LastLines | ForEach-Object {
            Write-Host "       $_" -ForegroundColor Gray
        }
    }
}

# ============================================================
# 0. ENVIRONMENT
# ============================================================

Write-Host ""
Write-Host "=== 0. Environment ===" -ForegroundColor Cyan

if (-not $AndroidHome -or -not (Test-Path $AndroidHome)) {
    Report `
        "Android SDK located" `
        $false `
        "ANDROID_HOME/ANDROID_SDK_ROOT is not set or the path does not exist."

    Write-Host ""
    Write-Host "Example:" -ForegroundColor Yellow
    Write-Host '.\verify-16kb.ps1 -AndroidHome "C:\Users\Admin\AppData\Local\Android\Sdk"' -ForegroundColor Yellow

    exit 1
}

Report "Android SDK located" $true $AndroidHome

# ------------------------------------------------------------
# JDK
# ------------------------------------------------------------

$javac = Get-Command javac -ErrorAction SilentlyContinue

if ($javac) {
    $javaResult = Invoke-NativeSafe -FilePath "javac" -Arguments @("-version")
    $javaVersion = ($javaResult.Output | Select-Object -First 1 | Out-String).Trim()

    Report "JDK (javac) available" ($javaResult.ExitCode -eq 0) $javaVersion
}
else {
    Report `
        "JDK (javac) available" `
        $false `
        "javac not found. JDK 17 is required."

    exit 1
}

# ------------------------------------------------------------
# Build Tools / zipalign
# ------------------------------------------------------------

$zipalign = Join-Path `
    $AndroidHome `
    "build-tools\$RequiredBuildTools\zipalign.exe"

$hasZipalign16 = Test-Path $zipalign

if ($hasZipalign16) {
    Report `
        "build-tools $RequiredBuildTools (zipalign -P 16 support)" `
        $true `
        $zipalign
}
else {
    Report `
        "build-tools $RequiredBuildTools (zipalign -P 16 support)" `
        $false `
        "Install build-tools $RequiredBuildTools."
}

# ------------------------------------------------------------
# NDK / llvm-objdump
# ------------------------------------------------------------

$objdump = Join-Path `
    $AndroidHome `
    "ndk\$RequiredNdk\toolchains\llvm\prebuilt\windows-x86_64\bin\llvm-objdump.exe"

$hasObjdump = Test-Path $objdump

if ($hasObjdump) {
    Report `
        "NDK $RequiredNdk llvm-objdump available" `
        $true `
        $objdump
}
else {
    Report `
        "NDK $RequiredNdk llvm-objdump available" `
        $false `
        "NDK $RequiredNdk is not installed."
}

# ------------------------------------------------------------
# ADB
# ------------------------------------------------------------

$adb = Get-Command adb -ErrorAction SilentlyContinue

if ($adb) {
    Report "adb available" $true $adb.Source
}
else {
    Report `
        "adb available" `
        $false `
        "adb not found on PATH. Add $AndroidHome\platform-tools to PATH."

    exit 1
}

if (-not $hasZipalign16) {
    Write-Host ""
    Write-Host "Stopping: build-tools 35.0.0 is required." -ForegroundColor Yellow
    exit 1
}

if (-not $hasObjdump) {
    Write-Host ""
    Write-Host "Stopping: NDK $RequiredNdk is required for ELF verification." -ForegroundColor Yellow
    Write-Host "Install it through Android Studio SDK Manager or sdkmanager." -ForegroundColor Yellow
    exit 1
}

# ============================================================
# 1. BACKEND TEST SUITE
# ============================================================

Write-Host ""
Write-Host "=== 1. Backend test suite ===" -ForegroundColor Cyan

Push-Location $RepoRoot

try {
    # Use pytest because this is the test runner already verified
    # in this project.
    $pytest = Get-Command pytest -ErrorAction SilentlyContinue

    if ($pytest) {
        $testResult = Invoke-NativeSafe `
            -FilePath "pytest" `
            -Arguments @("-q")

        $testOutput = $testResult.Output
        $testExitCode = $testResult.ExitCode
    }
    else {
        # Fallback to python -m pytest
        $testResult = Invoke-NativeSafe `
            -FilePath "python" `
            -Arguments @("-m", "pytest", "-q")

        $testOutput = $testResult.Output
        $testExitCode = $testResult.ExitCode
    }
}
finally {
    Pop-Location
}

$testPass = ($testExitCode -eq 0)

Report `
    "Backend test suite" `
    $testPass `
    $(if ($testPass) {
        "All tests passed."
    }
    else {
        "pytest returned exit code $testExitCode."
    })

Write-CommandOutput -Output $testOutput -LastLines 8

if (-not $testPass) {
    Write-Host ""
    Write-Host "Backend tests failed. Fix them before continuing." -ForegroundColor Red
    exit 1
}

# ============================================================
# 2. BUILD FRESH APK
# ============================================================

Write-Host ""
Write-Host "=== 2. Fresh debug APK build ===" -ForegroundColor Cyan

Push-Location $AndroidDir

try {
    $buildResult = Invoke-NativeSafe `
        -FilePath ".\gradlew.bat" `
        -Arguments @("clean", "assembleDebug")

    $buildOutput = $buildResult.Output
    $buildExitCode = $buildResult.ExitCode
}
finally {
    Pop-Location
}

$buildPass = ($buildExitCode -eq 0)

Report `
    "gradlew clean assembleDebug" `
    $buildPass `
    $(if ($buildPass) {
        "Build completed successfully."
    }
    else {
        "Gradle returned exit code $buildExitCode."
    })

if (-not $buildPass) {
    Write-CommandOutput -Output $buildOutput -LastLines 30

    Write-Host ""
    Write-Host "Stopping - Gradle build failed." -ForegroundColor Red
    exit 1
}

$apkPath = Join-Path `
    $AndroidDir `
    "app\build\outputs\apk\debug\app-debug.apk"

if (-not (Test-Path $apkPath)) {
    Report `
        "APK produced" `
        $false `
        "Expected APK at $apkPath"

    exit 1
}

Report "APK produced" $true $apkPath

# ============================================================
# 3. EXTRACT APK / FIND NATIVE LIBRARIES
# ============================================================

Write-Host ""
Write-Host "=== 3. Native libraries in APK ===" -ForegroundColor Cyan

$extractDir = Join-Path `
    $env:TEMP `
    "equiedge-apk-inspect"

if (Test-Path $extractDir) {
    Remove-Item -Recurse -Force $extractDir
}

New-Item `
    -ItemType Directory `
    -Path $extractDir `
    -Force | Out-Null

Add-Type -AssemblyName System.IO.Compression.FileSystem

try {
    [System.IO.Compression.ZipFile]::ExtractToDirectory(
        $apkPath,
        $extractDir
    )
}
catch {
    Report `
        "APK extraction" `
        $false `
        "Could not extract APK: $($_.Exception.Message)"

    exit 1
}

$libDir = Join-Path $extractDir "lib"

$soFiles = @()

if (Test-Path $libDir) {
    $soFiles = @(
        Get-ChildItem `
            -Path $libDir `
            -Recurse `
            -Filter "*.so" `
            -ErrorAction SilentlyContinue
    )
}

if ($soFiles.Count -eq 0) {
    Report `
        "Native libraries found" `
        $false `
        "No .so files found under lib/."

    exit 1
}

$abiList = @(
    $soFiles |
    ForEach-Object {
        Split-Path -Leaf $_.Directory.FullName
    } |
    Sort-Object -Unique
) -join ", "

Report `
    "Native libraries found" `
    $true `
    "$($soFiles.Count) .so file(s) across: $abiList"

foreach ($so in $soFiles) {
    $sizeKb = [math]::Round($so.Length / 1KB)

    Write-Host `
        "       $($so.Directory.Name)/$($so.Name) ($sizeKb KB)" `
        -ForegroundColor Gray
}

# ============================================================
# 4. APK ZIP ALIGNMENT
# ============================================================

Write-Host ""
Write-Host "=== 4. APK zip alignment (16 KB) ===" -ForegroundColor Cyan

$zipalignResult = Invoke-NativeSafe `
    -FilePath $zipalign `
    -Arguments @(
        "-v",
        "-c",
        "-P",
        "16",
        "4",
        $apkPath
    )

$zipalignPass = ($zipalignResult.ExitCode -eq 0)

Report `
    "zipalign -P 16 verification" `
    $zipalignPass `
    $(if ($zipalignPass) {
        "APK is correctly zip-aligned for 16 KB pages."
    }
    else {
        "zipalign returned exit code $($zipalignResult.ExitCode)."
    })

Write-CommandOutput `
    -Output $zipalignResult.Output `
    -LastLines 8

if (-not $zipalignPass) {
    Write-Host ""
    Write-Host "APK zip alignment failed." -ForegroundColor Red
    exit 1
}

# ============================================================
# 5. ELF LOAD SEGMENT ALIGNMENT
# ============================================================

Write-Host ""
Write-Host "=== 5. ELF LOAD segment alignment ===" -ForegroundColor Cyan

$elfAllPass = $true

foreach ($so in $soFiles) {

    $objdumpResult = Invoke-NativeSafe `
        -FilePath $objdump `
        -Arguments @(
            "-p",
            $so.FullName
        )

    $out = @(
        $objdumpResult.Output |
        ForEach-Object {
            $_.ToString()
        }
    )

    $loadLines = @(
        $out |
        Where-Object {
            $_ -match "^\s*LOAD\b"
        }
    )

    $minShift = $null

    foreach ($line in $loadLines) {

        if ($line -match "align\s+2\*\*(\d+)") {

            $shift = [int]$Matches[1]

            if (
                ($null -eq $minShift) -or
                ($shift -lt $minShift)
            ) {
                $minShift = $shift
            }
        }
    }

    $ok = (
        ($loadLines.Count -gt 0) -and
        ($null -ne $minShift) -and
        ($minShift -ge 14)
    )

    if (-not $ok) {
        $elfAllPass = $false
    }

    $label = "$($so.Directory.Name)/$($so.Name)"

    if ($null -ne $minShift) {

        $alignmentBytes = [math]::Pow(2, $minShift)

        $detail =
            "min LOAD align = 2**$minShift ($alignmentBytes bytes)"
    }
    else {

        $detail =
            "Could not parse ELF LOAD alignment."
    }

    if ($ok) {
        Write-Host `
            "       OK   $label - $detail" `
            -ForegroundColor Gray
    }
    else {
        Write-Host `
            "       BAD  $label - $detail" `
            -ForegroundColor Red
    }
}

Report `
    "All native libraries 16 KB-aligned" `
    $elfAllPass `
    $(if ($elfAllPass) {
        "Every native .so has LOAD alignment >= 2**14."
    }
    else {
        "At least one native library is not 16 KB aligned."
    })

if (-not $elfAllPass) {
    Write-Host ""
    Write-Host "16 KB ELF alignment FAILED." -ForegroundColor Red
    Write-Host "Do not proceed to device testing until this is fixed." -ForegroundColor Yellow
    exit 1
}

# ============================================================
# 6. FINAL NATIVE LIBRARY CHECK
# ============================================================

Write-Host ""
Write-Host "=== 6. Incompatible native library check ===" -ForegroundColor Cyan

$noIncompatibleLibrary = (
    $elfAllPass -and
    $hasObjdump
)

Report `
    "No incompatible native library" `
    $noIncompatibleLibrary `
    $(if ($noIncompatibleLibrary) {
        "All inspected native libraries are 16 KB compatible."
    }
    else {
        "Could not confirm compatibility."
    })

if (-not $noIncompatibleLibrary) {
    exit 1
}

# ============================================================
# 7. DEVICE DETECTION
# ============================================================

Write-Host ""
Write-Host "=== 7. Android device ===" -ForegroundColor Cyan

$adbArgs = @()

if ($Serial) {
    $adbArgs += @("-s", $Serial)
}

$devicesResult = Invoke-NativeSafe `
    -FilePath "adb" `
    -Arguments @("devices")

$devicesOutput = @(
    $devicesResult.Output |
    ForEach-Object {
        $_.ToString()
    }
)

$devicesOutput | ForEach-Object {
    Write-Host "       $_" -ForegroundColor Gray
}

$authorizedDevice = $false

foreach ($line in $devicesOutput) {

    if ($line -match "^\S+\s+device$") {
        $authorizedDevice = $true
        break
    }
}

if (-not $authorizedDevice) {

    Report `
        "Device connected and authorized" `
        $false `
        "No authorized Android device found."

    Write-Host ""
    Write-Host "Run:" -ForegroundColor Yellow
    Write-Host "adb devices" -ForegroundColor Yellow

    Write-Host ""
    Write-Host "The device must appear as:" -ForegroundColor Yellow
    Write-Host "XXXXXXXXXXXX    device" -ForegroundColor Yellow

    exit 1
}

Report `
    "Device connected and authorized" `
    $true

# ============================================================
# 8. INSTALL APK
# ============================================================

Write-Host ""
Write-Host "=== 8. Install APK on device ===" -ForegroundColor Cyan

$installResult = Invoke-NativeSafe `
    -FilePath "adb" `
    -Arguments (
        $adbArgs + @(
            "install",
            "-r",
            $apkPath
        )
    )

$installPass = ($installResult.ExitCode -eq 0)

Report `
    "adb install -r" `
    $installPass `
    $(if ($installPass) {
        "APK installed successfully."
    }
    else {
        "adb install returned exit code $($installResult.ExitCode)."
    })

Write-CommandOutput `
    -Output $installResult.Output `
    -LastLines 10

if (-not $installPass) {
    Write-Host ""
    Write-Host "APK installation failed." -ForegroundColor Red
    exit 1
}

# ============================================================
# 9. CLEAN APP STATE + LAUNCH
# ============================================================

Write-Host ""
Write-Host "=== 9. Launch and crash check ===" -ForegroundColor Cyan

# Clear application data so this is a clean verification run.
$clearResult = Invoke-NativeSafe `
    -FilePath "adb" `
    -Arguments (
        $adbArgs + @(
            "shell",
            "pm",
            "clear",
            $PackageName
        )
    )

# Clear old logcat entries.
$logClearResult = Invoke-NativeSafe `
    -FilePath "adb" `
    -Arguments (
        $adbArgs + @(
            "logcat",
            "-c"
        )
    )

# Launch application.
$launchResult = Invoke-NativeSafe `
    -FilePath "adb" `
    -Arguments (
        $adbArgs + @(
            "shell",
            "am",
            "start",
            "-n",
            "$PackageName/.MainActivity"
        )
    )

Start-Sleep -Seconds 5

# Read recent logcat.
$logResult = Invoke-NativeSafe `
    -FilePath "adb" `
    -Arguments (
        $adbArgs + @(
            "logcat",
            "-d",
            "-t",
            "500"
        )
    )

$log = @(
    $logResult.Output |
    ForEach-Object {
        $_.ToString()
    }
)

$crashed = @(
    $log |
    Select-String `
        -Pattern `
        "FATAL EXCEPTION|AndroidRuntime: FATAL|Process: ai\.equiedge\.mobile.*died"
)

$launchPass = ($crashed.Count -eq 0)

if ($launchPass) {

    Report `
        "App launched without crashing" `
        $true `
        "No fatal crash detected in logcat."

}
else {

    Report `
        "App launched without crashing" `
        $false `
        "Fatal crash detected."

    Write-Host ""
    Write-Host "Crash information:" -ForegroundColor Red

    $crashed |
        Select-Object -First 10 |
        ForEach-Object {
            Write-Host "       $($_.ToString())" -ForegroundColor Red
        }
}

# ============================================================
# 10-12. MANUAL VERIFICATION
# ============================================================

Write-Host ""
Write-Host "=== 10-12. Manual verification ===" -ForegroundColor Cyan

Write-Host ""
Write-Host "Perform these steps on the physical Android device:" -ForegroundColor Yellow

Write-Host ""
Write-Host "10. OVERLAY PERMISSION" -ForegroundColor Yellow
Write-Host "    Open EquiEdgeAI." -ForegroundColor Yellow
Write-Host "    Tap 'Enable Floating Overlay'." -ForegroundColor Yellow
Write-Host "    Android should open 'Display over other apps'." -ForegroundColor Yellow
Write-Host "    Enable the permission for EquiEdgeAI." -ForegroundColor Yellow
Write-Host "    Return to EquiEdgeAI." -ForegroundColor Yellow

Write-Host ""
Write-Host "11. SCREEN CAPTURE PERMISSION" -ForegroundColor Yellow
Write-Host "    Tap 'Enable Floating Overlay' again." -ForegroundColor Yellow
Write-Host "    Android should display the screen-capture consent dialog." -ForegroundColor Yellow
Write-Host "    Approve the capture." -ForegroundColor Yellow

Write-Host ""
Write-Host "12. FLOATING ANALYZE BUTTON" -ForegroundColor Yellow
Write-Host "    Confirm the floating ANALYZE button appears." -ForegroundColor Yellow
Write-Host "    Switch to another application." -ForegroundColor Yellow
Write-Host "    Confirm the button remains visible above other apps." -ForegroundColor Yellow
Write-Host "    Drag the button around the screen." -ForegroundColor Yellow
Write-Host "    Confirm it can be moved freely." -ForegroundColor Yellow

# ============================================================
# SUMMARY
# ============================================================

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "                         SUMMARY" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

foreach ($key in $results.Keys) {

    $pass = [bool]$results[$key]

    if ($pass) {
        Write-Host "[PASS] $key" -ForegroundColor Green
    }
    else {
        Write-Host "[FAIL] $key" -ForegroundColor Red
    }
}

$failCount = @(
    $results.Values |
    Where-Object {
        -not $_
    }
).Count

Write-Host ""

if ($failCount -eq 0) {

    Write-Host "ALL AUTOMATED CHECKS PASSED." -ForegroundColor Green
    Write-Host "Complete the manual overlay / screen-capture checks above." -ForegroundColor Green

}
else {

    Write-Host "$failCount CHECK(S) FAILED." -ForegroundColor Red
    Write-Host "Review the output above." -ForegroundColor Red

}

Write-Host ""