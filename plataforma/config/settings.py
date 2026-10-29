"""Configurações da plataforma MasterSeg Monitor.

Tudo que muda entre ambientes vem de variáveis de ambiente (veja .env.exemplo).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _carregar_env(caminho):
    """Lê um arquivo .env simples (CHAVE=valor). Variáveis já definidas no
    ambiente têm prioridade. Sem dependência externa."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


_carregar_env(BASE_DIR / ".env")


def env(nome, padrao=None):
    return os.environ.get(nome, padrao)


def env_bool(nome, padrao=False):
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in ("1", "true", "sim", "yes", "on")


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-inseguro-troque-em-producao")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "monitor",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Banco: SQLite para desenvolvimento; em produção defina DB_ENGINE=postgresql.
if env("DB_ENGINE", "sqlite") == "postgresql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "monitor"),
            "USER": env("DB_USER", "monitor"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"monitor": {"handlers": ["console"], "level": "INFO"}},
}

# ---------------------------------------------------------------------------
# Monitoramento
# ---------------------------------------------------------------------------
MONITOR = {
    # Chave Fernet para cifrar as senhas dos equipamentos. Se vazia, é derivada
    # do SECRET_KEY (troque o SECRET_KEY e as senhas antigas deixam de abrir).
    "CHAVE_CRIPTO": env("MONITOR_CHAVE_CRIPTO", ""),
    # Latência acima disso (ms) ou perda acima disso (%) => status "degradado".
    "LATENCIA_DEGRADADA_MS": int(env("MONITOR_LATENCIA_DEGRADADA_MS", "300")),
    "PERDA_DEGRADADA_PCT": float(env("MONITOR_PERDA_DEGRADADA_PCT", "20")),
    # Quantos intervalos sem notícia até considerar o agente offline.
    "AGENTE_TOLERANCIA_INTERVALOS": int(env("MONITOR_AGENTE_TOLERANCIA", "4")),
    # Dias de histórico de métricas mantidos.
    "RETENCAO_METRICAS_DIAS": int(env("MONITOR_RETENCAO_DIAS", "30")),
    # Equipamento que cai de novo até X min depois de voltar reabre o mesmo incidente.
    "JANELA_REABERTURA_MIN": int(env("MONITOR_JANELA_REABERTURA_MIN", "30")),
    # A partir de quantas falhas no mesmo incidente ele é tratado como intermitente.
    "INTERMITENTE_FALHAS": int(env("MONITOR_INTERMITENTE_FALHAS", "3")),
}

TRIAGEM = {
    # Sem chave, a triagem roda só com regras.
    "ANTHROPIC_API_KEY": env("ANTHROPIC_API_KEY", ""),
    "MODELO": env("TRIAGEM_MODELO", "claude-sonnet-5-5"),
    # "thread": IA em segundo plano (servidor próprio).
    # "painel": regras na hora e revisão da IA feita pelo painel aberto ou pelo
    # comando processar_triagem (PythonAnywhere, que não executa threads).
    "MODO": env("TRIAGEM_MODO", "thread"),
    # Modo sombra: a IA decide e registra, mas ninguém é acionado.
    "ACIONAMENTO_AUTOMATICO": env_bool("TRIAGEM_ACIONAMENTO_AUTOMATICO", False),
    # Webhook que recebe o chamado (n8n, WhatsApp API, script do VitalPBX...).
    "WEBHOOK_ACIONAMENTO": env("TRIAGEM_WEBHOOK", ""),
}

# Produção atrás do Nginx com HTTPS
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
