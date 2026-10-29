"""Telas de cadastro (clientes, agentes, equipamentos e usuários), no lugar do
admin do Django. Só para usuários com "Pode editar cadastros" (is_staff)."""
import io
import zipfile
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.forms.models import model_to_dict
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .forms import AgenteForm, ClienteForm, EquipamentoForm, LinkWANFormSet, UsuarioForm, criar_lote
from .models import Agente, Cliente, Equipamento, Status, gerar_token

POR_PAGINA = 50


def staff(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------- utilidades
def _data(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y %H:%M") if dt else "—"


def _filtro(request, nome, rotulo, opcoes):
    return {"nome": nome, "rotulo": rotulo, "valor": request.GET.get(nome, ""),
            "opcoes": [(str(v), r) for v, r in opcoes]}


def _filtro_clientes(request):
    return _filtro(request, "cliente", "Cliente",
                   Cliente.objects.order_by("chave").values_list("pk", "chave"))


FILTRO_ATIVO = [("1", "Ativos"), ("0", "Inativos")]


def _aplicar_ativo(request, qs, campo="ativo"):
    valor = request.GET.get("ativo")
    if valor in ("0", "1"):
        qs = qs.filter(**{campo: valor == "1"})
    return qs


def _cliente_get(request):
    valor = request.GET.get("cliente", "")
    return int(valor) if valor.isdigit() else None


def _lista(request, *, aba, titulo, qs, busca, colunas, linha, filtros=(), novo_url, novo_rotulo,
           busca_dica="Buscar"):
    q = request.GET.get("q", "").strip()
    if q:
        cond = Q()
        for campo in busca:
            cond |= Q(**{f"{campo}__icontains": q})
        qs = qs.filter(cond)
    pagina = Paginator(qs, POR_PAGINA).get_page(request.GET.get("pagina"))
    params = request.GET.copy()
    params.pop("pagina", None)
    return render(request, "monitor/cadastros/lista.html", {
        "aba": aba, "titulo": titulo, "colunas": colunas, "linhas": [linha(o) for o in pagina],
        "pagina": pagina, "params": params.urlencode(), "q": q, "busca_dica": busca_dica,
        "filtros": filtros, "filtrado": bool(q or any(f["valor"] for f in filtros)),
        "novo_url": novo_url, "novo_rotulo": novo_rotulo,
    })


def _secoes(form, definicao):
    return [{"titulo": titulo, "campos": [form[n] for n in nomes], "classe": classe, "nota": nota}
            for titulo, nomes, classe, nota in definicao]


def _excluir(request, *, aba, objeto, tipo, voltar, consequencias=(), alternativa=""):
    if request.method == "POST":
        nome = str(objeto)
        objeto.delete()
        messages.success(request, f"{tipo.capitalize()} “{nome}” excluído.")
        return redirect(voltar)
    return render(request, "monitor/cadastros/excluir.html", {
        "aba": aba, "objeto": objeto, "tipo": tipo, "voltar": voltar,
        "consequencias": [c for c in consequencias if c], "alternativa": alternativa,
    })


def _qtd(n, singular, plural):
    return f"{n} {singular if n == 1 else plural}" if n else ""


@staff
def inicio(request):
    return redirect("cad_clientes")


# ---------------------------------------------------------------- clientes
SECOES_CLIENTE = [
    ("Identificação", ["chave", "nome", "cidade", "endereco"], "", ""),
    ("Contato", ["contato_nome", "contato_telefone"], "", ""),
    ("Operação", ["gold", "portaria_remota", "ativo", "observacoes"], "", ""),
]


@staff
def clientes(request):
    qs = _aplicar_ativo(request, Cliente.objects.annotate(
        n_equip=Count("equipamentos", distinct=True), n_agentes=Count("agentes", distinct=True))
        .order_by("chave"))

    def linha(c):
        return {"url": reverse("cad_cliente", args=[c.pk]), "inativo": not c.ativo, "celulas": [
            {"t": c.chave, "forte": True},
            {"t": c.nome, "tag": " · ".join(t for t, sim in [("GOLD", c.gold), ("Portaria remota", c.portaria_remota)] if sim)},
            {"t": c.cidade or "—"},
            {"t": c.n_agentes, "num": True},
            {"t": c.n_equip, "num": True},
            {"t": "Ativo" if c.ativo else "Inativo"},
        ]}

    return _lista(request, aba="clientes", titulo="Clientes", qs=qs, linha=linha,
                  busca=["nome", "chave", "cidade", "contato_nome"],
                  busca_dica="Nome, chave, cidade ou contato",
                  colunas=["Chave", "Nome", "Cidade", "Agentes", "Equipamentos", "Situação"],
                  filtros=[_filtro(request, "ativo", "Situação", FILTRO_ATIVO)],
                  novo_url=reverse("cad_cliente_novo"), novo_rotulo="Novo cliente")


@staff
def cliente_form(request, pk=None):
    obj = get_object_or_404(Cliente, pk=pk) if pk else None
    form = ClienteForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj_salvo = form.save()
            if not obj:
                # Todo cliente precisa de um agente; já fica pronto para baixar o instalador.
                Agente.objects.create(cliente=obj_salvo)
        if obj:
            messages.success(request, f"Cliente “{obj_salvo}” salvo.")
            return redirect("cad_cliente", pk=obj_salvo.pk)
        messages.success(request, f"Cliente “{obj_salvo}” criado, já com o agente. "
                                  "Agora cadastre os equipamentos dele.")
        return redirect(reverse("cad_cliente", args=[obj_salvo.pk]) + "#equipamentos")

    contexto = {"aba": "clientes", "form": form, "objeto": obj,
                "titulo": obj.nome if obj else "Novo cliente",
                "subtitulo": obj.chave if obj else "",
                "secoes": _secoes(form, SECOES_CLIENTE), "voltar": reverse("cad_clientes"),
                "rotulo_salvar": "Salvar dados do cliente" if obj else "Criar cliente e cadastrar equipamentos"}
    if obj:
        contexto["excluir_url"] = reverse("cad_cliente_excluir", args=[obj.pk])
        contexto["agentes"] = obj.agentes.order_by("nome")
        contexto["grupos"] = _equipamentos_por_categoria(obj)
        contexto["total_equip"] = sum(len(g["itens"]) for g in contexto["grupos"])
    return render(request, "monitor/cadastros/cliente_form.html", contexto)


def _equipamentos_por_categoria(cliente):
    grupos = {}
    for e in cliente.equipamentos.order_by("ordem", "nome"):
        grupos.setdefault(e.categoria, []).append(e)
    return [{"categoria": c, "rotulo": rotulo, "itens": grupos[c]}
            for c, rotulo in Equipamento.Categoria.choices if c in grupos]


def _links_iniciais(origem):
    campos = ["interface", "nome", "provedor", "download_mbps", "upload_mbps",
              "tabela_roteamento", "alvo_ping", "contato_operadora", "designacao"]
    return [model_to_dict(l, fields=campos) for l in origem.links.all()]


def _url_equipamentos(cliente_id):
    return reverse("cad_cliente", args=[cliente_id]) + "#equipamentos"


@staff
def cliente_excluir(request, pk):
    obj = get_object_or_404(Cliente, pk=pk)
    return _excluir(
        request, aba="clientes", objeto=obj, tipo="cliente", voltar=reverse("cad_clientes"),
        consequencias=[_qtd(obj.agentes.count(), "agente", "agentes"),
                       _qtd(obj.equipamentos.count(), "equipamento", "equipamentos"),
                       _qtd(obj.eventos.count(), "evento do histórico", "eventos do histórico")],
        alternativa="Para só parar de monitorar sem perder o histórico, desmarque “Ativo” no cadastro.",
    )


# ---------------------------------------------------------------- agentes
SECOES_AGENTE = [
    ("Agente", ["cliente", "nome", "ativo"], "", ""),
    ("Checagem", ["intervalo_segundos", "falhas_para_offline"], "", ""),
]


@staff
def agentes(request):
    qs = _aplicar_ativo(request, Agente.objects.select_related("cliente")
                        .order_by("cliente__chave", "nome"))
    if cid := _cliente_get(request):
        qs = qs.filter(cliente_id=cid)

    def linha(a):
        online = a.online
        return {"url": reverse("cad_agente", args=[a.pk]), "inativo": not a.ativo, "celulas": [
            {"t": a.nome, "forte": True},
            {"t": a.cliente.chave, "sub": a.cliente.nome},
            {"t": a.hostname or "—", "sub": a.ip_local or ""},
            {"t": a.versao or "—"},
            {"t": _data(a.ultimo_contato)},
            {"t": "Comunicando" if online else "Sem contato",
             "st": Status.ONLINE if online else (Status.OFFLINE if a.ultimo_contato else Status.DESCONHECIDO)},
        ]}

    return _lista(request, aba="agentes", titulo="Agentes", qs=qs, linha=linha,
                  busca=["nome", "hostname", "cliente__nome", "cliente__chave"],
                  busca_dica="Nome, hostname ou cliente",
                  colunas=["Agente", "Cliente", "Computador", "Versão", "Último contato", "Situação"],
                  filtros=[_filtro_clientes(request), _filtro(request, "ativo", "Situação", FILTRO_ATIVO)],
                  novo_url=reverse("cad_agente_novo"), novo_rotulo="Novo agente")


def _agente_ini(request, agente):
    return ("[plataforma]\n"
            f"url = {request.build_absolute_uri('/').rstrip('/')}\n"
            f"token = {agente.token}\n"
            "verificar_tls = sim\n")


@staff
def agente_form(request, pk=None):
    obj = get_object_or_404(Agente.objects.select_related("cliente"), pk=pk) if pk else None
    form = AgenteForm(request.POST or None, instance=obj,
                      initial={"cliente": _cliente_get(request)} if not obj else None)
    if request.method == "POST" and form.is_valid():
        obj_salvo = form.save()
        if obj:
            messages.success(request, f"Agente “{obj_salvo}” salvo.")
            return redirect("cad_agentes")
        messages.success(request, "Agente criado. Abra esta página no servidor do cliente e baixe o instalador.")
        return redirect("cad_agente", pk=obj_salvo.pk)

    contexto = {"aba": "agentes", "form": form, "objeto": obj,
                "titulo": obj.nome if obj else "Novo agente",
                "subtitulo": str(obj.cliente) if obj else "",
                "secoes": _secoes(form, SECOES_AGENTE), "voltar": reverse("cad_agentes")}
    if obj:
        contexto["excluir_url"] = reverse("cad_agente_excluir", args=[obj.pk])
        contexto["ini"] = _agente_ini(request, obj)
        contexto["situacao"] = [
            ("Situação", "Comunicando" if obj.online else "Sem contato"),
            ("Último contato", _data(obj.ultimo_contato)),
            ("Computador", obj.hostname or "—"),
            ("Sistema", obj.sistema or "—"),
            ("IP local", obj.ip_local or "—"),
            ("IP público", obj.ip_publico or "—"),
            ("Versão", obj.versao or "—"),
            ("Em execução desde", _data(obj.iniciado_em)),
        ]
        contexto["n_equip"] = obj.equipamentos.count()
    return render(request, "monitor/cadastros/agente_form.html", contexto)


# Arquivos do pacote Windows: (caminho na pasta do agente, caminho dentro do zip).
ARQUIVOS_PACOTE = [
    ("windows/instalar.bat", "instalar.bat"),
    ("windows/instalar.ps1", "instalar.ps1"),
    ("windows/desinstalar.bat", "desinstalar.bat"),
    ("windows/trocar_url.bat", "trocar_url.bat"),
    ("windows/trocar_url.ps1", "trocar_url.ps1"),
    ("windows/LEIA-ME.txt", "LEIA-ME.txt"),
    ("agente.py", "agente.py"),
    ("verificacoes.py", "verificacoes.py"),
    ("requirements.txt", "requirements.txt"),
]
TEXTO_WINDOWS = {".bat", ".ps1", ".txt", ".ini"}


def _crlf(dados):
    # O cmd.exe se perde em .bat com fim de linha Unix; o clone no Linux pode vir assim.
    return dados.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


@staff
def agente_pacote(request, pk):
    """Zip com o agente para Windows e o agente.ini deste agente já preenchido:
    no cliente é só extrair e rodar o instalar.bat."""
    agente = get_object_or_404(Agente.objects.select_related("cliente"), pk=pk)
    pasta = Path(settings.MONITOR["PASTA_AGENTE"])
    if not (pasta / "agente.py").exists():
        raise Http404(f"Código do agente não encontrado em {pasta}.")

    arquivos = list(ARQUIVOS_PACOTE)
    arquivos += [(f"drivers/{p.name}", f"drivers/{p.name}") for p in sorted((pasta / "drivers").glob("*.py"))]
    if (pasta / "windows" / "masterseg-agente.exe").exists():
        arquivos.append(("windows/masterseg-agente.exe", "masterseg-agente.exe"))

    nome = f"masterseg-agente-{slugify(agente.cliente.chave)}"
    ini = (f"; Gerado pela plataforma em {_data(timezone.now())} para {agente.nome} ({agente.cliente.chave}).\n"
           "; Guarde com cuidado: o token da acesso ao cadastro deste cliente.\n"
           + _agente_ini(request, agente))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pacote:
        for origem, destino in arquivos:
            dados = (pasta / origem).read_bytes()
            if Path(destino).suffix in TEXTO_WINDOWS:
                dados = _crlf(dados)
            pacote.writestr(f"{nome}/{destino}", dados)
        pacote.writestr(f"{nome}/agente.ini", _crlf(ini.encode("utf-8")))

    resposta = HttpResponse(buffer.getvalue(), content_type="application/zip")
    resposta["Content-Disposition"] = f'attachment; filename="{nome}.zip"'
    resposta["Cache-Control"] = "no-store"
    return resposta


@require_POST
@staff
def agente_token(request, pk):
    obj = get_object_or_404(Agente, pk=pk)
    obj.token = gerar_token()
    obj.save(update_fields=["token"])
    messages.warning(request, "Token novo gerado. O agente instalado para de funcionar até receber "
                              "o agente.ini novo (copie o bloco abaixo).")
    return redirect("cad_agente", pk=pk)


@staff
def agente_excluir(request, pk):
    obj = get_object_or_404(Agente, pk=pk)
    n = obj.equipamentos.count()
    return _excluir(
        request, aba="agentes", objeto=obj, tipo="agente", voltar=reverse("cad_agentes"),
        consequencias=["O agente instalado no cliente deixa de ser aceito pela plataforma.",
                       f"{_qtd(n, 'equipamento', 'equipamentos')} passam para o primeiro agente do cliente."
                       if n else ""],
        alternativa="Para só suspender, desmarque “Ativo” no cadastro do agente.",
    )


# ---------------------------------------------------------------- equipamentos
SECOES_EQUIP = [
    ("Identificação", ["cliente", "agente", "nome", "categoria", "fabricante", "modelo"], "", ""),
    ("Conexão", ["ip", "porta_http", "usar_https", "portas_extras", "driver", "usuario", "senha"], "", ""),
    ("Cadastro em lote", ["quantidade", "numero_inicial"], "lote-cameras",
     "Só para câmeras: cadastra várias de uma vez, com IPs em sequência."),
    ("Monitoramento", ["depende_de", "critico", "ativo", "ordem"], "", ""),
]


@staff
def equipamentos(request):
    """Os equipamentos ficam dentro de cada cliente; o endereço antigo leva para lá."""
    if cid := _cliente_get(request):
        return redirect(_url_equipamentos(cid))
    return redirect("cad_clientes")


@staff
def equipamento_form(request, pk=None):
    obj = get_object_or_404(Equipamento.objects.select_related("cliente"), pk=pk) if pk else None
    copiar = request.GET.get("copiar", "")
    origem = (Equipamento.objects.select_related("cliente").filter(pk=copiar).first()
              if not obj and copiar.isdigit() else None)
    cliente = (obj or origem).cliente if (obj or origem) else Cliente.objects.filter(
        pk=_cliente_get(request)).first()
    dados = request.POST or None
    if origem:
        # Mesma configuração do original; nome e IP ficam em branco para preencher.
        inicial = model_to_dict(origem, fields=[f for f in EquipamentoForm._meta.fields
                                                if f not in ("nome", "ip")])
    else:
        inicial = {"categoria": request.GET.get("categoria")} if request.GET.get("categoria") else {}
    form = EquipamentoForm(dados, instance=obj, cliente=cliente, initial=inicial or None, copiar_de=origem)
    links = LinkWANFormSet(dados, instance=obj or Equipamento(), prefix="links",
                           initial=_links_iniciais(origem) if origem and not dados else None)
    if origem and not dados:
        links.extra = len(links.initial_extra or [])

    if request.method == "POST" and form.is_valid() and links.is_valid():
        with transaction.atomic():
            obj_salvo = form.save()
            links.instance = obj_salvo
            links.save()
            criar_lote(obj_salvo, form.lote)
        if len(form.lote) > 1:
            messages.success(request, f"{len(form.lote)} equipamentos cadastrados: {form.lote[0][0]} "
                                      f"({form.lote[0][1]}) até {form.lote[-1][0]} ({form.lote[-1][1]}).")
        else:
            messages.success(request, f"Equipamento “{obj_salvo.nome}” salvo.")
        if "_outro" in request.POST:
            # Com "copiar", o próximo também sai com a mesma configuração.
            return redirect(reverse("cad_equipamento_novo") + (
                f"?copiar={origem.pk}" if origem else f"?cliente={obj_salvo.cliente_id}"))
        return redirect(_url_equipamentos(obj_salvo.cliente_id))

    definicao = [s for s in SECOES_EQUIP if not (obj and s[2] == "lote-cameras")]
    contexto = {"aba": "clientes", "form": form, "links": links, "objeto": obj, "cliente": cliente,
                "titulo": obj.nome if obj else "Novo equipamento",
                "subtitulo": f"{cliente.chave} · {cliente.nome}" if cliente else "",
                "aviso": (f"Copiando a configuração de “{origem.nome}” ({origem.ip}), inclusive usuário e "
                          "senha. Informe o nome e o IP do novo equipamento.") if origem else "",
                "secoes": _secoes(form, definicao), "salvar_outro": True,
                "tem_links": bool(obj and obj.links.exists()),
                "interfaces_roteador": (obj.detalhes or {}).get("interfaces") or [] if obj else [],
                "voltar": _url_equipamentos(cliente.pk) if cliente else reverse("cad_clientes"),
                "rotulo_voltar": f"Voltar para {cliente.nome}" if cliente else ""}
    if obj:
        contexto["excluir_url"] = reverse("cad_equipamento_excluir", args=[obj.pk])
        contexto["estado"] = [
            ("Status", obj.get_status_display()),
            ("Desde", _data(obj.status_desde)),
            ("Última checagem", _data(obj.ultimo_check)),
            ("Latência", f"{obj.latencia_ms:.0f} ms" if obj.latencia_ms is not None else "—"),
            ("Perda", f"{obj.perda_pct:.0f}%" if obj.perda_pct is not None else "—"),
            ("Firmware", obj.firmware or "—"),
            ("Serial", obj.serial or "—"),
            ("MAC", obj.mac or "—"),
        ]
    return render(request, "monitor/cadastros/equipamento_form.html", contexto)


@staff
def equipamento_excluir(request, pk):
    obj = get_object_or_404(Equipamento, pk=pk)
    n = obj.dependentes.count()
    return _excluir(
        request, aba="clientes", objeto=obj, tipo="equipamento",
        voltar=_url_equipamentos(obj.cliente_id),
        consequencias=["O histórico de medições dele é apagado; os eventos continuam no histórico do cliente.",
                       f"{_qtd(n, 'equipamento depende', 'equipamentos dependem')} dele e "
                       f"{'fica' if n == 1 else 'ficam'} sem dependência." if n else ""],
        alternativa="Para só parar de monitorar, desmarque “Ativo” no cadastro.",
    )


# ---------------------------------------------------------------- usuários
SECOES_USUARIO = [
    ("Acesso", ["username", "senha1", "senha2"], "", ""),
    ("Dados", ["first_name", "last_name", "email"], "", ""),
    ("Permissões", ["is_staff", "is_active"], "", ""),
]


@staff
def usuarios(request):
    qs = _aplicar_ativo(request, User.objects.order_by("username"), campo="is_active")

    def linha(u):
        return {"url": reverse("cad_usuario", args=[u.pk]), "inativo": not u.is_active, "celulas": [
            {"t": u.username, "forte": True},
            {"t": u.get_full_name() or "—"},
            {"t": u.email or "—"},
            {"t": "Edita cadastros" if u.is_staff else "Só monitoramento"},
            {"t": _data(u.last_login)},
            {"t": "Ativo" if u.is_active else "Bloqueado"},
        ]}

    return _lista(request, aba="usuarios", titulo="Usuários", qs=qs, linha=linha,
                  busca=["username", "first_name", "last_name", "email"],
                  busca_dica="Usuário, nome ou e-mail",
                  colunas=["Usuário", "Nome", "E-mail", "Acesso", "Último acesso", "Situação"],
                  filtros=[_filtro(request, "ativo", "Situação", FILTRO_ATIVO)],
                  novo_url=reverse("cad_usuario_novo"), novo_rotulo="Novo usuário")


@staff
def usuario_form(request, pk=None):
    obj = get_object_or_404(User, pk=pk) if pk else None
    form = UsuarioForm(request.POST or None, instance=obj)
    proprio = obj is not None and obj.pk == request.user.pk
    if proprio:
        # Ninguém tira o próprio acesso por engano.
        for campo in ("is_staff", "is_active"):
            form.fields[campo].disabled = True
            form.fields[campo].help_text = "Outro usuário com acesso aos cadastros pode alterar isto."
    if request.method == "POST" and form.is_valid():
        usuario = form.save()
        if proprio and form.cleaned_data.get("senha1"):
            update_session_auth_hash(request, usuario)  # não derruba a própria sessão
        messages.success(request, f"Usuário “{usuario.username}” salvo.")
        return redirect("cad_usuarios")

    contexto = {"aba": "usuarios", "form": form, "objeto": obj,
                "titulo": obj.username if obj else "Novo usuário",
                "secoes": _secoes(form, SECOES_USUARIO), "voltar": reverse("cad_usuarios")}
    if obj and not proprio:
        contexto["excluir_url"] = reverse("cad_usuario_excluir", args=[obj.pk])
    return render(request, "monitor/cadastros/form.html", contexto)


@staff
def usuario_excluir(request, pk):
    obj = get_object_or_404(User, pk=pk)
    if obj.pk == request.user.pk:
        messages.error(request, "Você não pode excluir o próprio usuário.")
        return redirect("cad_usuarios")
    return _excluir(request, aba="usuarios", objeto=obj, tipo="usuário", voltar=reverse("cad_usuarios"),
                    alternativa="Para só bloquear o acesso, desmarque “Ativo” no cadastro.")
