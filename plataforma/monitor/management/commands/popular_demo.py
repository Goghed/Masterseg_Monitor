"""Cria clientes de demonstração (chave DEMO-xx) com histórico de 30 dias.

    python manage.py popular_demo           # cria
    python manage.py popular_demo --apagar  # remove só os clientes DEMO-
"""
import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from monitor.demo import cadastrar_links, links_agora
from monitor.models import Agente, Cliente, Equipamento, Evento, Metrica, MetricaLink, Status

CLIENTES = [
    ("Condomínio Residencial Vila Verde", "Itu", True),
    ("Edifício Monte Carlo", "Londrina", True),
    ("Supermercado Bom Preço", "Salto", False),
    ("Condomínio Parque das Árvores", "Itu", True),
    ("Clínica São Lucas", "Londrina", False),
    ("Escola Pequeno Saber", "Itu", False),
    ("Condomínio Alphaville Itu", "Itu", True),
    ("Transportadora Rota Sul", "Londrina", False),
    ("Residencial Jardim Europa", "Salto", True),
    ("Posto Avenida", "Itu", False),
    ("Edifício Solar das Palmeiras", "Londrina", True),
    ("Indústria Metalúrgica Tietê", "Salto", False),
]

D = Evento.Decisao
JUSTIFICATIVAS = {
    D.CHAMAR_TECNICO: "Gravador sem resposta e sem queda geral no local; não há ação remota possível.",
    D.AGUARDAR: "Câmera isolada caiu uma vez; acompanhar antes de deslocar alguém.",
    D.AVISAR_CLIENTE: "Agente e todos os equipamentos sumiram juntos: provável falta de energia ou internet do cliente.",
    D.ACAO_REMOTA: "Equipamento responde ping mas o serviço de vídeo travou; reinício remoto deve resolver.",
}


class Command(BaseCommand):
    help = "Popula a plataforma com dados de demonstração."

    def add_arguments(self, parser):
        parser.add_argument("--apagar", action="store_true")

    @transaction.atomic
    def handle(self, *args, **opts):
        Cliente.objects.filter(chave__startswith="DEMO-").delete()
        if opts["apagar"]:
            self.stdout.write("Clientes de demonstração removidos.")
            return
        random.seed(7)
        agora = timezone.now()
        for i, (nome, cidade, portaria) in enumerate(CLIENTES, start=1):
            self._criar_cliente(i, nome, cidade, portaria, agora)
        self.stdout.write(self.style.SUCCESS(f"{len(CLIENTES)} clientes de demonstração criados."))

    def _criar_cliente(self, i, nome, cidade, portaria, agora):
        cli = Cliente.objects.create(
            nome=nome, chave=f"DEMO-{i:02d}", cidade=cidade, portaria_remota=portaria,
            endereco=f"Rua Exemplo, {100 + i * 7}", contato_nome="Síndico", contato_telefone="(11) 0000-0000",
        )
        agente_ok = i != 6
        agente = Agente.objects.create(
            cliente=cli, hostname=f"rpi-{cli.chave.lower()}", versao="1.0.0", ip_local=f"192.168.{i}.250",
            ultimo_contato=agora - (timedelta(seconds=12) if agente_ok else timedelta(minutes=47)),
            marcado_offline=not agente_ok,
        )
        E = Equipamento.Categoria
        eqs = []

        def novo(nome, cat, fab, modelo, ip, **kw):
            e = Equipamento.objects.create(
                cliente=cli, agente=agente, nome=nome, categoria=cat, fabricante=fab,
                modelo=modelo, ip=ip, status=Status.ONLINE, status_desde=agora - timedelta(days=3),
                ultimo_check=agora - timedelta(seconds=10), latencia_ms=random.uniform(1, 12),
                perda_pct=0, firmware=kw.pop("firmware", ""), serial=f"SN{random.randint(10**8, 10**9)}",
                **kw,
            )
            eqs.append(e)
            return e

        net = f"192.168.{i}"
        gw = novo("MikroTik borda", E.GATEWAY, "mikrotik", "RB750Gr3", f"{net}.1",
                  firmware="RouterOS 7.16.2", critico=True,
                  detalhes={"uptime": "18d 4h", "cpu_pct": random.randint(4, 30), "memoria_pct": 34,
                            "temperatura_c": random.randint(38, 52),
                            "internet": {"alvo": "8.8.8.8", "perda_pct": 0, "latencia_ms": round(random.uniform(9, 22), 1)}})
        cadastrar_links(gw, i)
        gw.detalhes["links"] = links_agora(gw)
        gw.save()
        novo("Servidor de monitoramento", E.SERVIDOR, "outro", "Mini PC N100", f"{net}.10",
             detalhes={"so": "Windows 11 Pro", "cpu_pct": 23, "disco_livre_pct": 61})
        sw = novo("Switch PoE rack", E.SWITCH, "hikvision", "DS-3E1326P-EI", f"{net}.2",
                  depende_de=gw, firmware="V1.2.0",
                  detalhes={"portas": 24, "portas_ativas": 17, "poe_watts": 142})
        n_canais = random.choice([8, 16, 16, 32])
        canais = [{"id": c, "nome": f"CAM {c:02d}", "ip": f"{net}.{100 + c}", "online": True}
                  for c in range(1, n_canais + 1)]
        nvr = novo(f"NVR {n_canais} canais", E.GRAVADOR, "hikvision",
                   f"DS-76{n_canais:02d}NI-K2", f"{net}.20", depende_de=sw, firmware="V4.62.010",
                   detalhes={"canais": canais, "hdds": [{"id": 1, "status": "ok", "capacidade_gb": 4000,
                                                          "livre_gb": random.randint(10, 900)}]})
        if random.random() < 0.5:
            novo("DVR portaria", E.GRAVADOR, "intelbras", "MHDX 1108", f"{net}.21", depende_de=sw,
                 firmware="4.0.1", detalhes={"canais": [{"id": c, "nome": f"Analógica {c}", "online": True}
                                                         for c in range(1, 9)],
                                              "hdds": [{"id": 1, "status": "ok", "capacidade_gb": 2000}]})
        for c in range(1, min(n_canais, 10) + 1):
            fab = random.choice(["hikvision", "hikvision", "intelbras", "dahua"])
            modelo = {"hikvision": "DS-2CD1043G2-I", "intelbras": "VIP 3230 B", "dahua": "IPC-HFW1230S"}[fab]
            novo(f"CAM {c:02d} " + random.choice(["Portaria", "Garagem", "Hall", "Piscina", "Muro",
                                                    "Elevador", "Entrada", "Playground"]),
                 E.CAMERA, fab, modelo, f"{net}.{100 + c}", depende_de=sw)
        novo("Central de alarme", E.ALARME, random.choice(["intelbras", "hikvision"]),
             random.choice(["AMT 4010 Smart", "AX PRO DS-PWA96"]), f"{net}.30",
             detalhes={"particoes": 2, "zonas": 16, "armado": random.choice([True, False])})
        if portaria:
            novo("Controlador de acesso pedestre", E.ACESSO, "intelbras", "SS 3540 MF", f"{net}.40")
            novo("Porteiro SIP", E.INTERFONE, "intelbras", "XPE 1001 IP", f"{net}.41",
                 detalhes={"sip_registrado": True, "ramal": "2001"})

        # Estado atual com algumas falhas
        if not agente_ok:
            Equipamento.objects.filter(cliente=cli).update(ultimo_check=agora - timedelta(minutes=47))
        elif i in (2, 9):
            cam = next(e for e in eqs if e.categoria == E.CAMERA)
            cam.status, cam.status_desde, cam.latencia_ms = Status.OFFLINE, agora - timedelta(minutes=18), None
            cam.save()
            nvr.detalhes["canais"][0]["online"] = False
            nvr.save()
        elif i == 4:
            sw.status, sw.status_desde, sw.latencia_ms, sw.perda_pct = Status.DEGRADADO, agora - timedelta(minutes=6), 412, 28
            sw.save()
        elif i == 7:
            nvr.status, nvr.status_desde, nvr.latencia_ms = Status.OFFLINE, agora - timedelta(minutes=4), None
            nvr.save()

        # Métricas da última hora (sparklines)
        metricas = []
        for e in Equipamento.objects.filter(cliente=cli):
            base = e.latencia_ms or random.uniform(2, 10)
            for m in range(60):
                ts = agora - timedelta(minutes=60 - m)
                offline_agora = e.status == Status.OFFLINE and e.status_desde and ts >= e.status_desde
                metricas.append(Metrica(
                    equipamento=e, ts=ts, online=not offline_agora,
                    latencia_ms=None if offline_agora else max(0.5, random.gauss(base, base * 0.25)),
                    perda_pct=0,
                ))
        Metrica.objects.bulk_create(metricas)
        hist = []
        for m in range(0, 60, 1):
            ts = agora - timedelta(minutes=60 - m)
            for l in links_agora(gw):
                hist.append(MetricaLink(equipamento=gw, interface=l["interface"], ts=ts, status=l["status"],
                                        principal=l["principal"], rx_bps=l["rx_bps"], tx_bps=l["tx_bps"],
                                        latencia_ms=l["gateway_ping"]["latencia_ms"], perda_pct=0))
        MetricaLink.objects.bulk_create(hist)

        # Histórico de eventos (30 dias)
        T = Evento.Tipo
        opcoes = [
            (T.EQUIP_OFFLINE, "aviso", D.AGUARDAR, E.CAMERA, 9),
            (T.CANAL_OFFLINE, "aviso", D.AGUARDAR, E.GRAVADOR, 6),
            (T.EQUIP_OFFLINE, "critico", D.CHAMAR_TECNICO, E.GRAVADOR, 2),
            (T.HDD_ERRO, "critico", D.CHAMAR_TECNICO, E.GRAVADOR, 1),
            (T.DEGRADADO, "aviso", D.AGUARDAR, E.SWITCH, 3),
            (T.AGENTE_OFFLINE, "critico", D.AVISAR_CLIENTE, None, 2),
            (T.VIDEO_PERDIDO, "aviso", D.ACAO_REMOTA, E.GRAVADOR, 2),
            (T.EQUIP_OFFLINE, "critico", D.CHAMAR_TECNICO, E.GATEWAY, 1),
        ]
        pesos = [o[4] for o in opcoes]
        for _ in range(random.randint(6, 26)):
            tipo, sev, dec, cat, _p = random.choices(opcoes, weights=pesos)[0]
            quando = agora - timedelta(days=random.uniform(0.2, 29.5))
            eq = next((e for e in eqs if e.categoria == cat), None) if cat else None
            alvo = eq.nome if eq else "agente"
            Evento.objects.create(
                cliente=cli, equipamento=eq, tipo=tipo, severidade=sev,
                titulo=f"{T(tipo).label}: {alvo}", criado_em=quando,
                resolvido_em=quando + timedelta(minutes=random.randint(3, 240)),
                decisao=dec, prioridade={"critico": 2, "aviso": 4}[sev],
                justificativa=JUSTIFICATIVAS.get(dec, ""), analisado_por=random.choice(["ia", "regra"]),
                analisado_em=quando + timedelta(seconds=4),
            )

        # Eventos abertos coerentes com o estado atual
        for e in Equipamento.objects.filter(cliente=cli).exclude(status=Status.ONLINE):
            if e.status == Status.OFFLINE:
                critico = e.categoria in (E.GRAVADOR, E.GATEWAY)
                Evento.objects.create(
                    cliente=cli, equipamento=e, tipo=T.EQUIP_OFFLINE,
                    severidade="critico" if critico else "aviso",
                    titulo=f"{e.nome} ({e.get_categoria_display().lower()}) parou de responder",
                    detalhe=f"IP {e.ip}.", criado_em=e.status_desde,
                    decisao=D.CHAMAR_TECNICO if critico else D.AGUARDAR, prioridade=2 if critico else 4,
                    justificativa=JUSTIFICATIVAS[D.CHAMAR_TECNICO if critico else D.AGUARDAR],
                    analisado_por="ia", analisado_em=e.status_desde + timedelta(seconds=5),
                )
            elif e.status == Status.DEGRADADO:
                Evento.objects.create(
                    cliente=cli, equipamento=e, tipo=T.DEGRADADO, severidade="aviso",
                    titulo=f"{e.nome} com conexão degradada", criado_em=e.status_desde,
                    detalhe="Latência 412 ms, perda 28%.", decisao=D.AGUARDAR, prioridade=3,
                    justificativa="Degradação começou há poucos minutos; câmeras atrás do switch seguem gravando.",
                    analisado_por="ia", analisado_em=e.status_desde + timedelta(seconds=5),
                )
        fora = [l for l in gw.detalhes["links"] if l["status"] == "sem_internet"]
        if fora:
            reserva = next(l for l in gw.detalhes["links"] if l["principal"])
            Evento.objects.create(
                cliente=cli, equipamento=gw, tipo=T.LINK_OFFLINE, severidade="aviso", referencia="link:ether1",
                titulo=f"Link {fora[0]['nome']} caiu; operando pelo {reserva['nome']}",
                detalhe=f"{fora[0]['nome']} (ether1): a porta está ligada, mas sem internet. Suporte da operadora: 0800 000 0000.",
                criado_em=agora - timedelta(minutes=26), decisao=D.ACIONAR_OPERADORA, prioridade=3,
                justificativa="Problema no link do provedor: abrir chamado na operadora. O local segue no link reserva.",
                analisado_por="ia", analisado_em=agora - timedelta(minutes=26),
            )
        if not agente_ok:
            Evento.objects.create(
                cliente=cli, tipo=T.AGENTE_OFFLINE, severidade="critico", origem="plataforma",
                titulo=f"Agente de {cli.nome} sem contato", criado_em=agora - timedelta(minutes=45),
                detalhe="Último contato há 47 min.", decisao=D.AVISAR_CLIENTE, prioridade=2,
                justificativa=JUSTIFICATIVAS[D.AVISAR_CLIENTE], analisado_por="ia",
                analisado_em=agora - timedelta(minutes=45),
            )
