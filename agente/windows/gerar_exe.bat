@echo off
:: Gera o masterseg-agente.exe (nao precisa de Python no cliente).
:: Rode UMA VEZ no seu PC, que ja tem Python. O .exe sai nesta pasta, ao lado do instalar.bat.
:: Depois, para cada cliente, basta copiar a pasta "windows" inteira.
setlocal
cd /d "%~dp0.."
python -m venv .build-venv || goto :erro
call .build-venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt pyinstaller librouteros || goto :erro
pyinstaller --noconfirm --clean --onefile --name masterseg-agente ^
  --hidden-import librouteros --collect-submodules drivers agente.py || goto :erro
copy /y dist\masterseg-agente.exe windows\masterseg-agente.exe >nul
rmdir /s /q build dist .build-venv 2>nul
del /q masterseg-agente.spec 2>nul
echo.
echo Pronto: windows\masterseg-agente.exe
echo Copie a pasta "windows" para o cliente e rode instalar.bat como administrador.
pause
exit /b 0
:erro
echo.
echo Falhou. Confira se o Python 3.9+ esta instalado e no PATH.
pause
exit /b 1
