"""Pruebas de la capa HTTP.

Cada prueba fija una de las tres patologías en un número concreto. Si alguien
«mejora» el tablero sumando lo enviado a lo entregado, o dejando que una corrida
vacía termine en verde, estas pruebas se caen — que es justo lo que deben hacer.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from acuse.api.app import app
from acuse.demo import CANAL_ASINCRONO, CANAL_LOTE, CANAL_SINCRONO

# Escenario sembrado: 10 acusadas, 14 + 4 enviadas sin acuse, 2 sin ruta.
ACUSADAS = 10
SIN_ACUSE_B = 14
SIN_ACUSE_C = 4
RECHAZADAS = 2
TOTAL = ACUSADAS + SIN_ACUSE_B + SIN_ACUSE_C + RECHAZADAS
ENVIADAS = SIN_ACUSE_B + SIN_ACUSE_C


@pytest.fixture()
def cliente():
    """Cliente con el escenario recién sembrado.

    El reinicio explícito importa: los canales guardan el buzón en memoria y una
    prueba que consumiera lo que dejó la anterior mediría otra cosa.
    """
    with TestClient(app) as clienteh:
        assert clienteh.post("/api/reiniciar").status_code == 200
        yield clienteh


def test_health_responde_pero_no_afirma_que_todo_este_bien(cliente):
    respuesta = cliente.get("/health")
    assert respuesta.status_code == 200

    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "ok"
    assert cuerpo["servicio"] == "Acuse API"
    assert cuerpo["version"]
    # El health dice «respondo», no «todo llegó». La salud del flujo vive en el resumen.
    assert "acuse" in cuerpo["tesis"].lower()


def test_resumen_publica_los_dos_numeros_y_la_diferencia(cliente):
    """⭐ Patología 1: publicar sólo «lo que salió» es lo que esconde el problema."""
    cuerpo = cliente.get("/api/resumen").json()

    assert cuerpo["total"] == TOTAL
    assert cuerpo["por_estado"]["ACUSADO"] == ACUSADAS
    assert cuerpo["por_estado"]["ENVIADO"] == ENVIADAS
    assert cuerpo["por_estado"]["RECHAZADO"] == RECHAZADAS

    # Los DOS números, uno junto al otro, y su diferencia explícita.
    assert cuerpo["entregado_confirmado"] == ACUSADAS
    assert cuerpo["entregado_segun_emisor"] == ACUSADAS + ENVIADAS
    assert cuerpo["brecha_de_acuse"] == ENVIADAS
    assert cuerpo["entregado_segun_emisor"] > cuerpo["entregado_confirmado"]

    # Y la brecha desglosada por canal, para poder ir a reclamar con nombre.
    por_canal = {b["canal"]: b for b in cuerpo["brechas"]}
    assert por_canal[CANAL_SINCRONO]["acusados"] == ACUSADAS
    assert por_canal[CANAL_SINCRONO]["enviados"] == 0
    assert por_canal[CANAL_ASINCRONO]["enviados"] == SIN_ACUSE_B
    assert por_canal[CANAL_ASINCRONO]["acusados"] == 0
    assert por_canal[CANAL_LOTE]["enviados"] == SIN_ACUSE_C


def test_consumir_reduce_la_brecha_y_solo_por_lo_leido(cliente):
    """El acuse nace de que el receptor lea. Nada más lo baja."""
    antes = cliente.get("/api/resumen").json()["brecha_de_acuse"]
    assert antes == ENVIADAS

    respuesta = cliente.post(
        "/api/simular/consumir", json={"canal": CANAL_ASINCRONO, "n": 5}
    )
    assert respuesta.status_code == 200

    consumo = respuesta.json()
    assert consumo["consumidas"] == 5
    assert consumo["acuses_registrados"] == 5
    assert consumo["actor"] == f"receptor:{CANAL_ASINCRONO}"
    assert consumo["pendientes_restantes"] == SIN_ACUSE_B - 5

    despues = cliente.get("/api/resumen").json()
    assert despues["brecha_de_acuse"] == antes - 5
    assert despues["entregado_confirmado"] == ACUSADAS + 5
    # Lo que el emisor creía entregado NO se movió: sólo cambió cuánto de eso es cierto.
    assert despues["entregado_segun_emisor"] == ACUSADAS + ENVIADAS


def test_corrida_vacia_produce_una_corrida_sin_insumo(cliente):
    """⭐ Patología 3: correr sin insumo NO es una corrida exitosa."""
    corrida = cliente.post("/api/simular/corrida-vacia").json()

    assert corrida["estado"] == "SIN_INSUMO"
    assert corrida["tomados"] == 0
    assert corrida["procesados"] == 0
    assert corrida["hizo_algo"] is False
    assert corrida["detalle"]

    # Y queda a la vista en el tablero, no sólo en la respuesta de quien la disparó.
    corridas = cliente.get("/api/corridas").json()
    assert any(c["id"] == corrida["id"] and c["estado"] == "SIN_INSUMO" for c in corridas)


def test_los_trabajos_sin_ruta_quedan_rechazados_y_sin_canal(cliente):
    """⭐ Patología 2: sin regla no hay canal por omisión, hay un rechazo visible."""
    rechazadas = cliente.get("/api/entregas", params={"estado": "RECHAZADO"}).json()

    assert len(rechazadas) == RECHAZADAS
    for entrega in rechazadas:
        assert entrega["canal"] is None
        assert entrega["entregado"] is False
        assert "regla" in entrega["detalle"].lower()

    # La bitácora conserva el motivo, no sólo el estado final.
    eventos = cliente.get(
        "/api/eventos", params={"entrega_id": rechazadas[0]["id"]}
    ).json()
    assert any(e["tipo"] == "rechazado" for e in eventos)


def test_vencer_nombra_el_incumplimiento_pero_no_borra_la_brecha(cliente):
    """Marcar SIN_ACUSE es mirar el problema; mirarlo no lo resuelve."""
    antes = cliente.get("/api/resumen").json()["brecha_de_acuse"]

    vencimiento = cliente.post("/api/simular/vencer").json()
    assert vencimiento["marcadas"] == ENVIADAS

    despues = cliente.get("/api/resumen").json()
    assert despues["por_estado"]["SIN_ACUSE"] == ENVIADAS
    assert despues["por_estado"]["ENVIADO"] == 0
    # La brecha medida por el conciliador sigue ahí: nadie recibió nada.
    assert sum(b["sin_acuse"] for b in despues["brechas"]) == antes
