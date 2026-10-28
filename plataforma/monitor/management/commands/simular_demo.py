"""Mantém os clientes DEMO- "vivos", gerando relatórios como um agente faria.

Passa pelo mesmo caminho do agente real (processar_relatorio), então quedas
simuladas geram eventos e passam pela triagem.

    python manage.py simular_demo            # roda até Ctrl+C
    python manage.py simular_demo --ciclos 1
"""
import random
import time

from django.core.management.base import BaseCommand

from monitor.demo import links_agora
from monitor.models import Agente, Status
from monitor.servicos import processar_relatorio


class Command(BaseCommand):
    help = "Simula agentes para os clientes de demonstração."

    def add_arguments(self, parser):
        parser.add_argument("--intervalo", type=int, default=15)
        parser.add_argument("--ciclos", type=int, default=0, help="0 = infinito")

    def handle(self, *args, **opts):
        ciclo = 0
        self.stdout.write("Simulando agentes DEMO (Ctrl+C para parar)...")
        while True:
            ciclo += 1
            for agente in Agente.objects.filter(cliente__chave__startswith="DEMO-").select_related("cliente"):
                if agente.cliente.chave == "DEMO-06":
                    continue  # este fica sem agente de propósito
                resultados = []
                for eq in agente.cliente.equipamentos.filter(ativo=True):
                    online = eq.status != Status.OFFLINE
                    if online and random.random() < 0.0004:
                        online = False
                    elif not online and random.random() < 0.06:
                        online = True
                    base = 400 if eq.status == Status.DEGRADADO else (eq.latencia_ms or random.uniform(2, 10))
                    if eq.status == Status.DEGRADADO and random.random() < 0.03:
                        base = 6
                    base = min(base, 400) if base > 40 else min(max(base, 1), 15)
                    extra = {}
                    if eq.categoria == "gateway" and eq.links.exists():
                        extra = {"detalhes": {"links": links_agora(eq)}}
                    resultados.append({
                        **extra,
                        "id": eq.pk, "online": online,
                        "latencia_ms": round(max(0.4, random.gauss(base, base * 0.2)), 1) if online else None,
                        "perda_pct": (28 if base > 300 else 0) if online else 100,
                    })
                processar_relatorio(agente, {
                    "hostname": agente.hostname, "versao": "1.0.0-sim", "resultados": resultados,
                })
            self.stdout.write(f"ciclo {ciclo} ok")
            if opts["ciclos"] and ciclo >= opts["ciclos"]:
                break
            time.sleep(opts["intervalo"])
