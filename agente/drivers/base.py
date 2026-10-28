"""Interface comum dos drivers.

Um driver fala um PROTOCOLO (ISAPI, CGI Dahua...). Diferenças entre modelos da
mesma família ficam em `PERFIS`, sem precisar de um driver novo por modelo.
"""
import re
import warnings
import xml.etree.ElementTree as ET

import requests
from requests.auth import HTTPDigestAuth

warnings.filterwarnings("ignore", message="Unverified HTTPS request")


class ErroDriver(Exception):
    pass


class Driver:
    nome = "base"
    # Portas testadas no ciclo rápido, além das cadastradas.
    portas_padrao = [80]
    # Perfis por modelo: prefixo do modelo -> ajustes. O primeiro que casar vence.
    PERFIS = {}

    def __init__(self, equip, timeout=5):
        self.eq = equip
        self.timeout = timeout
        esquema = "https" if equip.get("https") else "http"
        porta = equip.get("porta_http") or (443 if esquema == "https" else 80)
        padrao = (esquema == "http" and porta == 80) or (esquema == "https" and porta == 443)
        self.base = f"{esquema}://{equip['ip']}" + ("" if padrao else f":{porta}")
        self.sessao = requests.Session()
        self.sessao.verify = False  # equipamentos usam certificado autoassinado
        if equip.get("usuario"):
            self.sessao.auth = HTTPDigestAuth(equip["usuario"], equip.get("senha") or "")
        self.perfil = {}

    # ------------------------------------------------------------ HTTP
    def get(self, caminho, **kw):
        try:
            r = self.sessao.get(self.base + caminho, timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise ErroDriver(f"sem resposta HTTP: {exc.__class__.__name__}") from exc
        if r.status_code == 401:
            raise ErroDriver("usuário ou senha recusados (401)")
        if r.status_code >= 400:
            raise ErroDriver(f"HTTP {r.status_code} em {caminho}")
        return r

    def aplicar_perfil(self, modelo):
        for prefixo, ajustes in self.PERFIS.items():
            if modelo and modelo.upper().startswith(prefixo.upper()):
                self.perfil = ajustes
                return

    def portas(self):
        cadastradas = [self.eq.get("porta_http") or 80] + list(self.eq.get("portas") or [])
        return list(dict.fromkeys(cadastradas + self.portas_padrao))

    # ------------------------------------------------------------ contrato
    @classmethod
    def reconhece(cls, equip):
        """True se o equipamento fala este protocolo (usado no modo auto)."""
        return False

    def coletar(self):
        """Coleta lenta (a cada poucos minutos). Retorna dict com chaves opcionais:
        modelo, firmware, serial, mac e `detalhes` (canais, hdds, ...)."""
        return {}

    def coleta_rapida(self):
        """Coleta feita a cada ciclo, junto com o ping (ex.: tráfego dos links).
        Retorna None quando o driver não tem nada rápido para ler."""
        return None

    def reiniciar(self):
        raise ErroDriver("reinício remoto não suportado por este driver")


# ---------------------------------------------------------------- utilidades
def xml_raiz(texto):
    """Faz o parse ignorando namespaces (a ISAPI usa vários)."""
    raiz = ET.fromstring(texto)
    for el in raiz.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return raiz


def xml_texto(el, caminho, padrao=None):
    achado = el.find(caminho)
    return achado.text.strip() if achado is not None and achado.text else padrao


def chave_valor(texto):
    """Parse da resposta CGI Dahua: linhas `chave=valor`."""
    dados = {}
    for linha in texto.splitlines():
        if "=" in linha:
            k, v = linha.split("=", 1)
            dados[k.strip()] = v.strip()
    return dados


def agrupar_indices(dados, prefixo):
    """`states[0].channel=1` -> {0: {"channel": "1"}}."""
    padrao = re.compile(re.escape(prefixo) + r"\[(\d+)\]\.(.+)")
    grupos = {}
    for k, v in dados.items():
        m = padrao.match(k)
        if m:
            grupos.setdefault(int(m.group(1)), {})[m.group(2)] = v
    return dict(sorted(grupos.items()))
