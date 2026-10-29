<#
  MasterSeg Agente - instalador para Windows 10/11 e Windows Server 2016+

  Uso (o instalar.bat faz isso por você, já como administrador):
    powershell -ExecutionPolicy Bypass -File instalar.ps1 -Url https://monitor.seudominio.com.br -Token SEU_TOKEN

  O que faz:
    1. Copia o agente para C:\MasterSeg\Agente
    2. Usa o masterseg-agente.exe se ele estiver nesta pasta. Se não estiver, instala
       o Python (via winget) e as dependências num ambiente isolado.
    3. Cria o agente.ini com a URL e o token (lidos do agente.ini do pacote, se houver)
    4. Roda um ciclo de teste e mostra o resultado
    5. Registra a tarefa agendada "MasterSeg Agente": inicia com o Windows, roda como
       SYSTEM, sem janela, e é reiniciada sozinha se parar.
#>
param(
    [string]$Url,
    [string]$Token,
    [switch]$SemTls,
    [string]$Destino = "C:\MasterSeg\Agente"
)

$ErrorActionPreference = "Stop"
# PowerShell 5.1 não usa TLS 1.2 por padrão; a Cloudflare exige.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Tudo o que aparece na tela também vai para este arquivo, para diagnóstico.
$LogInstalacao = Join-Path $env:TEMP "masterseg-instalacao.log"
try { Start-Transcript -Path $LogInstalacao -Force | Out-Null } catch {}

# Qualquer erro inesperado: mostra, diz onde está o log e NÃO fecha a janela.
trap {
    Write-Host "`nERRO INESPERADO: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Linha $($_.InvocationInfo.ScriptLineNumber): $($_.InvocationInfo.Line.Trim())" -ForegroundColor DarkGray
    Write-Host "Log da instalação: $LogInstalacao" -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host "`nEnter para fechar"
    exit 1
}
$Origem = Split-Path -Parent $MyInvocation.MyCommand.Path
# Os arquivos do agente podem estar na mesma pasta do instalador (pacote do cliente)
# ou na pasta de cima (projeto completo: agente\windows\instalar.bat).
$Raiz = @($Origem, (Split-Path -Parent $Origem)) | Where-Object { Test-Path (Join-Path $_ "agente.py") } | Select-Object -First 1
$Tarefa = "MasterSeg Agente"

function Passo($texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }
function Falha($texto) {
    Write-Host "`nERRO: $texto" -ForegroundColor Red
    Write-Host "Log da instalação: $LogInstalacao" -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host "`nEnter para fechar"
    exit 1
}

# ------------------------------------------------------------------ administrador
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Falha "Rode como administrador (clique com o botão direito no instalar.bat > Executar como administrador)." }

# ------------------------------------------------------------------ dados
# Pacote baixado da plataforma (Cadastros > Agentes > Baixar instalador): URL e token
# já vêm no agente.ini ao lado do instalador, e nada precisa ser digitado.
$IniPacote = Join-Path $Origem "agente.ini"
if ((-not $Url -or -not $Token) -and (Test-Path $IniPacote)) {
    $ini = Get-Content $IniPacote -Encoding UTF8
    function Ler-Ini($chave) {
        $linha = $ini | Where-Object { $_ -match "^\s*$chave\s*=" } | Select-Object -First 1
        if ($linha) { return ($linha -replace "^\s*$chave\s*=\s*", "").Trim() }
        return ""
    }
    if (-not $Url) { $Url = Ler-Ini "url" }
    if (-not $Token) { $Token = Ler-Ini "token" }
    if ((Ler-Ini "verificar_tls") -eq "nao") { $SemTls = $true }
    Write-Host "Configuração do pacote: $Url" -ForegroundColor Cyan
}
if (-not $Url)   { $Url   = Read-Host "URL da plataforma (ex.: https://monitor.seudominio.com.br)" }
if (-not $Token) { $Token = Read-Host "Token do agente (Cadastros > Agentes > abra o agente)" }
$Url = $Url.Trim().TrimEnd("/")
$Token = $Token.Trim()
if (-not $Url -or -not $Token) { Falha "URL e token são obrigatórios." }
if ($Url.StartsWith("http://") -and -not $SemTls) {
    Write-Host "A URL é http://, então a verificação de certificado será desligada." -ForegroundColor Yellow
    $SemTls = $true
}

# ------------------------------------------------------------------ parar versão anterior
if (Get-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue) {
    Passo "Parando a versão instalada"
    Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
    Get-Process masterseg-agente, pythonw -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$Destino*" } | Stop-Process -Force -ErrorAction SilentlyContinue
}

# ------------------------------------------------------------------ copiar arquivos
Passo "Copiando arquivos para $Destino"
New-Item -ItemType Directory -Force -Path $Destino | Out-Null
$exeOrigem = @("$Origem\masterseg-agente.exe", "$(Split-Path -Parent $Origem)\masterseg-agente.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1

if ($exeOrigem) {
    if ((Resolve-Path $exeOrigem).Path -ine (Join-Path $Destino "masterseg-agente.exe")) {
        Copy-Item $exeOrigem "$Destino\masterseg-agente.exe" -Force
    }
    $Executavel = "$Destino\masterseg-agente.exe"
    $Teste = $Executavel
    $ArgsTeste = @()
    $ArgsTarefa = @()
    Write-Host "Usando o executável pronto (não precisa de Python)."
} else {
    if (-not $Raiz) {
        Falha ("Não achei o agente.py junto do instalador (procurei em $Origem e na pasta de cima). " +
               "Copie a pasta inteira do pacote masterseg-agente-windows, não só o instalar.bat.")
    }
    $mesmaPasta = (Resolve-Path $Raiz).Path.TrimEnd("\") -ieq (Resolve-Path $Destino).Path.TrimEnd("\")
    foreach ($item in @("agente.py", "verificacoes.py", "requirements.txt", "drivers")) {
        if (-not (Test-Path "$Raiz\$item")) { Falha "Não achei $item em $Raiz. Copie a pasta inteira do pacote." }
        # Rodando de dentro do próprio destino: os arquivos já estão no lugar.
        if (-not $mesmaPasta) { Copy-Item "$Raiz\$item" $Destino -Recurse -Force }
    }

    Passo "Verificando o Python"
    function Achar-Python {
        # Cada candidato: executável + argumentos. Sem passar pelo cmd, para não brigar com aspas.
        $candidatos = @(
            @("py", "-3"), @("python"),
            @("$env:ProgramFiles\Python313\python.exe"), @("$env:ProgramFiles\Python312\python.exe"),
            @("$env:ProgramFiles\Python311\python.exe")
        )
        foreach ($c in $candidatos) {
            $exe = $c[0]; $extra = @($c | Select-Object -Skip 1)
            try {
                $ok = & $exe @extra -c "import sys; print(sys.version_info >= (3, 9))" 2>$null
                if ($LASTEXITCODE -eq 0 -and ($ok -contains "True")) { return ,$c }
            } catch {}
        }
        return $null
    }
    $python = Achar-Python
    if (-not $python) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Falha "Python não encontrado e sem winget para instalar. Instale o Python 3.12 de python.org (marque 'Add to PATH') e rode de novo."
        }
        Passo "Instalando o Python 3.12 (winget)"
        $ErrorActionPreference = "Continue"
        winget install --id Python.Python.3.12 -e --scope machine --silent --accept-package-agreements --accept-source-agreements
        $ErrorActionPreference = "Stop"
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        $python = Achar-Python
        if (-not $python) { Falha "O Python foi instalado, mas não consegui usá-lo. Feche esta janela e rode o instalador de novo." }
    }
    Write-Host ("Python: " + ($python -join " "))

    Passo "Criando o ambiente do agente e instalando dependências"
    $pyExe = $python[0]; $pyArgs = @($python | Select-Object -Skip 1)
    $ErrorActionPreference = "Continue"
    & $pyExe @pyArgs -m venv "$Destino\venv"
    $codigoVenv = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($codigoVenv -ne 0) { Falha "Não consegui criar o ambiente Python." }
    $ErrorActionPreference = "Continue"   # avisos do pip no stderr não são erro
    & "$Destino\venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r "$Destino\requirements.txt"
    $codigoPip = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($codigoPip -ne 0) { Falha "Falha no pip install. Confira a internet desta máquina." }
    $Executavel = "$Destino\venv\Scripts\pythonw.exe"   # sem janela
    $Teste = "$Destino\venv\Scripts\python.exe"         # com saída, para o teste
    $ArgsTeste = @("$Destino\agente.py")
    $ArgsTarefa = @("`"$Destino\agente.py`"")
}

# ------------------------------------------------------------------ configuração
Passo "Gravando agente.ini"
$tls = if ($SemTls) { "nao" } else { "sim" }
@"
[plataforma]
url = $Url
token = $Token
verificar_tls = $tls

[agente]
threads = 16
coleta_detalhes_segundos = 300
recarregar_config_segundos = 300
"@ | Set-Content -Path "$Destino\agente.ini" -Encoding UTF8

# Só administradores e o SYSTEM leem o token.
icacls "$Destino\agente.ini" /inheritance:r /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F" | Out-Null

# ------------------------------------------------------------------ teste
Passo "Testando: um ciclo completo contra a plataforma"
$ErrorActionPreference = "Continue"   # o log do agente sai no stderr
& $Teste @ArgsTeste --config "$Destino\agente.ini" --uma-vez
$codigoTeste = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($codigoTeste -ne 0) {
    Write-Host "`nO teste falhou. Confira URL, token e se esta máquina chega na plataforma." -ForegroundColor Yellow
    $seguir = Read-Host "Registrar a tarefa mesmo assim? (s/N)"
    if ($seguir -ne "s") { Falha "Instalação interrompida no teste." }
}

# ------------------------------------------------------------------ tarefa agendada
Passo "Registrando a tarefa agendada '$Tarefa'"
$argumentos = ($ArgsTarefa + @("--supervisionado", "--config", "`"$Destino\agente.ini`"", "--log", "`"$Destino\agente.log`"")) -join " "
$acao = New-ScheduledTaskAction -Execute $Executavel -Argument $argumentos -WorkingDirectory $Destino
$aoIniciar = New-ScheduledTaskTrigger -AtStartup
# Gatilho de segurança: a cada minuto tenta iniciar; se já estiver rodando, ignora.
# É ele que sobe o agente de volta depois do "Reiniciar" pedido pela plataforma.
$vigia = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$config = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$quem = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $Tarefa -Action $acao -Trigger @($aoIniciar, $vigia) -Settings $config `
    -Principal $quem -Description "Monitoramento de equipamentos MasterSeg" -Force | Out-Null
Start-ScheduledTask -TaskName $Tarefa

Start-Sleep -Seconds 3
$estado = (Get-ScheduledTask -TaskName $Tarefa).State
Write-Host "`nPronto. Tarefa '$Tarefa': $estado" -ForegroundColor Green
Write-Host "Log:          $Destino\agente.log"
Write-Host "Ver o log:    Get-Content '$Destino\agente.log' -Wait -Tail 30"
Write-Host "Trocar URL:   trocar_url.bat (nesta pasta)"
Write-Host "Desinstalar:  desinstalar.bat (nesta pasta)"
Write-Host "Log da instalação: $LogInstalacao"
try { Stop-Transcript | Out-Null } catch {}
Read-Host "`nEnter para fechar"
