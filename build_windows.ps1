param(
    [string]$PythonExe = "python",
    [string]$AppEntry = "Stream247_GUI.py",
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
& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name $AppName `
    $AppEntry
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

if (Test-Path -Path "config.json") {
    Copy-Item -Path "config.json" -Destination "dist/config.json.example" -Force
}

Write-Host ""
Write-Host "Build complete:"
Write-Host "  Binary: dist/$AppName.exe"
Write-Host "  Config template: dist/config.json.example"
