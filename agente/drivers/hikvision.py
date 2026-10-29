"""Hikvision ISAPI (NVR, DVR, câmeras IP, switches gerenciáveis, AX PRO).

Endpoints usados (autenticação Digest, respostas XML):
  GET /ISAPI/System/deviceInfo                          identificação
  GET /ISAPI/System/status                              CPU, memória, uptime
  GET /ISAPI/ContentMgmt/InputProxy/channels            nomes dos canais IP (NVR)
  GET /ISAPI/ContentMgmt/InputProxy/channels/status     canal online/offline (NVR)
  GET /ISAPI/ContentMgmt/Storage/hdd                    HDs
  PUT /ISAPI/System/reboot                              reinício remoto
Centrais de alarme (AX PRO, AX Hybrid PRO), a cada ciclo:
  GET /ISAPI/SecurityCP/status/subSystems               partições armadas/desarmadas, disparo
  GET /ISAPI/SecurityCP/status/zones                    estado de cada zona
  GET /ISAPI/SecurityCP/Configuration/zones             nomes das zonas (se o status não trouxer)
Endpoints que o modelo não tem devolvem 404/403 e são simplesmente pulados.
"""
import json
import time

from .base import Driver, ErroDriver, xml_raiz, xml_texto

HDD_OK = {"ok", "idle", "sleep", "normal", "active"}

# Estado da partição na ISAPI -> como aparece na plataforma.
ARMACAO = {"disarm": "desarmada", "away": "armada", "stay": "armada_parcial", "vacation": "armada",
           "arm": "armada", "instant": "armada_parcial"}
ZONA_FORA = {"offline", "breakdown", "heartbeatabnormal", "fault"}
NOMES_ZONAS = {}  # ip -> (lido_em, {id: nome}); os nomes quase nunca mudam
NOMES_VALIDADE_S = 600


def _sim(valor):
    return valor is True or str(valor).strip().lower() in ("true", "1", "yes")


def _itens(dados, chave):
    """Todos os dicts sob `chave` em qualquer nível (o formato muda entre modelos)."""
    if isinstance(dados, dict):
        for k, v in dados.items():
            if k == chave and isinstance(v, dict):
                yield v
            else:
                yield from _itens(v, chave)
    elif isinstance(dados, list):
        for v in dados:
            yield from _itens(v, chave)


class HikvisionISAPI(Driver):
    nome = "hikvision_isapi"
    portas_padrao = [80, 554, 8000]

    PERFIS = {
        # Câmeras não têm canais IP nem HD: evita 2 requisições inúteis por coleta.
        "DS-2CD": {"canais": False, "hdd": False},
        "DS-2DE": {"canais": False, "hdd": False},
        "DS-2SE": {"canais": False, "hdd": False},  # speed dome
        # Switches gerenciáveis: só identificação e status.
        "DS-3E": {"canais": False, "hdd": False},
        # NVRs e DVRs: tudo.
        "DS-76": {"canais": True, "hdd": True},
        "DS-77": {"canais": True, "hdd": True},
        "DS-96": {"canais": True, "hdd": True},
        "iDS-": {"canais": True, "hdd": True},
        # Centrais de alarme: não têm HD.
        "DS-PHA": {"hdd": False},
        "DS-PWA": {"canais": False, "hdd": False},
    }

    @classmethod
    def reconhece(cls, equip):
        try:
            r = cls(equip, timeout=4).get("/ISAPI/System/deviceInfo")
            return "<DeviceInfo" in r.text
        except ErroDriver:
            return False

    def coletar(self):
        info = xml_raiz(self.get("/ISAPI/System/deviceInfo").text)
        modelo = xml_texto(info, "model", "")
        self.aplicar_perfil(modelo)
        firmware = " ".join(filter(None, [xml_texto(info, "firmwareVersion"),
                                          xml_texto(info, "firmwareReleasedDate")]))
        res = {
            "modelo": modelo,
            "firmware": firmware,
            "serial": xml_texto(info, "serialNumber", ""),
            "mac": xml_texto(info, "macAddress", ""),
            "detalhes": {"tipo_dispositivo": xml_texto(info, "deviceType", "")},
        }
        det = res["detalhes"]

        try:
            st = xml_raiz(self.get("/ISAPI/System/status").text)
            cpu = st.find(".//CPU/cpuUtilization")
            if cpu is not None and cpu.text:
                det["cpu_pct"] = int(float(cpu.text))
            mem_uso, mem_total = st.find(".//Memory/memoryUsage"), st.find(".//Memory/memoryAvailable")
            if mem_uso is not None and mem_total is not None:
                uso, livre = float(mem_uso.text), float(mem_total.text)
                det["memoria_pct"] = round(100 * uso / (uso + livre)) if uso + livre else None
            up = xml_texto(st, "deviceUpTime")
            if up and up.isdigit():
                s = int(up)
                det["uptime"] = f"{s // 86400}d {s % 86400 // 3600}h"
        except ErroDriver:
            pass

        if self.perfil.get("canais", True):
            canais = self._canais()
            if canais:
                det["canais"] = canais
        if self.perfil.get("hdd", True):
            hdds = self._hdds()
            if hdds:
                det["hdds"] = hdds
        return res

    # ------------------------------------------------------------ central de alarme
    def coleta_rapida(self):
        if self.eq.get("categoria") != "alarme":
            return None
        alarme = self._alarme()
        return {"detalhes": {"alarme": alarme}} if alarme else None

    def _lista_isapi(self, caminho, item):
        """Lista da ISAPI em JSON (AX PRO) ou XML (modelos que só falam XML)."""
        try:
            return list(_itens(json.loads(self.get(caminho + "?format=json").text), item))
        except (ErroDriver, ValueError):
            pass
        raiz = xml_raiz(self.get(caminho).text)
        return [{filho.tag: (filho.text or "").strip() for filho in el} for el in raiz.iter(item)]

    def _nomes_zonas(self):
        lido_em, nomes = NOMES_ZONAS.get(self.eq["ip"], (0, {}))
        if time.monotonic() - lido_em > NOMES_VALIDADE_S:
            try:
                nomes = {str(z.get("id")): z.get("zoneName") or z.get("name") or ""
                         for z in self._lista_isapi("/ISAPI/SecurityCP/Configuration/zones", "Zone")}
            except ErroDriver:
                nomes = {}
            NOMES_ZONAS[self.eq["ip"]] = (time.monotonic(), nomes)
        return nomes

    def _alarme(self):
        try:
            particoes_isapi = self._lista_isapi("/ISAPI/SecurityCP/status/subSystems", "SubSys")
        except ErroDriver:
            return None  # não é uma central (ou não tem a API de segurança)
        particoes = []
        for p in particoes_isapi:
            if "enabled" in p and not _sim(p["enabled"]):
                continue
            arm = str(p.get("arming") or p.get("armingStatus") or "").strip()
            particoes.append({
                "id": str(p.get("id")),
                "nome": p.get("name") or p.get("subSystemName") or f"Partição {p.get('id')}",
                "estado": ARMACAO.get(arm.lower(), arm.lower() or "desconhecido"),
                "disparando": _sim(p.get("alarm")),
            })

        zonas = []
        try:
            zonas_isapi = self._lista_isapi("/ISAPI/SecurityCP/status/zones", "Zone")
        except ErroDriver:
            zonas_isapi = []
        nomes = None
        for z in zonas_isapi:
            status = str(z.get("status") or "").strip().lower()
            if status == "notrelated" or ("enabled" in z and not _sim(z["enabled"])):
                continue  # zona sem sensor cadastrado
            nome = z.get("name") or z.get("zoneName")
            if not nome:
                nomes = self._nomes_zonas() if nomes is None else nomes
                nome = nomes.get(str(z.get("id")), "")
            if _sim(z.get("alarm")):
                estado = "disparada"
            elif _sim(z.get("tamperEvident")):
                estado = "tamper"
            elif status in ZONA_FORA:
                estado = "falha"
            elif _sim(z.get("bypassed")):
                estado = "inibida"
            elif status == "trigger" or _sim(z.get("magnetOpenStatus")):
                estado = "aberta"
            else:
                estado = "normal"
            zid = str(z.get("id"))
            zonas.append({
                "id": zid,
                # A ISAPI numera a partir de 0; no teclado a zona é id + 1.
                "numero": int(zid) + 1 if zid.isdigit() else zid,
                "nome": nome or "",
                "particao": str(z.get("subSystemNo") or z.get("subSystem") or ""),
                "estado": estado,
                "bateria_fraca": _sim(z.get("charge") == "lowPower" or z.get("lowPower")),
            })
        return {"particoes": particoes, "zonas": zonas}

    def _canais(self):
        try:
            status = xml_raiz(self.get("/ISAPI/ContentMgmt/InputProxy/channels/status").text)
        except ErroDriver:
            return []
        nomes = {}
        try:
            lista = xml_raiz(self.get("/ISAPI/ContentMgmt/InputProxy/channels").text)
            for c in lista.iter("InputProxyChannel"):
                nomes[xml_texto(c, "id")] = xml_texto(c, "name", "")
        except ErroDriver:
            pass
        canais = []
        for c in status.iter("InputProxyChannelStatus"):
            cid = xml_texto(c, "id")
            canais.append({
                "id": cid,
                "nome": nomes.get(cid, ""),
                "ip": xml_texto(c, ".//ipAddress", ""),
                "online": (xml_texto(c, "online", "false").lower() == "true"),
                "detalhe": xml_texto(c, "chanDetectResult", ""),
            })
        return canais

    def _hdds(self):
        try:
            raiz = xml_raiz(self.get("/ISAPI/ContentMgmt/Storage/hdd").text)
        except ErroDriver:
            return []
        hdds = []
        for h in raiz.iter("hdd"):
            status = (xml_texto(h, "status", "") or "").lower()
            cap_mb = xml_texto(h, "capacity")
            livre_mb = xml_texto(h, "freeSpace")
            hdds.append({
                "id": xml_texto(h, "id"),
                "status": "ok" if status in HDD_OK else (status or "desconhecido"),
                "capacidade_gb": round(int(cap_mb) / 1024) if cap_mb and cap_mb.isdigit() else None,
                "livre_gb": round(int(livre_mb) / 1024) if livre_mb and livre_mb.isdigit() else None,
            })
        return hdds

    def reiniciar(self):
        try:
            r = self.sessao.put(self.base + "/ISAPI/System/reboot", timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            raise ErroDriver(str(exc)) from exc
        if r.status_code >= 400:
            raise ErroDriver(f"HTTP {r.status_code} ao reiniciar")
        return True
