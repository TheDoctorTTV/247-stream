param(
    [string]$PythonExe = "python",
    [string]$AppEntry = "Stream247.py",
    [string]$AppName = "stream247-server"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
    throw "Python not found: $PythonExe"
}

if (-not (Test-Path -Path $AppEntry)) {
    throw "Entry file not found: $AppEntry"
}

Write-Host "Installing/updating build dependencies..."
& $PythonExe -m pip install --upgrade pip pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Dependency install failed." }

Write-Host "Building $AppName from $AppEntry ..."
$addDataArgs = @()
if (Test-Path -Path "icon.ico") {
    $addDataArgs = @("--add-data", "icon.ico;.")
}
& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name $AppName `
    @addDataArgs `
    $AppEntry
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

Write-Host ""
Write-Host "Build complete:"
Write-Host "  Binary: dist/$AppName.exe"
