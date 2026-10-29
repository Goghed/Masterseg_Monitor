"""MikroTik RouterOS: links WAN, failover, tráfego e saúde do roteador.

Dois caminhos de acesso, escolhidos sozinhos:
  - REST API (RouterOS 7.1+): https://IP/rest/...  autenticação Basic.
    Precisa do serviço www-ssl (HTTPS) ou www (HTTP, a partir do 7.9).
  - API clássica (RouterOS 6), porta 8728, via biblioteca `librouteros`.

O que é lido:
  /system/resource, /system/health, /system/routerboard   CPU, memória, temperatura, versão
  /interface                                               running, link-downs, comentário
  /ip/route  (dst 0.0.0.0/0)                               quais são os links, qual está ativo
  /ip/address, /ip/dhcp-client, /interface/pppoe-client    IP e gateway de cada link
  /interface/monitor-traffic once                          tráfego agora (bps)
  /interface/ethernet/monitor once                         velocidade negociada da porta
  /ping (gateway do link, pela interface do link)          latência e perda da última milha
  /ping (alvo pela tabela de rotas do link, se cadastrada) internet de verdade por link
  /tool/netwatch                                           hosts que vocês já vigiam no roteador

Crie no MikroTik um usuário só leitura para o agente:
  /user group add name=monitor policy=read,api,rest-api,test,winbox
  /user add name=monitor group=monitor password=...
(`test` é a política que libera o ping.)
"""
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.auth import HTTPBasicAuth

from .base import Driver, ErroDriver

try:
    import librouteros  # só necessário para RouterOS 6
except ImportError:  # pragma: no cover
    librouteros = None

# Lembra, por equipamento, qual acesso funcionou (REST ou API).
_ACESSO = {}
_TRAVA = threading.Lock()


# ---------------------------------------------------------------- conversões
def duracao_ms(texto):
    """'453us' -> 0.453, '12ms345us' -> 12.345, '1s20ms' -> 1020."""
    if not texto:
        return None
    total, achou = 0.0, False
    for valor, unidade in re.findall(r"([\d.]+)(us|ms|s|m|h|d|w)", str(texto)):
        achou = True
        total += float(valor) * {"us": 0.001, "ms": 1, "s": 1000, "m": 60000,
                                 "h": 3600000, "d": 86400000, "w": 604800000}[unidade]
    return round(total, 3) if achou else None


def duracao_legivel(texto):
    """Uptime do RouterOS ('2w3d04:05:06' no v6, '2w3d4h5m6s' no v7) -> '17d 4h'."""
    if not texto:
        return ""
    texto = str(texto)
    s = 0
    for valor, unidade in re.findall(r"(\d+)([wdhms])", texto):
        s += int(valor) * {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}[unidade]
    relogio = re.search(r"(\d+):(\d+):(\d+)$", texto)
    if relogio:
        h, m, seg = map(int, relogio.groups())
        s += h * 3600 + m * 60 + seg
    return f"{s // 86400}d {s % 86400 // 3600}h" if s >= 86400 else f"{s // 3600}h {s % 3600 // 60}min"


def taxa_mbps(texto):
    """'1Gbps' -> 1000, '100Mbps' -> 100, '2.5Gbps' -> 2500."""
    m = re.match(r"([\d.]+)\s*([GM])bps", str(texto or ""))
    if not m:
        return None
    return float(m.group(1)) * (1000 if m.group(2) == "G" else 1)


def verdadeiro(v):
    return str(v).lower() in ("true", "yes")


def inteiro(v, padrao=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return padrao


# ---------------------------------------------------------------- acessos
class _Rest:
    def __init__(self, base, usuario, senha, timeout):
        self.base = base + "/rest"
        self.s = requests.Session()
        self.s.verify = False
        self.s.auth = HTTPBasicAuth(usuario, senha)
        self.timeout = timeout

    def _tratar(self, r, caminho):
        if r.status_code == 401:
            raise SenhaRecusada("usuário ou senha recusados pelo MikroTik (401)")
        if r.status_code >= 400:
            try:
                msg = r.json().get("detail") or r.json().get("message")
            except ValueError:
                msg = r.text[:120]
            raise ErroDriver(f"REST {caminho}: {msg}")
        dados = r.json()
        return dados if isinstance(dados, list) else [dados]

    def listar(self, caminho):
        try:
            r = self.s.get(f"{self.base}/{caminho}", timeout=self.timeout)
        except requests.RequestException as exc:
            raise ErroDriver(f"REST indisponível: {exc.__class__.__name__}") from exc
        return self._tratar(r, caminho)

    def comando(self, caminho, **params):
        try:
            r = self.s.post(f"{self.base}/{caminho}", json={k.replace("_", "-"): str(v) for k, v in params.items()},
                            timeout=self.timeout + 8)
        except requests.RequestException as exc:
            raise ErroDriver(f"REST indisponível: {exc.__class__.__name__}") from exc
        return self._tratar(r, caminho)


class _Api:
    """API clássica (RouterOS 6). Normaliza os valores como a REST devolve: tudo string."""

    def __init__(self, host, porta, usuario, senha, timeout):
        if librouteros is None:
            raise ErroDriver("RouterOS sem REST e biblioteca librouteros não instalada "
                             "(pip install librouteros)")
        try:
            self.api = librouteros.connect(host=host, port=porta, username=usuario,
                                           password=senha, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            raise ErroDriver(f"API 8728: {exc}") from exc

    @staticmethod
    def _norm(linhas):
        saida = []
        for linha in linhas:
            saida.append({k.lstrip("."): ("true" if v is True else "false" if v is False else str(v))
                          for k, v in linha.items()})
        return saida

    def listar(self, caminho):
        try:
            return self._norm(self.api.path(*caminho.split("/")))
        except Exception as exc:  # noqa: BLE001
            raise ErroDriver(f"API {caminho}: {exc}") from exc

    def comando(self, caminho, **params):
        args = {k.replace("_", "-"): (True if v == "" else v) for k, v in params.items()}
        try:
            return self._norm(self.api(f"/{caminho}", **args))
        except Exception as exc:  # noqa: BLE001
            raise ErroDriver(f"API {caminho}: {exc}") from exc

    def fechar(self):
        try:
            self.api.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- driver
class MikroTik(Driver):
    nome = "mikrotik"
    portas_padrao = [8291]  # Winbox: responde mesmo com www desligado
    PERFIS = {}

    def __init__(self, equip, timeout=6):
        super().__init__(equip, timeout)
        self._acesso = None
        self.links_cadastrados = {l["interface"]: l for l in equip.get("links") or []}

    @classmethod
    def reconhece(cls, equip):
        return equip.get("fabricante") == "mikrotik" and bool(equip.get("usuario"))

    def portas(self):
        cadastradas = [self.eq.get("porta_http") or 443] + list(self.eq.get("portas") or [])
        return list(dict.fromkeys(cadastradas + self.portas_padrao))

    # ------------------------------------------------------------ acesso
    def acesso(self):
        if self._acesso:
            return self._acesso
        eid = self.eq.get("id")
        usuario, senha = self.eq.get("usuario", ""), self.eq.get("senha") or ""
        preferido = _ACESSO.get(eid)
        tentativas = ["rest", "api"] if preferido != "api" else ["api", "rest"]
        erros = []
        for tipo in tentativas:
            try:
                if tipo == "rest":
                    a = _Rest(self.base, usuario, senha, self.timeout)
                    a.listar("system/resource")
                else:
                    a = _Api(self.eq["ip"], self.eq.get("porta_api") or 8728, usuario, senha, self.timeout)
                with _TRAVA:
                    _ACESSO[eid] = tipo
                self._acesso = a
                return a
            except ErroDriver as exc:
                erros.append(str(exc))
                if "401" in str(exc):
                    break  # senha errada: não adianta tentar o outro caminho
        raise ErroDriver("; ".join(erros))

    # ------------------------------------------------------------ coletas
    def coletar(self):
        """Coleta lenta: identificação do equipamento."""
        a = self.acesso()
        res = {"detalhes": {}}
        try:
            rb = a.listar("system/routerboard")[0]
            res["modelo"] = rb.get("model", "")
            res["serial"] = rb.get("serial-number", "")
            res["detalhes"]["firmware_placa"] = rb.get("current-firmware", "")
        except (ErroDriver, IndexError):
            pass  # CHR / x86 não tem routerboard
        return res

    def coleta_rapida(self):
        """A cada ciclo: saúde do roteador e estado de cada link."""
        a = self.acesso()
        rec = a.listar("system/resource")[0]
        total, livre = inteiro(rec.get("total-memory")), inteiro(rec.get("free-memory"))
        det = {
            "versao_routeros": rec.get("version", ""),
            "cpu_pct": inteiro(rec.get("cpu-load")),
            "memoria_pct": round(100 * (total - livre) / total) if total and livre is not None else None,
            "uptime": duracao_legivel(rec.get("uptime")),
            "placa": rec.get("board-name", ""),
        }
        det.update(self._saude(a))
        det["links"] = self._links(a)
        det["internet"] = self._ping(a, self._alvo_padrao())
        det["netwatch"] = self._netwatch(a)
        res = {"firmware": f"RouterOS {det['versao_routeros']}".strip(), "detalhes": det}
        if isinstance(a, _Api):
            a.fechar()
        return res

    def _alvo_padrao(self):
        return next((l.get("alvo_ping") for l in self.links_cadastrados.values() if l.get("alvo_ping")), "8.8.8.8")

    def _saude(self, a):
        try:
            linhas = a.listar("system/health")
        except ErroDriver:
            return {}
        saude = {}
        for l in linhas:
            if "name" in l:  # v7: uma linha por sensor
                if l["name"] in ("temperature", "cpu-temperature", "board-temperature1"):
                    saude.setdefault("temperatura_c", inteiro(l.get("value")))
                elif l["name"] == "voltage":
                    saude["tensao_v"] = l.get("value")
            else:  # v6: tudo numa linha só
                if l.get("temperature"):
                    saude["temperatura_c"] = inteiro(l["temperature"])
                if l.get("voltage"):
                    saude["tensao_v"] = l["voltage"]
        return saude

    def _netwatch(self, a):
        try:
            return [{"host": n.get("host"), "comentario": n.get("comment", ""),
                     "status": n.get("status", "")} for n in a.listar("tool/netwatch")
                    if not verdadeiro(n.get("disabled"))][:10]
        except ErroDriver:
            return []

    def _ping(self, a, alvo, **extra):
        if not alvo:
            return None
        try:
            linhas = a.comando("ping", address=alvo, count=3, **extra)
        except ErroDriver:
            return None
        resumo = next((l for l in reversed(linhas) if "packet-loss" in l), None)
        if resumo:
            return {"alvo": alvo, "perda_pct": inteiro(resumo.get("packet-loss"), 100),
                    "latencia_ms": duracao_ms(resumo.get("avg-rtt"))}
        tempos = [duracao_ms(l.get("time")) for l in linhas if l.get("time")]
        enviados = max(len(linhas), 1)
        return {"alvo": alvo, "perda_pct": round(100 * (1 - len(tempos) / enviados)),
                "latencia_ms": round(sum(tempos) / len(tempos), 2) if tempos else None}

    # ------------------------------------------------------------ links
    def _links(self, a):
        interfaces = {i["name"]: i for i in a.listar("interface")}
        rotas = [r for r in a.listar("ip/route") if r.get("dst-address") == "0.0.0.0/0"
                 and not verdadeiro(r.get("disabled"))]
        enderecos, remotos = {}, {}
        for e in a.listar("ip/address"):
            if not verdadeiro(e.get("disabled")):
                enderecos.setdefault(e.get("interface"), e.get("address", "").split("/")[0])
                if e.get("address", "").endswith("/32") and e.get("network"):
                    remotos[e.get("interface")] = e["network"]  # ponta remota do PPPoE
        dhcp = {}
        try:
            dhcp = {d["interface"]: d for d in a.listar("ip/dhcp-client")}
        except ErroDriver:
            pass
        pppoe = {}
        try:
            pppoe = {p["name"]: p for p in a.listar("interface/pppoe-client")}
        except ErroDriver:
            pass

        def interface_da_rota(r):
            ig = r.get("immediate-gw", "")
            if "%" in ig:
                return ig.split("%", 1)[1]
            gw = r.get("gateway", "").split(",")[0]
            if gw in interfaces:
                return gw
            m = re.search(r"via\s+(\S+)", r.get("gateway-status", ""))
            if m:
                return m.group(1)
            return next((n for n, d in dhcp.items() if d.get("gateway") == gw), None)

        rotas_por_if = {}
        for r in rotas:
            iface = interface_da_rota(r)
            if iface:
                rotas_por_if.setdefault(iface, []).append(r)

        # Quais interfaces são links: as cadastradas mandam; sem cadastro, detecta.
        if self.links_cadastrados:
            wans = list(self.links_cadastrados)
        else:
            wans = list(dict.fromkeys(list(rotas_por_if) + list(pppoe) + [
                i for i, d in dhcp.items() if d.get("add-default-route", "yes") != "no"]))

        # Link principal = rota padrão ativa com menor distância.
        ativas = [(inteiro(r.get("distance"), 1), interface_da_rota(r)) for r in rotas if verdadeiro(r.get("active"))]
        principal = min(ativas)[1] if ativas else None

        def um_link(iface):
            info = interfaces.get(iface, {})
            cad = self.links_cadastrados.get(iface, {})
            rota = sorted(rotas_por_if.get(iface, []), key=lambda r: inteiro(r.get("distance"), 1))
            rota = rota[0] if rota else {}
            d, p = dhcp.get(iface, {}), pppoe.get(iface)
            gateway = (d.get("gateway") or (rota.get("immediate-gw", "").split("%")[0])
                       or rota.get("gateway", "").split(",")[0])
            if gateway == iface or not gateway:
                gateway = remotos.get(iface, "")
            link = {
                "interface": iface,
                "nome": cad.get("nome") or info.get("comment") or iface,
                "tipo": "PPPoE" if p else "DHCP" if d else "Estático",
                "ip": enderecos.get(iface) or d.get("address", "").split("/")[0],
                "gateway": gateway,
                "distancia": inteiro(rota.get("distance")),
                "rota_ativa": verdadeiro(rota.get("active")),
                "principal": iface == principal,
                "running": verdadeiro(info.get("running")) and not verdadeiro(info.get("disabled")),
                "quedas_porta": inteiro(info.get("link-downs"), 0),
                "ultima_subida": info.get("last-link-up-time", ""),
            }
            if d:
                link["dhcp_status"] = d.get("status", "")
            if p:
                link["pppoe_uptime"] = duracao_legivel(p.get("uptime")) if p.get("uptime") else ""

            if link["running"]:
                try:
                    t = a.comando("interface/monitor-traffic", interface=iface, once="")[0]
                    link["rx_bps"] = inteiro(t.get("rx-bits-per-second"), 0)
                    link["tx_bps"] = inteiro(t.get("tx-bits-per-second"), 0)
                except (ErroDriver, IndexError):
                    pass
                fisica = iface if info.get("type") == "ether" else None
                if fisica:
                    try:
                        e = a.comando("interface/ethernet/monitor", numbers=fisica, once="")[0]
                        link["taxa_porta_mbps"] = taxa_mbps(e.get("rate"))
                        link["full_duplex"] = verdadeiro(e.get("full-duplex"))
                    except (ErroDriver, IndexError):
                        pass
                # Última milha: o gateway do provedor responde por esta interface?
                link["gateway_ping"] = self._ping(a, gateway, interface=iface) if gateway else None
                # Internet de verdade por este link, se houver tabela de rotas cadastrada.
                if cad.get("tabela_roteamento"):
                    link["internet_ping"] = self._ping(a, cad.get("alvo_ping") or "8.8.8.8",
                                                       routing_table=cad["tabela_roteamento"])

            link["status"] = self._status_link(link)
            return link

        # A conexão da API clássica não é thread-safe; a REST aguenta paralelo.
        with ThreadPoolExecutor(max_workers=4 if isinstance(a, _Rest) else 1) as pool:
            return list(pool.map(um_link, wans))

    @staticmethod
    def _status_link(l):
        if not l["running"]:
            return "offline"
        testes = [t for t in (l.get("internet_ping"), l.get("gateway_ping")) if t]
        if testes and testes[0]["perda_pct"] >= 100:
            return "sem_internet"
        if l["distancia"] is not None and not l["rota_ativa"]:
            return "sem_internet"  # check-gateway ou rota recursiva derrubou a rota
        if testes and (testes[0]["perda_pct"] >= 20 or (testes[0]["latencia_ms"] or 0) > 150):
            return "instavel"
        return "online"
