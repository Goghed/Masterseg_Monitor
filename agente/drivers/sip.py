"""Centrais telefônicas IP (Intelbras CIP 850 e qualquer PABX SIP).

Não há API pública de status: o teste é um SIP OPTIONS na porta 5060 (UDP),
o mesmo "ping" que telefones e operadoras usam. Qualquer resposta SIP, mesmo
401/403/404, mostra que o serviço de telefonia está de pé; silêncio, que caiu
(a página web pode continuar no ar com o SIP parado).
"""
import re
import socket
import time
import uuid

from .base import Driver, ErroDriver

PORTA_SIP = 5060


def sip_options(host, porta=PORTA_SIP, timeout_s=2.0, tentativas=2):
    """Devolve (código, latência_ms, user_agent) ou None sem resposta."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout_s)
        s.connect((host, porta))
        local_ip, local_porta = s.getsockname()
        callid = uuid.uuid4().hex
        msg = (f"OPTIONS sip:{host}:{porta} SIP/2.0\r\n"
               f"Via: SIP/2.0/UDP {local_ip}:{local_porta};branch=z9hG4bK{uuid.uuid4().hex[:12]};rport\r\n"
               "Max-Forwards: 70\r\n"
               f"From: <sip:masterseg@{local_ip}>;tag={uuid.uuid4().hex[:8]}\r\n"
               f"To: <sip:{host}:{porta}>\r\n"
               f"Call-ID: {callid}@{local_ip}\r\n"
               "CSeq: 1 OPTIONS\r\n"
               f"Contact: <sip:masterseg@{local_ip}:{local_porta}>\r\n"
               "Accept: application/sdp\r\n"
               "User-Agent: MasterSegMonitor\r\n"
               "Content-Length: 0\r\n\r\n").encode()
        for _ in range(tentativas):
            inicio = time.perf_counter()
            try:
                s.send(msg)
                resp = s.recv(4096).decode("utf-8", "ignore")
            except OSError:  # tempo esgotado ou porta fechada (ICMP unreachable)
                continue
            m = re.match(r"SIP/2\.0\s+(\d{3})", resp)
            if not m:
                continue
            ua = re.search(r"^(?:User-Agent|Server):\s*(.+)$", resp, re.I | re.M)
            return int(m.group(1)), (time.perf_counter() - inicio) * 1000, (ua.group(1).strip() if ua else "")
    return None


class PabxSIP(Driver):
    nome = "pabx_sip"
    portas_padrao = [80]

    @classmethod
    def reconhece(cls, equip):
        return sip_options(equip["ip"], timeout_s=1.5, tentativas=1) is not None

    def coleta_rapida(self):
        porta = next((int(p) for p in self.eq.get("portas") or [] if 5060 <= int(p) <= 5099), PORTA_SIP)
        resp = sip_options(self.eq["ip"], porta)
        if resp is None:
            raise ErroDriver(f"a central não respondeu ao SIP na porta {porta}: telefonia parada")
        codigo, ms, ua = resp
        return {"detalhes": {"sip": {"responde": True, "porta": porta, "codigo": codigo,
                                     "latencia_ms": round(ms, 1), "servidor": ua}}}
