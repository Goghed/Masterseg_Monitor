# Publicar no PythonAnywhere

Endereço final: `https://SEU_USUARIO.pythonanywhere.com` (fixo, com HTTPS). É a URL que vai no agente.

> Conta gratuita permite **uma** aplicação web por conta. Se o usuário já tem outro projeto publicado, crie outra conta (ex.: `mastersegmonitor`) ou use um plano pago.

## 1. No notebook: exportar os dados

Na pasta `plataforma`, com o venv ativo:

```
python manage.py dumpdata auth.user monitor --exclude monitor.metrica --exclude monitor.metricalink --indent 2 -o dados.json
python manage.py mostrar_chave_cripto
```

Guarde o `dados.json` e a linha `MONITOR_CHAVE_CRIPTO=...`. Sem essa chave, as senhas dos equipamentos não abrem no servidor e o agente recebe senha vazia (erro 401).

## 2. No PythonAnywhere: baixar o código

Em **Consoles > Bash**:

```
git clone https://github.com/Goghed/Masterseg_Monitor.git
bash ~/Masterseg_Monitor/plataforma/deploy/pythonanywhere_setup.sh
```

Repositório privado: o `git clone` pede usuário e um *personal access token* do GitHub no lugar da senha.

## 3. Ajustar o .env

```
nano ~/Masterseg_Monitor/plataforma/.env
```

- Cole a linha `MONITOR_CHAVE_CRIPTO=...` do passo 1.
- Opcional: `ANTHROPIC_API_KEY=sk-ant-...` para ligar a IA (o `api.anthropic.com` é liberado na conta gratuita).

Salve com `Ctrl+O`, Enter, `Ctrl+X`.

## 4. Importar os dados

Envie o `dados.json` em **Files** para `/home/SEU_USUARIO/Masterseg_Monitor/plataforma/` e, no Bash:

```
cd ~/Masterseg_Monitor/plataforma
~/.virtualenvs/masterseg/bin/python manage.py loaddata dados.json
~/.virtualenvs/masterseg/bin/python manage.py popular_demo --apagar
rm dados.json
```

## 5. Criar a aplicação web

Em **Web > Add a new web app**: Next > **Manual configuration** > a mesma versão de Python que o script usou.

Depois, na página da aplicação:

| Campo | Valor |
|---|---|
| Source code | `/home/SEU_USUARIO/Masterseg_Monitor/plataforma` |
| Virtualenv | `/home/SEU_USUARIO/.virtualenvs/masterseg` |
| Static files: URL `/static/` | `/home/SEU_USUARIO/Masterseg_Monitor/plataforma/staticfiles` |
| Force HTTPS | ligado |

No link do **WSGI configuration file**, apague tudo e cole o conteúdo de `deploy/pythonanywhere_wsgi.py`, trocando `SEU_USUARIO`. Clique em **Reload**.

## 6. Apontar o agente

No PC do cliente, `trocar_url.bat` como administrador, com `https://SEU_USUARIO.pythonanywhere.com`. O token continua o mesmo, porque veio no `dados.json`.

## Atualizar depois

```
cd ~/Masterseg_Monitor && git pull
bash plataforma/deploy/pythonanywhere_setup.sh
```

e **Reload** na aba Web.

## Como a triagem funciona aqui

O PythonAnywhere não executa threads em aplicações web, por isso o `.env` usa `TRIAGEM_MODO=painel`: as regras decidem na hora, e a revisão da IA acontece a cada atualização do painel (um evento por vez). Sem ninguém com o painel aberto, a revisão espera. Em plano pago, crie uma tarefa *always-on* com:

```
/home/SEU_USUARIO/.virtualenvs/masterseg/bin/python /home/SEU_USUARIO/Masterseg_Monitor/plataforma/manage.py processar_triagem --loop
```

O mesmo vale para a detecção de agente sem contato, que roda junto com o painel e com esse comando.

Conta gratuita: a aplicação expira em um mês sem acesso ao painel do PythonAnywhere. Entre na aba Web e clique em "Run until 1 month from today" antes disso.
