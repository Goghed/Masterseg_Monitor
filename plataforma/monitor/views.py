"""Telas e a API JSON consumida pelo front (atualização ao vivo por polling)."""
from collections import Counter, defaultdict
from datetime import timedelta

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import GRAVIDADE_STATUS, Agente, Cliente, Equipamento, Evento, Metrica, MetricaLink, Status
from .servicos import verificar_agentes

TIPOS_PROBLEMA = [t for t in Evento.Tipo.values if t not in Evento.TIPOS_RECUPERACAO]

# Ordem e rótulos das seções da tela do cliente (a "espinha" da topologia).
SECOES = [
    ("gateway", "Gateway", ["gateway"]),
    ("servidor", "Servidores", ["servidor"]),
    ("gravador", "Gravadores", ["gravador"]),
    ("switch", "Switches", ["switch"]),
    ("camera", "Câmeras", ["camera"]),
    ("seguranca", "Alarme e acesso", ["alarme", "acesso", "interfone"]),
    ("outro", "Outros equipamentos", ["outro"]),
]


# ---------------------------------------------------------------- páginas
@login_required
def dashboard(request):
    return render(request, "monitor/dashboard.html")


@login_required
def agentes(request):
    return render(request, "monitor/agentes.html")


@login_required
def cliente(request, chave):
    obj = get_object_or_404(Cliente, chave__iexact=chave)
    return render(request, "monitor/cliente.html", {"cliente": obj})


# ---------------------------------------------------------------- serialização
def _evento_json(e):
    return {
        "id": e.pk, "tipo": e.tipo, "tipo_label": e.get_tipo_display(),
        "severidade": e.severidade, "titulo": e.titulo, "detalhe": e.detalhe,
        "criado_em": e.criado_em.isoformat(),
        "ultima_ocorrencia": e.ultima_ocorrencia.isoformat(), "ocorrencias": e.ocorrencias,
        "resolvido": e.resolvido_em is not None,
        "problema": e.e_problema,
        "decisao": e.decisao, "decisao_label": e.get_decisao_display(),
        "prioridade": e.prioridade, "justificativa": e.justificativa,
        "analisado_por": e.analisado_por,
        "cliente": {"nome": e.cliente.nome, "chave": e.cliente.chave},
        "equipamento": e.equipamento.nome if e.equipamento_id else None,
    }


# Primeira versão do agente que obedece a comandos da plataforma.
VERSAO_CONTROLE = (1, 0, 3)


def _versao(texto):
    try:
        return tuple(int(p) for p in (texto or "").split("."))
    except ValueError:
        return ()


def _iso(dt):
    return dt.isoformat() if dt else None


def _agente_json(a):
    return {
        "id": a.pk, "nome": a.nome, "online": a.online, "hostname": a.hostname, "versao": a.versao,
        "ip_local": a.ip_local, "ip_publico": a.ip_publico, "sistema": a.sistema,
        "ultimo_contato": _iso(a.ultimo_contato), "iniciado_em": _iso(a.iniciado_em),
        "intervalo_segundos": a.intervalo_segundos, "pausado": a.pausado,
        "aceita_comandos": _versao(a.versao) >= VERSAO_CONTROLE,
        "comando": a.comando, "comando_label": a.get_comando_display() if a.comando else "",
        "comando_pedido_em": _iso(a.comando_pedido_em), "comando_pedido_por": a.comando_pedido_por,
        "comando_entregue": a.comando_entregue,
        "comando_entregue_label": a.get_comando_entregue_display() if a.comando_entregue else "",
        "comando_entregue_em": _iso(a.comando_entregue_em),
        "cliente": {"nome": a.cliente.nome, "chave": a.cliente.chave},
    }


def _equipamentos_por_cliente(**filtro):
    """Resumo dos equipamentos de cada cliente. Uma conta só, usada na visão geral,
    na página de agentes e na tela do cliente, para todas dizerem a mesma coisa."""
    resumo = defaultdict(lambda: {"total": 0, "online": 0, "offline": 0, "degradado": 0,
                                  "desconhecido": 0, "nomes_offline": [], "nomes_degradado": [],
                                  "statuses": []})
    for cid, st, nome in (Equipamento.objects.filter(ativo=True, cliente__ativo=True, **filtro)
                          .order_by("ordem", "nome").values_list("cliente_id", "status", "nome")):
        r = resumo[cid]
        r["total"] += 1
        r[st] = r.get(st, 0) + 1
        r["statuses"].append(st)
        if st == Status.OFFLINE:
            r["nomes_offline"].append(nome)
        elif st == Status.DEGRADADO:
            r["nomes_degradado"].append(nome)
    return resumo


def _equip_json(r):
    return {k: v for k, v in r.items() if k != "statuses"}


def _urgencia(e):
    """Ordena incidentes abertos: chamar técnico primeiro, depois a prioridade."""
    return (e.decisao != Evento.Decisao.CHAMAR_TECNICO, e.prioridade or 9, -e.ultima_ocorrencia.timestamp())


def _pior(statuses):
    if not statuses:
        return Status.DESCONHECIDO
    return max(statuses, key=lambda s: GRAVIDADE_STATUS.get(s, 0))


# ---------------------------------------------------------------- API web
@login_required
def api_busca(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 1:
        return JsonResponse({"resultados": []})
    qs = (Cliente.objects.filter(ativo=True)
          .filter(Q(nome__icontains=q) | Q(chave__icontains=q) | Q(cidade__icontains=q))
          .annotate(offline=Count("equipamentos", filter=Q(equipamentos__status=Status.OFFLINE)),
                    total=Count("equipamentos"))
          .order_by("nome")[:8])
    return JsonResponse({"resultados": [
        {"nome": c.nome, "chave": c.chave, "cidade": c.cidade,
         "offline": c.offline, "total": c.total} for c in qs
    ]})


@login_required
def api_dashboard(request):
    verificar_agentes()
    agora = timezone.now()
    inicio_30d = (timezone.localtime(agora) - timedelta(days=29)).replace(
        hour=0, minute=0, second=0, microsecond=0)

    # Mosaico: pior status de cada cliente.
    equip = _equipamentos_por_cliente()
    links_fora = defaultdict(list)
    for cid, det in Equipamento.objects.filter(ativo=True, categoria="gateway").values_list("cliente_id", "detalhes"):
        for l in (det or {}).get("links") or []:
            if l.get("status") in ("offline", "sem_internet"):
                links_fora[cid].append(l.get("nome") or l.get("interface"))
    clientes = list(Cliente.objects.filter(ativo=True).prefetch_related("agentes"))
    problemas = Evento.objects.filter(tipo__in=TIPOS_PROBLEMA)
    abertos = problemas.filter(resolvido_em__isnull=True)
    tecnico = defaultdict(list)
    for ev in sorted(abertos.filter(decisao=Evento.Decisao.CHAMAR_TECNICO), key=_urgencia):
        tecnico[ev.cliente_id].append(ev.titulo)
    mosaico = []
    for c in clientes:
        eq = equip[c.pk]
        sts = eq["statuses"]
        agentes = [a for a in c.agentes.all() if a.ativo]
        sem_agente = bool(agentes) and not any(a.online for a in agentes)
        status = Status.OFFLINE if sem_agente else _pior(sts)
        if links_fora.get(c.pk) and GRAVIDADE_STATUS[status] < GRAVIDADE_STATUS[Status.DEGRADADO]:
            status = Status.DEGRADADO  # um link caiu, mesmo com o reserva segurando
        mosaico.append({
            "chave": c.chave, "nome": c.nome,
            "status": status, "links_fora": links_fora.get(c.pk, []),
            "offline": eq["offline"], "total": eq["total"], "equipamentos": _equip_json(eq),
            "agente_offline": sem_agente,
            "agente": ("sem_contato" if sem_agente else "sem_agente" if not agentes
                       else "pausado" if all(a.pausado for a in agentes if a.online) else "comunicando"),
            "chamar_tecnico": tecnico.get(c.pk, []),
        })
    mosaico.sort(key=lambda m: (not m["chamar_tecnico"], -GRAVIDADE_STATUS.get(m["status"], 0), m["nome"]))

    contagem = Counter(st for r in equip.values() for st in r["statuses"])

    serie = (problemas.filter(criado_em__gte=inicio_30d)
             .annotate(dia=TruncDate("criado_em")).values("dia", "severidade")
             .annotate(n=Count("id")))
    dias = [(inicio_30d + timedelta(days=i)).date() for i in range(30)]
    por_dia = {d: {"critico": 0, "aviso": 0, "info": 0} for d in dias}
    for linha in serie:
        if linha["dia"] in por_dia:
            por_dia[linha["dia"]][linha["severidade"]] = linha["n"]

    rotulos_tipo = dict(Evento.Tipo.choices)
    tipos = (problemas.filter(criado_em__gte=inicio_30d).values("tipo")
             .annotate(n=Count("id")).order_by("-n")[:7])
    top_clientes = (problemas.filter(criado_em__gte=inicio_30d)
                    .values("cliente__nome", "cliente__chave")
                    .annotate(n=Count("id")).order_by("-n")[:6])
    ultimos = Evento.objects.select_related("cliente", "equipamento")[:20]

    return JsonResponse({
        "agora": agora.isoformat(),
        "kpis": {
            "clientes": len(clientes),
            "equipamentos": sum(contagem.values()),
            "online": contagem.get(Status.ONLINE, 0),
            "degradado": contagem.get(Status.DEGRADADO, 0),
            "offline": contagem.get(Status.OFFLINE, 0),
            "sem_dados": contagem.get(Status.DESCONHECIDO, 0),
            "eventos_24h": problemas.filter(criado_em__gte=agora - timedelta(hours=24)).count(),
            "abertos": abertos.count(),
            "chamar_tecnico": abertos.filter(decisao=Evento.Decisao.CHAMAR_TECNICO).count(),
            "agentes_offline": sum(1 for m in mosaico if m["agente_offline"]),
        },
        "mosaico": mosaico,
        "serie": {
            "dias": [d.strftime("%d/%m") for d in dias],
            "critico": [por_dia[d]["critico"] for d in dias],
            "aviso": [por_dia[d]["aviso"] for d in dias],
            "info": [por_dia[d]["info"] for d in dias],
        },
        "tipos": [{"tipo": t["tipo"], "label": rotulos_tipo.get(t["tipo"], t["tipo"]),
                   "n": t["n"]} for t in tipos],
        "top_clientes": [{"nome": t["cliente__nome"], "chave": t["cliente__chave"], "n": t["n"]}
                         for t in top_clientes],
        "ultimos": [_evento_json(e) for e in ultimos],
    })


@login_required
def api_cliente(request, chave):
    try:
        cli = Cliente.objects.prefetch_related("agentes").get(chave__iexact=chave)
    except Cliente.DoesNotExist:
        raise Http404
    agora = timezone.now()
    eqs = list(Equipamento.objects.filter(cliente=cli, ativo=True).select_related("depende_de"))

    # Uma única consulta para as sparklines de todos os equipamentos (última hora).
    linhas = defaultdict(list)
    for eid, ts, on, lat in (Metrica.objects
                             .filter(equipamento__cliente=cli, ts__gte=agora - timedelta(hours=1))
                             .order_by("ts").values_list("equipamento_id", "ts", "online", "latencia_ms")):
        linhas[eid].append(lat if on else None)
    # Histórico dos links (tráfego e latência da última hora), também numa consulta só.
    hist_links = defaultdict(lambda: {"rx": [], "tx": [], "lat": [], "ok": []})
    for eid, iface, rx, tx, lat, st in (MetricaLink.objects
                                         .filter(equipamento__cliente=cli, ts__gte=agora - timedelta(hours=1))
                                         .order_by("ts").values_list("equipamento_id", "interface", "rx_bps",
                                                                     "tx_bps", "latencia_ms", "status")):
        h = hist_links[(eid, iface)]
        h["rx"].append(rx); h["tx"].append(tx); h["lat"].append(lat)
        h["ok"].append(st not in ("offline", "sem_internet"))
    disp_links = {
        (r["equipamento_id"], r["interface"]): r for r in MetricaLink.objects
        .filter(equipamento__cliente=cli, ts__gte=agora - timedelta(hours=24))
        .values("equipamento_id", "interface")
        .annotate(total=Count("id"), ok=Count("id", filter=~Q(status__in=["offline", "sem_internet"])))
    }

    disponib = {
        r["equipamento_id"]: r for r in Metrica.objects
        .filter(equipamento__cliente=cli, ts__gte=agora - timedelta(hours=24))
        .values("equipamento_id")
        .annotate(total=Count("id"), on=Count("id", filter=Q(online=True)))
    }

    def links_json(e):
        saida = []
        for l in (e.detalhes or {}).get("links") or []:
            h = hist_links.get((e.pk, l.get("interface")), {"rx": [], "tx": [], "lat": [], "ok": []})
            d = disp_links.get((e.pk, l.get("interface")))
            saida.append({**l, "historico": {k: v[-120:] for k, v in h.items()},
                          "disponibilidade_24h": round(100 * d["ok"] / d["total"], 1) if d and d["total"] else None})
        # Principal primeiro, depois pela distância da rota
        return sorted(saida, key=lambda l: (not l.get("principal"), l.get("distancia") or 99))

    incidentes = {}
    for ev in sorted(Evento.objects.filter(cliente=cli, equipamento__isnull=False, resolvido_em__isnull=True,
                                           tipo__in=TIPOS_PROBLEMA), key=_urgencia):
        incidentes.setdefault(ev.equipamento_id, ev)

    def incidente_json(ev):
        if not ev:
            return None
        return {"id": ev.pk, "titulo": ev.titulo, "detalhe": ev.detalhe,
                "decisao": ev.decisao, "decisao_label": ev.get_decisao_display(),
                "prioridade": ev.prioridade, "justificativa": ev.justificativa,
                "analisado_por": ev.analisado_por, "ocorrencias": ev.ocorrencias,
                "desde": ev.criado_em.isoformat(),
                "chamar_tecnico": ev.decisao == Evento.Decisao.CHAMAR_TECNICO}

    def eq_json(e):
        d = disponib.get(e.pk)
        detalhes = dict(e.detalhes or {})
        if detalhes.get("links"):
            detalhes["links"] = links_json(e)
        return {
            "id": e.pk, "nome": e.nome, "categoria": e.categoria,
            "categoria_label": e.get_categoria_display(),
            "fabricante": e.get_fabricante_display(), "modelo": e.modelo,
            "ip": e.ip, "porta": e.porta_http, "status": e.status,
            "status_desde": e.status_desde.isoformat() if e.status_desde else None,
            "ultimo_check": e.ultimo_check.isoformat() if e.ultimo_check else None,
            "latencia_ms": e.latencia_ms, "perda_pct": e.perda_pct,
            "firmware": e.firmware, "serial": e.serial, "mac": e.mac,
            "critico": e.critico, "depende_de": e.depende_de.nome if e.depende_de else None,
            "detalhes": detalhes,
            "incidente": incidente_json(incidentes.get(e.pk)),
            "sparkline": linhas.get(e.pk, [])[-60:],
            "disponibilidade_24h": round(100 * d["on"] / d["total"], 1) if d and d["total"] else None,
        }

    secoes = []
    for chave_secao, titulo, cats in SECOES:
        itens = [eq_json(e) for e in eqs if e.categoria in cats]
        if not itens:
            continue
        secoes.append({
            "chave": chave_secao, "titulo": titulo, "itens": itens,
            "status": _pior([i["status"] for i in itens]),
            "online": sum(1 for i in itens if i["status"] == Status.ONLINE),
        })

    agentes = [_agente_json(a) for a in cli.agentes.all() if a.ativo]

    eventos = Evento.objects.filter(cliente=cli).select_related("cliente", "equipamento")[:30]
    contagem = Counter(e.status for e in eqs)
    return JsonResponse({
        "agora": agora.isoformat(),
        "cliente": {
            "nome": cli.nome, "chave": cli.chave, "cidade": cli.cidade, "endereco": cli.endereco,
            "contato": cli.contato_nome, "telefone": cli.contato_telefone,
            "portaria_remota": cli.portaria_remota,
        },
        "agentes": agentes,
        "resumo": {s: contagem.get(s, 0) for s in Status.values} | {"total": len(eqs)},
        "secoes": secoes,
        "eventos": [_evento_json(e) for e in eventos],
    })


@login_required
def api_agentes(request):
    verificar_agentes()
    lista = Agente.objects.filter(ativo=True, cliente__ativo=True).select_related("cliente")
    equip = _equipamentos_por_cliente()
    saida = []
    for a in lista:
        item = _agente_json(a)
        item["equipamentos"] = _equip_json(equip[a.cliente_id])
        saida.append(item)
    # Sem contato primeiro: é o que precisa de atenção.
    saida.sort(key=lambda a: (a["online"], not a["pausado"], a["cliente"]["nome"]))
    return JsonResponse({"agora": timezone.now().isoformat(), "agentes": saida})


@require_POST
@user_passes_test(lambda u: u.is_active and u.is_staff)
def api_agente_comando(request, pk):
    agente = get_object_or_404(Agente, pk=pk, ativo=True)
    comando = request.POST.get("comando", "")
    if comando == "cancelar":
        Agente.objects.filter(pk=pk).update(comando="")
        return JsonResponse({"ok": True})
    if comando not in Agente.Comando.values:
        return JsonResponse({"erro": "Comando inválido"}, status=400)
    if _versao(agente.versao) < VERSAO_CONTROLE:
        return JsonResponse({"erro": f"O agente está na versão {agente.versao or '?'} e não recebe comandos. "
                                     "Atualize para a 1.0.3 ou mais nova."}, status=409)
    Agente.objects.filter(pk=pk).update(comando=comando, comando_pedido_em=timezone.now(),
                                        comando_pedido_por=request.user.get_username())
    return JsonResponse({"ok": True})
