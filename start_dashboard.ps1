# Run from any folder. Keep this terminal open; Ctrl+C stops the local server.
$ErrorActionPreference = 'Stop'
$routerPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $routerPython)) {
    throw 'Create the virtual environment and install requirements.txt first (see README).'
}
Write-Host 'Open http://127.0.0.1:8000 in your browser after the server starts.'
& $routerPython (Join-Path $PSScriptRoot 'api.py')
