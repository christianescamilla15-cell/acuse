"""Bordes del orquestador y del conciliador: vacíos, plazos por canal, actor y desenlaces."""
from __future__ import annotations

from datetime import timedelta

import pytest

from acuse.conciliador import brechas, marcar_vencidas, resumen
from acuse.modelo import EstadoEntrega, ModoCanal, ahora
from acuse.persistencia import EstadoCorrida

from apoyo import (
    CanalDoble,
    entrega_por_referencia,
    nueva_bd,
    orquestador,
    sembrar_entrega,
    tipos_evento,
    trabajo,
)


@pytest.mark.parametrize(
    ("plazo_canal", "esperado"),
    [(60, 60), (3600, 3600), (0, 900), (-5, 900), (None, 900), ("60", 900), (60.0, 900)],
    ids=["canal-60", "canal-3600", "canal-cero", "canal-negativo", "canal-sin-plazo", "canal-texto", "canal-float"],
)
def test_cada_canal_impone_su_plazo_y_si_no_es_valido_rige_el_del_orquestador(plazo_canal, esperado):
    sesiones = nueva_bd()
    canal = CanalDoble("buzon", ModoCanal.ASINCRONO)
    canal.plazo_acuse_s = plazo_canal
    orquestador(sesiones, {"buzon": canal}, plazo_acuse_s=900).procesar([trabajo("001")])
    assert entrega_por_referencia(sesiones, "LIQ-001").plazo_acuse_s == esperado


def test_el_actor_del_acuse_se_guarda_sin_espacios_alrededor():
    sesiones = nueva_bd()
    orq = orquestador(sesiones)
    orq.procesar([trabajo("002")])
    assert orq.registrar_acuse(["LIQ-002"], actor="  receptor-liquidacion  ") == 1
    assert entrega_por_referencia(sesiones, "LIQ-002").acuse_por == "receptor-liquidacion"


def test_acusar_una_lista_vacia_no_toca_nada():
    sesiones = nueva_bd()
    orq = orquestador(sesiones)
    orq.procesar([trabajo("003")])
    eventos = tipos_evento(sesiones)
    assert orq.registrar_acuse([], actor="receptor") == 0
    assert tipos_evento(sesiones) == eventos


@pytest.mark.parametrize("actor", ["", "   ", "\t\n"], ids=["vacio", "espacios", "blancos"])
def test_sin_actor_no_hay_acuse_aunque_la_lista_este_vacia(actor):
    with pytest.raises(ValueError, match="sin actor"):
        orquestador(nueva_bd()).registrar_acuse([], actor=actor)


@pytest.mark.parametrize(
    ("destinos", "estado"),
    [
        (["registro"], EstadoCorrida.EXITOSA),
        (["buzon"], EstadoCorrida.EXITOSA),
        (["registro", "nadie"], EstadoCorrida.CON_PROBLEMAS),
        (["nadie"], EstadoCorrida.CON_PROBLEMAS),
        ([], EstadoCorrida.SIN_INSUMO),
    ],
    ids=["sincrono", "asincrono", "uno-sin-ruta", "todo-sin-ruta", "vacia"],
)
def test_el_desenlace_de_la_corrida(destinos, estado):
    corrida = orquestador(nueva_bd()).procesar([trabajo(f"{i:03d}", d) for i, d in enumerate(destinos)])
    assert corrida.estado is estado
    assert corrida.tomados == len(destinos)
    assert corrida.terminada_en is not None and corrida.terminada_en >= corrida.iniciada_en


def test_una_corrida_exitosa_con_duplicados_lo_dice_en_su_detalle():
    sesiones = nueva_bd()
    orq = orquestador(sesiones)
    orq.procesar([trabajo("010")])
    corrida = orq.procesar([trabajo("010"), trabajo("011")])
    assert corrida.estado is EstadoCorrida.EXITOSA
    assert "1 ignorados por duplicados" in corrida.detalle
    assert "todavía NO son entregas" in corrida.detalle


def test_un_canal_sincrono_que_no_acepta_deja_fallido_sin_actor():
    sesiones = nueva_bd()
    canal = CanalDoble("registro", ModoCanal.SINCRONO, aceptado=False)
    orquestador(sesiones, {"registro": canal}).procesar([trabajo("012", "registro")])
    fila = entrega_por_referencia(sesiones, "LIQ-012")
    assert (fila.estado, fila.acuse_por, fila.acusado_en) == (EstadoEntrega.FALLIDO, None, None)


# ------------------------------------------------------------------ conciliador


def test_en_una_base_vacia_no_hay_brechas_y_el_resumen_lista_todos_los_estados_en_cero():
    sesiones = nueva_bd()
    with sesiones() as ses:
        assert brechas(ses) == []
        foto = resumen(ses)
        assert marcar_vencidas(ses) == 0
    assert foto["total"] == foto["entregado_segun_emisor"] == foto["entregado_confirmado"] == 0
    assert foto["por_estado"] == {e.value: 0 for e in EstadoEntrega}


def test_la_antiguedad_de_la_brecha_cuenta_tambien_lo_ya_marcado_sin_acuse():
    sesiones = nueva_bd()
    sembrar_entrega(sesiones, EstadoEntrega.SIN_ACUSE, referencia="LIQ-VIEJA", enviado_hace_s=86_400)
    sembrar_entrega(sesiones, EstadoEntrega.ENVIADO, referencia="LIQ-NUEVA", enviado_hace_s=10, plazo_acuse_s=900)
    with sesiones() as ses:
        (brecha,) = brechas(ses)
    assert (brecha.enviados, brecha.sin_acuse) == (2, 1)
    assert 86_400 <= brecha.mas_antiguo_s < 86_400 + 60


def test_un_canal_con_todo_acusado_no_tiene_brecha():
    sesiones = nueva_bd()
    orquestador(sesiones).procesar([trabajo("020", "registro"), trabajo("021", "registro")])
    with sesiones() as ses:
        (brecha,) = brechas(ses, ahora() + timedelta(days=365))
    assert (brecha.acusados, brecha.enviados, brecha.hay_brecha, brecha.mas_antiguo_s) == (2, 0, False, 0)


def test_marcar_vencidas_deja_constancia_del_conciliador_como_actor():
    sesiones = nueva_bd()
    sembrar_entrega(sesiones, EstadoEntrega.ENVIADO, referencia="LIQ-030", enviado_hace_s=3600, plazo_acuse_s=60)
    with sesiones() as ses:
        assert marcar_vencidas(ses) == 1
    assert tipos_evento(sesiones) == ["sin_acuse"]
    fila = entrega_por_referencia(sesiones, "LIQ-030")
    assert "excedido por" in fila.detalle and fila.estado is EstadoEntrega.SIN_ACUSE
