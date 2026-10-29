"""Regras de negócio: processar relatórios do agente e gerar eventos."""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Agente, Equipamento, Evento, LinkWAN, Metrica, MetricaLink, Status

log = logging.getLogger("monitor")


def _status_a_partir(resultado):
    if not resultado.get("online"):
        return Status.OFFLINE
    if resultado.get("servico_ok") is False:
        return Status.DEGRADADO
    lat = resultado.get("latencia_ms")
    perda = resultado.get("perda_pct")
    cfg = settings.MONITOR
    if (lat is not None and lat > cfg["LATENCIA_DEGRADADA_MS"]) or (
        perda is not None and perda > cfg["PERDA_DEGRADADA_PCT"]
    ):
        return Status.DEGRADADO
    return Status.ONLINE


def criar_evento(**campos):
    """Cria o evento e dispara a triagem depois do commit."""
    campos.setdefault("ultima_ocorrencia", campos.get("criado_em") or timezone.now())
    evento = Evento.objects.create(**campos)
    if evento.e_problema:
        from .triagem import agendar_triagem
        transaction.on_commit(lambda: agendar_triagem(evento.pk))
    else:
        evento.decisao = Evento.Decisao.NENHUMA
        evento.analisado_por = "regra"
        evento.analisado_em = timezone.now()
        evento.save(update_fields=["decisao", "analisado_por", "analisado_em"])
    return evento


def _resolver_abertos(equipamento, tipos, quando):
    Evento.objects.filter(
        equipamento=equipamento, tipo__in=tipos, resolvido_em__isnull=True,
    ).update(resolvido_em=quando)


GRAVIDADE_SEVERIDADE = {Evento.Severidade.INFO: 0, Evento.Severidade.AVISO: 1, Evento.Severidade.CRITICO: 2}


def incidente_recente(eq, agora):
    """Incidente de falha do equipamento ainda aberto, ou fechado há pouco (dentro
    da janela de reabertura). Aberto tem preferência."""
    janela = timedelta(minutes=settings.MONITOR["JANELA_REABERTURA_MIN"])
    return (Evento.objects.filter(equipamento=eq, tipo__in=Evento.FALHAS_EQUIPAMENTO)
            .filter(Q(resolvido_em__isnull=True) | Q(resolvido_em__gte=agora - janela))
            .order_by(F("resolvido_em").desc(nulls_first=True), "-criado_em").first())


def _falha_equipamento(eq, tipo, severidade, titulo, detalhe, agora):
    """Um equipamento que cai, volta e cai de novo (ou alterna entre offline e
    serviço parado) é UM incidente: soma ocorrências em vez de abrir outro evento."""
    inc = incidente_recente(eq, agora)
    if not inc:
        return criar_evento(cliente=eq.cliente, equipamento=eq, tipo=tipo, severidade=severidade,
                            titulo=titulo, detalhe=detalhe, criado_em=agora)

    ordem = Evento.FALHAS_EQUIPAMENTO
    if ordem.index(tipo) <= ordem.index(inc.tipo):
        inc.tipo = tipo  # o incidente assume a forma mais grave que já apareceu
    if GRAVIDADE_SEVERIDADE[severidade] > GRAVIDADE_SEVERIDADE[inc.severidade]:
        inc.severidade = severidade
    inc.ocorrencias += 1
    inc.ultima_ocorrencia = agora
    inc.resolvido_em = None
    desde = timezone.localtime(inc.criado_em).strftime("%d/%m %H:%M")
    if inc.ocorrencias >= settings.MONITOR["INTERMITENTE_FALHAS"]:
        inc.titulo = f"{eq.nome} intermitente: {inc.ocorrencias} falhas desde {desde}"
    else:
        inc.titulo = titulo
    inc.detalhe = f"{detalhe} Última falha às {timezone.localtime(agora):%H:%M}; {inc.ocorrencias} falhas neste incidente."
    inc.save(update_fields=["tipo", "severidade", "ocorrencias", "ultima_ocorrencia",
                            "resolvido_em", "titulo", "detalhe"])
    # Reanalisa só quando vira intermitente: a decisão muda, e a IA não é chamada a cada oscilação.
    if inc.ocorrencias == settings.MONITOR["INTERMITENTE_FALHAS"]:
        from .triagem import agendar_triagem
        transaction.on_commit(lambda: agendar_triagem(inc.pk))
    return inc


def _transicao_status(eq, novo, agora, servico_ok=None):
    antigo = eq.status
    if novo == antigo:
        return
    eq.status = novo
    eq.status_desde = agora
    nome = eq.nome

    if novo == Status.OFFLINE:
        _falha_equipamento(
            eq, Evento.Tipo.EQUIP_OFFLINE,
            severidade=Evento.Severidade.CRITICO if eq.critico or eq.categoria in (
                "gateway", "servidor", "gravador", "switch") else Evento.Severidade.AVISO,
            titulo=f"{nome} parou de responder",
            detalhe=f"IP {eq.ip}. Status anterior: {Status(antigo).label.lower()}.",
            agora=agora,
        )
    elif novo == Status.DEGRADADO and servico_ok is False:
        _falha_equipamento(
            eq, Evento.Tipo.SERVICO_PARADO,
            severidade=Evento.Severidade.AVISO,
            titulo=f"{nome} responde na rede, mas o serviço parou",
            detalhe=(f"IP {eq.ip} responde, porém a leitura falhou: {(eq.detalhes or {}).get('erro_coleta')}."
                     if (eq.detalhes or {}).get("erro_coleta")
                     else f"IP {eq.ip} responde, porém a porta {eq.porta_http} (web/API) não abre."),
            agora=agora,
        )
    elif novo == Status.DEGRADADO:
        _falha_equipamento(
            eq, Evento.Tipo.DEGRADADO,
            severidade=Evento.Severidade.AVISO,
            titulo=f"{nome} com conexão degradada",
            detalhe=f"Latência {eq.latencia_ms or 0:.0f} ms, perda {eq.perda_pct or 0:.0f}%.",
            agora=agora,
        )
    elif novo == Status.ONLINE and antigo in (Status.OFFLINE, Status.DEGRADADO):
        inc = incidente_recente(eq, agora)
        _resolver_abertos(eq, Evento.FALHAS_EQUIPAMENTO, agora)
        # Num equipamento oscilando, o próprio incidente mostra "resolvido";
        # um "voltou" a cada volta só enche o feed.
        if antigo == Status.OFFLINE and (not inc or inc.ocorrencias == 1):
            criar_evento(
                cliente=eq.cliente, equipamento=eq, tipo=Evento.Tipo.EQUIP_ONLINE,
                severidade=Evento.Severidade.INFO,
                titulo=f"{nome} voltou a responder", criado_em=agora,
            )


ROTULO_ARMACAO = {"armada": "armada", "armada_parcial": "armada parcial", "desarmada": "desarmada"}


def _comparar_alarme(eq, antigo, novo, agora):
    """Central de alarme: disparo abre um evento por partição (com as zonas que
    dispararam) e fecha quando a central sai do disparo; armar/desarmar fica no histórico."""
    part_ant = {p.get("id"): p for p in antigo.get("particoes", [])}
    zonas = novo.get("zonas", [])
    primeira_leitura = not antigo
    for p in novo.get("particoes", []):
        pid, ant = p.get("id"), part_ant.get(p.get("id"), {})
        ref = f"particao:{pid}"
        nome = p.get("nome") or f"Partição {pid}"
        if p.get("disparando") and not ant.get("disparando"):
            disparadas = [z for z in zonas if z.get("estado") == "disparada"
                          and (not z.get("particao") or z.get("particao") == pid)]
            lista = ", ".join(f"zona {z.get('numero')}" + (f" ({z['nome']})" if z.get("nome") else "")
                              for z in disparadas)
            criar_evento(cliente=eq.cliente, equipamento=eq, tipo=Evento.Tipo.ALARME_DISPARO,
                         severidade=Evento.Severidade.CRITICO, referencia=ref, criado_em=agora,
                         titulo=f"Alarme disparou em {eq.nome}: {nome}"[:200],
                         detalhe=(f"Disparo em {lista}." if lista else "A central não informou a zona.")
                         + f" Partição {ROTULO_ARMACAO.get(p.get('estado'), p.get('estado'))}.")
        elif ant.get("disparando") and not p.get("disparando"):
            Evento.objects.filter(equipamento=eq, referencia=ref, tipo=Evento.Tipo.ALARME_DISPARO,
                                  resolvido_em__isnull=True).update(resolvido_em=agora)
        estado, estado_ant = p.get("estado"), ant.get("estado")
        if (not primeira_leitura and estado_ant and estado != estado_ant
                and {estado, estado_ant} <= set(ROTULO_ARMACAO)):
            criar_evento(cliente=eq.cliente, equipamento=eq, tipo=Evento.Tipo.ALARME_ESTADO,
                         severidade=Evento.Severidade.INFO, referencia=ref, criado_em=agora,
                         titulo=f"{eq.nome}: {nome} {ROTULO_ARMACAO[estado]}"[:200])


def _comparar_detalhes(eq, antigos, novos, agora):
    """Gera eventos a partir de mudanças nos detalhes (canais do NVR, HDs)."""
    # Canais (câmeras dentro do gravador); os que não estão em uso não geram evento.
    canais_ant = {str(c.get("id")): c for c in antigos.get("canais", [])}
    canais = novos.get("canais", [])
    if eq.canais_em_uso is not None:
        canais = canais[:eq.canais_em_uso]
    for canal in canais:
        cid = str(canal.get("id"))
        estava_online = canais_ant.get(cid, {}).get("online", True)
        if estava_online and not canal.get("online", True):
            criar_evento(
                cliente=eq.cliente, equipamento=eq, tipo=Evento.Tipo.CANAL_OFFLINE,
                severidade=Evento.Severidade.AVISO,
                titulo=f"Canal {cid} sem sinal em {eq.nome}",
                detalhe=f"Câmera: {canal.get('nome') or 'sem nome'} {canal.get('ip') or ''}".strip(),
                criado_em=agora,
            )

    if isinstance(novos.get("alarme"), dict):
        _comparar_alarme(eq, antigos.get("alarme") or {}, novos["alarme"], agora)

    # HDs
    hdds_ant = {str(h.get("id")): h for h in antigos.get("hdds", [])}
    for hd in novos.get("hdds", []):
        hid = str(hd.get("id"))
        ok_antes = hdds_ant.get(hid, {}).get("status", "ok") == "ok"
        if ok_antes and hd.get("status", "ok") != "ok":
            criar_evento(
                cliente=eq.cliente, equipamento=eq, tipo=Evento.Tipo.HDD_ERRO,
                severidade=Evento.Severidade.CRITICO,
                titulo=f"HD {hid} com problema em {eq.nome}",
                detalhe=f"Status informado pelo equipamento: {hd.get('status')}.",
                criado_em=agora,
            )


LINK_RUIM = {"offline", "sem_internet"}
CICLOS_SATURACAO = 3      # relatórios seguidos acima do limite antes de avisar
LIMITE_SATURACAO = 0.90   # 90% do contratado
LIMITE_ALIVIO = 0.70      # abaixo disso, a saturação é considerada resolvida


def _processar_links(eq, antigos, novos, agora, metricas_link):
    """Enriquece os links com o contratado, gera eventos e guarda o histórico.
    Devolve a lista que fica salva em eq.detalhes['links']."""
    contratados = {l.interface: l for l in LinkWAN.objects.filter(equipamento=eq)}
    ant = {l.get("interface"): l for l in antigos or []}
    online_agora = [l for l in novos if l.get("status") not in LINK_RUIM]
    principal_novo = next((l for l in novos if l.get("principal")), None)
    principal_ant = next((l for l in ant.values() if l.get("principal")), None)

    for link in novos:
        iface = link.get("interface")
        velho = ant.get(iface, {})
        cad = contratados.get(iface)
        ref = f"link:{iface}"
        if cad:
            link["nome"] = cad.nome or link.get("nome") or iface
            link["provedor"] = cad.provedor
            link["download_mbps"] = cad.download_mbps
            link["upload_mbps"] = cad.upload_mbps
        if link.get("download_mbps") and link.get("rx_bps") is not None:
            link["uso_down_pct"] = round(100 * link["rx_bps"] / (link["download_mbps"] * 1e6), 1)
        if link.get("upload_mbps") and link.get("tx_bps") is not None:
            link["uso_up_pct"] = round(100 * link["tx_bps"] / (link["upload_mbps"] * 1e6), 1)
        nome = link.get("nome") or iface
        st_novo, st_velho = link.get("status"), velho.get("status")
        link["status_desde"] = velho.get("status_desde") if st_novo == st_velho else agora.isoformat()

        # Queda e volta do link
        if st_novo in LINK_RUIM and st_velho not in LINK_RUIM and st_velho is not None:
            outros = [l for l in online_agora if l.get("interface") != iface]
            motivo = ("a porta perdeu o sinal" if st_novo == "offline"
                      else link.get("motivo") or "a porta está ligada, mas sem internet")
            if outros:
                titulo = f"Link {nome} caiu; operando pelo {outros[0].get('nome') or outros[0]['interface']}"
                sev = Evento.Severidade.AVISO
            else:
                titulo = f"Link {nome} caiu e não há link reserva ativo"
                sev = Evento.Severidade.CRITICO
            criar_evento(cliente=eq.cliente, equipamento=eq, tipo=Evento.Tipo.LINK_OFFLINE, severidade=sev,
                         referencia=ref, titulo=titulo, criado_em=agora,
                         detalhe=f"{nome} ({iface}): {motivo}." + (
                             f" Suporte da operadora: {cad.contato_operadora}." if cad and cad.contato_operadora else "")
                         + (f" Designação: {cad.designacao}." if cad and cad.designacao else ""))
        elif st_velho in LINK_RUIM and st_novo not in LINK_RUIM:
            Evento.objects.filter(equipamento=eq, referencia=ref, resolvido_em__isnull=True,
                                  tipo__in=[Evento.Tipo.LINK_OFFLINE, Evento.Tipo.LINK_INSTAVEL]
                                  ).update(resolvido_em=agora)
            criar_evento(cliente=eq.cliente, equipamento=eq, tipo=Evento.Tipo.LINK_ONLINE,
                         severidade=Evento.Severidade.INFO, referencia=ref, criado_em=agora,
                         titulo=f"Link {nome} voltou")
        elif st_novo == "instavel" and st_velho == "online":
            teste = link.get("internet_ping") or link.get("gateway_ping") or {}
            criar_evento(cliente=eq.cliente, equipamento=eq, tipo=Evento.Tipo.LINK_INSTAVEL,
                         severidade=Evento.Severidade.AVISO, referencia=ref, criado_em=agora,
                         titulo=f"Link {nome} instável",
                         detalhe=f"Latência {teste.get('latencia_ms') or '?'} ms, perda {teste.get('perda_pct', '?')}%.")
        elif st_velho == "instavel" and st_novo == "online":
            Evento.objects.filter(equipamento=eq, referencia=ref, tipo=Evento.Tipo.LINK_INSTAVEL,
                                  resolvido_em__isnull=True).update(resolvido_em=agora)

        # Saturação: N relatórios seguidos acima de 90% do contratado
        uso = max(link.get("uso_down_pct") or 0, link.get("uso_up_pct") or 0) / 100
        ciclos = velho.get("ciclos_saturado", 0)
        link["ciclos_saturado"] = ciclos + 1 if uso >= LIMITE_SATURACAO else (ciclos if uso >= LIMITE_ALIVIO else 0)
        if link["ciclos_saturado"] == CICLOS_SATURACAO:
            criar_evento(cliente=eq.cliente, equipamento=eq, tipo=Evento.Tipo.LINK_SATURADO,
                         severidade=Evento.Severidade.AVISO, referencia=ref, criado_em=agora,
                         titulo=f"Link {nome} saturado",
                         detalhe=f"Uso de {uso * 100:.0f}% do contratado há {CICLOS_SATURACAO} leituras seguidas. "
                                 "Câmeras na nuvem e a portaria remota podem travar.")
        elif link["ciclos_saturado"] == 0 and ciclos >= CICLOS_SATURACAO:
            Evento.objects.filter(equipamento=eq, referencia=ref, tipo=Evento.Tipo.LINK_SATURADO,
                                  resolvido_em__isnull=True).update(resolvido_em=agora)

        teste = link.get("internet_ping") or link.get("gateway_ping") or {}
        metricas_link.append(MetricaLink(
            equipamento=eq, interface=iface, ts=agora, status=st_novo or "", principal=bool(link.get("principal")),
            rx_bps=link.get("rx_bps"), tx_bps=link.get("tx_bps"),
            latencia_ms=teste.get("latencia_ms"), perda_pct=teste.get("perda_pct"),
        ))

    # Failover sem queda (troca de rota com o principal ainda de pé) e retorno ao principal
    if principal_ant and principal_novo and principal_ant.get("interface") != principal_novo.get("interface"):
        de = principal_ant.get("nome") or principal_ant.get("interface")
        para = principal_novo.get("nome") or principal_novo.get("interface")
        dist_ant, dist_novo = principal_ant.get("distancia") or 0, principal_novo.get("distancia") or 0
        if dist_novo < dist_ant:
            Evento.objects.filter(equipamento=eq, tipo=Evento.Tipo.FAILOVER, resolvido_em__isnull=True
                                  ).update(resolvido_em=agora)
            criar_evento(cliente=eq.cliente, equipamento=eq, tipo=Evento.Tipo.FAILOVER_RETORNO,
                         severidade=Evento.Severidade.INFO, criado_em=agora,
                         titulo=f"Tráfego voltou para o link principal ({para})")
        elif ant.get(principal_ant.get("interface"), {}).get("status") not in LINK_RUIM and \
                next((l for l in novos if l.get("interface") == principal_ant.get("interface")), {}
                     ).get("status") not in LINK_RUIM:
            criar_evento(cliente=eq.cliente, equipamento=eq, tipo=Evento.Tipo.FAILOVER,
                         severidade=Evento.Severidade.AVISO, criado_em=agora,
                         titulo=f"Failover: tráfego passou de {de} para {para}",
                         detalhe=f"{de} continua de pé, mas deixou de ser a rota principal "
                                 "(check-gateway ou rota recursiva).")
    return novos


@transaction.atomic
def processar_relatorio(agente: Agente, dados: dict, ip_origem=None):
    agora = timezone.now()
    _registrar_contato(agente, dados, ip_origem, agora)

    ids = [r.get("id") for r in dados.get("resultados", []) if r.get("id")]
    equipamentos = {
        e.pk: e for e in Equipamento.objects.select_for_update()
        .select_related("cliente").filter(pk__in=ids, cliente=agente.cliente)
    }
    metricas, metricas_link = [], []
    for r in dados.get("resultados", []):
        eq = equipamentos.get(r.get("id"))
        if not eq:
            continue
        if r.get("online") and r.get("latencia_ms") is None:
            # Agente até 1.0.4 no Windows: "host de destino inacessível" contava como
            # resposta de ping. Sem nenhum tempo de resposta, o equipamento não respondeu.
            r.update(online=False, perda_pct=100.0, servico_ok=None)
        eq.ultimo_check = agora
        eq.latencia_ms = r.get("latencia_ms")
        eq.perda_pct = r.get("perda_pct")
        for campo in ("firmware", "serial", "mac"):
            if r.get(campo):
                setattr(eq, campo, str(r[campo])[:80])
        if r.get("modelo") and not eq.modelo:
            eq.modelo = str(r["modelo"])[:80]
        if isinstance(r.get("detalhes"), dict) and r["detalhes"]:
            if isinstance(r["detalhes"].get("links"), list):
                r["detalhes"]["links"] = _processar_links(
                    eq, (eq.detalhes or {}).get("links"), r["detalhes"]["links"], agora, metricas_link)
            _comparar_detalhes(eq, eq.detalhes or {}, r["detalhes"], agora)
            eq.detalhes = {**(eq.detalhes or {}), **r["detalhes"]}
            if not r["detalhes"].get("erro_coleta"):
                eq.detalhes.pop("erro_coleta", None)

        _transicao_status(eq, _status_a_partir(r), agora, r.get("servico_ok"))
        eq.save()
        metricas.append(Metrica(
            equipamento=eq, ts=agora, online=bool(r.get("online")),
            latencia_ms=r.get("latencia_ms"), perda_pct=r.get("perda_pct"),
        ))

        for ev in r.get("eventos", []) or []:
            registrar_evento_dispositivo(eq, ev, agora)

    Metrica.objects.bulk_create(metricas)
    MetricaLink.objects.bulk_create(metricas_link)
    _verificar_queda_geral(agente, agora)
    return len(metricas)


def registrar_evento_dispositivo(eq, ev, agora=None):
    """Evento empurrado pelo próprio equipamento (alertStream, eventManager)."""
    mapa = {
        "videoloss": Evento.Tipo.VIDEO_PERDIDO, "VideoLoss": Evento.Tipo.VIDEO_PERDIDO,
        "hdError": Evento.Tipo.HDD_ERRO, "StorageFailure": Evento.Tipo.HDD_ERRO,
        "ipConflict": Evento.Tipo.CONFLITO_IP, "IPConflict": Evento.Tipo.CONFLITO_IP,
    }
    tipo = mapa.get(ev.get("tipo"), Evento.Tipo.OUTRO)
    criar_evento(
        cliente=eq.cliente, equipamento=eq, tipo=tipo, origem="dispositivo",
        severidade=ev.get("severidade", Evento.Severidade.AVISO),
        titulo=ev.get("titulo") or f"{Evento.Tipo(tipo).label} em {eq.nome}",
        detalhe=ev.get("detalhe", ""), criado_em=agora or timezone.now(),
    )


def _registrar_contato(agente, dados, ip_origem, agora):
    voltou = agente.marcado_offline
    agente.ultimo_contato = agora
    agente.hostname = (dados.get("hostname") or agente.hostname)[:100]
    agente.versao = (dados.get("versao") or agente.versao)[:20]
    agente.ip_local = dados.get("ip_local") or agente.ip_local
    agente.ip_publico = ip_origem or agente.ip_publico
    agente.sistema = (dados.get("sistema") or agente.sistema)[:100]
    if dados.get("iniciado_em"):
        agente.iniciado_em = parse_datetime(dados["iniciado_em"]) or agente.iniciado_em
    agente.pausado = bool(dados.get("pausado"))
    agente.marcado_offline = False
    # Só os campos de contato: os de comando são escritos pela tela ao mesmo tempo.
    agente.save(update_fields=["ultimo_contato", "hostname", "versao", "ip_local", "ip_publico",
                               "sistema", "iniciado_em", "pausado", "marcado_offline"])
    if voltou:
        Evento.objects.filter(
            cliente=agente.cliente, tipo=Evento.Tipo.AGENTE_OFFLINE, resolvido_em__isnull=True,
        ).update(resolvido_em=agora)
        criar_evento(
            cliente=agente.cliente, tipo=Evento.Tipo.AGENTE_ONLINE,
            severidade=Evento.Severidade.INFO, origem="plataforma",
            titulo=f"Agente de {agente.cliente.nome} voltou a reportar", criado_em=agora,
        )


def _verificar_queda_geral(agente, agora):
    """Se a maioria dos equipamentos caiu ao mesmo tempo, registra um evento
    único de queda geral (provável link, energia ou switch central)."""
    eqs = list(Equipamento.objects.filter(cliente=agente.cliente, ativo=True)
               .values_list("status", "status_desde"))
    if len(eqs) < 4:
        return
    recentes = [s for s, d in eqs if s == Status.OFFLINE and d and (agora - d).total_seconds() < 300]
    if len(recentes) / len(eqs) < 0.6:
        return
    ja_existe = Evento.objects.filter(
        cliente=agente.cliente, tipo=Evento.Tipo.FALHA_GERAL, resolvido_em__isnull=True,
    ).exists()
    if not ja_existe:
        criar_evento(
            cliente=agente.cliente, tipo=Evento.Tipo.FALHA_GERAL,
            severidade=Evento.Severidade.CRITICO, origem="plataforma",
            titulo=f"Queda geral em {agente.cliente.nome}",
            detalhe=f"{len(recentes)} de {len(eqs)} equipamentos caíram nos últimos 5 minutos "
                    "com o agente ainda respondendo: provável switch, energia ou rede interna.",
            criado_em=agora,
        )


def verificar_agentes():
    """Marca agentes silenciosos como offline. Barato: chamado pelo dashboard
    e pelo comando `verificar_agentes` (cron/systemd timer)."""
    agora = timezone.now()
    for agente in Agente.objects.filter(ativo=True, marcado_offline=False,
                                        ultimo_contato__isnull=False).select_related("cliente"):
        if agente.online:
            continue
        agente.marcado_offline = True
        agente.save(update_fields=["marcado_offline"])
        minutos = int((agora - agente.ultimo_contato).total_seconds() // 60)
        criar_evento(
            cliente=agente.cliente, tipo=Evento.Tipo.AGENTE_OFFLINE,
            severidade=Evento.Severidade.CRITICO, origem="plataforma",
            titulo=f"Agente de {agente.cliente.nome} sem contato",
            detalhe=f"Último contato há {minutos} min. Pode ser internet, energia ou o próprio "
                    "agente. Os status dos equipamentos deste cliente estão congelados.",
            criado_em=agora,
        )
        log.warning("Agente %s marcado offline", agente)
