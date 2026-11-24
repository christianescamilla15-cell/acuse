"""Pruebas de arquitectura: cada capa importa sólo lo que le toca.

Recorre con `ast` TODOS los imports de cada módulo de `acuse/` (también los que están
dentro de funciones o al final del archivo) y los compara contra la tabla de capas de
docs/arquitectura/README.md. Un módulo nuevo sin capa asignada también falla: la
arquitectura no se degrada por omisión.

    dominio         modelo, estados, ruteo, cifras     sólo biblioteca estándar
    canales         canales (+ sincrono, asincrono, lote)   dominio
    infraestructura persistencia                       dominio + SQLAlchemy
    aplicacion      orquestador, conciliador           dominio, canales, infraestructura + SQLAlchemy
    composicion     demo                               dominio, canales
    presentacion    api (+ app)                        todo + FastAPI/Pydantic
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from apoyo import RAIZ_REPO

PAQUETE = RAIZ_REPO / "acuse"

CAPAS: dict[str, str] = {
    "acuse": "raiz",
    "acuse.modelo": "dominio",
    "acuse.estados": "dominio",
    "acuse.ruteo": "dominio",
    "acuse.cifras": "dominio",
    "acuse.canales": "canales",
    "acuse.canales.sincrono": "canales",
    "acuse.canales.asincrono": "canales",
    "acuse.canales.lote": "canales",
    "acuse.persistencia": "infraestructura",
    "acuse.orquestador": "aplicacion",
    "acuse.conciliador": "aplicacion",
    "acuse.demo": "composicion",
    "acuse.api": "presentacion",
    "acuse.api.app": "presentacion",
}

# Capas internas que cada capa puede importar (además de sí misma).
PERMITIDAS: dict[str, set[str]] = {
    "raiz": set(),
    "dominio": set(),
    "canales": {"dominio"},
    "infraestructura": {"dominio"},
    "aplicacion": {"dominio", "canales", "infraestructura"},
    "composicion": {"dominio", "canales"},
    "presentacion": {"raiz", "dominio", "canales", "infraestructura", "aplicacion", "composicion"},
}

# Terceros que cada capa puede importar. El dominio y los canales, ninguno.
TERCEROS: dict[str, set[str]] = {
    "raiz": set(),
    "dominio": set(),
    "canales": set(),
    "infraestructura": {"sqlalchemy"},
    "aplicacion": {"sqlalchemy"},
    "composicion": set(),
    "presentacion": {"fastapi", "pydantic", "sqlalchemy", "starlette"},
}


def _nombre_modulo(ruta: Path) -> str:
    partes = ruta.relative_to(RAIZ_REPO).with_suffix("").parts
    return ".".join(partes[:-1] if partes[-1] == "__init__" else partes)


def _imports(codigo: str, modulo: str, es_paquete: bool) -> list[tuple[int, str]]:
    """(línea, módulo importado absoluto) de cada import, en cualquier nivel del archivo."""
    encontrados: list[tuple[int, str]] = []
    for nodo in ast.walk(ast.parse(codigo)):
        if isinstance(nodo, ast.Import):
            encontrados += [(nodo.lineno, alias.name) for alias in nodo.names]
        elif isinstance(nodo, ast.ImportFrom):
            if nodo.level:                                  # import relativo → absoluto
                base = modulo.split(".")
                base = base if es_paquete else base[:-1]
                base = base[: len(base) - (nodo.level - 1)]
                destino = ".".join(base + ([nodo.module] if nodo.module else []))
            else:
                destino = nodo.module or ""
            encontrados.append((nodo.lineno, destino))
    return encontrados


def _capa_interna(destino: str) -> str | None:
    """Capa del MÓDULO importado; un nombre que no es módulo del paquete no hereda la del padre."""
    return CAPAS.get(destino)


def violaciones(modulo: str, codigo: str, es_paquete: bool = False) -> list[str]:
    capa = CAPAS[modulo]
    salida: list[str] = []
    for linea, destino in _imports(codigo, modulo, es_paquete):
        raiz = destino.split(".")[0]
        if raiz == "acuse":
            interna = _capa_interna(destino)
            if interna is None:
                salida.append(f"{modulo}:{linea} importa «{destino}», que no tiene capa asignada")
            elif interna != capa and interna not in PERMITIDAS[capa]:
                salida.append(f"{modulo}:{linea} ({capa}) importa «{destino}» ({interna})")
        elif raiz == "__future__" or raiz in sys.stdlib_module_names:
            continue
        elif raiz not in TERCEROS[capa]:
            salida.append(f"{modulo}:{linea} ({capa}) importa el tercero «{raiz}»")
    return salida


MODULOS = sorted(PAQUETE.rglob("*.py"))


def test_todo_modulo_del_paquete_tiene_capa():
    sin_capa = [_nombre_modulo(r) for r in MODULOS if _nombre_modulo(r) not in CAPAS]
    assert sin_capa == []
    assert len(MODULOS) == len(CAPAS)


@pytest.mark.parametrize("ruta", MODULOS, ids=lambda r: _nombre_modulo(r))
def test_cada_modulo_respeta_su_capa(ruta):
    modulo = _nombre_modulo(ruta)
    assert violaciones(modulo, ruta.read_text(encoding="utf-8"), ruta.name == "__init__.py") == []


# ------------------------------------------------- controles negativos del verificador


@pytest.mark.parametrize(
    ("modulo", "codigo", "esperado"),
    [
        ("acuse.modelo", "from fastapi import FastAPI", "importa el tercero «fastapi»"),
        ("acuse.modelo", "import sqlalchemy.orm", "importa el tercero «sqlalchemy»"),
        ("acuse.cifras", "from acuse.persistencia import EntregaORM", "(dominio) importa «acuse.persistencia»"),
        ("acuse.ruteo", "from acuse.canales import Canal", "(dominio) importa «acuse.canales»"),
        ("acuse.canales.lote", "from acuse.orquestador import Orquestador", "(canales) importa «acuse.orquestador»"),
        ("acuse.persistencia", "from acuse.api.app import app", "(infraestructura) importa «acuse.api.app»"),
        ("acuse.orquestador", "from fastapi import HTTPException", "importa el tercero «fastapi»"),
        ("acuse.conciliador", "from acuse.demo import sembrar", "(aplicacion) importa «acuse.demo»"),
        ("acuse.demo", "from acuse.persistencia import Base", "(composicion) importa «acuse.persistencia»"),
        ("acuse.estados", "def f():\n    from pydantic import BaseModel\n", "importa el tercero «pydantic»"),
        ("acuse.canales.lote", "from ..persistencia import Base", "(canales) importa «acuse.persistencia»"),
        ("acuse.modelo", "from acuse.nuevo import X", "no tiene capa asignada"),
    ],
    ids=["dominio-fastapi", "dominio-sqlalchemy", "dominio-persistencia", "dominio-canales", "canales-aplicacion",
         "infra-presentacion", "aplicacion-fastapi", "aplicacion-composicion", "composicion-infra",
         "import-dentro-de-funcion", "import-relativo", "modulo-sin-capa"],
)
def test_el_verificador_detecta_cada_violacion(modulo, codigo, esperado):
    (violacion,) = violaciones(modulo, codigo, es_paquete=modulo == "acuse.canales")
    assert esperado in violacion


def test_el_verificador_deja_pasar_lo_permitido():
    assert violaciones("acuse.orquestador", "from sqlalchemy import select\nfrom acuse.canales import Canal") == []
    assert violaciones("acuse.canales.sincrono", "from . import ResultadoEnvio\nimport hashlib") == []
