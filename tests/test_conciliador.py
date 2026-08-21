"""Pruebas del conciliador.

Miden el caso que motiva el proyecto: nadie del otro lado consume, todo el tablero
sigue en verde, y la única señal de que algo va mal es la brecha entre lo que el
emisor cree entregado y lo que alguien confirmó haber recibido.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from acuse.canales import ResultadoEnvio
from acuse.conciliador import brechas, marcar_vencidas, resumen
from acuse.modelo import EstadoEntrega, ModoCanal, ReglaRuteo, Trabajo, ahora
from acuse.orquestador import Orquestador
from acuse.persistencia import (
    EntregaORM,
    EventoORM,
    crear_engine,
    crear_session_factory,
    crear_tablas,
)
from acuse.ruteo import Enrutador

PLAZO_S = 60  # plazo corto: la prueba no espera, adelanta el reloj


class CanalDoble:
    """Canal de prueba conforme a `acuse.canales.Canal`: siempre acepta el envío."""

    def __init__(
        self,
        nombre: str,
        modo: ModoCanal,
        *,
        acuse_por: str | None = None,
        plazo_acuse_s: int = PLAZO_S,
    ) -> None:
        self.nombre = nombre
        self.modo = modo
        self.acuse_por = acuse_por
        self.plazo_acuse_s = plazo_acuse_s

    def enviar(self, entrega, carga: dict) -> ResultadoEnvio:
        inmediato = self.modo is ModoCanal.SINCRONO
        return ResultadoEnvio(
            aceptado=True,
            acuse_inmediato=inmediato,
            acuse_por=(self.acuse_por or f"receptor:{self.nombre}") if inmediato else None,
        )


REGLAS = [
    ReglaRuteo(nombre="al-buzon", canal="buzon", cuando={"destino": "buzon"}, prioridad=10),
    ReglaRuteo(
        nombre="al-registro", canal="registro", cuando={"destino": "registro"}, prioridad=20
    ),
]


@pytest.fixture()
def sesiones():
    engine = crear_engine("sqlite+pysqlite:///:memory:")
    crear_tablas(engine)
    return crear_session_factory(engine)


@pytest.fixture()
def orq(sesiones):
    canales = {
        "buzon": CanalDoble("buzon", ModoCanal.ASINCRONO),
        "registro": CanalDoble("registro", ModoCanal.SINCRONO, acuse_por="sistema-de-registro"),
    }
    return Orquestador(sesiones, Enrutador(REGLAS), canales, plazo_acuse_s=PLAZO_S)


def _trabajos(destino: str, *sufijos: str) -> list[Trabajo]:
    return [
        Trabajo(id=f"T-{s}", referencia=f"LIQ-{s}", atributos={"destino": destino})
        for s in sufijos
    ]


def _una(brechas_lista, canal):
    return next(b for b in brechas_lista if b.canal == canal)


# ------------------------------------------------ nadie consume: la brecha crece


def test_sin_consumir_la_brecha_crece(sesiones, orq):
    """Tres corridas «exitosas» seguidas y cero entregas confirmadas."""
    with sesiones() as ses:
        assert brechas(ses) == []

    orq.procesar(_trabajos("buzon", "001"))
    with sesiones() as ses:
        assert _una(brechas(ses), "buzon").enviados == 1

    orq.procesar(_trabajos("buzon", "002", "003"))
    with sesiones() as ses:
        brecha = _una(brechas(ses), "buzon")
        assert brecha.enviados == 3
        assert brecha.acusados == 0
        # Todavía dentro del plazo: hay envíos sin acuse, pero aún no incumplen.
        assert brecha.sin_acuse == 0
        assert brecha.hay_brecha is False

    # Se adelanta el reloj más allá del plazo convenido.
    despues = ahora() + timedelta(seconds=PLAZO_S * 10)
    with sesiones() as ses:
        brecha = _una(brechas(ses, despues), "buzon")
        assert brecha.sin_acuse == 3
        assert brecha.hay_brecha is True
        assert brecha.mas_antiguo_s >= PLAZO_S


def test_la_brecha_separa_los_canales(sesiones, orq):
    """Un canal que confirma y otro que no, no se promedian en un solo número."""
    orq.procesar(_trabajos("buzon", "010", "011") + _trabajos("registro", "012"))

    with sesiones() as ses:
        lista = brechas(ses)

    assert [b.canal for b in lista] == ["buzon", "registro"]
    assert (_una(lista, "buzon").enviados, _una(lista, "buzon").acusados) == (2, 0)
    assert (_una(lista, "registro").enviados, _una(lista, "registro").acusados) == (0, 1)


def test_los_rechazados_no_se_esconden_en_ningun_canal(sesiones, orq):
    """Sin canal no hay brecha de canal; el rechazo se ve en el resumen."""
    orq.procesar([Trabajo(id="T-013", referencia="LIQ-013", atributos={"destino": "?"})])

    with sesiones() as ses:
        assert brechas(ses) == []
        foto = resumen(ses)

    assert foto["por_estado"][EstadoEntrega.RECHAZADO.value] == 1
    assert foto["entregado_segun_emisor"] == 0
    assert foto["con_problema"] == 1


# ------------------------------------------------------------- marcar_vencidas


def test_marcar_vencidas_mueve_a_sin_acuse(sesiones, orq):
    orq.procesar(_trabajos("buzon", "020", "021"))
    despues = ahora() + timedelta(seconds=PLAZO_S * 10)

    with sesiones() as ses:
        assert marcar_vencidas(ses, despues) == 2

    with sesiones() as ses:
        entregas = list(ses.scalars(select(EntregaORM)))
        tipos = list(ses.scalars(select(EventoORM.tipo)))

    assert {e.estado for e in entregas} == {EstadoEntrega.SIN_ACUSE}
    assert all(e.entregado is False for e in entregas)
    # El detalle explica el plazo excedido, no deja un código que haya que descifrar.
    assert all(str(PLAZO_S) in e.detalle for e in entregas)
    assert all("plazo" in e.detalle for e in entregas)
    assert tipos.count("sin_acuse") == 2


def test_dentro_del_plazo_no_se_marca_nada(sesiones, orq):
    orq.procesar(_trabajos("buzon", "022"))

    with sesiones() as ses:
        assert marcar_vencidas(ses, ahora() + timedelta(seconds=PLAZO_S // 2)) == 0
        assert list(ses.scalars(select(EntregaORM.estado))) == [EstadoEntrega.ENVIADO]


def test_marcar_vencidas_es_idempotente_y_no_borra_la_brecha(sesiones, orq):
    """Marcar el problema no es resolverlo: el número no puede bajar por mirarlo."""
    orq.procesar(_trabajos("buzon", "023", "024"))
    despues = ahora() + timedelta(seconds=PLAZO_S * 10)

    with sesiones() as ses:
        antes = _una(brechas(ses, despues), "buzon")
        marcar_vencidas(ses, despues)

    with sesiones() as ses:
        assert marcar_vencidas(ses, despues) == 0
        ahora_brecha = _una(brechas(ses, despues), "buzon")

    assert (ahora_brecha.enviados, ahora_brecha.sin_acuse) == (antes.enviados, antes.sin_acuse)
    assert ahora_brecha.sin_acuse == 2
    assert ahora_brecha.hay_brecha is True


def test_el_acuse_si_cierra_la_brecha(sesiones, orq):
    """Lo único que baja el número es que alguien confirme haber recibido."""
    orq.procesar(_trabajos("buzon", "025", "026"))
    orq.registrar_acuse(["LIQ-025", "LIQ-026"], actor="receptor-liquidacion")
    despues = ahora() + timedelta(seconds=PLAZO_S * 10)

    with sesiones() as ses:
        assert marcar_vencidas(ses, despues) == 0
        brecha = _una(brechas(ses, despues), "buzon")

    assert (brecha.enviados, brecha.acusados, brecha.sin_acuse) == (0, 2, 0)
    assert brecha.hay_brecha is False


# --------------------------------------------------- los DOS números del resumen


def test_resumen_distingue_segun_emisor_de_confirmado(sesiones, orq):
    """El caso que motiva el proyecto, en dos cifras que no se pueden confundir."""
    orq.procesar(_trabajos("buzon", "030", "031") + _trabajos("registro", "032"))

    with sesiones() as ses:
        foto = resumen(ses)

    assert foto["total"] == 3
    # Lo que el emisor reportaría contando «lo que salió»…
    assert foto["entregado_segun_emisor"] == 3
    # …contra lo que alguien confirmó haber recibido.
    assert foto["entregado_confirmado"] == 1
    assert foto["entregadas"] == 1
    assert foto["brecha_de_acuse"] == 2
    assert "2" in foto["nota"]

    assert foto["por_estado"][EstadoEntrega.ENVIADO.value] == 2
    assert foto["por_estado"][EstadoEntrega.ACUSADO.value] == 1
    # Todos los estados figuran, aunque estén en cero: lo que no se reporta no se vigila.
    assert set(foto["por_estado"]) == {e.value for e in EstadoEntrega}


def test_el_resumen_deja_de_mentir_cuando_hay_acuse(sesiones, orq):
    orq.procesar(_trabajos("buzon", "040", "041"))
    orq.registrar_acuse(["LIQ-040", "LIQ-041"], actor="receptor-liquidacion")

    with sesiones() as ses:
        foto = resumen(ses)

    assert foto["entregado_segun_emisor"] == foto["entregado_confirmado"] == 2
    assert foto["brecha_de_acuse"] == 0
    assert "Sin brecha" in foto["nota"]


def test_vencer_no_mejora_el_numero_confirmado(sesiones, orq):
    """Al vencer, lo no confirmado sale de ENVIADO: la cifra honesta NO sube."""
    orq.procesar(_trabajos("buzon", "050", "051"))
    despues = ahora() + timedelta(seconds=PLAZO_S * 10)

    with sesiones() as ses:
        marcar_vencidas(ses, despues)
        foto = resumen(ses)

    assert foto["entregado_confirmado"] == 0
    assert foto["entregado_segun_emisor"] == 0   # ya no puede presumirlas como enviadas
    assert foto["sin_acuse"] == 2
    assert foto["con_problema"] == 2
