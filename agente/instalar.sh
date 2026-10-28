#!/usr/bin/env bash
# Instala o agente no Raspberry Pi OS / Debian / Ubuntu.
# Uso: sudo ./instalar.sh
set -euo pipefail
DESTINO=/opt/masterseg-agente
ORIGEM="$(cd "$(dirname "$0")" && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Rode com sudo."; exit 1; fi

apt-get update -qq
apt-get install -y -qq python3 python3-venv iputils-ping

id masterseg &>/dev/null || useradd --system --home "$DESTINO" --shell /usr/sbin/nologin masterseg
mkdir -p "$DESTINO"
cp -r "$ORIGEM"/agente.py "$ORIGEM"/verificacoes.py "$ORIGEM"/drivers "$ORIGEM"/requirements.txt "$DESTINO"/
[[ -f "$DESTINO/agente.ini" ]] || cp "$ORIGEM/agente.ini.exemplo" "$DESTINO/agente.ini"

python3 -m venv "$DESTINO/venv"
"$DESTINO/venv/bin/pip" install -q -r "$DESTINO/requirements.txt"
chown -R masterseg:masterseg "$DESTINO"
chmod 600 "$DESTINO/agente.ini"

# Watchdog de hardware do Raspberry: reinicia a placa se o sistema travar.
if [[ -e /dev/watchdog ]] && ! grep -q '^RuntimeWatchdogSec' /etc/systemd/system.conf; then
  echo 'RuntimeWatchdogSec=15' >> /etc/systemd/system.conf
  systemctl daemon-reexec
fi

cp "$ORIGEM/masterseg-agente.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable masterseg-agente

echo
echo "Instalado em $DESTINO."
echo "1) Edite $DESTINO/agente.ini com a URL e o token."
echo "2) Teste:   sudo -u masterseg $DESTINO/venv/bin/python $DESTINO/agente.py --uma-vez"
echo "3) Inicie:  sudo systemctl start masterseg-agente && journalctl -u masterseg-agente -f"
