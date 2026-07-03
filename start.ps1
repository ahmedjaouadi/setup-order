Set-Location -LiteralPath $PSScriptRoot
$python = $env:SETUP_ORDER_PYTHON
if (-not $python) {
    $candidate = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $candidate) {
        $python = $candidate
    }
}
if (-not $python) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
    if (Test-Path -LiteralPath $candidate) {
        $python = $candidate
    }
}
if (-not $python) {
    $python = "python"
}
& $python .\run.py @args
