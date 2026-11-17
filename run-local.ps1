$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
$PythonCommand = $null
$PythonArguments = @()

if ($PythonLauncher) {
    foreach ($Version in @("-3.12", "-3.11")) {
        & $PythonLauncher.Source $Version --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $PythonCommand = $PythonLauncher
            $PythonArguments = @($Version)
            break
        }
    }
}

if (-not $PythonCommand) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        $DetectedVersion = & $PythonCommand.Source --version
        if ($DetectedVersion -notmatch "^Python 3\.(11|12)\.") {
            $PythonCommand = $null
        }
    }
}

if (-not $PythonCommand) {
    throw "Python 3.11 or 3.12 is required. Install it from https://www.python.org/downloads/ and rerun this script."
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    & $PythonCommand.Source @PythonArguments -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the virtual environment."
    }
}

& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed. Check your internet connection and try again."
}

& $VenvPython -m streamlit run (Join-Path $ProjectRoot "dashboard.py")
if ($LASTEXITCODE -ne 0) {
    throw "The dashboard exited with an error."
}
