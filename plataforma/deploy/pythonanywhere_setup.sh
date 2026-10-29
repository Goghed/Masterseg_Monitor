#!/usr/bin/env bash
# Prepara a plataforma no PythonAnywhere. Rode num "Bash console":
#   bash ~/Masterseg_Monitor/plataforma/deploy/pythonanywhere_setup.sh
# (troque Masterseg_Monitor pelo nome da pasta do seu clone, se for outro)
# Pode rodar de novo a cada atualização (git pull): ele só aplica o que mudou.
set -euo pipefail

# Pasta "plataforma" a partir de onde este script está (funciona com qualquer nome de pasta)
PROJETO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$HOME/.virtualenvs/masterseg"
DOMINIO="${USER}.pythonanywhere.com"

cd "$PROJETO"

# Python mais novo disponível (o Django 5.2 precisa de 3.10+)
PY=""
for v in 3.13 3.12 3.11 3.10; do
  if command -v "python$v" >/dev/null 2>&1; then PY="python$v"; break; fi
done
[ -n "$PY" ] || { echo "Nenhum Python 3.10+ encontrado"; exit 1; }

if [ ! -d "$VENV" ]; then
  echo "==> Criando o virtualenv com $PY"
  "$PY" -m venv "$VENV"
fi
echo "==> Instalando dependências"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "django>=5.1,<6" "cryptography>=42" "requests>=2.31"

if [ ! -f .env ]; then
  echo "==> Criando o .env"
  SEGREDO=$("$VENV/bin/python" -c "import secrets; print(secrets.token_urlsafe(50))")
  cat > .env <<CONF
DJANGO_SECRET_KEY=$SEGREDO
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=$DOMINIO
DJANGO_CSRF_ORIGINS=https://$DOMINIO

# PythonAnywhere não executa threads nas aplicações web
TRIAGEM_MODO=painel

# Cole aqui a linha que o comando "mostrar_chave_cripto" mostrou no notebook,
# senão as senhas dos equipamentos importados não abrem:
MONITOR_CHAVE_CRIPTO=

# IA (opcional): chave da Anthropic
ANTHROPIC_API_KEY=
TRIAGEM_ACIONAMENTO_AUTOMATICO=0
TRIAGEM_WEBHOOK=
CONF
  chmod 600 .env
  echo "   .env criado. Edite para colocar MONITOR_CHAVE_CRIPTO e, se quiser, ANTHROPIC_API_KEY."
fi

echo "==> Banco e arquivos estáticos"
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py collectstatic --noinput -v0
"$VENV/bin/python" manage.py check --deploy 2>&1 | tail -n 3 || true

echo
echo "Pronto. Na aba Web do PythonAnywhere, confira:"
echo "  Virtualenv:      $VENV"
echo "  Static files:    URL /static/  ->  $PROJETO/staticfiles"
echo "  WSGI:            conteúdo de deploy/pythonanywhere_wsgi.py com PROJETO = \"$PROJETO\""
echo "Depois clique em Reload."
