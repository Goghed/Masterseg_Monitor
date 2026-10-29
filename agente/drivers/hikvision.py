"""Hikvision ISAPI (NVR, DVR, câmeras IP, switches gerenciáveis, AX PRO).

Endpoints usados (autenticação Digest, respostas XML):
  GET /ISAPI/System/deviceInfo                          identificação
  GET /ISAPI/System/status                              CPU, memória, uptime
  GET /ISAPI/ContentMgmt/InputProxy/channels            nomes dos canais IP (NVR)
  GET /ISAPI/ContentMgmt/InputProxy/channels/status     canal online/offline (NVR)
  GET /ISAPI/ContentMgmt/Storage/hdd                    HDs
  PUT /ISAPI/System/reboot                              reinício remoto
Endpoints que o modelo não tem devolvem 404/403 e são simplesmente pulados.
"""
from .base import Driver, ErroDriver, xml_raiz, xml_texto

HDD_OK = {"ok", "idle", "sleep", "normal", "active"}


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
