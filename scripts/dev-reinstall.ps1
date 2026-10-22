$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "venv not found: $python"
}
& $python -m pip install -q --no-deps --force-reinstall $root
& $python -m pytest (Join-Path $root "tests") -q
