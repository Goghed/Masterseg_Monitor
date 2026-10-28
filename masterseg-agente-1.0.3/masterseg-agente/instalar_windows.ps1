<#
  MasterSeg Agente: instala como serviço do Windows (NSSM).

  O serviço "MasterSeg Agente" inicia junto com o Windows, volta sozinho se cair
  e atende ao botão "Reiniciar" da plataforma. Log em .\logs\agente.log.

  Uso (o instalar_windows.cmd já faz isto, como administrador):
    powershell -ExecutionPolicy Bypass -File instalar_windows.ps1
    powershell -ExecutionPolicy Bypass -File instalar_windows.ps1 -Remover
  Sem internet para baixar o NSSM: coloque o nssm.exe (64 bits) nesta pasta.
#>
param(
    [switch]$Remover,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Servico = "masterseg-agente"
$Pasta = $PSScriptRoot
$NssmUrl = "https://nssm.cc/release/nssm-2.24.zip"

function Passo($texto) { Write-Host "`n>> $texto" -ForegroundColor Cyan }
function Falha($texto) { Write-Host "`nERRO: $texto" -ForegroundColor Red; Read-Host "Enter para sair"; exit 1 }

# ---------------------------------------------------------------- administrador
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
          ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $argumentos = @("-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Remover) { $argumentos += "-Remover" }
    if ($Python) { $argumentos += @("-Python", "`"$Python`"") }
    Start-Process powershell -Verb RunAs -ArgumentList $argumentos
    exit
}

# ---------------------------------------------------------------- NSSM
$Nssm = Join-Path $Pasta "nssm.exe"
if (-not (Test-Path $Nssm)) {
    Passo "Baixando o NSSM"
    $zip = Join-Path $env:TEMP "nssm-2.24.zip"
    $tmp = Join-Path $env:TEMP "nssm-2.24"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest $NssmUrl -OutFile $zip -UseBasicParsing
        Expand-Archive $zip -DestinationPath $tmp -Force
        Copy-Item (Join-Path $tmp "nssm-2.24\win64\nssm.exe") $Nssm
    } catch {
        Falha "Não consegui baixar o NSSM ($($_.Exception.Message)). Baixe $NssmUrl, extraia e coloque o win64\nssm.exe nesta pasta: $Pasta"
    }
}

function Nssm { & $Nssm @args | Out-Null; if ($LASTEXITCODE -ne 0) { Falha "nssm $($args -join ' ') falhou" } }
$existe = [bool](Get-Service $Servico -ErrorAction SilentlyContinue)

# ---------------------------------------------------------------- remover
if ($Remover) {
    if (-not $existe) { Write-Host "O serviço não está instalado."; Read-Host "Enter para sair"; exit }
    Passo "Parando e removendo o serviço"
    & $Nssm stop $Servico | Out-Null
    Nssm remove $Servico confirm
    Write-Host "Serviço removido. O agente não roda mais sozinho." -ForegroundColor Green
    Read-Host "Enter para sair"
    exit
}

# ---------------------------------------------------------------- conferências
if (-not (Test-Path (Join-Path $Pasta "agente.ini"))) {
    Falha "Falta o agente.ini nesta pasta ($Pasta). Copie o agente.ini.exemplo, preencha URL e token, e rode de novo."
}

Passo "Procurando o Python"
if (-not $Python) {
    $venv = Join-Path $Pasta "venv\Scripts\python.exe"
    if (Test-Path $venv) { $Python = $venv }
    else {
        foreach ($cmd in @("py", "python")) {
            if (Get-Command $cmd -ErrorAction SilentlyContinue) {
                $extra = if ($cmd -eq "py") { @("-3") } else { @() }
                $achado = & $cmd @extra -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $achado) { $Python = $achado.Trim(); break }
            }
        }
    }
}
if (-not $Python -or -not (Test-Path $Python)) {
    Falha "Python não encontrado. Instale o Python 3.11+ de python.org marcando 'Add python.exe to PATH' (ou passe -Python C:\caminho\python.exe)."
}
if ($Python -like "*\WindowsApps\*") {
    Falha "Este é o Python da Microsoft Store ($Python), que não roda como serviço. Instale o Python de python.org e rode de novo."
}
# Um venv aponta para o Python de onde foi criado; se for o da Store, o serviço não abre.
$cfgVenv = Join-Path (Split-Path (Split-Path $Python)) "pyvenv.cfg"
if (Test-Path $cfgVenv) {
    $base = (Select-String -Path $cfgVenv -Pattern '^\s*home\s*=\s*(.+)$').Matches.Groups[1].Value.Trim()
    if ($base -like "*\WindowsApps\*" -or $base -like "*\Microsoft\WindowsApps*") {
        Falha ("O venv em $(Split-Path (Split-Path $Python)) foi criado com o Python da Microsoft Store ($base), " +
               "que não roda como serviço. Instale o Python de python.org (marque 'Add python.exe to PATH'), " +
               "apague a pasta venv e rode este instalador de novo.")
    }
}
Write-Host "Usando $Python"

Passo "Instalando as dependências"
& $Python -m pip install --disable-pip-version-check -q -r (Join-Path $Pasta "requirements.txt")
if ($LASTEXITCODE -ne 0) { Falha "pip install falhou (veja a mensagem acima)." }

# ---------------------------------------------------------------- agente aberto à mão
$manuais = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like "*agente.py*" -and $_.CommandLine -notlike "*--testar*" }
if ($existe) {
    $manuais = $manuais | Where-Object { $_.ParentProcessId -notin (Get-CimInstance Win32_Process -Filter "Name='nssm.exe'").ProcessId }
}
if ($manuais) {
    Passo "Fechando o agente que estava rodando à mão (PID $($manuais.ProcessId -join ', '))"
    Write-Host "Dois agentes ao mesmo tempo testariam tudo em dobro."
    $manuais | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

# ---------------------------------------------------------------- serviço
$logs = Join-Path $Pasta "logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$log = Join-Path $logs "agente.log"

if ($existe) {
    Passo "Atualizando o serviço existente"
    & $Nssm stop $Servico | Out-Null
    Nssm set $Servico Application $Python
} else {
    Passo "Criando o serviço"
    Nssm install $Servico $Python
}
Nssm set $Servico AppParameters "`"$(Join-Path $Pasta 'agente.py')`""
Nssm set $Servico AppDirectory $Pasta
Nssm set $Servico DisplayName "MasterSeg Agente"
Nssm set $Servico Description "Testa os equipamentos do cliente e reporta para a plataforma MasterSeg Monitor."
Nssm set $Servico Start SERVICE_AUTO_START
# Avisa o agente que há supervisor: no "Reiniciar" ele só sai e o NSSM sobe de novo.
Nssm set $Servico AppEnvironmentExtra "MASTERSEG_SUPERVISOR=1" "PYTHONIOENCODING=utf-8" "PYTHONUNBUFFERED=1"
Nssm set $Servico AppExit Default Restart
Nssm set $Servico AppRestartDelay 5000
Nssm set $Servico AppStopMethodConsole 10000
Nssm set $Servico AppStdout $log
Nssm set $Servico AppStderr $log
Nssm set $Servico AppRotateFiles 1
Nssm set $Servico AppRotateOnline 1
Nssm set $Servico AppRotateBytes 5242880

Passo "Iniciando"
& $Nssm start $Servico | Out-Null
Start-Sleep -Seconds 10
$estado = (Get-Service $Servico).Status
if ($estado -ne "Running") {
    # "Paused" no NSSM = o agente fechou logo ao abrir e ele está esperando para tentar de novo.
    Write-Host "`nO agente fechou logo ao iniciar (estado do serviço: $estado). Final do log:" -ForegroundColor Yellow
    if (Test-Path $log) { Get-Content $log -Tail 25 } else { Write-Host "(o log nem chegou a ser criado: o Python não abriu)" }
    & $Nssm stop $Servico | Out-Null
    Falha "Mande uma foto desta tela para o suporte. Log completo: $log"
}

Write-Host "`nServiço instalado e rodando." -ForegroundColor Green
Write-Host "  Nome em Serviços: MasterSeg Agente"
Write-Host "  Log:             $log"
Write-Host "  Reiniciar:       pela página Agentes da plataforma, ou Restart-Service $Servico"
Write-Host "  Remover:         instalar_windows.cmd -Remover`n"
if (Test-Path $log) {
    Write-Host "Últimas linhas do log:" -ForegroundColor Cyan
    Get-Content $log -Tail 8
}
Read-Host "`nEnter para sair"
