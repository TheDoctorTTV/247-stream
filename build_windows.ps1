param(
    [string]$PythonExe = "",
    [string]$AppEntry = "Stream247.py",
    [string]$AppName = "stream247-server"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Resolve-PythonCommand {
    param([string]$Preferred)

    $candidates = @()
    if ($Preferred) { $candidates += ,@($Preferred) }
    $candidates += ,@("py", "-3")
    $candidates += ,@("python")
    $candidates += ,@("python3")

    foreach ($candidate in $candidates) {
        $cmd = $candidate[0]
        $cmdInfo = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($cmdInfo) {
            $cmdSource = ""
            if ($cmdInfo.Source) {
                $cmdSource = [string]$cmdInfo.Source
            } elseif ($cmdInfo.Path) {
                $cmdSource = [string]$cmdInfo.Path
            }
            if (($cmd -eq "python" -or $cmd -eq "python3") -and $cmdSource -match "WindowsApps[\\/](python|python3)\.exe$") {
                continue
            }
            try {
                $probeArgs = @()
                if ($candidate.Count -gt 1) {
                    $probeArgs = $candidate[1..($candidate.Count - 1)]
                }
                & $cmd @probeArgs --version | Out-Null
                if ($LASTEXITCODE -eq 0) { return ,$candidate }
            } catch {
                # Try next candidate.
            }
        }
    }

    return $null
}

function Install-PythonIfMissing {
    if (-not (Get-Command "winget" -ErrorAction SilentlyContinue)) {
        throw "Python not found and winget is unavailable. Install Python 3.11+ manually and rerun."
    }

    Write-Host "Python not found. Installing Python 3.12 with winget (source: winget)..."
    winget install -e --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "First winget install attempt failed. Resetting winget sources and retrying..."
        winget source reset --force
        winget install -e --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Automatic Python install failed. Install Python manually from https://www.python.org/downloads/windows/ then rerun this script."
    }

    $possiblePaths = @(
        (Join-Path -Path $env:LocalAppData -ChildPath "Programs\Python\Python312\python.exe"),
        (Join-Path -Path $env:LocalAppData -ChildPath "Programs\Python\Python311\python.exe"),
        (Join-Path -Path ${env:ProgramFiles} -ChildPath "Python312\python.exe"),
        (Join-Path -Path ${env:ProgramFiles} -ChildPath "Python311\python.exe")
    )
    foreach ($path in $possiblePaths) {
        if (Test-Path $path) {
            $parent = Split-Path -Parent $path
            if (-not ($env:Path -split ";" | Where-Object { $_ -eq $parent })) {
                $env:Path = "$parent;$env:Path"
            }
        }
    }
}

function Invoke-Python {
    param(
        [string[]]$Command,
        [string[]]$Args
    )

    if (-not $Command -or $Command.Count -eq 0) {
        throw "No Python command was provided."
    }

    $exe = $Command[0]
    $prefixArgs = @()
    if ($Command.Count -gt 1) {
        $prefixArgs = $Command[1..($Command.Count - 1)]
    }

    & $exe @prefixArgs @Args
}

if (-not (Test-Path -Path $AppEntry)) {
    throw "Entry file not found: $AppEntry"
}

if (-not $PythonExe) {
    $pythonCmd = Resolve-PythonCommand -Preferred ""
} else {
    $pythonCmd = Resolve-PythonCommand -Preferred $PythonExe
}
if (-not $pythonCmd) {
    Install-PythonIfMissing
    $pythonCmd = Resolve-PythonCommand -Preferred $PythonExe
}
if (-not $pythonCmd) {
    throw "Unable to resolve a working Python command after installation."
}

Write-Host ("Using Python command: " + ($pythonCmd -join " "))

Write-Host "Installing/updating pip and build dependencies..."
Invoke-Python -Command $pythonCmd -Args @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed." }

if (Test-Path -Path "requirements.txt") {
    Write-Host "Installing project dependencies from requirements.txt..."
    Invoke-Python -Command $pythonCmd -Args @("-m", "pip", "install", "-r", "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Project dependency install failed." }
}

Write-Host "Installing/updating PyInstaller..."
Invoke-Python -Command $pythonCmd -Args @("-m", "pip", "install", "--upgrade", "pyinstaller")
if ($LASTEXITCODE -ne 0) { throw "Dependency install failed." }

Write-Host "Building $AppName from $AppEntry ..."
$addDataArgs = @()
if (Test-Path -Path "icon.ico") {
    $addDataArgs = @("--add-data", "icon.ico;.")
}
$pyInstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", $AppName
)
if ($addDataArgs.Count -gt 0) {
    $pyInstallerArgs += $addDataArgs
}
$pyInstallerArgs += $AppEntry
Invoke-Python -Command $pythonCmd -Args $pyInstallerArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

Write-Host ""
Write-Host "Build complete:"
Write-Host "  Binary: dist/$AppName.exe"
