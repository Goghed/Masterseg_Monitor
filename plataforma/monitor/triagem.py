"""Triagem de eventos: regras primeiro, IA depois.

As regras resolvem o que é determinístico (queda em cascata, queda geral).
O que sobra vai para o modelo da Anthropic com o contexto do cliente, e a
resposta vem em JSON estruturado. Sem ANTHROPIC_API_KEY, tudo fica nas regras.

Por padrão roda em MODO SOMBRA: a decisão é registrada, mas ninguém é acionado
até TRIAGEM_ACIONAMENTO_AUTOMATICO=1.
"""
import json
import logging
import threading
from datetime import timedelta

import requests
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from .models import Equipamento, Evento, Status

log = logging.getLogger("monitor")
D = Evento.Decisao

# Decisão padrão por tipo, usada quando a IA não está configurada ou falha.
PADRAO_POR_TIPO = {
    Evento.Tipo.FALHA_GERAL: (D.CHAMAR_TECNICO, 1,
                              "Vários equipamentos caíram juntos com o agente ativo: rede interna, switch ou energia no local."),
    Evento.Tipo.HDD_ERRO: (D.CHAMAR_TECNICO, 2, "Falha de HD compromete a gravação e não se resolve remotamente."),
    Evento.Tipo.AGENTE_OFFLINE: (D.AVISAR_CLIENTE, 2,
                                 "Agente sem contato: provável queda de internet ou energia do cliente. Confirmar com o local antes de deslocar técnico."),
    Evento.Tipo.EQUIP_OFFLINE: (D.AGUARDAR, 3, "Equipamento isolado sem resposta; acompanhar antes de deslocar alguém."),
    Evento.Tipo.CANAL_OFFLINE: (D.AGUARDAR, 3, "Câmera sem sinal no gravador; acompanhar antes de deslocar alguém."),
    Evento.Tipo.SERVICO_PARADO: (D.AGUARDAR, 3,
                                 "Equipamento responde na rede, mas o serviço parou. Costuma voltar sozinho ou com reinício."),
    Evento.Tipo.DEGRADADO: (D.AGUARDAR, 4, "Conexão degradada, mas o equipamento segue respondendo."),
    Evento.Tipo.VIDEO_PERDIDO: (D.AGUARDAR, 3, "Perda de vídeo informada pelo gravador; acompanhar."),
    Evento.Tipo.CONFLITO_IP: (D.ACAO_REMOTA, 3, "Conflito de IP se resolve corrigindo o endereçamento remotamente."),
    Evento.Tipo.LINK_OFFLINE: (D.ACIONAR_OPERADORA, 3,
                               "Problema no link do provedor: abrir chamado na operadora. O local segue no link reserva."),
    Evento.Tipo.LINK_INSTAVEL: (D.AGUARDAR, 4, "Link instável, mas ainda passando tráfego; acompanhar."),
    Evento.Tipo.LINK_SATURADO: (D.AVISAR_CLIENTE, 4,
                                "Link no limite do contratado: verificar o que está consumindo ou propor upgrade."),
    Evento.Tipo.FAILOVER: (D.AGUARDAR, 4, "Failover funcionou e o local continua conectado; acompanhar o link principal."),
}
INFRA = {"gateway", "servidor", "gravador", "switch"}


def agendar_triagem(evento_id):
    """Dispara a triagem de um evento novo.

    MODO "thread" (padrão, servidor próprio): regras + IA numa thread, sem
    atrasar a resposta ao agente.
    MODO "painel" (PythonAnywhere e outros que não executam threads): as regras
    rodam na hora; a revisão da IA fica marcada como pendente e é feita aos
    poucos pelo painel aberto (api_dashboard / api_cliente) ou pelo comando
    `processar_triagem`.
    """
    if settings.TRIAGEM["MODO"] == "thread":
        threading.Thread(target=_executar, args=(evento_id,), daemon=True).start()
        return
    try:
        evento = Evento.objects.select_related("cliente", "equipamento").get(pk=evento_id)
        analisar(evento, usar_ia=False)
    except Exception:  # noqa: BLE001
        log.exception("Falha na triagem do evento %s", evento_id)


def processar_pendentes_ia(limite=1):
    """Revisa com a IA os eventos que as regras já analisaram. Chamado a cada
    atualização do painel (modo "painel") e pelo comando processar_triagem."""
    if not settings.TRIAGEM["ANTHROPIC_API_KEY"]:
        return 0
    feitos = 0
    for evento in (Evento.objects.select_related("cliente", "equipamento")
                   .filter(aguarda_ia=True).order_by("criado_em")[:limite]):
        # Marca antes de chamar: duas abas abertas não analisam o mesmo evento.
        if not Evento.objects.filter(pk=evento.pk, aguarda_ia=True).update(aguarda_ia=False):
            continue
        if evento.resolvido_em:
            continue  # já resolveu sozinho; a decisão das regras fica registrada
        try:
            analisar(evento)
            feitos += 1
        except Exception:  # noqa: BLE001
            log.exception("Falha na revisão da IA do evento %s", evento.pk)
    return feitos


def _executar(evento_id):
    close_old_connections()
    try:
        evento = Evento.objects.select_related("cliente", "equipamento").get(pk=evento_id)
        analisar(evento)
    except Exception:  # noqa: BLE001 — a triagem nunca pode derrubar a ingestão
        log.exception("Falha na triagem do evento %s", evento_id)
    finally:
        close_old_connections()


def analisar(evento: Evento, usar_ia=True):
    decisao = _regras_deterministicas(evento)
    deterministica = decisao is not None
    tem_ia = bool(settings.TRIAGEM["ANTHROPIC_API_KEY"])
    if decisao is None and tem_ia and usar_ia:
        decisao = _perguntar_ia(evento)
    if decisao is None:
        dec, prio, just = PADRAO_POR_TIPO.get(evento.tipo, (D.AGUARDAR, 4, "Sem regra específica; acompanhar."))
        eq = evento.equipamento
        if eq and evento.tipo == Evento.Tipo.EQUIP_OFFLINE and (eq.critico or eq.categoria in INFRA):
            dec, prio = D.CHAMAR_TECNICO, 2
            just = (f"{eq.get_categoria_display()} sem resposta e sem queda geral no local; "
                    "não há ação remota possível com o equipamento fora da rede.")
        if evento.tipo == Evento.Tipo.LINK_OFFLINE and evento.severidade == Evento.Severidade.CRITICO:
            prio = 1 if evento.cliente.portaria_remota else 2
            just = "Link caiu sem reserva ativo: abrir chamado na operadora imediatamente."
        if eq and dec == D.CHAMAR_TECNICO and evento.cliente.portaria_remota:
            prio = 1
            just += " Cliente com portaria remota: prioridade máxima."
        reincidencias = _reincidencias(evento)
        if reincidencias >= 3 and dec == D.AGUARDAR:
            dec, prio = D.CHAMAR_TECNICO, 2
            just = f"Equipamento caiu {reincidencias} vezes nas últimas 24h: instabilidade recorrente."
        decisao = (dec, prio, just, "regra")

    dec, prio, just, por = decisao
    evento.decisao = dec
    evento.prioridade = prio
    evento.justificativa = just[:2000]
    evento.analisado_por = por
    evento.analisado_em = timezone.now()
    # Só as regras rodaram, mas a IA está configurada: fica pendente de revisão.
    evento.aguarda_ia = (not usar_ia) and tem_ia and not deterministica
    evento.save(update_fields=["decisao", "prioridade", "justificativa",
                               "analisado_por", "analisado_em", "aguarda_ia"])

    # Com revisão da IA pendente, o acionamento espera a decisão final dela.
    if dec == D.CHAMAR_TECNICO and settings.TRIAGEM["ACIONAMENTO_AUTOMATICO"] and not evento.aguarda_ia:
        acionar_tecnico(evento)
    return evento


def _reincidencias(evento):
    if not evento.equipamento_id:
        return 0
    return Evento.objects.filter(
        equipamento_id=evento.equipamento_id, tipo=evento.tipo,
        criado_em__gte=timezone.now() - timedelta(hours=24),
    ).count()


def _regras_deterministicas(evento):
    eq = evento.equipamento
    if evento.tipo == Evento.Tipo.EQUIP_OFFLINE and eq:
        pai = eq.depende_de
        if pai and pai.status == Status.OFFLINE:
            return (D.AGUARDAR, 5,
                    f"Consequência da queda de {pai.nome}, que já está sendo tratada.", "regra")
        geral = Evento.objects.filter(
            cliente=evento.cliente, tipo=Evento.Tipo.FALHA_GERAL, resolvido_em__isnull=True,
        ).exclude(pk=evento.pk).exists()
        if geral:
            return (D.AGUARDAR, 5, "Faz parte da queda geral já registrada no local.", "regra")
    return None


def _contexto(evento):
    cli = evento.cliente
    eqs = Equipamento.objects.filter(cliente=cli, ativo=True)
    resumo = {s: eqs.filter(status=s).count() for s in Status.values}
    eq = evento.equipamento
    historico = list(
        Evento.objects.filter(cliente=cli, criado_em__gte=timezone.now() - timedelta(days=7))
        .exclude(pk=evento.pk).values("tipo", "titulo", "criado_em", "resolvido_em")[:15]
    )
    for h in historico:
        h["criado_em"] = h["criado_em"].isoformat()
        h["resolvido_em"] = h["resolvido_em"].isoformat() if h["resolvido_em"] else None

    contexto = {
        "agora": timezone.localtime().isoformat(),
        "cliente": {"nome": cli.nome, "portaria_remota": cli.portaria_remota,
                    "observacoes": cli.observacoes[:500]},
        "evento": {"tipo": evento.tipo, "titulo": evento.titulo, "detalhe": evento.detalhe,
                   "severidade": evento.severidade, "origem": evento.origem},
        "equipamentos_do_cliente_por_status": resumo,
        "historico_7_dias": historico,
        "reincidencias_24h": _reincidencias(evento),
    }
    if eq:
        contexto["equipamento"] = {
            "nome": eq.nome, "categoria": eq.categoria, "fabricante": eq.fabricante,
            "modelo": eq.modelo, "critico": eq.critico,
            "status_atual": eq.status, "latencia_ms": eq.latencia_ms, "perda_pct": eq.perda_pct,
            "depende_de": eq.depende_de.nome if eq.depende_de else None,
            "dependentes": list(eq.dependentes.values_list("nome", flat=True)[:30]),
            "detalhes": {k: v for k, v in (eq.detalhes or {}).items() if k != "netwatch"},
        }
    return contexto


PROMPT_SISTEMA = """Você é o analista de triagem da central de monitoramento da MasterSeg, \
empresa de segurança eletrônica (CFTV, alarme, controle de acesso, portaria remota).
Recebe um evento de falha e o contexto do cliente e decide o que fazer.

Decisões possíveis:
- chamar_tecnico: precisa de alguém no local.
- acao_remota: dá para resolver à distância (reiniciar, reconfigurar).
- avisar_cliente: o problema é do lado do cliente (energia, equipamento desligado, uso excessivo do link).
- acionar_operadora: o problema é no link do provedor de internet; abrir chamado na operadora.
- aguardar: provavelmente transitório; acompanhar antes de mobilizar alguém.
- nenhuma: não exige ação.

Prioridade de 1 (urgente) a 5 (baixa). Portaria remota sem gateway, servidor ou gravador \
é sempre urgente. Queda de um link com o reserva assumindo não exige técnico, exige \
chamado na operadora; queda sem reserva em portaria remota é urgente. Câmera isolada caindo uma vez costuma ser aguardar. Reincidência \
indica instabilidade e justifica técnico. Seja conservador: deslocar técnico custa caro.

Responda APENAS com JSON: {"decisao": "...", "prioridade": N, "justificativa": "..."} \
com a justificativa em português, em até 3 frases."""


def _perguntar_ia(evento):
    cfg = settings.TRIAGEM
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": cfg["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": cfg["MODELO"],
                "max_tokens": 400,
                "system": PROMPT_SISTEMA,
                "messages": [{"role": "user", "content": json.dumps(
                    _contexto(evento), ensure_ascii=False, default=str)}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        texto = "".join(b.get("text", "") for b in resp.json().get("content", []))
        inicio, fim = texto.find("{"), texto.rfind("}")
        dados = json.loads(texto[inicio:fim + 1])
        dec = dados.get("decisao")
        if dec not in D.values:
            raise ValueError(f"decisão inválida: {dec}")
        prio = min(5, max(1, int(dados.get("prioridade", 3))))
        return (dec, prio, str(dados.get("justificativa", "")), "ia")
    except Exception as exc:  # noqa: BLE001
        log.warning("IA indisponível para o evento %s (%s); usando regras", evento.pk, exc)
        return None


def acionar_tecnico(evento):
    if evento.tecnico_acionado_em:
        return  # nunca aciona duas vezes o mesmo evento
    url = settings.TRIAGEM["WEBHOOK_ACIONAMENTO"]
    if not url:
        log.info("Acionamento automático ligado, mas sem webhook configurado")
        return
    payload = {
        "evento_id": evento.pk, "cliente": evento.cliente.nome, "chave": evento.cliente.chave,
        "endereco": evento.cliente.endereco, "titulo": evento.titulo, "detalhe": evento.detalhe,
        "prioridade": evento.prioridade, "justificativa": evento.justificativa,
    }
    try:
        requests.post(url, json=payload, timeout=10).raise_for_status()
        evento.tecnico_acionado_em = timezone.now()
        evento.save(update_fields=["tecnico_acionado_em"])
    except requests.RequestException:
        log.exception("Falha ao acionar técnico para o evento %s", evento.pk)
