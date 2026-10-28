"""Mostra a chave que cifra as senhas dos equipamentos neste ambiente.

Use ao levar o banco para outro servidor: coloque o valor em MONITOR_CHAVE_CRIPTO
no .env do servidor novo, senão as senhas importadas não abrem."""
import base64
import hashlib

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Mostra o valor para MONITOR_CHAVE_CRIPTO."

    def handle(self, *args, **opts):
        chave = settings.MONITOR.get("CHAVE_CRIPTO")
        if not chave:
            bruto = hashlib.sha256((settings.SECRET_KEY + ":monitor-senhas").encode()).digest()
            chave = base64.urlsafe_b64encode(bruto).decode()
        self.stdout.write(f"MONITOR_CHAVE_CRIPTO={chave}")
