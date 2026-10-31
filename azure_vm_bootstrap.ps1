# ============================================================
# Azure Windows VM Automated Setup Script for FXAlexG Bot
# ============================================================

$ErrorActionPreference = "Continue"

Write-Host "=== 1. Creating Bot Directory ===" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path "C:\TradingBot" | Out-Null
Set-Location "C:\TradingBot"

Write-Host "=== 2. Installing Python 3.11 ===" -ForegroundColor Cyan
$pythonInstallerUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
$installerPath = "$env:TEMP\python-3.11.9-amd64.exe"
Invoke-WebRequest -Uri $pythonInstallerUrl -OutFile $installerPath
Start-Process -FilePath $installerPath -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 TargetDir=C:\Python311" -Wait

# Add to path immediately
$env:Path = "C:\Python311;C:\Python311\Scripts;" + $env:Path

Write-Host "=== 3. Installing Python Dependencies ===" -ForegroundColor Cyan
& "C:\Python311\python.exe" -m pip install --upgrade pip
& "C:\Python311\python.exe" -m pip install MetaTrader5 requests pandas numpy python-dotenv schedule

Write-Host "=== 4. Downloading MetaTrader 5 ===" -ForegroundColor Cyan
$mt5Url = "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
$mt5Installer = "$env:TEMP\mt5setup.exe"
Invoke-WebRequest -Uri $mt5Url -OutFile $mt5Installer
Start-Process -FilePath $mt5Installer -ArgumentList "/auto" -Wait

Write-Host "=== 5. Setup Complete ===" -ForegroundColor Green
