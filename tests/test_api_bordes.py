"""Bordes de la capa HTTP: errores con motivo, entradas fuera de contrato y filtros.

Complementa `test_api.py` (las tres patologías en el escenario sembrado). Los casos de
contrato (405, entradas que antes se coaccionaban, el avance que desbordaba) salieron de
la prueba con Schemathesis y quedan fijados aquí con un ejemplo cada uno.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from acuse.api.app import AVANCE_MAXIMO_S, app
from acuse.demo import CANAL_ASINCRONO, CANAL_LOTE, CANAL_SINCRONO, LOTE_SEMBRADO


@pytest.fixture()
def cliente():
    with TestClient(app) as c:
        assert c.post("/api/reiniciar").status_code == 200
        yield c


def test_consumir_un_canal_que_no_existe_es_404_con_los_registrados(cliente):
    r = cliente.post("/api/simular/consumir", json={"canal": "inexistente"})
    assert r.status_code == 404
    assert CANAL_ASINCRONO in r.json()["detail"] and CANAL_LOTE in r.json()["detail"]


def test_consumir_un_canal_sincrono_es_409_porque_no_tiene_buzon(cliente):
    r = cliente.post("/api/simular/consumir", json={"canal": CANAL_SINCRONO})
    assert r.status_code == 409
    assert "síncrono" in r.json()["detail"]


def test_consumir_el_canal_por_lote_acusa_el_corte_completo(cliente):
    r = cliente.post("/api/simular/consumir", json={"canal": CANAL_LOTE})
    assert r.status_code == 200
    assert (r.json()["consumidas"], r.json()["acuses_registrados"]) == (1, 4)
    assert cliente.get(f"/api/lotes/{LOTE_SEMBRADO}/cifras").json()["consumido"] is True


def test_consumir_dos_veces_no_inventa_acuses(cliente):
    cliente.post("/api/simular/consumir", json={"canal": CANAL_ASINCRONO})
    r = cliente.post("/api/simular/consumir", json={"canal": CANAL_ASINCRONO})
    assert (r.json()["consumidas"], r.json()["acuses_registrados"]) == (0, 0)
    assert "nada pendiente" in r.json()["nota"]


def test_las_cifras_de_un_lote_que_no_existe_son_404(cliente):
    r = cliente.get("/api/lotes/corte-que-no-existe/cifras")
    assert r.status_code == 404
    assert LOTE_SEMBRADO in r.json()["detail"]


@pytest.mark.parametrize(
    ("ruta", "cuerpo"),
    [
        ("/api/simular/corrida", {"n": -1}),
        ("/api/simular/corrida", {"n": 501}),
        ("/api/simular/corrida", {"n": False}),             # un booleano no es un entero
        ("/api/simular/corrida", {"n": 2.5}),
        ("/api/simular/corrida", {"n": "6"}),               # ni un texto
        ("/api/simular/corrida", {"incluir_sin_ruta": 0}),  # ni un 0 es un booleano
        ("/api/simular/corrida", {"n": 1, "extra": True}),  # campos que el contrato no tiene
        ("/api/simular/consumir", {"n": -1}),
        ("/api/simular/consumir", {"n": False}),
        ("/api/simular/vencer", {"avance_s": 0}),
        ("/api/simular/vencer", {"avance_s": AVANCE_MAXIMO_S + 1}),
        ("/api/simular/vencer", {"avance_s": 9223372036854775808}),   # antes: 500 por desborde
    ],
    ids=["n-negativo", "n-501", "n-booleano", "n-fraccion", "n-texto", "bandera-entera", "campo-extra",
         "consumo-negativo", "consumo-booleano", "avance-cero", "avance-sobre-el-tope", "avance-desborde"],
)
def test_lo_que_el_contrato_no_admite_se_rechaza_con_422(cliente, ruta, cuerpo):
    assert cliente.post(ruta, json=cuerpo).status_code == 422


def test_un_entero_escrito_como_6_punto_0_es_un_entero(cliente):
    """JSON Schema considera entero a 6.0: el contrato lo admite y la API también."""
    r = cliente.post("/api/simular/corrida", json={"n": 6.0})
    assert r.status_code == 200 and r.json()["tomados"] == 6


def test_un_cuerpo_ilegible_es_400(cliente):
    r = cliente.post("/api/simular/consumir", content=b"\xff", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


@pytest.mark.parametrize(
    ("metodo", "ruta", "permitido"),
    [("get", "/api/reiniciar", "POST"), ("get", "/api/simular/corrida", "POST"), ("delete", "/api/resumen", "GET")],
)
def test_un_metodo_equivocado_es_405_no_404(cliente, metodo, ruta, permitido):
    """El tablero montado en «/» ya no tapa la API: la ruta existe, el método no."""
    r = cliente.request(metodo.upper(), ruta)
    assert r.status_code == 405
    assert permitido in r.headers["allow"]


def test_una_ruta_de_api_inexistente_es_404_y_el_tablero_sigue_en_la_raiz(cliente):
    assert cliente.get("/api/no-existe").status_code == 404
    raiz = cliente.get("/")
    assert raiz.status_code == 200 and "text/html" in raiz.headers["content-type"]


def test_corrida_con_cero_trabajos_es_sin_insumo(cliente):
    corrida = cliente.post("/api/simular/corrida", json={"n": 0}).json()
    assert (corrida["estado"], corrida["hizo_algo"]) == ("SIN_INSUMO", False)


def test_corrida_con_trabajos_sin_ruta_queda_con_problemas_y_no_reusa_folios(cliente):
    a = cliente.post("/api/simular/corrida", json={"n": 8, "incluir_sin_ruta": True}).json()
    b = cliente.post("/api/simular/corrida", json={"n": 4}).json()
    assert (a["estado"], a["rechazados"]) == ("CON_PROBLEMAS", 2)
    assert b["estado"] == "EXITOSA" and b["tomados"] == 4         # folios nuevos: no son duplicados
    assert cliente.get("/api/resumen").json()["total"] == 30 + 8 + 4


@pytest.mark.parametrize(
    ("filtro", "esperadas"),
    [({"estado": "ACUSADO"}, 10), ({"estado": "RECHAZADO"}, 2), ({"canal": CANAL_ASINCRONO}, 14),
     ({"canal": CANAL_LOTE, "estado": "ENVIADO"}, 4), ({"limit": 3}, 3), ({"canal": "no-existe"}, 0)],
    ids=["acusadas", "rechazadas", "por-canal", "canal-y-estado", "limite", "canal-vacio"],
)
def test_los_filtros_de_entregas(cliente, filtro, esperadas):
    assert len(cliente.get("/api/entregas", params=filtro).json()) == esperadas


@pytest.mark.parametrize("params", [{"estado": "ENTREGADO"}, {"limit": 0}, {"limit": 1001}],
                         ids=["estado-inexistente", "limite-cero", "limite-excesivo"])
def test_filtros_fuera_de_contrato_son_422(cliente, params):
    assert cliente.get("/api/entregas", params=params).status_code == 422


def test_vencer_con_avance_explicito_corto_no_marca_nada(cliente):
    r = cliente.post("/api/simular/vencer", json={"avance_s": 1}).json()
    assert (r["avance_s"], r["marcadas"]) == (1, 0)


def test_la_bitacora_de_una_entrega_y_el_limite_de_eventos(cliente):
    (entrega,) = cliente.get("/api/entregas", params={"estado": "ACUSADO", "limit": 1}).json()
    eventos = cliente.get("/api/eventos", params={"entrega_id": entrega["id"]}).json()
    assert [e["tipo"] for e in reversed(eventos)] == ["creada", "enrutado", "acusado"]
    assert len(cliente.get("/api/eventos", params={"limit": 5}).json()) == 5


def test_brechas_como_recurso_propio_coincide_con_el_resumen(cliente):
    assert cliente.get("/api/brechas").json() == cliente.get("/api/resumen").json()["brechas"]
