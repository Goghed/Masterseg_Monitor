from django.contrib import admin, messages
from django.utils.html import format_html

from .forms import EquipamentoForm, criar_lote
from .models import Agente, Cliente, Equipamento, Evento, LinkWAN


class AgenteInline(admin.TabularInline):
    model = Agente
    extra = 0
    fields = ("nome", "intervalo_segundos", "falhas_para_offline", "ativo", "ultimo_contato")
    readonly_fields = ("ultimo_contato",)


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("chave", "nome", "cidade", "gold", "portaria_remota", "ativo")
    list_filter = ("ativo", "gold", "portaria_remota", "cidade")
    search_fields = ("nome", "chave", "cidade")
    inlines = [AgenteInline]


@admin.register(Agente)
class AgenteAdmin(admin.ModelAdmin):
    list_display = ("nome", "cliente", "hostname", "versao", "ultimo_contato", "ativo")
    list_filter = ("ativo",)
    search_fields = ("nome", "cliente__nome", "cliente__chave", "hostname")
    readonly_fields = ("token", "config_ini", "hostname", "ip_local", "ip_publico",
                       "versao", "ultimo_contato")
    actions = ["gerar_novo_token"]

    @admin.action(description="Gerar novo token (o antigo para de funcionar)")
    def gerar_novo_token(self, request, queryset):
        from .models import gerar_token
        for agente in queryset:
            agente.token = gerar_token()
            agente.save(update_fields=["token"])
        self.message_user(
            request,
            f"Token novo gerado para {queryset.count()} agente(s). Abra cada um, copie o token "
            "e atualize o agente.ini no cliente (trocar_url.bat ou instalar.bat).",
            messages.WARNING,
        )

    @admin.display(description="Arquivo agente.ini")
    def config_ini(self, obj):
        if not obj.pk:
            return "Salve o agente para gerar o token."
        texto = (
            "[plataforma]\n"
            "url = https://SEU-DOMINIO\n"
            f"token = {obj.token}\n"
            "verificar_tls = sim\n"
        )
        return format_html(
            '<pre style="background:#0f1b2d;color:#e6edf7;padding:12px;border-radius:6px">{}</pre>'
            "<p>Copie para <code>/opt/masterseg-agente/agente.ini</code> no Raspberry.</p>",
            texto,
        )


class LinkWANInline(admin.TabularInline):
    model = LinkWAN
    extra = 0
    fields = ("interface", "nome", "provedor", "download_mbps", "upload_mbps",
              "tabela_roteamento", "alvo_ping", "contato_operadora", "designacao")
    verbose_name_plural = "Links de internet (opcional: sem cadastro os links são detectados sozinhos)"


@admin.register(Equipamento)
class EquipamentoAdmin(admin.ModelAdmin):
    form = EquipamentoForm
    inlines = [LinkWANInline]
    list_display = ("nome", "cliente", "categoria", "fabricante", "ip", "status", "ultimo_check")
    list_filter = ("categoria", "fabricante", "status", "cliente")
    search_fields = ("nome", "ip", "modelo", "serial", "cliente__nome", "cliente__chave")
    autocomplete_fields = ("cliente", "depende_de")
    readonly_fields = ("status", "status_desde", "ultimo_check", "latencia_ms", "perda_pct",
                       "firmware", "serial", "mac", "detalhes")
    fieldsets = (
        (None, {"fields": ("cliente", "agente", "nome", "categoria", "fabricante", "modelo",
                           "critico", "ativo", "ordem")}),
        ("Conexão", {"fields": ("ip", "porta_http", "usar_https", "portas_extras", "driver",
                                "usuario", "senha", "depende_de")}),
        ("Estado (preenchido pelo agente)", {"classes": ("collapse",), "fields": (
            "status", "status_desde", "ultimo_check", "latencia_ms", "perda_pct",
            "firmware", "serial", "mac", "detalhes")}),
    )

    class Media:
        js = ("monitor/admin_lote.js",)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None:
            lote = ("Cadastro em lote", {"classes": ("lote-cameras",),
                                         "fields": ("quantidade", "numero_inicial")})
            fieldsets = (*fieldsets[:2], lote, *fieldsets[2:])
        return fieldsets

    def save_model(self, request, obj, form, change):
        lote = getattr(form, "lote", [])
        if change or len(lote) <= 1:
            return super().save_model(request, obj, form, change)

        super().save_model(request, obj, form, change)
        criar_lote(obj, lote)
        self.message_user(
            request,
            f"{len(lote)} equipamentos cadastrados: {lote[0][0]} ({lote[0][1]}) "
            f"até {lote[-1][0]} ({lote[-1][1]}).",
            messages.SUCCESS,
        )


@admin.register(Evento)
class EventoAdmin(admin.ModelAdmin):
    list_display = ("criado_em", "cliente", "titulo", "severidade", "decisao",
                    "prioridade", "analisado_por", "resolvido_em")
    list_filter = ("severidade", "decisao", "tipo", "analisado_por")
    search_fields = ("titulo", "detalhe", "cliente__nome", "cliente__chave")
    date_hierarchy = "criado_em"
    actions = ["reanalisar"]
    # A triagem preenche estes campos sozinha: na tela só aparecem para leitura.
    readonly_fields = ("cliente", "equipamento", "tipo", "severidade", "titulo", "detalhe", "origem",
                       "referencia", "criado_em", "ultima_ocorrencia", "ocorrencias",
                       "decisao", "prioridade", "justificativa",
                       "analisado_por", "analisado_em", "aguarda_ia", "tecnico_acionado_em")
    fieldsets = (
        ("Evento", {"fields": ("cliente", "equipamento", "tipo", "severidade", "titulo", "detalhe",
                               "origem", "referencia", "criado_em", "ultima_ocorrencia",
                               "ocorrencias", "resolvido_em")}),
        ("Triagem (preenchida pelas regras ou pela IA)", {"fields": (
            "decisao", "prioridade", "justificativa", "analisado_por", "analisado_em",
            "aguarda_ia", "tecnico_acionado_em")}),
    )

    def has_add_permission(self, request):
        return False  # eventos nascem dos relatórios do agente

    @admin.action(description="Analisar de novo (regras + IA)")
    def reanalisar(self, request, queryset):
        from django.conf import settings
        from .triagem import analisar
        feitos = 0
        for evento in queryset.select_related("cliente", "equipamento")[:20]:
            if evento.e_problema:
                analisar(evento)
                feitos += 1
        com_ia = "com a IA" if settings.TRIAGEM["ANTHROPIC_API_KEY"] else "só com regras (ANTHROPIC_API_KEY não configurada)"
        self.message_user(request, f"{feitos} evento(s) analisado(s) {com_ia}.")
