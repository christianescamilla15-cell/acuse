"""Prueba de contrato: el OpenAPI versionado es el que publica la app, y está completo.

`docs/api/openapi.json` se genera con `herramientas/exportar_openapi.py`. Si alguien
cambia un endpoint, un esquema o un código de respuesta sin regenerarlo, esta prueba
falla y dice cómo arreglarlo: un contrato que se desincroniza en silencio es un contrato
que miente. La prueba contra la app en proceso (Schemathesis) está en
`test_contrato_schemathesis.py`.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from acuse.api.app import app

from apoyo import RAIZ_REPO

CONTRATO = RAIZ_REPO / "docs" / "api" / "openapi.json"
METODOS = {"get", "post", "put", "patch", "delete"}


def _operaciones(esquema: dict) -> list[tuple[str, str, dict]]:
    return [(m, ruta, op) for ruta, ops in esquema["paths"].items() for m, op in ops.items() if m in METODOS]


def test_el_contrato_versionado_es_el_que_publica_la_app():
    versionado = json.loads(CONTRATO.read_text(encoding="utf-8"))
    assert versionado == app.openapi(), (
        "docs/api/openapi.json no coincide con la API: corre "
        "`python3 herramientas/exportar_openapi.py` y revisa el diff antes de subirlo."
    )


def test_el_contrato_cubre_todas_las_rutas_publicas():
    rutas = {ruta for _, ruta, _ in _operaciones(json.loads(CONTRATO.read_text(encoding="utf-8")))}
    assert rutas == {
        "/health", "/api/resumen", "/api/brechas", "/api/entregas", "/api/corridas", "/api/eventos",
        "/api/lotes/{lote}/cifras", "/api/simular/corrida", "/api/simular/corrida-vacia",
        "/api/simular/consumir", "/api/simular/vencer", "/api/reiniciar",
    }


@pytest.mark.parametrize(
    "operacion",
    _operaciones(app.openapi()),
    ids=lambda o: f"{o[0].upper()} {o[1]}",
)
def test_cada_operacion_esta_documentada_con_esquemas(operacion):
    metodo, ruta, op = operacion
    assert op.get("summary") and op.get("description"), f"{metodo} {ruta} sin resumen o descripción"
    exitos = {codigo: r for codigo, r in op["responses"].items() if codigo.startswith("2")}
    assert exitos, f"{metodo} {ruta} no documenta una respuesta de éxito"
    for codigo, respuesta in op["responses"].items():
        assert respuesta.get("description"), f"{metodo} {ruta} {codigo} sin descripción"
        assert respuesta["content"]["application/json"]["schema"], f"{metodo} {ruta} {codigo} sin esquema"
    for parametro in op.get("parameters", []):
        assert parametro.get("schema"), f"{metodo} {ruta}: el parámetro {parametro['name']} no declara tipo"


def test_los_importes_del_contrato_viajan_como_texto_no_como_numero():
    esquemas = app.openapi()["components"]["schemas"]
    assert esquemas["CifrasSalida"]["properties"]["suma"]["type"] == "string"
    assert esquemas["FilaCifrasSalida"]["properties"]["importe"]["type"] == "string"


@pytest.mark.parametrize(
    ("ruta", "canonica"),
    [("/api/simular/lote", "/api/simular/corrida"), ("/api/simular/reiniciar", "/api/reiniciar")],
)
def test_los_sinonimos_del_tablero_responden_fuera_del_contrato(ruta, canonica):
    """El tablero usa estos nombres; existen, responden y no se publican como API."""
    with TestClient(app) as cliente:
        assert cliente.post("/api/reiniciar").status_code == 200
        assert cliente.post(ruta).status_code == 200
    assert ruta not in app.openapi()["paths"] and canonica in app.openapi()["paths"]
