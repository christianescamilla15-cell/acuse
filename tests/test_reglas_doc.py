"""docs/reglas.md es la compuerta «una prueba por regla»: esta prueba evita que la tabla mienta.

Cada `tests/...py::prueba` citado debe existir como función de prueba de nivel módulo y no estar marcado
skip/xfail; cada fila de regla debe citar al menos una prueba, y TODA prueba del repo debe estar citada por alguna
regla (una prueba sin regla es una regla sin escribir, o una prueba que sobra).
"""
from __future__ import annotations

import ast
import re

from apoyo import RAIZ_REPO

DOC = RAIZ_REPO / "docs" / "reglas.md"
_REF = re.compile(r"`(tests/[\w/]+\.py)::(test_\w+)`")
_FILA_REGLA = re.compile(r"^\|\s*([A-Z]+\d+)\s*\|")
ESTE_ARCHIVO = "tests/test_reglas_doc.py"


def _filas() -> list[tuple[str, str]]:
    return [(m.group(1), linea) for linea in DOC.read_text(encoding="utf-8").splitlines()
            if (m := _FILA_REGLA.match(linea))]


def _pruebas_de(archivo: str) -> dict[str, bool]:
    """{nombre: ¿se salta?} de las funciones de prueba de nivel módulo."""
    arbol = ast.parse((RAIZ_REPO / archivo).read_text(encoding="utf-8"))
    saltado = any(isinstance(n, ast.Assign) and "pytestmark" in ast.unparse(n) and
                  re.search(r"skip|xfail", ast.unparse(n)) for n in arbol.body)
    pruebas = {}
    for nodo in arbol.body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name.startswith("test_"):
            marcas = {ast.unparse(d) for d in nodo.decorator_list}
            pruebas[nodo.name] = saltado or any("skip" in m or "xfail" in m for m in marcas)
    return pruebas


def test_la_tabla_tiene_todas_sus_reglas() -> None:
    identificadores = [ident for ident, _ in _filas()]
    assert len(identificadores) >= 85, "la tabla perdió reglas"
    assert len(identificadores) == len(set(identificadores)), "hay identificadores de regla repetidos"


def test_cada_regla_cita_al_menos_una_prueba() -> None:
    assert [ident for ident, linea in _filas() if not _REF.search(linea)] == []


def test_cada_prueba_citada_existe_y_no_se_salta() -> None:
    faltantes, saltadas = [], []
    for archivo, prueba in _REF.findall(DOC.read_text(encoding="utf-8")):
        existentes = _pruebas_de(archivo) if (RAIZ_REPO / archivo).is_file() else {}
        if prueba not in existentes:
            faltantes.append(f"{archivo}::{prueba}")
        elif existentes[prueba]:
            saltadas.append(f"{archivo}::{prueba}")
    assert faltantes == []
    assert saltadas == []


def test_ninguna_prueba_queda_sin_regla() -> None:
    citadas = set(_REF.findall(DOC.read_text(encoding="utf-8")))
    huerfanas = []
    for ruta in sorted((RAIZ_REPO / "tests").rglob("test_*.py")):
        archivo = ruta.relative_to(RAIZ_REPO).as_posix()
        if archivo == ESTE_ARCHIVO:
            continue
        huerfanas += [f"{archivo}::{p}" for p in _pruebas_de(archivo) if (archivo, p) not in citadas]
    assert huerfanas == []
