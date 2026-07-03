param(
    [ValidateSet("p1", "p2", "p3", "all")]
    [string]$Tier = "p1",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Files = switch ($Tier) {
    "p1" { @("requirements-forecasting-p1.txt") }
    "p2" { @("requirements-forecasting-p2.txt") }
    "p3" { @("requirements-forecasting-p3.txt") }
    "all" { @("requirements-forecasting.txt") }
}

foreach ($File in $Files) {
    & $Python -m pip install -r $File
    if ($LASTEXITCODE -ne 0) {
        throw "Forecasting dependency installation failed for $File"
    }
}

& $Python scripts/check_forecasting_stack.py
if ($LASTEXITCODE -ne 0) {
    throw "Forecasting readiness check failed"
}
