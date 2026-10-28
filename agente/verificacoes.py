"""Testes de rede básicos: ping (ICMP) e conexão TCP. Funciona em Linux e Windows."""
import platform
import re
import socket
import subprocess
import time

WINDOWS = platform.system().lower() == "windows"


def ping(host, tentativas=3, timeout_s=1.0):
    """Retorna (perda_pct, latencia_media_ms | None). None,None se o ping não existir."""
    if WINDOWS:
        cmd = ["ping", "-n", str(tentativas), "-w", str(int(timeout_s * 1000)), host]
    else:
        cmd = ["ping", "-n", "-q", "-c", str(tentativas), "-W", str(max(1, int(timeout_s))), "-i", "0.3", host]
    try:
        saida = subprocess.run(
            cmd, capture_output=True, text=True, timeout=tentativas * (timeout_s + 1) + 2,
            encoding="utf-8", errors="ignore",
            creationflags=0x08000000 if WINDOWS else 0,  # sem janela no Windows
        ).stdout
    except (FileNotFoundError, PermissionError):
        return None, None
    except subprocess.TimeoutExpired:
        return 100.0, None

    perda = re.search(r"(\d+(?:[.,]\d+)?)%", saida)
    perda = float(perda.group(1).replace(",", ".")) if perda else 100.0
    if WINDOWS:
        # Saída localizada: "Média = 3ms" / "Average = 3ms" é o último "= Nms".
        medias = re.findall(r"=\s*(\d+)\s*ms", saida)
        lat = float(medias[-1]) if medias and perda < 100 else None
    else:
        m = re.search(r"=\s*[\d.]+/([\d.]+)/", saida)
        lat = float(m.group(1)) if m else None
    return perda, lat


def tcp(host, porta, timeout_s=2.0):
    """Retorna o tempo de conexão em ms, ou None se a porta não respondeu."""
    inicio = time.perf_counter()
    try:
        with socket.create_connection((host, porta), timeout=timeout_s):
            return (time.perf_counter() - inicio) * 1000
    except OSError:
        return None


def alcancavel(host, portas, tentativas_ping=3):
    """Teste rápido de cada ciclo.

    Online = respondeu ping OU qualquer porta TCP abriu (muitos equipamentos
    bloqueiam ICMP). A latência vem do ping; sem ping, do tempo de conexão TCP.
    """
    perda, lat = ping(host, tentativas_ping)
    portas_ok = {}
    for porta in portas:
        portas_ok[porta] = tcp(host, porta)
        if portas_ok[porta] is not None and perda not in (None, 100.0):
            break  # já sabemos que está vivo, não precisa testar todas
    alguma_porta = any(v is not None for v in portas_ok.values())
    online = (perda is not None and perda < 100) or alguma_porta
    if lat is None and alguma_porta:
        lat = min(v for v in portas_ok.values() if v is not None)
    if perda is None:  # sem ping disponível: usa só TCP
        perda = 0.0 if alguma_porta else 100.0
    return {
        "online": online,
        "latencia_ms": round(lat, 2) if lat is not None and online else None,
        "perda_pct": perda,
        "portas": {str(p): v is not None for p, v in portas_ok.items()},
    }
