<#
  Sobe a plataforma e o túnel da Cloudflare de uma vez, e mostra o endereço público.

  Uso (na pasta "plataforma"):
    powershell -ExecutionPolicy Bypass -File .\iniciar_com_tunel.ps1
    powershell -ExecutionPolicy Bypass -File .\iniciar_com_tunel.ps1 -Porta 8085
#>
param([int]$Porta = 8085)

$ErrorActionPreference = "Stop"
# PowerShell 5.1 não usa TLS 1.2 por padrão; a Cloudflare exige.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Aqui = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Aqui

# ---------------------------------------------------------------- Python do projeto
$python = @("$Aqui\venv\Scripts\python.exe", "$Aqui\..\venv\Scripts\python.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) { $python = "python" }

# ---------------------------------------------------------------- cloudflared
$cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cf) {
    $cf = @("${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe", "$env:ProgramFiles\cloudflared\cloudflared.exe",
            "$Aqui\cloudflared.exe", "$Aqui\cloudflared-windows-amd64.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $cf) { Write-Host "cloudflared não encontrado. Instale: winget install --id Cloudflare.cloudflared" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- parar o que já estiver rodando
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
$ocupada = Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue
if ($ocupada) {
    Write-Host "A porta $Porta já está em uso (processo $($ocupada[0].OwningProcess)). Usando o servidor que já está rodando." -ForegroundColor Yellow
} else {
    Write-Host "==> Subindo a plataforma na porta $Porta (janela separada)" -ForegroundColor Cyan
    Start-Process -FilePath $python -ArgumentList "manage.py", "runserver", "0.0.0.0:$Porta" -WorkingDirectory $Aqui
}

# Espera a plataforma responder localmente
$ok = $false
foreach ($i in 1..30) {
    try { Invoke-WebRequest "http://127.0.0.1:$Porta/entrar/" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { Start-Sleep 1 }
}
if (-not $ok) { Write-Host "A plataforma não respondeu em http://127.0.0.1:$Porta. Veja a janela do runserver." -ForegroundColor Red; exit 1 }
Write-Host "Plataforma respondendo em http://localhost:$Porta" -ForegroundColor Green

# ---------------------------------------------------------------- túnel
Write-Host "==> Abrindo o túnel da Cloudflare" -ForegroundColor Cyan
$log = Join-Path $env:TEMP "cloudflared-masterseg.log"
Remove-Item $log -ErrorAction SilentlyContinue
$tunel = Start-Process -FilePath $cf -ArgumentList "tunnel", "--no-autoupdate", "--url", "http://localhost:$Porta" `
    -RedirectStandardError $log -WindowStyle Minimized -PassThru

$url = $null
foreach ($i in 1..60) {
    Start-Sleep 1
    if (Test-Path $log) {
        $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value; break }
    }
    if ($tunel.HasExited) { break }
}
if (-not $url) { Write-Host "Não consegui o endereço do túnel. Log: $log" -ForegroundColor Red; exit 1 }

# O DNS do endereço novo leva alguns segundos para propagar
Write-Host "Endereço: $url (aguardando ficar acessível...)"
$publico = $false
foreach ($i in 1..40) {
    try { Invoke-WebRequest "$url/entrar/" -UseBasicParsing -TimeoutSec 5 | Out-Null; $publico = $true; break } catch { Start-Sleep 2 }
}
try { Set-Clipboard -Value $url } catch {}

Write-Host ""
if ($publico) { Write-Host "TÚNEL NO AR" -ForegroundColor Green } else { Write-Host "Túnel criado, mas ainda não respondeu. Teste no navegador em 1 minuto." -ForegroundColor Yellow }
Write-Host "URL para o agente:  $url   (já copiada para a área de transferência)" -ForegroundColor Green
Write-Host ""
Write-Host "No PC do cliente, rode o trocar_url.bat e cole essa URL."
Write-Host "Mantenha esta janela, a do runserver e o cloudflared abertos. Ctrl+C aqui não derruba o túnel."
Write-Host "Log do túnel: $log"
