"""Bordes de los canales: el contrato de registro y los parámetros que no se admiten."""
from __future__ import annotations

import pytest

from acuse.canales import (
    REGISTRO,
    AcuseImposible,
    CanalInvalido,
    CanalNoRegistrado,
    ResultadoEnvio,
    limpiar_registro,
    obtener,
    registrar,
)
from acuse.canales.asincrono import CanalAsincrono
from acuse.canales.lote import CanalLote
from acuse.canales.sincrono import CanalSincrono
from acuse.modelo import Entrega, ModoCanal


@pytest.fixture(autouse=True)
def registro_limpio():
    limpiar_registro()
    yield
    limpiar_registro()


class CanalArmable:
    """Canal al que se le puede romper cualquier parte del contrato."""

    def __init__(self, **campos) -> None:
        self.nombre, self.modo, self.plazo_acuse_s = "armable", ModoCanal.ASINCRONO, 60
        self.enviar = lambda entrega, carga: ResultadoEnvio(aceptado=True)
        for campo, valor in campos.items():
            setattr(self, campo, valor)


@pytest.mark.parametrize(
    ("campos", "motivo"),
    [
        ({"nombre": ""}, "nombre"),
        ({"nombre": "   "}, "nombre"),
        ({"nombre": None}, "nombre"),
        ({"nombre": 7}, "nombre"),
        ({"modo": "ASINCRONO"}, "modo"),          # un texto no es un ModoCanal
        ({"plazo_acuse_s": 0}, "plazo_acuse_s"),
        ({"plazo_acuse_s": -60}, "plazo_acuse_s"),
        ({"plazo_acuse_s": True}, "plazo_acuse_s"),   # bool es int en Python: no es un plazo
        ({"plazo_acuse_s": 60.0}, "plazo_acuse_s"),
        ({"plazo_acuse_s": None}, "plazo_acuse_s"),
        ({"enviar": None}, "enviar"),
    ],
    ids=["nombre-vacio", "nombre-en-blanco", "nombre-nulo", "nombre-numero", "modo-texto", "plazo-cero",
         "plazo-negativo", "plazo-booleano", "plazo-float", "plazo-nulo", "sin-enviar"],
)
def test_registrar_valida_el_contrato_antes_de_que_alguien_dependa_del_canal(campos, motivo):
    with pytest.raises(CanalInvalido, match=motivo):
        registrar(CanalArmable(**campos))
    assert REGISTRO == {}                     # lo inválido no queda registrado a medias


def test_reemplazar_un_canal_solo_es_posible_si_se_pide():
    primero = registrar(CanalSincrono("x"))
    segundo = CanalAsincrono("x")
    assert registrar(segundo, reemplazar=True) is segundo
    assert obtener("x") is segundo and obtener("x") is not primero


def test_pedir_un_canal_no_registrado_dice_cuales_hay():
    registrar(CanalSincrono("directo"))
    with pytest.raises(CanalNoRegistrado) as error:
        obtener("diferido")
    assert "directo" in str(error.value) and "No existe canal por omisión" in str(error.value)


@pytest.mark.parametrize(
    ("fabrica", "motivo"),
    [
        (lambda: CanalSincrono("s", tasa_rechazo=-0.01), "tasa_rechazo"),
        (lambda: CanalSincrono("s", tasa_rechazo=1.01), "tasa_rechazo"),
        (lambda: CanalSincrono("s", plazo_acuse_s=0), "plazo_acuse_s"),
        (lambda: CanalAsincrono("a", plazo_acuse_s=0), "plazo_acuse_s"),
        (lambda: CanalAsincrono("a", plazo_acuse_s=-1), "plazo_acuse_s"),
        (lambda: CanalLote("l", plazo_acuse_s=0), "plazo_acuse_s"),
    ],
    ids=["tasa-negativa", "tasa-mayor-a-uno", "sincrono-plazo-cero", "asincrono-plazo-cero",
         "asincrono-plazo-negativo", "lote-plazo-cero"],
)
def test_los_canales_no_se_construyen_con_parametros_imposibles(fabrica, motivo):
    with pytest.raises(ValueError, match=motivo):
        fabrica()


@pytest.mark.parametrize(
    "kwargs",
    [{"aceptado": True, "acuse_inmediato": True}, {"aceptado": False, "acuse_inmediato": True, "acuse_por": "r",
                                                   "detalle": "x"}],
    ids=["acuse-sin-actor", "acuse-de-lo-no-aceptado"],
)
def test_un_resultado_imposible_no_se_puede_construir(kwargs):
    with pytest.raises(AcuseImposible):
        ResultadoEnvio(**kwargs)


def _entrega(referencia: str) -> Entrega:
    return Entrega(id=f"E-{referencia}", trabajo_id=f"T-{referencia}", referencia=referencia, canal="c")


def test_consumir_cero_o_negativo_en_el_asincrono():
    canal = CanalAsincrono("a")
    canal.enviar(_entrega("R1"), {})
    assert canal.consumir(0) == [] and canal.pendientes() == 1
    with pytest.raises(ValueError, match="negativo"):
        canal.consumir(-1)


def test_consumir_un_buzon_vacio_no_inventa_nada():
    canal = CanalAsincrono("a")
    assert canal.consumir() == [] and canal.pendientes() == 0 and canal.mas_antiguo_pendiente() is None


def test_la_misma_referencia_dos_veces_en_un_lote_viaja_una_sola_vez():
    canal = CanalLote("l")
    for _ in range(3):
        assert canal.enviar(_entrega("R1"), {"lote": "L1", "importe": "1", "moneda": "MXN"}).aceptado
    assert canal.referencias_del_lote("L1") == ["R1"]
    assert len(canal.registros_del_lote("L1")) == 1


@pytest.mark.parametrize("lote", ["", "   ", None, 7], ids=["vacio", "en-blanco", "nulo", "no-texto"])
def test_un_lote_vacio_o_que_no_es_texto_se_rechaza_en_voz_alta(lote):
    canal = CanalLote("l")
    resultado = canal.enviar(_entrega("R1"), {"lote": lote})
    assert resultado.aceptado is False and "lote" in resultado.detalle
    assert canal.lotes_depositados == {}


def test_el_lote_puede_viajar_en_otra_llave_declarada():
    canal = CanalLote("l", clave_lote="corte")
    assert canal.enviar(_entrega("R1"), {"corte": "C-1", "importe": "1"}).aceptado
    assert canal.registros_del_lote("C-1") == [("R1", {"importe": "1"})]   # la llave del lote no se repite


def test_leer_los_registros_devuelve_copias():
    canal = CanalLote("l")
    canal.enviar(_entrega("R1"), {"lote": "L1", "importe": "1"})
    canal.registros_del_lote("L1")[0][1]["importe"] = "999"
    assert canal.registros_del_lote("L1")[0][1]["importe"] == "1"
