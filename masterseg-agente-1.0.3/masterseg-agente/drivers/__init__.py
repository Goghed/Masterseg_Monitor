"""Registro de drivers e detecção automática."""
from .dahua import DahuaCGI
from .generico import Generico
from .hikvision import HikvisionISAPI
from .mikrotik import MikroTik

DRIVERS = {d.nome: d for d in (HikvisionISAPI, DahuaCGI, MikroTik, Generico)}

# Ordem da detecção automática por fabricante cadastrado.
ORDEM_DETECCAO = {
    "hikvision": [HikvisionISAPI, DahuaCGI],
    "dahua": [DahuaCGI, HikvisionISAPI],
    "intelbras": [DahuaCGI, HikvisionISAPI],
}
CATEGORIAS_SEM_API = {"gateway", "servidor", "outro"}


def escolher_driver(equip):
    """Devolve a CLASSE do driver para o equipamento."""
    nome = equip.get("driver") or "auto"
    if nome in DRIVERS:
        return DRIVERS[nome]
    if MikroTik.reconhece(equip):
        return MikroTik  # MikroTik com usuário cadastrado: sempre pela API dele
    if equip.get("categoria") in CATEGORIAS_SEM_API or not equip.get("usuario"):
        return Generico
    for classe in ORDEM_DETECCAO.get(equip.get("fabricante"), [HikvisionISAPI, DahuaCGI]):
        if classe.reconhece(equip):
            return classe
    return Generico
