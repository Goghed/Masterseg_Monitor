from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from monitor.models import Metrica, MetricaLink
from monitor.servicos import verificar_agentes


class Command(BaseCommand):
    help = "Marca agentes sem contato como offline e apaga métricas antigas. Rodar a cada minuto."

    def handle(self, *args, **opts):
        verificar_agentes()
        limite = timezone.now() - timedelta(days=settings.MONITOR["RETENCAO_METRICAS_DIAS"])
        apagadas, _ = Metrica.objects.filter(ts__lt=limite).delete()
        apagadas += MetricaLink.objects.filter(ts__lt=limite).delete()[0]
        if apagadas:
            self.stdout.write(f"{apagadas} métricas antigas removidas")
