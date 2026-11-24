"""Cifras control por lote y su conciliación: emisor declara, receptor reproduce.

Las pruebas de paridad contra el archivo golden están en `test_paridad_golden.py`; aquí
van las reglas una por una, con un control negativo por cada tipo de diferencia.
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest
from apoyo import nueva_bd

from acuse.canales.lote import CanalLote, LoteDesconocido
from acuse.cifras import (
    CifraInvalida,
    EstadoConciliacion,
    TipoDiferencia,
    cifras_de_lote,
    conciliar_cifras,
)
from acuse.conciliador import cifras_declaradas, conciliar_lote
from acuse.modelo import ModoCanal, ReglaRuteo, Trabajo
from acuse.orquestador import Orquestador
from acuse.ruteo import Enrutador

LOTE = "corte-prueba"
BASE = [
    ("LIQ-001", {"importe": "100.00", "moneda": "MXN"}),
    ("LIQ-002", {"importe": "2.675", "moneda": "MXN"}),
    ("LIQ-003", {"importe": 50, "moneda": "MXN"}),
]


def _cifras(registros=BASE, lote=LOTE):
    return cifras_de_lote(lote, registros)


def test_conteo_suma_y_hash_del_lote():
    cifras = _cifras()
    assert (cifras.lote, cifras.moneda, cifras.conteo) == (LOTE, "MXN", 3)
    assert cifras.suma == Decimal("152.68")
    assert [f.referencia for f in cifras.filas] == ["LIQ-001", "LIQ-002", "LIQ-003"]
    assert len(cifras.hash_lote) == 64 and all(len(f.hash) == 64 for f in cifras.filas)
    assert cifras.como_dict()["suma"] == "152.68"


@pytest.mark.parametrize("semilla", range(6))
def test_el_orden_de_llegada_no_cambia_las_cifras(semilla):
    """La llave estable es la referencia: cualquier permutación da el mismo lote."""
    revuelto = list(BASE)
    random.Random(semilla).shuffle(revuelto)
    assert _cifras(revuelto) == _cifras(BASE)


def test_las_cifras_no_dependen_del_id_de_la_entrega():
    """Dos corridas en bases distintas generan UUID distintos y las mismas cifras."""
    obtenidas = []
    for orden in (BASE, list(reversed(BASE))):
        canal = CanalLote("lote")
        orq = Orquestador(nueva_bd(), Enrutador([ReglaRuteo("todo", "lote", {"d": "c"})]), {"lote": canal})
        orq.procesar([
            Trabajo(id=f"T-{r}", referencia=r, atributos={"d": "c"}, carga={**c, "lote": LOTE}, lote=LOTE)
            for r, c in orden
        ])
        obtenidas.append(cifras_de_lote(LOTE, canal.registros_del_lote(LOTE)))
    assert obtenidas[0] == obtenidas[1] == _cifras()


@pytest.mark.parametrize(
    ("registros", "motivo"),
    [
        ([("LIQ-1", {"importe": "1", "moneda": "MXN"}), ("LIQ-1", {"importe": "1", "moneda": "MXN"})],
         "viene dos veces"),
        ([("LIQ-1", {"importe": "1", "moneda": "MXN"}), ("LIQ-2", {"importe": "1", "moneda": "USD"})],
         "mezcla monedas"),
        ([("LIQ-1", {"moneda": "MXN"})], "no trae importe"),
        ([("LIQ-1", {"importe": None, "moneda": "MXN"})], "no trae importe"),
        ([("LIQ-1", {"importe": "1"})], "moneda None"),
        ([("LIQ-1", {"importe": "1", "moneda": "mxn"})], "moneda 'mxn'"),
        ([("LIQ-1", {"importe": "1", "moneda": "PESOS"})], "moneda 'PESOS'"),
        ([("", {"importe": "1", "moneda": "MXN"})], "sin referencia"),
        ([("   ", {"importe": "1", "moneda": "MXN"})], "sin referencia"),
        ([("LIQ-1", {"importe": 1.5, "moneda": "MXN"})], "float"),
    ],
    ids=["referencia-duplicada", "monedas-mezcladas", "importe-ausente", "importe-nulo", "moneda-ausente",
         "moneda-minusculas", "moneda-no-iso", "referencia-vacia", "referencia-en-blanco", "importe-float"],
)
def test_lo_que_obligaria_a_inventar_se_rechaza(registros, motivo):
    with pytest.raises(CifraInvalida, match=motivo):
        _cifras(registros)


@pytest.mark.parametrize("lote", ["", "   "], ids=["vacio", "en-blanco"])
def test_un_lote_sin_nombre_se_rechaza(lote):
    with pytest.raises(CifraInvalida, match="sin nombre"):
        _cifras(lote=lote)


# ---------------------------------------------------------------- conciliación


def test_lo_mismo_de_los_dos_lados_cuadra():
    resultado = conciliar_lote(_cifras(), [(r, dict(c)) for r, c in reversed(BASE)])
    assert resultado.estado is EstadoConciliacion.CUADRA and resultado.cuadra is True
    assert resultado.diferencias == ()


# (caso, registros del receptor, tipo esperado, referencia, declarado, recibido)
CONTROLES_NEGATIVOS = [
    ("falta-una-fila", BASE[:2], TipoDiferencia.FALTANTE, "LIQ-003", "50.00", None),
    ("sobra-una-fila", [*BASE, ("LIQ-999", {"importe": "1", "moneda": "MXN"})],
     TipoDiferencia.SOBRANTE, "LIQ-999", None, "1.00"),
    ("un-centavo-de-diferencia", [BASE[0], ("LIQ-002", {"importe": "2.67", "moneda": "MXN"}), BASE[2]],
     TipoDiferencia.IMPORTE_DISTINTO, "LIQ-002", "2.68", "2.67"),
    ("otra-moneda", [("LIQ-001", {"importe": "100", "moneda": "USD"}),
                     ("LIQ-002", {"importe": "2.675", "moneda": "USD"}),
                     ("LIQ-003", {"importe": 50, "moneda": "USD"})],
     TipoDiferencia.MONEDA_DISTINTA, "LIQ-001", "MXN", "USD"),
    ("referencia-cambiada", [BASE[0], BASE[1], ("LIQ-03", {"importe": 50, "moneda": "MXN"})],
     TipoDiferencia.FALTANTE, "LIQ-003", "50.00", None),
]


@pytest.mark.parametrize(
    ("recibidos", "tipo", "referencia", "declarado", "recibido"),
    [c[1:] for c in CONTROLES_NEGATIVOS],
    ids=[c[0] for c in CONTROLES_NEGATIVOS],
)
def test_cada_alteracion_descuadra_con_la_diferencia_clasificada(recibidos, tipo, referencia, declarado, recibido):
    resultado = conciliar_lote(_cifras(), recibidos)
    assert resultado.estado is EstadoConciliacion.DESCUADRA
    assert resultado.declaradas.hash_lote != resultado.recibidas.hash_lote
    primera = next(d for d in resultado.diferencias if d.tipo is tipo)
    assert (primera.referencia, primera.declarado, primera.recibido) == (referencia, declarado, recibido)


def test_las_diferencias_salen_ordenadas_por_referencia():
    recibidos = [("LIQ-000", {"importe": "1", "moneda": "MXN"}), BASE[1]]
    resultado = conciliar_lote(_cifras(), recibidos)
    assert [(d.referencia, d.tipo) for d in resultado.diferencias] == [
        ("LIQ-000", TipoDiferencia.SOBRANTE),
        ("LIQ-001", TipoDiferencia.FALTANTE),
        ("LIQ-003", TipoDiferencia.FALTANTE),
    ]


def test_un_lote_sin_filas_no_cuadra_es_sin_registros():
    resultado = conciliar_lote(_cifras([]), [])
    assert resultado.estado is EstadoConciliacion.SIN_REGISTROS
    assert resultado.cuadra is False
    assert (resultado.declaradas.conteo, resultado.declaradas.suma, resultado.declaradas.moneda) == (
        0, Decimal("0.00"), None)


def test_el_receptor_sin_nada_de_un_lote_declarado_descuadra_todo():
    resultado = conciliar_lote(_cifras(), [])
    assert resultado.estado is EstadoConciliacion.DESCUADRA
    assert [d.tipo for d in resultado.diferencias] == [TipoDiferencia.FALTANTE] * 3


def test_no_se_concilian_lotes_distintos():
    with pytest.raises(ValueError, match="lotes distintos"):
        conciliar_cifras(_cifras(lote="A"), _cifras(lote="B"))


def test_el_emisor_declara_solo_los_trabajos_de_ese_lote():
    trabajos = [
        Trabajo(id="T-1", referencia="LIQ-001", carga=dict(BASE[0][1]), lote=LOTE),
        Trabajo(id="T-2", referencia="LIQ-002", carga=dict(BASE[1][1]), lote=LOTE),
        Trabajo(id="T-3", referencia="LIQ-003", carga=dict(BASE[2][1]), lote=LOTE),
        Trabajo(id="T-4", referencia="LIQ-OTRO", carga={"importe": "9", "moneda": "MXN"}, lote="otro"),
        Trabajo(id="T-5", referencia="LIQ-SIN", carga={"importe": "9", "moneda": "MXN"}, lote=None),
    ]
    assert cifras_declaradas(LOTE, trabajos) == _cifras()


# ------------------------------------------------------- el canal por lote y la API


def test_leer_los_registros_del_lote_no_lo_altera_ni_inventa_lotes():
    canal = CanalLote("lote")
    with pytest.raises(LoteDesconocido):
        canal.registros_del_lote("no-existe")
    assert canal.lotes_depositados == {}
    assert CanalLote.modo is ModoCanal.ASINCRONO
