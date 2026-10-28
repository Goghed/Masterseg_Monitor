"""Geração de links simulados para os clientes DEMO (usado por popular_demo e simular_demo)."""
import random

PROVEDORES = [("Vivo Fibra", "Vivo", 500, 250), ("Claro", "Claro", 300, 100),
              ("Algar Fibra", "Algar", 400, 200), ("Starlink", "Starlink", 200, 20)]

# Situação fixa de alguns clientes, para a demonstração mostrar os casos interessantes.
CENARIOS = {
    "DEMO-04": {"link1": "sem_internet"},   # failover ativo: operando pelo reserva
    "DEMO-11": {"uso1": 0.86},              # link principal perto do limite
    "DEMO-02": {"uso1": 0.95},              # link saturado
}


def cadastrar_links(gw, indice):
    from .models import LinkWAN
    dois = gw.cliente.portaria_remota or indice % 3 == 0
    p1 = PROVEDORES[indice % 3]
    LinkWAN.objects.create(equipamento=gw, interface="ether1", nome=p1[0], provedor=p1[1],
                           download_mbps=p1[2], upload_mbps=p1[3], contato_operadora="0800 000 0000")
    if dois:
        p2 = PROVEDORES[(indice + 1) % 4] if indice % 2 else PROVEDORES[3]
        LinkWAN.objects.create(equipamento=gw, interface="pppoe-out1", nome=p2[0], provedor=p2[1],
                               download_mbps=p2[2], upload_mbps=p2[3])


def links_agora(gw, ruido=True):
    """Monta a lista de links como o driver MikroTik reportaria."""
    cen = CENARIOS.get(gw.cliente.chave, {})
    cad = list(gw.links.all().order_by("interface"))
    saida = []
    l1_ok = cen.get("link1", "online") == "online"
    for i, c in enumerate(cad):
        primeiro = c.interface == "ether1"
        status = cen.get("link1", "online") if primeiro else "online"
        principal = (primeiro and l1_ok) or (not primeiro and not l1_ok)
        if principal:
            uso = cen.get("uso1", random.uniform(0.15, 0.45))
        else:
            uso = random.uniform(0.002, 0.01)
        if ruido:
            uso *= random.uniform(0.92, 1.08)
        ok = status not in ("offline", "sem_internet")
        rx = int(c.download_mbps * 1e6 * uso) if ok else 0
        tx = int(c.upload_mbps * 1e6 * uso * random.uniform(0.3, 0.6)) if ok else 0
        saida.append({
            "interface": c.interface, "nome": c.nome, "tipo": "DHCP" if primeiro else "PPPoE",
            "ip": f"177.{40 + i}.{gw.pk % 200}.{10 + i}", "gateway": f"177.{40 + i}.{gw.pk % 200}.1",
            "distancia": 1 if primeiro else 2, "rota_ativa": ok, "principal": principal,
            "running": status != "offline", "quedas_porta": random.randint(0, 6) if not ruido else 3,
            "rx_bps": rx, "tx_bps": tx,
            "taxa_porta_mbps": 1000.0 if primeiro else None, "full_duplex": True,
            "gateway_ping": {"alvo": "gw", "perda_pct": 0 if ok else 100,
                             "latencia_ms": round(random.uniform(1.5, 4) if primeiro else random.uniform(18, 35), 1) if ok else None},
            "pppoe_uptime": "" if primeiro else "6d 3h",
            "status": status,
        })
    return saida
