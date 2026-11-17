$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$PythonCommand = $null
$PythonArguments = @()
$SupportedVersion = $null
$Candidates = @()

foreach ($Name in @("python", "python3")) {
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($Command) {
        $Candidates += @{ Command = $Command; Arguments = @() }
    }
}

$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PythonLauncher) {
    $Candidates += @{ Command = $PythonLauncher; Arguments = @("-3") }
}

foreach ($Candidate in $Candidates) {
    $Command = $Candidate.Command
    $Arguments = $Candidate.Arguments
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $VersionOutput = & $Command.Source @Arguments -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
        $ExitCode = $LASTEXITCODE
    }
    catch {
        $ExitCode = 1
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    if ($ExitCode -eq 0 -and "$VersionOutput" -match "^3\.(11|12|13|14)$") {
        $PythonCommand = $Command
        $PythonArguments = $Arguments
        $SupportedVersion = "$VersionOutput"
        break
    }
}

if (-not $PythonCommand) {
    throw "Python 3.11 through 3.14 is required. Install one from https://www.python.org/downloads/ and rerun this script."
}

Write-Host "Using Python $SupportedVersion"

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
