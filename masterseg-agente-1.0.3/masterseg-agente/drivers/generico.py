"""Genérico: só ping e portas. Usado para gateway, servidor e qualquer coisa
sem API conhecida. Para MikroTik e servidores Windows, o próximo passo é um
driver SNMP ou a API do RouterOS."""
from .base import Driver


class Generico(Driver):
    nome = "generico"
    portas_padrao = []

    def coletar(self):
        return {}
