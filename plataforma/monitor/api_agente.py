"""API usada pelo agente instalado no cliente.

Autenticação: cabeçalho `Authorization: Token <token do agente>`.
A conexão é sempre do agente para a plataforma (nenhuma porta aberta no cliente).
"""
import json
import logging
from functools import wraps

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Agente, Equipamento
from .servicos import processar_relatorio, registrar_evento_dispositivo

log = logging.getLogger("monitor")
MAX_CORPO = 2 * 1024 * 1024


def _ip_origem(request):
    encaminhado = request.META.get("HTTP_X_FORWARDED_FOR")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def autenticar_agente(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        cab = request.META.get("HTTP_AUTHORIZATION", "")
        if not cab.startswith("Token "):
            return JsonResponse({"erro": "Token ausente"}, status=401)
        try:
            agente = Agente.objects.select_related("cliente").get(token=cab[6:].strip(), ativo=True)
        except Agente.DoesNotExist:
            return JsonResponse({"erro": "Token inválido ou agente desativado"}, status=401)
        request.agente = agente
        return view(request, *args, **kwargs)
    return wrapper


def _corpo_json(request):
    if len(request.body) > MAX_CORPO:
        raise ValueError("Corpo maior que 2 MB")
    return json.loads(request.body or b"{}")


@csrf_exempt
@require_GET
@autenticar_agente
def config(request):
    agente = request.agente
    Agente.objects.filter(pk=agente.pk).update(ultimo_contato=timezone.now())
    qs = Equipamento.objects.filter(cliente=agente.cliente, ativo=True)
    # Equipamentos sem agente definido ficam com o primeiro agente do cliente.
    primeiro = agente.cliente.agentes.filter(ativo=True).order_by("pk").first()
    if primeiro and primeiro.pk == agente.pk:
        qs = qs.filter(agente__in=[agente]) | qs.filter(agente__isnull=True)
    else:
        qs = qs.filter(agente=agente)

    return JsonResponse({
        "agente": {
            "id": agente.pk, "cliente": agente.cliente.nome,
            "intervalo_segundos": agente.intervalo_segundos,
            "falhas_para_offline": agente.falhas_para_offline,
            "pausado": agente.pausado,
        },
        "equipamentos": [{
            "id": e.pk, "nome": e.nome, "categoria": e.categoria, "fabricante": e.fabricante,
            "driver": e.driver, "ip": e.ip, "porta_http": e.porta_http, "https": e.usar_https,
            "portas": e.lista_portas(), "usuario": e.usuario, "senha": e.senha,
            "links": [{"interface": l.interface, "nome": l.nome, "alvo_ping": l.alvo_ping,
                       "tabela_roteamento": l.tabela_roteamento} for l in e.links.all()],
        } for e in qs.distinct().prefetch_related("links")],
        "gerado_em": timezone.now().isoformat(),
    })


@csrf_exempt
@require_POST
@autenticar_agente
def relatorio(request):
    try:
        dados = _corpo_json(request)
    except ValueError as exc:
        return JsonResponse({"erro": f"JSON inválido: {exc}"}, status=400)
    n = processar_relatorio(request.agente, dados, _ip_origem(request))
    return JsonResponse({"ok": True, "processados": n, "comando": _entregar_comando(request.agente)})


def _entregar_comando(agente):
    """Entrega o comando pendente uma única vez (o update condicional evita
    entregar duas vezes se dois relatórios chegarem juntos)."""
    comando = Agente.objects.filter(pk=agente.pk).values_list("comando", flat=True).first()
    if not comando:
        return None
    entregue = Agente.objects.filter(pk=agente.pk, comando=comando).update(
        comando="", comando_entregue=comando, comando_entregue_em=timezone.now())
    if entregue:
        log.info("Comando %s entregue ao agente %s", comando, agente)
        return comando
    return None


@csrf_exempt
@require_POST
@autenticar_agente
def eventos(request):
    """Eventos empurrados pelos equipamentos e repassados pelo agente."""
    try:
        dados = _corpo_json(request)
    except ValueError as exc:
        return JsonResponse({"erro": f"JSON inválido: {exc}"}, status=400)
    eqs = {e.pk: e for e in Equipamento.objects.select_related("cliente")
           .filter(cliente=request.agente.cliente)}
    n = 0
    for ev in dados.get("eventos", []):
        eq = eqs.get(ev.get("equipamento_id"))
        if eq:
            registrar_evento_dispositivo(eq, ev)
            n += 1
    return JsonResponse({"ok": True, "registrados": n})
