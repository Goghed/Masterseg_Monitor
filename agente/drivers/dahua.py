"""Dahua HTTP API (CGI) — atende também Intelbras OEM Dahua (NVD, MHDX, VIP).

Endpoints usados (autenticação Digest, respostas `chave=valor`):
  /cgi-bin/magicBox.cgi?action=getDeviceType
  /cgi-bin/magicBox.cgi?action=getSystemInfo
  /cgi-bin/magicBox.cgi?action=getSoftwareVersion
  /cgi-bin/LogicDeviceManager.cgi?action=getCameraState&uniqueChannels[0]=-1   (NVR)
  /cgi-bin/storageDevice.cgi?action=getDeviceAllInfo                            (HD)
  /cgi-bin/configManager.cgi?action=getConfig&name=Network                      (MAC)
  /cgi-bin/magicBox.cgi?action=reboot
Leitores faciais / controle de acesso Intelbras (SS 3530, SS 3540, SS 5530...):
  /cgi-bin/accessControl.cgi?action=getDoorStatus&channel=1                     porta (a cada ciclo)
  /cgi-bin/recordFinder.cgi?action=getQuerySize&name=AccessUserInfo             usuários
  /cgi-bin/recordFinder.cgi?action=getQuerySize&name=AccessControlCard          cartões
O formato varia entre firmwares; tudo é lido de forma tolerante. Valide com
`python agente.py --testar IP --driver dahua_cgi` num equipamento real.
"""
import re

from .base import Driver, ErroDriver, agrupar_indices, chave_valor


class DahuaCGI(Driver):
    nome = "dahua_cgi"
    portas_padrao = [80, 554, 37777]

    PERFIS = {
        # Câmeras: sem canais remotos nem HD.
        "IPC-": {"canais": False, "hdd": False},
        "VIP": {"canais": False, "hdd": False},
        "VHD": {"canais": False, "hdd": False},
        # Gravadores
        "NVR": {"canais": True, "hdd": True},
        "NVD": {"canais": True, "hdd": True},
        "MHDX": {"canais": True, "hdd": True},
        "XVR": {"canais": True, "hdd": True},
        "DHI-": {"canais": True, "hdd": True},
        # Leitores faciais e controladoras Intelbras/Dahua.
        "SS": {"canais": False, "hdd": False},
        "ASI": {"canais": False, "hdd": False},
    }

    @classmethod
    def reconhece(cls, equip):
        try:
            r = cls(equip, timeout=4).get("/cgi-bin/magicBox.cgi?action=getDeviceType")
            return "type=" in r.text
        except ErroDriver:
            return False

    def _cgi(self, caminho):
        return chave_valor(self.get(caminho).text)

    # ------------------------------------------------------------ leitor facial / acesso
    def coleta_rapida(self):
        if self.eq.get("categoria") not in ("facial", "acesso"):
            return None
        portas = []
        for canal in (1, 2):
            try:
                st = self._cgi(f"/cgi-bin/accessControl.cgi?action=getDoorStatus&channel={canal}")
            except ErroDriver:
                break
            valor = (st.get("Info.status") or st.get("status") or "").lower()
            if not valor:
                break
            portas.append({"id": str(canal), "estado": {"close": "fechada", "open": "aberta"}.get(valor, valor)})
        return {"detalhes": {"acesso": {"portas": portas}}} if portas else None

    def _acesso_cadastros(self):
        res = {}
        for nome, chave in (("AccessUserInfo", "usuarios"), ("AccessControlCard", "cartoes")):
            try:
                n = self._cgi(f"/cgi-bin/recordFinder.cgi?action=getQuerySize&name={nome}").get("count")
            except ErroDriver:
                continue
            if n and n.isdigit():
                res[chave] = int(n)
        return res

    def coletar(self):
        tipo = self._cgi("/cgi-bin/magicBox.cgi?action=getDeviceType").get("type", "")
        info, versao = {}, {}
        try:
            info = self._cgi("/cgi-bin/magicBox.cgi?action=getSystemInfo")
            versao = self._cgi("/cgi-bin/magicBox.cgi?action=getSoftwareVersion")
        except ErroDriver:
            pass
        modelo = tipo or info.get("deviceType", "")
        self.aplicar_perfil(modelo)
        res = {
            "modelo": modelo,
            "firmware": versao.get("version", ""),
            "serial": info.get("serialNumber", ""),
            "detalhes": {"hardware": info.get("hardwareVersion", "")},
        }
        try:
            rede = self._cgi("/cgi-bin/configManager.cgi?action=getConfig&name=Network")
            res["mac"] = next((v for k, v in rede.items() if k.endswith("PhysicalAddress")), "")
        except ErroDriver:
            pass

        det = res["detalhes"]
        if self.perfil.get("canais", True):
            canais = self._canais()
            if canais:
                det["canais"] = canais
        if self.perfil.get("hdd", True):
            hdds = self._hdds()
            if hdds:
                det["hdds"] = hdds
        if self.eq.get("categoria") in ("facial", "acesso"):
            cadastros = self._acesso_cadastros()
            if cadastros:
                det["acesso_cadastros"] = cadastros
        return res

    def _canais(self):
        try:
            dados = self._cgi(
                "/cgi-bin/LogicDeviceManager.cgi?action=getCameraState&uniqueChannels[0]=-1")
        except ErroDriver:
            return []
        canais = []
        for _, c in agrupar_indices(dados, "states").items():
            if "channel" not in c:
                continue
            estado = c.get("connectionState", "")
            canais.append({
                "id": int(c["channel"]) + 1,  # API conta do 0; o painel do NVR, do 1
                "nome": "",
                "online": estado.lower() == "connected",
                "detalhe": estado,
            })
        return canais

    def _hdds(self):
        try:
            dados = self._cgi("/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo")
        except ErroDriver:
            return []
        hdds = []
        for i, d in agrupar_indices(dados, "list.info").items():
            total = d.get("Detail[0].TotalBytes")
            usado = d.get("Detail[0].UsedBytes")
            estado = d.get("State", "")
            ok = estado.lower() in ("success", "normal", "") and not re.search(r"error|fail", estado, re.I)
            gb = lambda v: round(float(v) / 1024 ** 3) if v else None  # noqa: E731
            hdds.append({
                "id": i + 1,
                "status": "ok" if ok else estado.lower(),
                "capacidade_gb": gb(total),
                "livre_gb": gb(float(total) - float(usado)) if total and usado else None,
            })
        return hdds

    def reiniciar(self):
        self.get("/cgi-bin/magicBox.cgi?action=reboot")
        return True
