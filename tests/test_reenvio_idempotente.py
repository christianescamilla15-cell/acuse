"""Reenvío idempotente: el mismo lote dos veces no duplica nada.

Ni filas, ni llamadas al canal, ni objetos en el buzón, ni acuses, ni contadores. Lo que
sí cambia en la segunda pasada es la bitácora, que deja constancia de cada duplicado.

Defecto que fijan (semana 3): un trabajo repetido DENTRO de la misma corrida se
reintentaba en la misma pasada si el primer intento fallaba (dos llamadas al canal, dos
fallidos contados para una sola entrega). Ahora se procesa una sola vez.
"""
from __future__ import annotations

import pytest

from acuse.conciliador import brechas, resumen
from acuse.demo import CANAL_ASINCRONO, CANAL_LOTE, CANAL_SINCRONO, LOTE_SEMBRADO, canales, reglas, trabajos_sembrados
from acuse.modelo import EstadoEntrega, ModoCanal, Trabajo
from acuse.orquestador import Orquestador
from acuse.persistencia import EstadoCorrida
from acuse.ruteo import Enrutador

from apoyo import (
    CanalDoble,
    entrega_por_referencia,
    entregas,
    nueva_bd,
    orquestador,
    tipos_evento,
    trabajo,
)

# (desenlace, destino, canales) — cada forma en que puede terminar el primer intento.
DESENLACES = {
    "acusado-en-la-llamada": ("registro", {"registro": CanalDoble("registro", ModoCanal.SINCRONO)}),
    "enviado-sin-acuse": ("buzon", {"buzon": CanalDoble("buzon", ModoCanal.ASINCRONO)}),
    "rechazado-sin-regla": ("nadie", {"buzon": CanalDoble("buzon", ModoCanal.ASINCRONO)}),
    "fallido-no-aceptado": ("buzon", {"buzon": CanalDoble("buzon", ModoCanal.ASINCRONO, aceptado=False)}),
    "fallido-revienta": ("buzon", {"buzon": CanalDoble("buzon", ModoCanal.ASINCRONO, explota=True)}),
}


def _llamadas(canales_usados) -> int:
    return sum(len(c.enviados) for c in canales_usados.values())


@pytest.mark.parametrize("desenlace", list(DESENLACES), ids=list(DESENLACES))
def test_el_mismo_trabajo_dos_veces_en_una_corrida_se_procesa_una_vez(desenlace):
    destino, canales_usados = DESENLACES[desenlace]
    # Instancias nuevas por caso: el doble cuenta sus llamadas.
    canales_usados = {n: CanalDoble(c.nombre, c.modo, aceptado=c.aceptado, explota=c.explota)
                      for n, c in canales_usados.items()}
    sesiones = nueva_bd()
    t = trabajo("001", destino)

    corrida = orquestador(sesiones, canales_usados).procesar([t, t, t])

    assert len(entregas(sesiones)) == 1
    assert _llamadas(canales_usados) <= 1
    assert entregas(sesiones)[0].intentos <= 1
    procesados = corrida.enviados + corrida.acusados + corrida.rechazados + corrida.fallidos
    assert procesados == 1                       # una entrega, contada una vez
    assert tipos_evento(sesiones).count("duplicado_en_corrida") == 2


@pytest.mark.parametrize("desenlace", ["acusado-en-la-llamada", "enviado-sin-acuse"])
def test_reenviar_en_otra_corrida_lo_que_esta_vivo_no_lo_vuelve_a_enviar(desenlace):
    destino, plantilla = DESENLACES[desenlace]
    canales_usados = {n: CanalDoble(c.nombre, c.modo) for n, c in plantilla.items()}
    sesiones = nueva_bd()
    orq = orquestador(sesiones, canales_usados)

    orq.procesar([trabajo("002", destino)])
    antes = entrega_por_referencia(sesiones, "LIQ-002")
    segunda = orq.procesar([trabajo("002", destino)])
    despues = entrega_por_referencia(sesiones, "LIQ-002")

    assert _llamadas(canales_usados) == 1
    assert (despues.id, despues.estado, despues.intentos, despues.enviado_en) == (
        antes.id, antes.estado, antes.intentos, antes.enviado_en)
    assert segunda.estado is EstadoCorrida.SIN_INSUMO


def test_el_lote_sembrado_dos_veces_no_duplica_nada():
    """Los 30 trabajos de la demo, por los canales reales, dos veces."""
    sesiones = nueva_bd()
    instancias = canales()
    orq = Orquestador(sesiones, Enrutador(reglas()), instancias)

    primera = orq.procesar(trabajos_sembrados())
    with sesiones() as ses:
        foto_1, brechas_1 = resumen(ses), brechas(ses)
    segunda = orq.procesar(trabajos_sembrados())
    with sesiones() as ses:
        foto_2, brechas_2 = resumen(ses), brechas(ses)

    assert (foto_2, brechas_2) == (foto_1, brechas_1)
    assert len(entregas(sesiones)) == 30
    assert len(instancias[CANAL_SINCRONO].recibidos) == 10
    assert instancias[CANAL_ASINCRONO].depositados == 14
    assert len(instancias[CANAL_LOTE].lotes_depositados) == 1
    assert len(instancias[CANAL_LOTE].referencias_del_lote(LOTE_SEMBRADO)) == 4

    assert primera.estado is EstadoCorrida.CON_PROBLEMAS
    # Los 28 vivos se ignoran; los 2 sin ruta se REINTENTAN (sobre la misma fila) y
    # vuelven a quedar RECHAZADO con motivo: reintentar no es duplicar.
    assert (segunda.enviados, segunda.acusados, segunda.rechazados) == (0, 0, 2)
    eventos = tipos_evento(sesiones)
    assert eventos.count("duplicado_ignorado") == 28
    assert eventos.count("reintento") == 2


def test_reenviar_despues_del_acuse_no_lo_reabre():
    sesiones = nueva_bd()
    orq = orquestador(sesiones)
    orq.procesar([trabajo("003")])
    assert orq.registrar_acuse(["LIQ-003"], actor="receptor") == 1

    orq.procesar([trabajo("003")])

    fila = entrega_por_referencia(sesiones, "LIQ-003")
    assert fila.estado is EstadoEntrega.ACUSADO and fila.acuse_por == "receptor"


def test_acusar_dos_veces_no_cuenta_dos_ni_cambia_al_autor():
    sesiones = nueva_bd()
    orq = orquestador(sesiones)
    orq.procesar([trabajo("004")])

    assert orq.registrar_acuse(["LIQ-004", "LIQ-004"], actor="receptor-1") == 1
    primera = entrega_por_referencia(sesiones, "LIQ-004")
    assert orq.registrar_acuse(["LIQ-004"], actor="receptor-2") == 0
    segunda = entrega_por_referencia(sesiones, "LIQ-004")

    assert (segunda.acuse_por, segunda.acusado_en) == ("receptor-1", primera.acusado_en)
    assert tipos_evento(sesiones).count("acuse_ignorado") == 2


def test_la_clave_es_trabajo_mas_referencia_y_el_acuse_es_por_referencia():
    """Dos trabajos distintos con la misma referencia son dos entregas; un acuse por esa
    referencia las confirma a ambas (el receptor sólo conoce la referencia)."""
    sesiones = nueva_bd()
    orq = orquestador(sesiones)
    orq.procesar([
        Trabajo(id="T-A", referencia="LIQ-COMUN", atributos={"destino": "buzon"}),
        Trabajo(id="T-B", referencia="LIQ-COMUN", atributos={"destino": "buzon"}),
    ])

    assert sorted(e.idempotencia for e in entregas(sesiones)) == ["T-A:LIQ-COMUN", "T-B:LIQ-COMUN"]
    assert orq.registrar_acuse(["LIQ-COMUN"], actor="receptor") == 2
