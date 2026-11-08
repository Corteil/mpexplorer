# Build the Windows program on this PC (the same steps as CI's windows-build.yml), from PowerShell:
#     .\packaging\windows\build-local.ps1                 # tests on every core, then build, then check the exe
#     .\packaging\windows\build-local.ps1 -NoTests        # build only (about 2 minutes)
#     .\packaging\windows\build-local.ps1 -Workers 1      # the tests on one core (to rule out a parallel-only problem)
#     .\packaging\windows\build-local.ps1 -Zip            # also make mpexplorer-windows.zip like CI's
# The program lands in <Out>\dist\mpexplorer\mpexplorer.exe. Needs MSYS2 (see CONTRIBUTING.md, "Build on Windows").
param(
    [string]$Msys = "D:\msys64",
    [string]$Out = "D:\mpexplorer-build",
    [switch]$NoTests,
    [string]$Workers = "auto",
    [switch]$Zip
)
$bash = Join-Path $Msys "usr\bin\bash.exe"
if (-not (Test-Path $bash)) {
    Write-Error "MSYS2 not found at $Msys (pass -Msys <folder>)"
    exit 1
}
$env:MSYSTEM = "UCRT64"
$env:CHERE_INVOKING = "1"          # the login shell stays in the current folder
$env:MPX_OUT = $Out
$env:MPX_TESTS = if ($NoTests) { "0" } else { "1" }
$env:MPX_WORKERS = $Workers
$env:MPX_ZIP = if ($Zip) { "1" } else { "0" }
Push-Location (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
try {
    & $bash -l packaging/windows/build-local.sh
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
