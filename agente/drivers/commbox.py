"""Commbox MIO (MIO-100, MIO-400, MIO-800): módulos de automação IP com
entradas digitais e saídas a relé.

A Commbox não publica o protocolo HTTP do módulo. Por enquanto o driver confere
se a página web do módulo responde e lê o título dela; a leitura das entradas
entra aqui quando houver o manual de integração (ou uma resposta real do módulo).
Para ver o que o módulo devolve: `python agente.py --testar IP --driver commbox_mio`.
"""
import re

from .base import Driver, ErroDriver


class CommboxMIO(Driver):
    nome = "commbox_mio"
    portas_padrao = [80]

    @classmethod
    def reconhece(cls, equip):
        try:
            return "commbox" in cls(equip, timeout=4).get("/").text.lower()
        except ErroDriver:
            return False

    def _pagina(self):
        r = self.get("/")
        titulo = re.search(r"<title[^>]*>\s*(.*?)\s*</title>", r.text, re.I | re.S)
        return {"web": "respondendo", "titulo_web": titulo.group(1)[:80] if titulo else ""}

    def coletar(self):
        det = self._pagina()
        modelo = re.search(r"MIO[\s-]?\d{3}", det["titulo_web"], re.I)
        return {"modelo": modelo.group(0).upper().replace(" ", "-") if modelo else "", "detalhes": det}
