from django import forms
from django.contrib import admin
from django.utils.html import format_html

from .models import Agente, Cliente, Equipamento, Evento, LinkWAN


class EquipamentoForm(forms.ModelForm):
    senha = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Fica cifrada no banco. Deixe em branco para manter a atual.",
    )

    class Meta:
        model = Equipamento
        fields = "__all__"

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("senha"):
            obj.senha = self.cleaned_data["senha"]
        if commit:
            obj.save()
        return obj


class AgenteInline(admin.TabularInline):
    model = Agente
    extra = 0
    fields = ("nome", "intervalo_segundos", "falhas_para_offline", "ativo", "ultimo_contato")
    readonly_fields = ("ultimo_contato",)


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("chave", "nome", "cidade", "portaria_remota", "ativo")
    list_filter = ("ativo", "portaria_remota", "cidade")
    search_fields = ("nome", "chave", "cidade")
    inlines = [AgenteInline]


@admin.register(Agente)
class AgenteAdmin(admin.ModelAdmin):
    list_display = ("nome", "cliente", "hostname", "versao", "ultimo_contato", "ativo")
    list_filter = ("ativo",)
    search_fields = ("nome", "cliente__nome", "cliente__chave", "hostname")
    readonly_fields = ("token", "config_ini", "hostname", "ip_local", "ip_publico",
                       "versao", "ultimo_contato")

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
                       "referencia", "criado_em", "decisao", "prioridade", "justificativa",
                       "analisado_por", "analisado_em", "aguarda_ia", "tecnico_acionado_em")
    fieldsets = (
        ("Evento", {"fields": ("cliente", "equipamento", "tipo", "severidade", "titulo", "detalhe",
                               "origem", "referencia", "criado_em", "resolvido_em")}),
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
