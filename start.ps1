$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path "frontend\node_modules")) {
    Push-Location frontend
    npm install
    Pop-Location
}

if (-not (Test-Path "frontend\dist\index.html")) {
    Push-Location frontend
    npm run build
    Pop-Location
}

& .\.venv\Scripts\python.exe run.py
