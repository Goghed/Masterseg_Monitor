"""Cifra das senhas dos equipamentos (Fernet / AES-128 + HMAC)."""
import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


@lru_cache(maxsize=1)
def _fernet():
    chave = settings.MONITOR.get("CHAVE_CRIPTO")
    if not chave:
        bruto = hashlib.sha256((settings.SECRET_KEY + ":monitor-senhas").encode()).digest()
        chave = base64.urlsafe_b64encode(bruto)
    return Fernet(chave)


def cifrar(texto: str) -> str:
    if not texto:
        return ""
    return _fernet().encrypt(texto.encode()).decode()


def decifrar(cifrado: str) -> str:
    if not cifrado:
        return ""
    try:
        return _fernet().decrypt(cifrado.encode()).decode()
    except InvalidToken:
        return ""
