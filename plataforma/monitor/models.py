import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from .cripto import cifrar, decifrar


class Status(models.TextChoices):
    ONLINE = "online", "Online"
    DEGRADADO = "degradado", "Degradado"
    OFFLINE = "offline", "Offline"
    DESCONHECIDO = "desconhecido", "Sem dados"


# Ordem de gravidade usada para calcular o "pior status" de um cliente.
GRAVIDADE_STATUS = {
    Status.ONLINE: 0,
    Status.DESCONHECIDO: 1,
    Status.DEGRADADO: 2,
    Status.OFFLINE: 3,
}


class Cliente(models.Model):
    nome = models.CharField(max_length=150)
    chave = models.CharField(
        "chave de monitoramento", max_length=30, unique=True,
        help_text="Código curto que identifica o cliente na busca (ex.: MS-0001).",
    )
    cidade = models.CharField(max_length=80, blank=True)
    endereco = models.CharField("endereço", max_length=200, blank=True)
    contato_nome = models.CharField("contato", max_length=100, blank=True)
    contato_telefone = models.CharField("telefone", max_length=30, blank=True)
    portaria_remota = models.BooleanField(default=False)
    gold = models.BooleanField("cliente GOLD", default=False,
                               help_text="Mostra a medalha dourada ao lado do nome no painel.")
    observacoes = models.TextField("observações", blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nome"]

    def __str__(self):
        return f"{self.chave} · {self.nome}"


def gerar_token():
    return secrets.token_urlsafe(32)


class Agente(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="agentes")
    nome = models.CharField(max_length=80, default="Agente principal")
    token = models.CharField(max_length=64, unique=True, default=gerar_token, editable=False)
    intervalo_segundos = models.PositiveIntegerField(
        "intervalo de checagem (s)", default=30,
    )
    falhas_para_offline = models.PositiveSmallIntegerField(
        default=3, help_text="Falhas seguidas antes de declarar um equipamento offline.",
    )
    hostname = models.CharField(max_length=100, blank=True)
    ip_local = models.GenericIPAddressField(null=True, blank=True)
    ip_publico = models.GenericIPAddressField(null=True, blank=True)
    versao = models.CharField(max_length=20, blank=True)
    ultimo_contato = models.DateTimeField(null=True, blank=True)
    marcado_offline = models.BooleanField(default=False, editable=False)
    ativo = models.BooleanField(default=True)

    # Informado pelo próprio agente a cada relatório.
    sistema = models.CharField(max_length=100, blank=True)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    pausado = models.BooleanField(default=False)

    # Controle remoto: o comando fica pendente até o próximo relatório do agente,
    # que o recebe na resposta (a conexão continua sendo só de dentro para fora).
    class Comando(models.TextChoices):
        REINICIAR = "reiniciar", "Reiniciar"
        PAUSAR = "pausar", "Pausar"
        RETOMAR = "retomar", "Retomar"
        RECARREGAR = "recarregar", "Recarregar cadastro"

    comando = models.CharField(max_length=20, choices=Comando.choices, blank=True)
    comando_pedido_em = models.DateTimeField(null=True, blank=True)
    comando_pedido_por = models.CharField(max_length=150, blank=True)
    comando_entregue = models.CharField(max_length=20, choices=Comando.choices, blank=True)
    comando_entregue_em = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.nome} ({self.cliente.chave})"

    @property
    def limite_silencio(self):
        tolerancia = settings.MONITOR["AGENTE_TOLERANCIA_INTERVALOS"]
        return timedelta(seconds=max(self.intervalo_segundos, 10) * tolerancia)

    @property
    def online(self):
        if not self.ultimo_contato:
            return False
        return timezone.now() - self.ultimo_contato <= self.limite_silencio


class Equipamento(models.Model):
    class Categoria(models.TextChoices):
        GATEWAY = "gateway", "Gateway / roteador"
        SERVIDOR = "servidor", "Servidor"
        GRAVADOR = "gravador", "DVR / NVR"
        CAMERA = "camera", "Câmera"
        SWITCH = "switch", "Switch gerenciável"
        ALARME = "alarme", "Central de alarme"
        ACESSO = "acesso", "Controladora de acesso"
        FACIAL = "facial", "Leitor facial"
        INTERFONE = "interfone", "Interfone / porteiro"
        PABX = "pabx", "Central telefônica (PABX)"
        AUDIO = "audio", "Corneta / caixa de som IP"
        AUTOMACAO = "automacao", "Módulo de automação (I/O)"
        OUTRO = "outro", "Outro"

    class Fabricante(models.TextChoices):
        HIKVISION = "hikvision", "Hikvision"
        DAHUA = "dahua", "Dahua"
        INTELBRAS = "intelbras", "Intelbras"
        MIKROTIK = "mikrotik", "MikroTik"
        OUTRO = "outro", "Outro"

    class Driver(models.TextChoices):
        AUTO = "auto", "Detectar automaticamente"
        HIKVISION = "hikvision_isapi", "Hikvision ISAPI"
        DAHUA = "dahua_cgi", "Dahua / Intelbras CGI"
        MIKROTIK = "mikrotik", "MikroTik (REST / API)"
        GENERICO = "generico", "Genérico (ping e portas)"

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="equipamentos")
    agente = models.ForeignKey(
        Agente, on_delete=models.SET_NULL, null=True, blank=True, related_name="equipamentos",
        help_text="Agente que testa este equipamento. Vazio = primeiro agente do cliente.",
    )
    nome = models.CharField(max_length=100)
    categoria = models.CharField(max_length=20, choices=Categoria.choices)
    fabricante = models.CharField(max_length=20, choices=Fabricante.choices, default=Fabricante.OUTRO)
    modelo = models.CharField(max_length=80, blank=True)
    driver = models.CharField(max_length=20, choices=Driver.choices, default=Driver.AUTO)
    ip = models.CharField("IP / host", max_length=100)
    porta_http = models.PositiveIntegerField(default=80)
    usar_https = models.BooleanField(default=False)
    portas_extras = models.CharField(
        max_length=100, blank=True,
        help_text="Portas TCP adicionais a testar, separadas por vírgula (ex.: 554,8000).",
    )
    usuario = models.CharField(max_length=60, blank=True)
    senha_cifrada = models.TextField(blank=True, editable=False)
    depende_de = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="dependentes",
        help_text="Equipamento do qual este depende (ex.: switch onde a câmera está ligada).",
    )
    critico = models.BooleanField(
        default=False, help_text="Queda deste equipamento sempre gera prioridade alta.",
    )
    ativo = models.BooleanField(default=True)
    ordem = models.PositiveIntegerField(default=0)
    canais_em_uso = models.PositiveSmallIntegerField(
        "canais em uso", null=True, blank=True,
        help_text="DVR/NVR e centrais: quantos canais conferir (os primeiros). "
                  "Vazio = todos; 0 = não mostrar canais.",
    )

    # Estado atual (preenchido pelos relatórios do agente)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.DESCONHECIDO)
    status_desde = models.DateTimeField(null=True, blank=True)
    ultimo_check = models.DateTimeField(null=True, blank=True)
    latencia_ms = models.FloatField(null=True, blank=True)
    perda_pct = models.FloatField(null=True, blank=True)
    firmware = models.CharField(max_length=80, blank=True)
    serial = models.CharField(max_length=80, blank=True)
    mac = models.CharField(max_length=30, blank=True)
    detalhes = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["ordem", "nome"]
        indexes = [models.Index(fields=["cliente", "status"])]

    def __str__(self):
        return f"{self.nome} ({self.ip})"

    @property
    def senha(self):
        return decifrar(self.senha_cifrada)

    @senha.setter
    def senha(self, valor):
        self.senha_cifrada = cifrar(valor)

    def lista_portas(self):
        portas = []
        for parte in self.portas_extras.split(","):
            parte = parte.strip()
            if parte.isdigit():
                portas.append(int(parte))
        return portas


class LinkWAN(models.Model):
    """Dados contratados de um link de internet. Opcional: sem cadastro o agente
    detecta os links pelas rotas padrão do roteador."""
    equipamento = models.ForeignKey(Equipamento, on_delete=models.CASCADE, related_name="links")
    interface = models.CharField(
        max_length=50, help_text="Nome da interface no roteador (ex.: ether1, pppoe-out1). Com PPPoE, pode ser a porta ou o PPPoE.",
    )
    nome = models.CharField(max_length=60, blank=True, help_text="Como aparece na tela (ex.: Vivo Fibra).")
    provedor = models.CharField(max_length=60, blank=True)
    download_mbps = models.PositiveIntegerField("download contratado (Mbps)", null=True, blank=True)
    upload_mbps = models.PositiveIntegerField("upload contratado (Mbps)", null=True, blank=True)
    alvo_ping = models.CharField(max_length=60, blank=True, help_text="Padrão: 8.8.8.8")
    tabela_roteamento = models.CharField(
        max_length=60, blank=True,
        help_text="Tabela de rotas que sai por este link. Com ela o agente testa a internet "
                  "de verdade por este link, e não só o gateway do provedor.",
    )
    contato_operadora = models.CharField("suporte da operadora", max_length=100, blank=True)
    designacao = models.CharField("designação / contrato", max_length=60, blank=True)

    class Meta:
        verbose_name = "link de internet"
        verbose_name_plural = "links de internet"
        unique_together = [("equipamento", "interface")]

    def __str__(self):
        return self.nome or self.interface


class MetricaLink(models.Model):
    equipamento = models.ForeignKey(Equipamento, on_delete=models.CASCADE, related_name="metricas_link")
    interface = models.CharField(max_length=50)
    ts = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(max_length=15)
    principal = models.BooleanField(default=False)
    rx_bps = models.BigIntegerField(null=True)
    tx_bps = models.BigIntegerField(null=True)
    latencia_ms = models.FloatField(null=True)
    perda_pct = models.FloatField(null=True)

    class Meta:
        indexes = [models.Index(fields=["equipamento", "interface", "ts"])]


class Metrica(models.Model):
    equipamento = models.ForeignKey(Equipamento, on_delete=models.CASCADE, related_name="metricas")
    ts = models.DateTimeField(default=timezone.now, db_index=True)
    online = models.BooleanField()
    latencia_ms = models.FloatField(null=True)
    perda_pct = models.FloatField(null=True)

    class Meta:
        indexes = [models.Index(fields=["equipamento", "ts"])]


class Evento(models.Model):
    class Tipo(models.TextChoices):
        EQUIP_OFFLINE = "equipamento_offline", "Equipamento offline"
        EQUIP_ONLINE = "equipamento_online", "Equipamento voltou"
        DEGRADADO = "degradado", "Conexão degradada"
        SERVICO_PARADO = "servico_parado", "Serviço do equipamento parou"
        LINK_OFFLINE = "link_offline", "Link de internet caiu"
        LINK_ONLINE = "link_online", "Link de internet voltou"
        LINK_INSTAVEL = "link_instavel", "Link de internet instável"
        LINK_SATURADO = "link_saturado", "Link de internet saturado"
        FAILOVER = "failover", "Failover para link reserva"
        FAILOVER_RETORNO = "failover_retorno", "Voltou para o link principal"
        CANAL_OFFLINE = "canal_offline", "Câmera sem sinal no gravador"
        HDD_ERRO = "hdd_erro", "Falha de HD"
        VIDEO_PERDIDO = "video_perdido", "Perda de vídeo"
        CONFLITO_IP = "conflito_ip", "Conflito de IP"
        AGENTE_OFFLINE = "agente_offline", "Agente sem contato"
        AGENTE_ONLINE = "agente_online", "Agente voltou"
        FALHA_GERAL = "falha_geral", "Queda geral no local"
        ALARME_DISPARO = "alarme_disparo", "Central de alarme disparou"
        ALARME_ESTADO = "alarme_estado", "Central armada / desarmada"
        OUTRO = "outro", "Outro"

    class Severidade(models.TextChoices):
        INFO = "info", "Informativo"
        AVISO = "aviso", "Aviso"
        CRITICO = "critico", "Crítico"

    class Decisao(models.TextChoices):
        PENDENTE = "pendente", "Em análise"
        CHAMAR_TECNICO = "chamar_tecnico", "Chamar técnico"
        ACAO_REMOTA = "acao_remota", "Ação remota"
        AVISAR_CLIENTE = "avisar_cliente", "Avisar cliente"
        ACIONAR_OPERADORA = "acionar_operadora", "Abrir chamado na operadora"
        AGUARDAR = "aguardar", "Aguardar"
        NENHUMA = "nenhuma", "Sem ação"

    TIPOS_RECUPERACAO = {Tipo.EQUIP_ONLINE, Tipo.AGENTE_ONLINE, Tipo.LINK_ONLINE, Tipo.FAILOVER_RETORNO}
    # Só registro no histórico: não são problema nem pedem decisão.
    TIPOS_INFORMATIVOS = {Tipo.ALARME_ESTADO}
    # Falhas do próprio equipamento, da mais grave para a mais leve. Viram um
    # incidente só: oscilar entre elas não abre evento novo.
    FALHAS_EQUIPAMENTO = (Tipo.EQUIP_OFFLINE, Tipo.SERVICO_PARADO, Tipo.DEGRADADO)

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="eventos")
    equipamento = models.ForeignKey(
        Equipamento, on_delete=models.SET_NULL, null=True, blank=True, related_name="eventos",
    )
    tipo = models.CharField(max_length=30, choices=Tipo.choices)
    severidade = models.CharField(max_length=10, choices=Severidade.choices, default=Severidade.AVISO)
    titulo = models.CharField(max_length=200)
    detalhe = models.TextField(blank=True)
    origem = models.CharField(max_length=20, default="agente")
    referencia = models.CharField(
        max_length=60, blank=True, db_index=True,
        help_text="Parte do equipamento a que o evento se refere (ex.: link:ether1).",
    )
    criado_em = models.DateTimeField(default=timezone.now, db_index=True)
    resolvido_em = models.DateTimeField(null=True, blank=True)
    ocorrencias = models.PositiveIntegerField(
        default=1, help_text="Quantas vezes o problema se repetiu dentro deste mesmo incidente.",
    )
    ultima_ocorrencia = models.DateTimeField(default=timezone.now, db_index=True)

    decisao = models.CharField(max_length=20, choices=Decisao.choices, default=Decisao.PENDENTE)
    prioridade = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1 = mais urgente")
    justificativa = models.TextField(blank=True)
    analisado_por = models.CharField(max_length=20, blank=True)  # "regra" ou "ia"
    analisado_em = models.DateTimeField(null=True, blank=True)
    aguarda_ia = models.BooleanField(
        default=False, db_index=True,
        help_text="As regras já decidiram; falta a revisão da IA (modo painel).",
    )
    tecnico_acionado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-ultima_ocorrencia"]
        indexes = [
            models.Index(fields=["cliente", "-criado_em"]),
            models.Index(fields=["tipo", "criado_em"]),
        ]

    def __str__(self):
        return self.titulo

    @property
    def e_problema(self):
        return self.tipo not in self.TIPOS_RECUPERACAO | self.TIPOS_INFORMATIVOS
