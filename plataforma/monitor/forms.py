"""Formulários dos cadastros (telas próprias e admin do Django)."""
import ipaddress

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q

from .models import Agente, Cliente, Equipamento, LinkWAN


class SelectPorCliente(forms.Select):
    """Select cujas opções levam o cliente em data-cliente: o JS mostra só as
    do cliente escolhido no formulário."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.clientes = {}
        self.attrs["data-por-cliente"] = ""

    def create_option(self, name, value, *args, **kwargs):
        opcao = super().create_option(name, value, *args, **kwargs)
        cliente = self.clientes.get(str(value))
        if cliente:
            opcao["attrs"]["data-cliente"] = cliente
        return opcao


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ["chave", "nome", "cidade", "endereco", "contato_nome", "contato_telefone",
                  "portaria_remota", "ativo", "observacoes"]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 4})}


class AgenteForm(forms.ModelForm):
    class Meta:
        model = Agente
        fields = ["cliente", "nome", "intervalo_segundos", "falhas_para_offline", "ativo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cliente"].queryset = Cliente.objects.order_by("chave")


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
        fields = ["cliente", "agente", "nome", "categoria", "fabricante", "modelo", "critico",
                  "ativo", "ordem", "ip", "porta_http", "usar_https", "portas_extras", "driver",
                  "usuario", "depende_de"]
        widgets = {"agente": SelectPorCliente, "depende_de": SelectPorCliente}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lote = []
        if "cliente" in self.fields:
            self.fields["cliente"].queryset = Cliente.objects.order_by("chave")
        if "agente" in self.fields:
            agentes = Agente.objects.select_related("cliente").order_by("cliente__chave", "nome")
            self.fields["agente"].queryset = agentes
            self.fields["agente"].widget.clientes = {str(a.pk): str(a.cliente_id) for a in agentes}
        if "depende_de" in self.fields:
            # Câmera raramente é "pai" de alguém: fora da lista ela só pesaria no select.
            pais = Equipamento.objects.filter(~Q(categoria=Equipamento.Categoria.CAMERA)
                                              | Q(pk=self.instance.depende_de_id))
            if self.instance.pk:
                pais = pais.exclude(pk=self.instance.pk)
            pais = pais.order_by("nome")
            self.fields["depende_de"].queryset = pais
            self.fields["depende_de"].widget.clientes = {
                str(pk): str(cid) for pk, cid in pais.values_list("pk", "cliente_id")}

    def clean(self):
        dados = super().clean()
        cliente = dados.get("cliente")
        if cliente:
            for campo in ("agente", "depende_de"):
                rel = dados.get(campo)
                if rel and rel.cliente_id != cliente.pk:
                    self.add_error(campo, "Escolha um item do mesmo cliente.")

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
        if len(self.lote) > 1:
            obj.nome, obj.ip = self.lote[0]
        if commit:
            obj.save()
        return obj


def criar_lote(obj, lote):
    """Cria as câmeras 2..N do lote copiando a primeira (já salva)."""
    for nome, ip in lote[1:]:
        copia = Equipamento.objects.get(pk=obj.pk)
        copia.pk = None
        copia._state.adding = True
        copia.nome, copia.ip = nome, ip
        copia.save()


LinkWANFormSet = forms.inlineformset_factory(
    Equipamento, LinkWAN, extra=0, can_delete=True,
    fields=["interface", "nome", "provedor", "download_mbps", "upload_mbps",
            "tabela_roteamento", "alvo_ping", "contato_operadora", "designacao"],
)


class UsuarioForm(forms.ModelForm):
    senha1 = forms.CharField(
        label="Senha", required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    senha2 = forms.CharField(
        label="Confirmar senha", required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_staff", "is_active"]
        labels = {"is_staff": "Pode editar cadastros", "is_active": "Ativo"}
        help_texts = {
            "username": "Usado para entrar. Letras, números e @ . + - _",
            "is_staff": "Sem isso o usuário só acompanha o monitoramento.",
            "is_active": "Desmarque para bloquear o acesso sem apagar o usuário.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["senha1"].help_text = "Deixe em branco para manter a atual."
        else:
            self.fields["senha1"].required = True
            self.fields["senha2"].required = True

    def clean(self):
        dados = super().clean()
        s1, s2 = dados.get("senha1"), dados.get("senha2")
        if s1 or s2:
            if s1 != s2:
                self.add_error("senha2", "As senhas não conferem.")
            else:
                try:
                    validate_password(s1, self.instance)
                except forms.ValidationError as erro:
                    self.add_error("senha1", erro)
        return dados

    def save(self, commit=True):
        usuario = super().save(commit=False)
        if self.cleaned_data.get("senha1"):
            usuario.set_password(self.cleaned_data["senha1"])
        if commit:
            usuario.save()
        return usuario
