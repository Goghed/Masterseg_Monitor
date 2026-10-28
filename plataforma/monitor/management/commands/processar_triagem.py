"""Faz a revisão da IA dos eventos pendentes (modo "painel").

    python manage.py processar_triagem           # processa o que houver e sai
    python manage.py processar_triagem --loop    # fica rodando (tarefa "always-on")
"""
import time

from django.core.management.base import BaseCommand

from monitor.servicos import verificar_agentes
from monitor.triagem import processar_pendentes_ia


class Command(BaseCommand):
    help = "Revisa com a IA os eventos já triados pelas regras."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="não sai; verifica a cada 15 s")

    def handle(self, *args, **opts):
        while True:
            verificar_agentes()
            n = processar_pendentes_ia(limite=20)
            if n:
                self.stdout.write(f"{n} evento(s) revisado(s) pela IA")
            if not opts["loop"]:
                break
            time.sleep(15)
