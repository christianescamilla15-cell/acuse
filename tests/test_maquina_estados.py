"""El acuse como máquina de estados: TODAS las transiciones, válidas e inválidas.

La tabla esperada está escrita aquí a mano, independiente de `acuse.estados`: si alguien
agrega una transición al código sin decidirlo, esta prueba lo detecta. Después se
intentan las transiciones inválidas por las operaciones públicas (orquestador y
conciliador), para probar que el código de verdad pasa por la máquina.
"""
from __future__ import annotations

import itertools

import pytest
from apoyo import (
    canales_dobles,
    entrega_por_referencia,
    nueva_bd,
    orquestador,
    sembrar_entrega,
    tipos_evento,
)

from acuse.conciliador import marcar_vencidas
from acuse.estados import DESPACHABLES, TRANSICIONES, Evento, TransicionInvalida, es_valida, siguiente
from acuse.modelo import EstadoEntrega, Trabajo

P, E, A, S, F, R = (
    EstadoEntrega.PENDIENTE,
    EstadoEntrega.ENVIADO,
    EstadoEntrega.ACUSADO,
    EstadoEntrega.SIN_ACUSE,
    EstadoEntrega.FALLIDO,
    EstadoEntrega.RECHAZADO,
)

# Lo que el negocio permite. Despachar (o reintentar) sólo desde P, F, R y S; acusar y
# vencer sólo desde ENVIADO; ACUSADO es terminal y nadie vuelve a PENDIENTE.
ESPERADO: dict[tuple[EstadoEntrega, Evento], EstadoEntrega] = {}
for _origen in (P, F, R, S):
    ESPERADO[(_origen, Evento.SIN_RUTA)] = R
    ESPERADO[(_origen, Evento.FALLA_CANAL)] = F
    ESPERADO[(_origen, Evento.DEPOSITO)] = E
    ESPERADO[(_origen, Evento.ACUSE_INMEDIATO)] = A
ESPERADO[(E, Evento.ACUSE_RECEPTOR)] = A
ESPERADO[(E, Evento.VENCIMIENTO)] = S

TODAS = list(itertools.product(EstadoEntrega, Evento))
VALIDAS = [(o, ev) for o, ev in TODAS if (o, ev) in ESPERADO]
INVALIDAS = [(o, ev) for o, ev in TODAS if (o, ev) not in ESPERADO]


def _id(par) -> str:
    return f"{par[0].value}-{par[1].value}"


def test_la_tabla_del_codigo_es_exactamente_la_del_negocio():
    assert TRANSICIONES == ESPERADO
    assert len(TODAS) == 36 and len(VALIDAS) == 18 and len(INVALIDAS) == 18


@pytest.mark.parametrize("par", VALIDAS, ids=_id)
def test_transicion_valida(par):
    origen, evento = par
    assert es_valida(origen, evento) is True
    assert siguiente(origen, evento) is ESPERADO[par]


@pytest.mark.parametrize("par", INVALIDAS, ids=_id)
def test_transicion_invalida_se_rechaza_con_motivo(par):
    origen, evento = par
    assert es_valida(origen, evento) is False
    with pytest.raises(TransicionInvalida) as error:
        siguiente(origen, evento)
    assert error.value.estado is origen and error.value.evento is evento
    assert origen.value in str(error.value) and evento.value in str(error.value)


def test_acusado_es_el_unico_estado_terminal():
    terminales = {o for o in EstadoEntrega if not any(es_valida(o, ev) for ev in Evento)}
    assert terminales == {A}
    assert "ninguno (terminal)" in str(TransicionInvalida(A, Evento.DEPOSITO))


def test_nadie_vuelve_a_pendiente_y_todo_estado_es_alcanzable():
    assert P not in set(TRANSICIONES.values())
    alcanzables, frontera = {P}, [P]
    while frontera:
        actual = frontera.pop()
        for (origen, _), destino in TRANSICIONES.items():
            if origen is actual and destino not in alcanzables:
                alcanzables.add(destino)
                frontera.append(destino)
    assert alcanzables == set(EstadoEntrega)


def test_enviado_y_acusado_no_se_despachan_de_nuevo():
    assert DESPACHABLES == {P, F, R, S}


# ------------------------------------------- las inválidas, por las operaciones públicas

NO_ACUSABLES = [P, A, F, R, S]


@pytest.mark.parametrize("estado", NO_ACUSABLES, ids=lambda e: e.value)
def test_el_acuse_del_receptor_solo_mueve_lo_que_esta_enviado(estado):
    sesiones = nueva_bd()
    sembrar_entrega(sesiones, estado, referencia="LIQ-X")

    marcadas = orquestador(sesiones).registrar_acuse(["LIQ-X"], actor="receptor")

    assert marcadas == 0
    assert entrega_por_referencia(sesiones, "LIQ-X").estado is estado
    # Lo vencido conserva el incumplimiento y deja constancia del acuse tardío.
    esperado = "acuse_tardio" if estado is S else "acuse_ignorado"
    assert tipos_evento(sesiones) == [esperado]


@pytest.mark.parametrize("estado", [P, A, F, R, S], ids=lambda e: e.value)
def test_el_vencimiento_solo_mueve_lo_que_esta_enviado(estado):
    sesiones = nueva_bd()
    sembrar_entrega(sesiones, estado, referencia="LIQ-X", enviado_hace_s=10**6, plazo_acuse_s=1)

    with sesiones() as ses:
        assert marcar_vencidas(ses) == 0

    assert entrega_por_referencia(sesiones, "LIQ-X").estado is estado


@pytest.mark.parametrize("estado", [E, A], ids=lambda e: e.value)
def test_lo_vivo_no_se_reenvia(estado):
    sesiones = nueva_bd()
    sembrar_entrega(sesiones, estado, referencia="LIQ-X")
    canales = canales_dobles()

    corrida = orquestador(sesiones, canales).procesar(
        [Trabajo(id="T-LIQ-X", referencia="LIQ-X", atributos={"destino": "buzon"})]
    )

    assert canales["buzon"].enviados == []
    assert entrega_por_referencia(sesiones, "LIQ-X").estado is estado
    assert corrida.estado.value == "SIN_INSUMO"
    assert tipos_evento(sesiones) == ["duplicado_ignorado"]


@pytest.mark.parametrize("estado", [P, F, R, S], ids=lambda e: e.value)
def test_lo_despachable_se_reintenta_sobre_la_misma_entrega(estado):
    sesiones = nueva_bd()
    id_original = sembrar_entrega(sesiones, estado, referencia="LIQ-X")
    canales = canales_dobles()

    orquestador(sesiones, canales).procesar(
        [Trabajo(id="T-LIQ-X", referencia="LIQ-X", atributos={"destino": "buzon"})]
    )

    fila = entrega_por_referencia(sesiones, "LIQ-X")
    assert fila.id == id_original
    assert fila.estado is E
    assert canales["buzon"].enviados == ["LIQ-X"]
    assert tipos_evento(sesiones) == ["reintento", "enrutado", "enviado"]
