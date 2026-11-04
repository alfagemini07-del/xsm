$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

Write-Host "Agent kutubxonalari o'rnatilmoqda..." -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install "pyinstaller>=6.11,<7" "pywin32>=311" "Pillow>=10,<13" "qrcode>=8.2,<9" "reportlab>=4.2,<5"
if ($LASTEXITCODE -ne 0) { throw "Kutubxonalarni o'rnatib bo'lmadi" }

Write-Host "AgentSetup.exe yig'ilmoqda..." -ForegroundColor Cyan
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name AgentSetup `
    --collect-data reportlab `
    --hidden-import win32timezone `
    local_agent.py
if ($LASTEXITCODE -ne 0) { throw "AgentSetup.exe yaratilmadi" }

$result = Join-Path $projectRoot "dist\AgentSetup.exe"
if (-not (Test-Path -LiteralPath $result)) { throw "Natija topilmadi: $result" }
Write-Host "Tayyor: $result" -ForegroundColor Green
