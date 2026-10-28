@echo off
:: Troca o endereco da plataforma no agente instalado e reinicia o agente.
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0trocar_url.ps1" %*
