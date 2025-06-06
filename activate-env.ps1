$EnvDir = '.venv'

if (-not (Test-Path $EnvDir)) {
    Write-Host 'Creating uv virtual environment...'
    uv venv $EnvDir
    Write-Host 'Installing dependencies from requirements.txt...'
    uv pip install -r requirements.txt
} else {
    Write-Host "Using existing virtual environment at $EnvDir"
}

$ActivateScript = Join-Path $EnvDir 'Scripts/Activate.ps1'
if (Test-Path $ActivateScript) {
    & $ActivateScript
    Write-Host 'Environment activated'
} else {
    Write-Error "Could not find activation script in $EnvDir"
    exit 1
}
