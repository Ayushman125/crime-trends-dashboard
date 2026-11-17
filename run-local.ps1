param(
    [switch]$SetupOnly
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$PythonCommand = $null
$PythonExecutable = $null
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
        $ProbeOutput = & $Command.Source @Arguments -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}'); print(sys.executable)" 2>&1
        $ExitCode = $LASTEXITCODE
    }
    catch {
        $ExitCode = 1
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    $ProbeLines = @($ProbeOutput | ForEach-Object { "$_".Trim() })
    if (
        $ExitCode -eq 0 -and
        $ProbeLines.Count -ge 2 -and
        $ProbeLines[0] -match "^3\.(11|12|13|14)$" -and
        (Test-Path $ProbeLines[1])
    ) {
        $PythonCommand = $Command
        $PythonExecutable = $ProbeLines[1]
        $SupportedVersion = $ProbeLines[0]
        break
    }
}

if (-not $PythonCommand) {
    throw "Python 3.11 through 3.14 is required. Install one from https://www.python.org/downloads/ and rerun this script."
}

Write-Host "Using Python $SupportedVersion"

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    & $PythonExecutable -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the virtual environment."
    }
}

if (-not (Test-Path $VenvPython)) {
    throw "Python $SupportedVersion did not create the expected virtual-environment interpreter at $VenvPython."
}

& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed. Check your internet connection and try again."
}

if ($SetupOnly) {
    return
}

& $VenvPython -m streamlit run (Join-Path $ProjectRoot "dashboard.py")
if ($LASTEXITCODE -ne 0) {
    throw "The dashboard exited with an error."
}
