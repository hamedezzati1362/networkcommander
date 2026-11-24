# Builds the Windows package. Run on Windows with Python 3.12 and Node 20+ installed (CI does this automatically).
#   powershell -ExecutionPolicy Bypass -File installer\build-windows.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location "$root\frontend"; npm ci; npm run build; Pop-Location
Push-Location "$root\backend"
python -m pip install -r requirements.txt pyinstaller==6.10.0
python -m PyInstaller --noconfirm --clean --name NetworkCommander --windowed `
  --paths . --add-data "..\frontend\dist;frontend" --add-data "alembic;alembic" --add-data "alembic.ini;." `
  --collect-submodules netmiko --collect-submodules paramiko --collect-data netmiko --collect-data ntc_templates `
  --hidden-import nc.models --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto --hidden-import uvicorn.protocols.http.auto `
  nc\launcher.py
Pop-Location
Write-Host "Package folder: $root\backend\dist\NetworkCommander"
if (Get-Command iscc -ErrorAction SilentlyContinue) {
  iscc /DSourceDir="$root\backend\dist\NetworkCommander" "$root\installer\NetworkCommander.iss"
}
