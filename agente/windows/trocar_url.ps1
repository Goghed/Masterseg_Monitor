<#
  Troca o endereço da plataforma no agente já instalado e reinicia o agente.
  Use quando o endereço do túnel mudar. O trocar_url.bat roda isto como administrador.
#>
param([string]$Url)

$ErrorActionPreference = "Stop"
# PowerShell 5.1 não usa TLS 1.2 por padrão; a Cloudflare exige.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Ini = "C:\MasterSeg\Agente\agente.ini"
$Log = "C:\MasterSeg\Agente\agente.log"
$Tarefa = "MasterSeg Agente"

trap {
    Write-Host "`nERRO: $($_.Exception.Message)" -ForegroundColor Red
    Read-Host "`nEnter para fechar"; exit 1
}

if (-not (Test-Path $Ini)) { throw "Agente não instalado ($Ini não existe). Rode o instalar.bat primeiro." }

$atual = (Get-Content $Ini -Encoding UTF8 | Where-Object { $_ -match '^\s*url\s*=' }) -replace '^\s*url\s*=\s*', ''
Write-Host "Endereço atual: $atual"
if (-not $Url) { $Url = Read-Host "Novo endereço (ex.: https://xxxx.trycloudflare.com)" }
$Url = $Url.Trim().TrimEnd("/")
if ($Url -notmatch '^https?://') { throw "O endereço precisa começar com https://" }

Write-Host "Testando $Url ..."
try {
    Invoke-WebRequest "$Url/entrar/" -UseBasicParsing -TimeoutSec 15 | Out-Null
    Write-Host "A plataforma respondeu." -ForegroundColor Green
} catch {
    Write-Host "A plataforma NÃO respondeu nesse endereço: $($_.Exception.Message)" -ForegroundColor Yellow
    if ((Read-Host "Gravar mesmo assim? (s/N)") -ne "s") { exit 1 }
}

$linhas = Get-Content $Ini -Encoding UTF8 | ForEach-Object { if ($_ -match '^\s*url\s*=') { "url = $Url" } else { $_ } }
$tls = if ($Url.StartsWith("http://")) { "nao" } else { "sim" }
$linhas = $linhas | ForEach-Object { if ($_ -match '^\s*verificar_tls\s*=') { "verificar_tls = $tls" } else { $_ } }
$linhas | Set-Content $Ini -Encoding UTF8

Write-Host "Reiniciando o agente..."
Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
Get-Process pythonw, masterseg-agente -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "C:\MasterSeg\Agente*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 2
Start-ScheduledTask -TaskName $Tarefa
Start-Sleep 12

Write-Host "`nÚltimas linhas do log:" -ForegroundColor Cyan
Get-Content $Log -Tail 6 -Encoding UTF8
Read-Host "`nEnter para fechar"
