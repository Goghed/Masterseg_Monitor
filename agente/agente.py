#!/usr/bin/env python3
"""MasterSeg Agente — testa os equipamentos da rede do cliente e reporta à plataforma.

Uso:
  python agente.py                         roda continuamente (serviço)
  python agente.py --uma-vez               um ciclo, mostra o resultado e sai
  python agente.py --testar 192.168.1.20 --usuario admin --senha X [--driver hikvision_isapi]
                                           testa um equipamento sem a plataforma

A conexão é sempre de dentro para fora (HTTPS): não abre porta no cliente.
"""
import argparse
import configparser
import json
import logging
import os
import platform
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drivers import DRIVERS, escolher_driver  # noqa: E402
from drivers.base import ErroDriver, SenhaRecusada  # noqa: E402
from verificacoes import alcancavel  # noqa: E402

VERSAO = "1.0.9"
log = logging.getLogger("agente")


class Agente:
    def __init__(self, cfg):
        self.url = cfg.get("plataforma", "url").rstrip("/")
        self.token = cfg.get("plataforma", "token")
        self.verificar_tls = cfg.getboolean("plataforma", "verificar_tls", fallback=True)
        self.threads = cfg.getint("agente", "threads", fallback=16)
        self.coleta_lenta_s = cfg.getint("agente", "coleta_detalhes_segundos", fallback=300)
        self.recarregar_config_s = cfg.getint("agente", "recarregar_config_segundos", fallback=300)
        # Depois de uma senha recusada, espera isto antes de tentar a MESMA senha de novo.
        # Câmeras Intelbras/Dahua/Hikvision bloqueiam o usuário após poucas tentativas erradas.
        self.espera_senha_s = cfg.getint("agente", "espera_senha_recusada_segundos", fallback=1800)

        self.http = requests.Session()
        self.http.headers.update({
            "Authorization": f"Token {self.token}",
            "User-Agent": f"MasterSegAgente/{VERSAO}",
        })
        self.http.verify = self.verificar_tls

        self.intervalo = 30
        self.falhas_para_offline = 3
        self.modo_unico = False
        self.equipamentos = []
        self.config_em = 0
        # Controle remoto (comandos chegam na resposta do relatório).
        self.pausado = False
        self.reiniciar_pedido = False
        self.iniciado_em = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Estado por equipamento: debounce, driver escolhido e última coleta lenta.
        self.estado = {}
        self.parar = threading.Event()
        self.ultimo_envio_ok = False

    # ------------------------------------------------------------ plataforma
    def carregar_config(self):
        r = self.http.get(f"{self.url}/api/agente/config/", timeout=15)
        if r.status_code == 401:
            raise SystemExit("Token recusado pela plataforma. Confira o agente.ini.")
        r.raise_for_status()
        dados = r.json()
        # Pausa sobrevive a um reinício: a plataforma lembra o último estado informado.
        self.pausado = bool(dados["agente"].get("pausado", self.pausado))
        self.intervalo = max(10, dados["agente"]["intervalo_segundos"])
        self.falhas_para_offline = 1 if self.modo_unico else max(1, dados["agente"]["falhas_para_offline"])
        novos = {e["id"]: e for e in dados["equipamentos"]}
        # Se IP, credencial ou driver mudou, refaz a detecção do driver e coleta já no
        # próximo ciclo (inclusive se estava esperando por causa de senha recusada).
        for eid, eq in novos.items():
            antigo = next((e for e in self.equipamentos if e["id"] == eid), None)
            if antigo and antigo != eq and eid in self.estado:
                self.estado[eid].pop("driver", None)
                self.estado[eid]["coleta_em"] = 0
        self.equipamentos = list(novos.values())
        self.config_em = time.monotonic()
        log.info("Configuração carregada: %s, %d equipamentos, intervalo %ss",
                 dados["agente"]["cliente"], len(self.equipamentos), self.intervalo)

    def enviar(self, resultados):
        payload = {
            "hostname": socket.gethostname(), "versao": VERSAO, "ip_local": ip_local(),
            "sistema": f"{platform.system()} {platform.release()}", "resultados": resultados,
            "pausado": self.pausado, "iniciado_em": self.iniciado_em,
        }
        r = self.http.post(f"{self.url}/api/agente/relatorio/", json=payload, timeout=20)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------ checagem
    def _driver(self, eq):
        st = self.estado.setdefault(eq["id"], {})
        if "driver" not in st:
            st["driver"] = escolher_driver(eq)
            log.info("%s (%s): driver %s", eq["nome"], eq["ip"], st["driver"].nome)
        return st["driver"](eq)

    def checar(self, eq):
        st = self.estado.setdefault(eq["id"], {"falhas": 0, "confirmado": None, "coleta_em": 0})
        st.setdefault("falhas", 0)
        st.setdefault("confirmado", None)
        st.setdefault("coleta_em", 0)
        driver = self._driver(eq)
        teste = alcancavel(eq["ip"], driver.portas())

        # Debounce: só declara offline depois de N falhas seguidas.
        if teste["online"]:
            st["falhas"] = 0
            st["confirmado"] = True
        else:
            st["falhas"] += 1
            if st["falhas"] >= self.falhas_para_offline:
                st["confirmado"] = False
        if st["confirmado"] is None:
            return None  # ainda não dá para afirmar nada sobre este equipamento
        if not teste["online"] and st["confirmado"]:
            return None  # falhou, mas ainda dentro da tolerância: não muda nada na plataforma

        resultado = {
            "id": eq["id"], "online": st["confirmado"],
            "latencia_ms": teste["latencia_ms"] if teste["online"] else None,
            "perda_pct": teste["perda_pct"],
        }
        # Responde na rede mas a porta do serviço (web/API) não abre: serviço travado.
        porta_servico = str(eq.get("porta_http") or 80)
        if (teste["online"] and driver.nome not in ("generico", "mikrotik")
                and teste["portas"].get(porta_servico) is False):
            resultado["servico_ok"] = False

        # Coleta rápida (a cada ciclo): ex. links e tráfego do MikroTik.
        if teste["online"]:
            try:
                rapida = driver.coleta_rapida()
                if rapida:
                    resultado.update({k: v for k, v in rapida.items() if v and k != "detalhes"})
                    resultado["detalhes"] = {**rapida.get("detalhes", {}), "erro_coleta": None}
            except ErroDriver as exc:
                log.warning("%s (%s): leitura rápida falhou: %s", eq["nome"], eq["ip"], exc)
                resultado["detalhes"] = {"erro_coleta": str(exc)}
                if driver.nome in ("mikrotik", "pabx_sip"):
                    resultado["servico_ok"] = False  # responde na rede, mas a API (ou o SIP) não
                if driver.nome == "pabx_sip":
                    resultado["detalhes"]["sip"] = None  # não deixa o card mostrar o "respondendo" antigo
            except Exception:  # noqa: BLE001
                log.exception("%s: erro inesperado na leitura rápida", eq["nome"])

        # Coleta lenta (identificação, canais, HDs) só com o equipamento respondendo.
        if teste["online"] and time.monotonic() - st["coleta_em"] >= self.coleta_lenta_s:
            st["coleta_em"] = time.monotonic()
            try:
                dados = driver.coletar()
                resultado.update({k: v for k, v in dados.items() if v and k != "detalhes"})
                if dados.get("detalhes") is not None:
                    resultado["detalhes"] = {**resultado.get("detalhes", {}), **dados["detalhes"],
                                             "portas_testadas": teste["portas"]}
            except SenhaRecusada as exc:
                # Adia a próxima tentativa para não manter o usuário bloqueado no equipamento.
                st["coleta_em"] = time.monotonic() + self.espera_senha_s - self.coleta_lenta_s
                minutos = self.espera_senha_s // 60
                log.warning("%s (%s): %s; nova tentativa em %d min ou quando a senha mudar na plataforma",
                            eq["nome"], eq["ip"], exc, minutos)
                resultado["detalhes"] = {**resultado.get("detalhes", {}),
                                         "erro_coleta": f"{exc}. Nova tentativa em {minutos} min ou ao alterar "
                                                        "a senha no cadastro",
                                         "portas_testadas": teste["portas"]}
            except ErroDriver as exc:
                log.warning("%s (%s): coleta falhou: %s", eq["nome"], eq["ip"], exc)
                resultado["detalhes"] = {**resultado.get("detalhes", {}), "erro_coleta": str(exc),
                                         "portas_testadas": teste["portas"]}
            except Exception:  # noqa: BLE001 — um equipamento nunca derruba o ciclo
                log.exception("%s: erro inesperado na coleta", eq["nome"])
        return resultado

    def ciclo(self):
        if not self.equipamentos or time.monotonic() - self.config_em > self.recarregar_config_s:
            try:
                self.carregar_config()
            except requests.RequestException as exc:
                log.warning("Não consegui baixar a configuração (%s); usando a anterior", exc)
        # Pausado ou sem equipamentos, o relatório vai vazio: serve de sinal de vida
        # e é por ele que chegam os comandos (inclusive o "retomar").
        resultados = []
        if self.equipamentos and not self.pausado:
            with ThreadPoolExecutor(max_workers=self.threads) as pool:
                resultados = [r for r in pool.map(self._checar_seguro, self.equipamentos) if r]
        try:
            resp = self.enviar(resultados)
            self.ultimo_envio_ok = True
            if self.pausado:
                log.info("Pausado pela plataforma: nenhum equipamento testado")
            else:
                on = sum(1 for r in resultados if r["online"])
                log.info("Relatório enviado: %d/%d online (%s processados)",
                         on, len(resultados), resp.get("processados"))
            if not self.modo_unico:
                self.executar_comando(resp.get("comando"))
        except requests.RequestException as exc:
            # Status é estado, não histórico: no próximo ciclo vai o estado atual.
            self.ultimo_envio_ok = False
            log.warning("Falha ao enviar relatório (%s); tento de novo no próximo ciclo", exc)
        return resultados

    def executar_comando(self, comando):
        if not comando:
            return
        log.warning("Comando recebido da plataforma: %s", comando)
        if comando == "pausar":
            self.pausado = True
        elif comando == "retomar":
            self.pausado = False
        elif comando == "recarregar":
            self.config_em = float("-inf")  # recarrega no próximo ciclo
            for st in self.estado.values():
                st["coleta_em"] = 0  # e lê os detalhes de novo
        elif comando == "reiniciar":
            self.reiniciar_pedido = True
            self.parar.set()
        else:
            log.warning("Comando desconhecido ignorado: %s", comando)

    def _checar_seguro(self, eq):
        try:
            return self.checar(eq)
        except Exception:  # noqa: BLE001
            log.exception("Erro ao checar %s", eq.get("nome"))
            return None

    def rodar(self):
        log.info("MasterSeg Agente %s iniciando, plataforma %s", VERSAO, self.url)
        while not self.parar.is_set():
            inicio = time.monotonic()
            self.ciclo()
            espera = max(1, self.intervalo - (time.monotonic() - inicio))
            self.parar.wait(espera)
        log.info("Agente encerrado")


def ip_local():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
    except OSError:
        return None


def testar_equipamento(args):
    """Modo de bancada: testa um equipamento e imprime tudo o que o driver leu."""
    eq = {"id": 0, "nome": args.testar, "ip": args.testar, "porta_http": args.porta,
          "https": args.https, "usuario": args.usuario, "senha": args.senha,
          "driver": args.driver, "fabricante": args.fabricante, "categoria": args.categoria, "portas": [],
          "links": []}
    classe = escolher_driver(eq)
    print(f"Driver: {classe.nome}")
    driver = classe(eq)
    print("Rede:", json.dumps(alcancavel(eq["ip"], driver.portas()), ensure_ascii=False))
    for etapa, funcao in (("Coleta lenta", driver.coletar), ("Coleta rápida", driver.coleta_rapida)):
        try:
            dados = funcao()
            if dados:
                print(f"{etapa}:")
                print(json.dumps(dados, indent=2, ensure_ascii=False))
        except ErroDriver as exc:
            print(f"{etapa} falhou: {exc}")


def pasta_do_agente():
    """Pasta onde estão o agente.ini e o log. No .exe (PyInstaller) é a pasta do
    executável, e não a pasta temporária onde ele se descompacta."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main():
    p = argparse.ArgumentParser(description="MasterSeg Agente")
    p.add_argument("--config", default=os.path.join(pasta_do_agente(), "agente.ini"))
    p.add_argument("--supervisionado", action="store_true",
                   help="roda sob um supervisor (tarefa agendada, NSSM, systemd): no reinício pedido "
                        "pela plataforma, sai e deixa o supervisor subir de novo")
    p.add_argument("--log", metavar="ARQUIVO", help="grava o log também neste arquivo (rotativo, 5 MB x 3)")
    p.add_argument("--uma-vez", action="store_true", help="executa um ciclo e sai")
    p.add_argument("--testar", metavar="IP", help="testa um equipamento sem a plataforma")
    p.add_argument("--driver", default="auto", choices=["auto", *DRIVERS])
    p.add_argument("--fabricante", default="")
    p.add_argument("--categoria", default="gravador", help="ex.: alarme, para ler partições e zonas")
    p.add_argument("--usuario", default="")
    p.add_argument("--senha", default="")
    p.add_argument("--porta", type=int, default=80)
    p.add_argument("--https", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    if args.supervisionado:
        os.environ["MASTERSEG_SUPERVISOR"] = "1"

    saidas = []
    if sys.stdout is not None:  # no pythonw / .exe sem console não há stdout
        saidas.append(logging.StreamHandler())
    if args.log:
        from logging.handlers import RotatingFileHandler
        saidas.append(RotatingFileHandler(args.log, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", handlers=saidas or None)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    if args.testar:
        return testar_equipamento(args)

    cfg = configparser.ConfigParser()
    cfg.BOOLEAN_STATES = {**cfg.BOOLEAN_STATES, "sim": True, "nao": False, "não": False}
    # utf-8-sig: aceita o agente.ini salvo com BOM (Bloco de Notas, PowerShell).
    if not cfg.read(args.config, encoding="utf-8-sig"):
        raise SystemExit(f"Arquivo de configuração não encontrado: {args.config}")
    agente = Agente(cfg)

    if args.uma_vez:
        agente.modo_unico = True
        for r in agente.ciclo():
            nome = next(e["nome"] for e in agente.equipamentos if e["id"] == r["id"])
            situacao = "ONLINE " if r["online"] else "OFFLINE"
            print(f"{situacao} {nome:<32} {r.get('latencia_ms') or '':>8} ms  {r.get('modelo', '')}")
        if not agente.ultimo_envio_ok:
            print("Os testes rodaram, mas o relatório não chegou na plataforma.")
            sys.exit(1)
        print("Relatório entregue na plataforma.")
        if not agente.equipamentos:
            print("Atenção: este cliente ainda não tem equipamentos cadastrados. "
                  "Cadastre na plataforma e o agente começa a testar sozinho.")
        return

    signal.signal(signal.SIGTERM, lambda *_: agente.parar.set())
    try:
        agente.rodar()
    except KeyboardInterrupt:
        agente.parar.set()
    if agente.reiniciar_pedido:
        reiniciar_processo()


def reiniciar_processo():
    """Reinício pedido pela plataforma. Com supervisor (systemd, NSSM), basta sair
    com erro que ele sobe de novo; rodando à mão, o agente se relança sozinho."""
    if os.environ.get("MASTERSEG_SUPERVISOR"):
        log.info("Saindo para o supervisor reiniciar o agente")
        sys.exit(75)
    log.info("Reiniciando o agente")
    if os.name == "nt":
        # os.execv no Windows não protege caminhos com espaço; o Popen sim.
        subprocess.Popen([sys.executable, *sys.argv])
        sys.exit(0)
    os.execv(sys.executable, [sys.executable, *sys.argv])


if __name__ == "__main__":
    main()
