@echo off
rem Duplo clique para instalar (ou atualizar) o MasterSeg Agente como servico do Windows.
rem Para remover: instalar_windows.cmd -Remover
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar_windows.ps1" %*
