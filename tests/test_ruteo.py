"""Ruteo explícito (ADR 0003): una prueba por regla, con sus bordes parametrizados."""
from __future__ import annotations

import itertools

import pytest

from acuse.modelo import ReglaRuteo
from acuse.ruteo import Enrutador, SinRutaAplicable

A = ReglaRuteo(nombre="a", canal="canal-a", cuando={"destino": "A"}, prioridad=10)
B = ReglaRuteo(nombre="b", canal="canal-b", cuando={"destino": "A"}, prioridad=20)
C = ReglaRuteo(nombre="c", canal="canal-c", cuando={"destino": "A", "moneda": "MXN"}, prioridad=30)


@pytest.mark.parametrize("orden", list(itertools.permutations([A, B, C])), ids=lambda o: "-".join(r.nombre for r in o))
def test_gana_la_menor_prioridad_sin_importar_el_orden_declarado(orden):
    decision = Enrutador(list(orden)).resolver("LIQ-1", {"destino": "A", "moneda": "MXN"})
    assert (decision.regla, decision.canal) == ("a", "canal-a")


@pytest.mark.parametrize("invertir", [False, True], ids=["declarada-primero-x", "declarada-primero-y"])
def test_con_la_misma_prioridad_y_condiciones_distintas_decide_el_orden_declarado(invertir):
    x = ReglaRuteo(nombre="x", canal="cx", cuando={"destino": "A"}, prioridad=10)
    y = ReglaRuteo(nombre="y", canal="cy", cuando={"moneda": "MXN"}, prioridad=10)
    reglas = [y, x] if invertir else [x, y]
    decision = Enrutador(reglas).resolver("LIQ-1", {"destino": "A", "moneda": "MXN"})
    assert decision.regla == reglas[0].nombre


@pytest.mark.parametrize(
    ("primera", "segunda"),
    [
        ({"destino": "A"}, {"destino": "A"}),
        ({"destino": "A", "moneda": "MXN"}, {"moneda": "MXN", "destino": "A"}),   # mismo dict, otro orden
        ({}, {}),
    ],
    ids=["identicas", "mismo-contenido-otro-orden", "dos-comodines"],
)
def test_dos_reglas_con_la_misma_condicion_y_prioridad_son_un_error_al_construir(primera, segunda):
    with pytest.raises(ValueError, match="Ruteo ambiguo"):
        Enrutador([ReglaRuteo("r1", "c1", primera, 5), ReglaRuteo("r2", "c2", segunda, 5)])


def test_la_misma_condicion_con_prioridad_distinta_no_es_ambigua():
    decision = Enrutador([B, A]).resolver("LIQ-1", {"destino": "A"})
    assert decision.regla == "a"


@pytest.mark.parametrize(
    ("atributos", "casa"),
    [
        ({"destino": "A"}, True),
        ({"destino": "A", "otro": "x"}, True),        # atributos de más no estorban
        ({"destino": "a"}, False),                    # igualdad exacta: sin normalizar mayúsculas
        ({"destino": " A"}, False),                   # ni espacios
        ({"destino": ""}, False),                     # vacío no es A
        ({"destino": None}, False),
        ({}, False),                                  # el atributo ausente NO cae en un canal cualquiera
        ({"Destino": "A"}, False),                    # la llave también es exacta
    ],
    ids=["exacto", "con-extras", "minusculas", "espacio", "vacio", "nulo", "ausente", "llave-con-mayuscula"],
)
def test_las_condiciones_casan_por_igualdad_exacta(atributos, casa):
    assert A.aplica(atributos) is casa


@pytest.mark.parametrize(
    ("atributos", "casa"),
    [({"destino": "A", "moneda": "MXN"}, True), ({"destino": "A"}, False), ({"moneda": "MXN"}, False)],
    ids=["ambas", "falta-moneda", "falta-destino"],
)
def test_una_condicion_de_varios_atributos_exige_todos(atributos, casa):
    assert C.aplica(atributos) is casa


def test_una_regla_sin_condicion_es_legal_explicita_y_se_explica():
    """ADR 0003 §6: un comodín escrito, con nombre y prioridad alta, no es una omisión."""
    comodin = ReglaRuteo(nombre="resto-a-revision", canal="revision", cuando={}, prioridad=1000)
    enrutador = Enrutador([A, comodin])
    assert enrutador.resolver("LIQ-1", {"destino": "A"}).regla == "a"
    decision = enrutador.resolver("LIQ-2", {"destino": "Z"})
    assert (decision.regla, decision.canal) == ("resto-a-revision", "revision")
    assert "(sin condición)" in decision.explicacion and "1000" in decision.explicacion


def test_la_explicacion_dice_que_regla_con_que_prioridad_y_que_atributos():
    decision = Enrutador([C]).resolver("LIQ-1", {"destino": "A", "moneda": "MXN"})
    assert "«c»" in decision.explicacion and "prioridad 30" in decision.explicacion
    assert "destino=A" in decision.explicacion and "moneda=MXN" in decision.explicacion


@pytest.mark.parametrize(
    ("reglas", "atributos", "en_el_mensaje"),
    [
        ([A, B], {"destino": "Z"}, ["LIQ-9", "'destino': 'Z'", "a, b", "No existe canal por omisión"]),
        ([], {"destino": "A"}, ["Reglas declaradas: ninguna"]),
        ([A], {}, ["Atributos: {}"]),
    ],
    ids=["sin-coincidencia", "sin-reglas", "sin-atributos"],
)
def test_sin_ruta_el_error_carga_el_diagnostico_completo(reglas, atributos, en_el_mensaje):
    with pytest.raises(SinRutaAplicable) as error:
        Enrutador(reglas).resolver("LIQ-9", atributos)
    for fragmento in en_el_mensaje:
        assert fragmento in str(error.value)
    assert (error.value.referencia, error.value.atributos) == ("LIQ-9", atributos)
    assert error.value.reglas == [r.nombre for r in sorted(reglas, key=lambda r: r.prioridad)]
