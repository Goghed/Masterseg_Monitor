@echo off
:: Remove a tarefa agendada e a pasta C:\MasterSeg\Agente.
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Stop-ScheduledTask -TaskName 'MasterSeg Agente' -ErrorAction SilentlyContinue;" ^
  "Unregister-ScheduledTask -TaskName 'MasterSeg Agente' -Confirm:$false -ErrorAction SilentlyContinue;" ^
  "Get-Process masterseg-agente, pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like 'C:\MasterSeg\Agente*' } | Stop-Process -Force;" ^
  "Start-Sleep 2; Remove-Item 'C:\MasterSeg\Agente' -Recurse -Force -ErrorAction SilentlyContinue;" ^
  "Write-Host 'Agente removido.' -ForegroundColor Green"
pause
