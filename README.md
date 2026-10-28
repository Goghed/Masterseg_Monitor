# MasterSeg Monitor

Plataforma Django que recebe o estado dos equipamentos dos clientes, mostra tudo ao vivo e faz a triagem dos eventos (regras + IA) para decidir se o técnico precisa ser chamado. Um agente em Python, instalado num Raspberry Pi (ou qualquer Linux/Windows) na rede do cliente, faz os testes e reporta.

```
agente (no cliente)  ──HTTPS──▶  plataforma Django  ──▶  triagem (regras + IA)  ──▶  webhook do técnico
 ping, portas, ISAPI,             status, histórico,       decisão, prioridade,
 CGI Dahua/Intelbras              eventos, telas            justificativa
```

## 1. Subir a plataforma (desenvolvimento)

```bash
cd plataforma
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8000
```

Abra `http://localhost:8000`, entre com o usuário criado.

**Dados de demonstração** (opcional, para ver as telas cheias):

```bash
python manage.py popular_demo          # 12 clientes DEMO-xx com 30 dias de histórico
python manage.py simular_demo          # mantém os DEMO "vivos" (deixe rodando num terminal)
python manage.py popular_demo --apagar # remove só os DEMO-xx
```

## 2. Cadastrar o primeiro cliente real

Em **Cadastros** (admin):

1. **Cliente**: nome e chave de monitoramento (ex.: `MS-0001`). A chave é o que se digita na busca.
2. **Agente** (dentro do cliente ou em Agentes): salve, abra de novo e copie o bloco **Arquivo agente.ini** com o token.
3. **Equipamentos**: nome, categoria (define a seção na tela), fabricante, IP, porta HTTP, usuário e senha.
   - `Driver`: deixe em *Detectar automaticamente*. Gateway e servidor usam o genérico (ping + portas).
   - `Depende de`: aponte câmeras para o switch, switch para o gateway. É isso que evita 15 chamados quando cai um switch.
   - `Crítico`: queda sempre vira chamado.
   - A senha fica cifrada no banco (Fernet).

## 3. Instalar o agente no Raspberry Pi

Raspberry Pi 5 com Raspberry Pi OS Lite (64-bit). Copie a pasta `agente/` para ele e:

```bash
sudo ./instalar.sh
sudo nano /opt/masterseg-agente/agente.ini     # URL da plataforma + token
sudo -u masterseg /opt/masterseg-agente/venv/bin/python /opt/masterseg-agente/agente.py --uma-vez
sudo systemctl start masterseg-agente
journalctl -u masterseg-agente -f
```

O `--uma-vez` mostra na hora o que cada equipamento respondeu. O instalador cria um usuário sem privilégios, um serviço systemd que volta sozinho se cair e ativa o watchdog de hardware do Raspberry.

**Testar um equipamento na bancada, sem plataforma:**

```bash
python agente.py --testar 192.168.1.20 --usuario admin --senha SENHA
python agente.py --testar 192.168.1.30 --usuario admin --senha SENHA --driver dahua_cgi
```

Imprime tudo o que o driver leu (modelo, firmware, canais, HDs). Use isso para validar cada modelo que vocês instalam.

**Windows:** use a pasta `agente/windows` (veja o `LEIA-ME.txt` dela). Clique com o botão direito em `instalar.bat` > *Executar como administrador*, informe URL e token, e o agente fica instalado em `C:\MasterSeg\Agente` como tarefa agendada (sobe com o Windows, roda como SYSTEM, sem janela, volta sozinho se parar). Para o cliente não precisar de Python, gere antes o `masterseg-agente.exe` com o `gerar_exe.bat` no seu PC, ou pelo workflow `.github/workflows/agente-windows.yml` se o projeto estiver no GitHub.

## Como o agente decide

- A cada ciclo (padrão 30 s): ping e portas TCP. Online = respondeu ping **ou** abriu alguma porta (muitos equipamentos bloqueiam ICMP).
- Só declara offline depois de N falhas seguidas (`falhas_para_offline`, padrão 3). Falhas dentro da tolerância não mudam nada na plataforma.
- Se o equipamento responde na rede mas a porta HTTP/API não abre, vira **degradado: serviço parou** (o caso do NVR que pinga mas travou).
- A cada 5 min, com o equipamento respondendo: coleta lenta pelo driver (identificação, canais do NVR, HDs, CPU).
- Se a plataforma estiver fora, o agente segue testando e manda o estado atual quando ela volta (status é estado, não histórico).

### Drivers

Um driver por **protocolo**, com **perfis por linha de modelo** (dicionário `PERFIS` em cada driver):

| Driver | Atende | Lê |
|---|---|---|
| `hikvision_isapi` | NVR, DVR, câmeras, switches Hikvision | deviceInfo, status, canais IP, HDs |
| `dahua_cgi` | Dahua e Intelbras OEM (NVD, MHDX, VIP) | tipo, versão, serial, MAC, canais, HDs |
| `mikrotik` | RouterOS 7 (REST) e 6 (API 8728) | links WAN, failover, tráfego, CPU, memória, temperatura |
| `generico` | gateway, servidor, qualquer outro | ping e portas |

Modelo novo da mesma família: acrescente uma linha em `PERFIS`, não um driver novo. Fabricante novo: crie `drivers/novo.py` herdando de `Driver` e registre em `drivers/__init__.py`.

### MikroTik e links de internet

Cadastre o gateway com fabricante **MikroTik**, usuário e senha. O agente descobre sozinho os links pelas rotas padrão (`0.0.0.0/0`), DHCP client e PPPoE, e a cada ciclo lê de cada link: se a porta tem sinal, se a rota está ativa, qual é o principal e qual é o reserva, IP, tráfego de download e upload naquele momento, velocidade negociada da porta, latência e perda até o gateway do provedor e quantas vezes a porta caiu.

Crie no MikroTik um usuário só leitura para o agente:

```
/user group add name=monitor policy=read,api,rest-api,test,winbox
/user add name=monitor group=monitor password=SENHA-FORTE
```

- **RouterOS 7**: usa a REST API. Ative `www-ssl` (cadastre porta 443 e marque HTTPS) ou `www` (porta 80, a partir do 7.9), liberado só para o IP do agente em `/ip service`.
- **RouterOS 6**: usa a API clássica na porta 8728. Instale `pip install librouteros` no agente.

No cadastro do gateway, a seção **Links de internet** é opcional, mas destrava mais coisa: nome do link, velocidade contratada (aparece a barra de uso e o aviso de saturação acima de 90% por 3 leituras seguidas), telefone do suporte da operadora e designação do circuito (vão no evento para quem for abrir o chamado). Preenchendo a **tabela de rotas** do link, o agente testa a internet de verdade por aquele link, e não só o gateway do provedor.

Eventos gerados: link caiu (com o nome do reserva que assumiu), link voltou, link instável, link saturado, failover sem queda e retorno ao principal. A triagem trata queda de link com reserva como **abrir chamado na operadora**, não como chamado técnico.

## Triagem e acionamento do técnico

Cada evento de problema passa por `monitor/triagem.py`:

1. **Regras determinísticas**: câmera caiu porque o switch dela caiu → *aguardar, consequência*. Mais de 60% do local caiu junto com o agente ativo → um único evento de **queda geral**.
2. **IA** (se `ANTHROPIC_API_KEY` estiver definida): recebe o evento, o equipamento, o resumo do cliente, histórico de 7 dias e reincidências, e devolve decisão, prioridade e justificativa em JSON.
3. **Regras padrão** quando não há IA: gravador/gateway/servidor/switch offline → chamar técnico; portaria remota → prioridade 1; 3 quedas em 24 h → chamar técnico.

Começa em **modo sombra**: a decisão aparece no painel (clique no evento), mas ninguém é acionado. Depois de comparar algumas semanas, ligue:

```
TRIAGEM_ACIONAMENTO_AUTOMATICO=1
TRIAGEM_WEBHOOK=https://seu-n8n/webhook/chamado     # recebe JSON com cliente, endereço, evento, prioridade
```

O webhook é o ponto para WhatsApp, Telegram ou uma originate no VitalPBX via AMI.

## Produção

**PythonAnywhere:** siga `plataforma/deploy/PYTHONANYWHERE.md` (script de instalação, WSGI e importação dos dados prontos).


- Variáveis em `plataforma/.env.exemplo`. Use PostgreSQL (`DB_ENGINE=postgresql`), `DJANGO_DEBUG=0` e um `DJANGO_SECRET_KEY` novo. Defina `MONITOR_CHAVE_CRIPTO` (gere com `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`) para as senhas não dependerem do SECRET_KEY.
- `gunicorn config.wsgi -w 4 -b 127.0.0.1:8000` atrás do Nginx com HTTPS (Let's Encrypt). `python manage.py collectstatic`.
- Cron a cada minuto: `python manage.py verificar_agentes` (marca agentes silenciosos e limpa métricas com mais de 30 dias).

## Próximos passos sugeridos

- Escutar `alertStream` (Hikvision) e `eventManager attach` (Dahua) no agente para receber eventos na hora; a API `/api/agente/eventos/` já está pronta.
- Driver SNMP para servidores e switches de outros fabricantes.
- Driver da central AMT Intelbras (protocolo ISECNet).
- Celery + Redis para a triagem, quando o volume crescer (hoje roda numa thread por evento).
- Django Channels para empurrar atualizações (hoje a tela consulta a cada 10 s).
