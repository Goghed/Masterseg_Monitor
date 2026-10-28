"""Junta eventos repetidos de um mesmo equipamento num incidente só.

Para bancos com eventos gerados antes do agrupamento automático (ex.: uma
câmera oscilando que abriu um evento a cada queda). Sem --aplicar, só mostra
o que faria.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from monitor.models import Evento
from monitor.servicos import GRAVIDADE_SEVERIDADE
from monitor.triagem import analisar

FALHAS = Evento.FALHAS_EQUIPAMENTO


class Command(BaseCommand):
    help = "Agrupa eventos repetidos de queda/serviço parado/degradado do mesmo equipamento."

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true", help="Grava as mudanças (padrão: só simula).")

    def handle(self, *args, aplicar=False, **opts):
        janela = timedelta(minutes=settings.MONITOR["JANELA_REABERTURA_MIN"])
        limite = settings.MONITOR["INTERMITENTE_FALHAS"]
        grupos = []
        eventos = (Evento.objects.filter(tipo__in=FALHAS, equipamento__isnull=False)
                   .select_related("equipamento").order_by("equipamento_id", "criado_em"))
        atual, fim = None, None
        for ev in eventos:
            mesmo = atual and ev.equipamento_id == atual[0].equipamento_id
            if mesmo and (fim is None or ev.criado_em <= fim + janela):
                atual.append(ev)
            else:
                atual = [ev]
                grupos.append(atual)
                fim = ev.resolvido_em
                continue
            # O grupo só termina quando um evento fecha e nada novo surge dentro da janela.
            fim = None if fim is None or ev.resolvido_em is None else max(fim, ev.resolvido_em)

        grupos = [g for g in grupos if len(g) > 1]
        if not grupos:
            self.stdout.write("Nada a consolidar.")
            return

        apagados = 0
        with transaction.atomic():
            for g in grupos:
                base, resto = g[0], g[1:]
                eq = base.equipamento
                ultimo = g[-1]
                tipo = min((e.tipo for e in g), key=FALHAS.index)
                sev = max((e.severidade for e in g), key=GRAVIDADE_SEVERIDADE.get)
                abertos = [e for e in g if e.resolvido_em is None]
                resolvido = None if abertos else max(e.resolvido_em for e in g)
                n = len(g)
                desde = timezone.localtime(base.criado_em).strftime("%d/%m %H:%M")
                self.stdout.write(f"{eq.nome}: {n} eventos -> 1 incidente "
                                  f"({'aberto' if resolvido is None else 'resolvido'}, desde {desde})")
                # "Voltou a responder" no meio de uma oscilação é ruído.
                voltas = Evento.objects.filter(equipamento=eq, tipo=Evento.Tipo.EQUIP_ONLINE,
                                               criado_em__gt=base.criado_em, criado_em__lt=ultimo.criado_em)
                apagados += len(resto) + voltas.count()
                if not aplicar:
                    continue

                base.tipo, base.severidade, base.ocorrencias = tipo, sev, n
                base.ultima_ocorrencia = ultimo.criado_em
                base.resolvido_em = resolvido
                base.titulo = (f"{eq.nome} intermitente: {n} falhas desde {desde}" if n >= limite
                               else ultimo.titulo)
                base.detalhe = ultimo.detalhe
                base.save()
                voltas.delete()
                Evento.objects.filter(pk__in=[e.pk for e in resto]).delete()
                if resolvido is None:
                    analisar(base)

        verbo = "Removidos" if aplicar else "Seriam removidos"
        self.stdout.write(self.style.SUCCESS(f"{verbo} {apagados} eventos repetidos em {len(grupos)} incidentes."))
        if not aplicar:
            self.stdout.write("Rode de novo com --aplicar para gravar.")
