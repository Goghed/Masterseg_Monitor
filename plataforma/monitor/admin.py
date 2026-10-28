import ipaddress

from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Agente, Cliente, Equipamento, Evento, LinkWAN


class EquipamentoForm(forms.ModelForm):
    # autocomplete="new-password": sem isso o navegador preenche a senha salva do
    # site (de outro equipamento) e ela sobrescreve a deste ao salvar.
    senha = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Fica cifrada no banco. Deixe em branco para manter a atual.",
    )
    quantidade = forms.IntegerField(
        required=False, min_value=1, max_value=254, initial=1,
        help_text="Quantas câmeras cadastrar. O IP acima é o da primeira; as seguintes "
                  "recebem os IPs em sequência (último octeto +1) e a mesma configuração.",
    )
    numero_inicial = forms.IntegerField(
        required=False, min_value=0, initial=1,
        help_text="Numeração acrescentada ao nome (ex.: “Câmera” vira “Câmera 01”, “Câmera 02”…).",
    )

    class Meta:
        model = Equipamento
        fields = "__all__"

    def clean(self):
        dados = super().clean()
        self.lote = []
        qtd = dados.get("quantidade") or 1
        if self.instance.pk or qtd <= 1:
            return dados
        if dados.get("categoria") != Equipamento.Categoria.CAMERA:
            raise forms.ValidationError("Cadastro em lote está disponível só para câmeras.")
        try:
            ip = ipaddress.IPv4Address((dados.get("ip") or "").strip())
        except ValueError:
            self.add_error("ip", "Para cadastro em lote informe um IPv4 (ex.: 192.168.1.101).")
            return dados
        ultimo_octeto = int(ip) & 0xFF
        if ultimo_octeto + qtd - 1 > 254:
            self.add_error("quantidade",
                           f"A partir de {ip} cabem no máximo {255 - ultimo_octeto} IPs na faixa.")
            return dados

        ips = [str(ip + i) for i in range(qtd)]
        cliente = dados.get("cliente")
        if cliente:
            ocupados = list(Equipamento.objects.filter(cliente=cliente, ip__in=ips)
                            .values_list("ip", flat=True))
            if ocupados:
                self.add_error("ip", "Já existem equipamentos deste cliente nos IPs: "
                                     + ", ".join(sorted(ocupados, key=ipaddress.IPv4Address)))
                return dados

        inicio = dados.get("numero_inicial")
        inicio = 1 if inicio is None else inicio
        largura = max(2, len(str(inicio + qtd - 1)))
        nome = dados.get("nome", "").strip()
        # "C-" vira "C-01"; "Câmera" vira "Câmera 01".
        sep = "" if nome[-1:] in "-_." else " "
        self.lote = [(f"{nome}{sep}{inicio + i:0{largura}d}", ips[i]) for i in range(qtd)]
        return dados

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

        obj.nome, obj.ip = lote[0]
        super().save_model(request, obj, form, change)
        for nome, ip in lote[1:]:
            copia = Equipamento.objects.get(pk=obj.pk)
            copia.pk = None
            copia._state.adding = True
            copia.nome, copia.ip = nome, ip
            copia.save()
        self.message_user(
            request,
            f"{len(lote)} câmeras cadastradas: {lote[0][0]} ({lote[0][1]}) "
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
