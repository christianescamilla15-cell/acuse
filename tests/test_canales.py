"""Pruebas de los canales.

Cada prueba fija por escrito una de las tres formas del éxito silencioso:

  1. enviado ≠ recibido → el asíncrono NUNCA acusa al enviar.
  2. ruteo por omisión   → el canal por lote rechaza en vez de inventar un lote.
  3. verde ≠ hizo algo   → depositar es «aceptado» y aun así deja pendientes.
"""
from __future__ import annotations

import pytest

from acuse.canales import (
    AcuseImposible,
    CanalDuplicado,
    CanalNoRegistrado,
    ResultadoEnvio,
    enviar_por,
    limpiar_registro,
    obtener,
    registrar,
    validar_resultado,
)
from acuse.canales.asincrono import CanalAsincrono
from acuse.canales.lote import CanalLote, LoteDesconocido, carga_de_lote
from acuse.canales.sincrono import CanalSincrono
from acuse.modelo import Entrega, ModoCanal, Trabajo


def entrega(referencia: str, trabajo_id: str = "T-1", canal: str = "c") -> Entrega:
    return Entrega(
        id=f"E-{referencia}",
        trabajo_id=trabajo_id,
        referencia=referencia,
        canal=canal,
    )


@pytest.fixture(autouse=True)
def registro_limpio():
    """El registro es global: cada prueba parte de cero."""
    limpiar_registro()
    yield
    limpiar_registro()


# --------------------------------------------------------------------------------------
# Canal síncrono: sí sabe si llegó
# --------------------------------------------------------------------------------------
def test_sincrono_acusa_de_inmediato():
    canal = CanalSincrono("receptor-directo")
    resultado = canal.enviar(entrega("LIQ-001"), {"monto": 100})

    assert resultado.aceptado is True
    assert resultado.acuse_inmediato is True
    assert resultado.acuse_por == "receptor:receptor-directo"
    assert canal.recibidos == ["LIQ-001"]


def test_sincrono_receptor_caido_no_acepta():
    canal = CanalSincrono("receptor-directo", receptor_disponible=False)
    resultado = canal.enviar(entrega("LIQ-002"), {})

    assert resultado.aceptado is False
    assert resultado.acuse_inmediato is False
    assert resultado.acuse_por is None
    assert resultado.detalle  # un fallo sin motivo se archiva como ruido
    assert canal.recibidos == []


def test_sincrono_rechazo_es_determinista_sin_azar():
    """La misma referencia da siempre el mismo veredicto, en cualquier instancia."""
    a = CanalSincrono("receptor-x", tasa_rechazo=0.5)
    b = CanalSincrono("receptor-x", tasa_rechazo=0.5)
    refs = [f"LIQ-{i:03d}" for i in range(40)]

    veredictos_a = [a.rechaza(r) for r in refs]
    veredictos_b = [b.rechaza(r) for r in refs]

    assert veredictos_a == veredictos_b
    assert veredictos_a == [a.rechaza(r) for r in refs]  # estable al repetir
    assert any(veredictos_a) and not all(veredictos_a)  # la tasa hace algo


def test_sincrono_rechazo_total_no_acepta_nada():
    canal = CanalSincrono("receptor-estricto", tasa_rechazo=1.0)
    resultado = canal.enviar(entrega("LIQ-003"), {})

    assert resultado.aceptado is False
    assert "rechazó" in resultado.detalle


# --------------------------------------------------------------------------------------
# Canal asíncrono: depositar NO es entregar
# --------------------------------------------------------------------------------------
def test_asincrono_nunca_acusa_al_enviar():
    """El corazón de la tesis: el depósito es exitoso y aun así NO hay entrega."""
    canal = CanalAsincrono("deposito-objetos")
    resultado = canal.enviar(entrega("LIQ-010"), {"monto": 1})

    assert resultado.aceptado is True          # el depósito sí funcionó
    assert resultado.acuse_inmediato is False  # y aun así nadie recibió nada
    assert resultado.acuse_por is None


def test_asincrono_lo_depositado_queda_pendiente_hasta_consumir():
    canal = CanalAsincrono("deposito-objetos")
    for i in range(3):
        assert canal.enviar(entrega(f"LIQ-{i}"), {}).aceptado is True

    # Nadie del otro lado ha leído: tres objetos apilados, cero acuses.
    assert canal.pendientes() == 3
    assert canal.depositados == 3
    assert canal.referencias_pendientes() == ["LIQ-0", "LIQ-1", "LIQ-2"]

    consumidas = canal.consumir()

    assert consumidas == ["LIQ-0", "LIQ-1", "LIQ-2"]
    assert canal.pendientes() == 0
    assert canal.depositados == 3  # el histórico se conserva como evidencia


def test_asincrono_consumir_devuelve_las_referencias_en_orden_y_por_partes():
    canal = CanalAsincrono("deposito-objetos")
    for ref in ("A-1", "A-2", "A-3"):
        canal.enviar(entrega(ref), {})

    assert canal.consumir(2) == ["A-1", "A-2"]
    assert canal.pendientes() == 1
    assert canal.consumir() == ["A-3"]
    assert canal.consumir() == []  # no re-acusa lo ya leído


def test_asincrono_sin_consumidor_la_pila_crece_en_silencio():
    """Verde ≠ hizo algo: 25 envíos «exitosos» y 25 entregas sin acusar."""
    canal = CanalAsincrono("deposito-objetos")
    resultados = [canal.enviar(entrega(f"LIQ-{i}"), {}) for i in range(25)]

    assert all(r.aceptado for r in resultados)
    assert not any(r.acuse_inmediato for r in resultados)
    assert canal.pendientes() == 25
    assert canal.mas_antiguo_pendiente() is not None


# --------------------------------------------------------------------------------------
# Canal por lote: un objeto por lote, no uno por registro
# --------------------------------------------------------------------------------------
def test_lote_agrupa_tres_trabajos_en_un_solo_objeto():
    canal = CanalLote("deposito-lotes")
    trabajos = [
        Trabajo(id=f"T-{i}", referencia=f"LIQ-{i}", lote="CORTE-2026-01", carga={"monto": i})
        for i in range(3)
    ]

    for t in trabajos:
        resultado = canal.enviar(entrega(t.referencia, t.id), carga_de_lote(t))
        assert resultado.aceptado is True
        assert resultado.acuse_inmediato is False  # sigue sin ser una entrega

    # 3 registros ⇒ 1 objeto, no 3 eventos.
    assert len(canal.lotes_depositados) == 1
    assert canal.pendientes() == 1
    assert canal.referencias_del_lote("CORTE-2026-01") == ["LIQ-0", "LIQ-1", "LIQ-2"]


def test_lote_consumir_lote_acusa_todas_sus_referencias():
    canal = CanalLote("deposito-lotes")
    for i in range(3):
        t = Trabajo(id=f"T-{i}", referencia=f"LIQ-{i}", lote="CORTE-A")
        canal.enviar(entrega(t.referencia, t.id), carga_de_lote(t))

    assert canal.consumir_lote("CORTE-A") == ["LIQ-0", "LIQ-1", "LIQ-2"]
    assert canal.pendientes() == 0
    assert canal.consumir_lote("CORTE-A") == []  # un lote se acusa una vez

    with pytest.raises(LoteDesconocido):
        canal.consumir_lote("CORTE-QUE-NO-EXISTE")


def test_lote_separa_lotes_distintos():
    canal = CanalLote("deposito-lotes")
    for lote in ("CORTE-A", "CORTE-B"):
        for i in range(2):
            t = Trabajo(id=f"T-{lote}-{i}", referencia=f"{lote}-{i}", lote=lote)
            canal.enviar(entrega(t.referencia, t.id), carga_de_lote(t))

    assert len(canal.lotes_depositados) == 2
    canal.consumir_lote("CORTE-A")
    assert canal.pendientes() == 1  # B sigue sin leerse y se ve


def test_lote_sin_lote_se_rechaza_en_vez_de_inventar_uno():
    """Ruteo por omisión: el campo ausente NO elige destino."""
    canal = CanalLote("deposito-lotes")
    resultado = canal.enviar(entrega("LIQ-SIN-LOTE"), {"monto": 1})

    assert resultado.aceptado is False
    assert canal.lotes_depositados == {}
    assert "lote" in resultado.detalle

    with pytest.raises(ValueError):
        carga_de_lote(Trabajo(id="T-9", referencia="LIQ-9", lote=None))


def test_lote_ya_consumido_no_se_reabre_en_silencio():
    canal = CanalLote("deposito-lotes")
    t1 = Trabajo(id="T-1", referencia="LIQ-1", lote="CORTE-C")
    canal.enviar(entrega(t1.referencia, t1.id), carga_de_lote(t1))
    canal.consumir_lote("CORTE-C")

    t2 = Trabajo(id="T-2", referencia="LIQ-2", lote="CORTE-C")
    resultado = canal.enviar(entrega(t2.referencia, t2.id), carga_de_lote(t2))

    assert resultado.aceptado is False
    assert canal.referencias_del_lote("CORTE-C") == ["LIQ-1"]


# --------------------------------------------------------------------------------------
# Registro e invariante del acuse
# --------------------------------------------------------------------------------------
def test_registro_da_de_alta_y_resuelve_por_nombre():
    sincrono = registrar(CanalSincrono("directo"))
    registrar(CanalAsincrono("diferido"))

    assert obtener("directo") is sincrono
    assert obtener("diferido").modo is ModoCanal.ASINCRONO

    with pytest.raises(CanalDuplicado):
        registrar(CanalSincrono("directo"))
    with pytest.raises(CanalNoRegistrado):
        obtener("inexistente")


def test_enviar_por_valida_el_invariante_del_acuse():
    registrar(CanalSincrono("directo"))
    registrar(CanalAsincrono("diferido"))

    assert enviar_por("directo", entrega("LIQ-20"), {}).acuse_inmediato is True
    assert enviar_por("diferido", entrega("LIQ-21"), {}).acuse_inmediato is False


def test_un_canal_asincrono_no_puede_declarar_acuse_inmediato():
    """El invariante duro: si un asíncrono dice que acusó, es un error, no un dato."""
    canal = CanalAsincrono("diferido")
    mentira = ResultadoEnvio(aceptado=True, acuse_inmediato=True, acuse_por="receptor:diferido")

    with pytest.raises(AcuseImposible):
        validar_resultado(canal, mentira)

    assert validar_resultado(CanalSincrono("directo"), mentira) is mentira


def test_un_acuse_sin_actor_no_es_un_acuse():
    with pytest.raises(AcuseImposible):
        ResultadoEnvio(aceptado=True, acuse_inmediato=True, acuse_por=None)
    with pytest.raises(ValueError):
        ResultadoEnvio(aceptado=False, detalle="")
