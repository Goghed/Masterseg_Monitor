"""Variáveis disponíveis em todos os templates."""
from pathlib import Path

_PASTA = Path(__file__).parent / "static" / "monitor"
# Muda quando um deploy altera CSS/JS: o navegador baixa o arquivo novo em vez de usar o do cache.
VERSAO_ESTATICOS = str(int(max((p.stat().st_mtime for p in _PASTA.glob("*.*")), default=0)))


def estaticos(request):
    return {"v": VERSAO_ESTATICOS}
