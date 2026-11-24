"""Integración contra la persistencia REAL: SQLite en archivo.

Acuse no usa un servidor de base de datos: persiste en SQLite, en memoria por omisión y
en archivo con `ACUSE_BD=sqlite+pysqlite:///ruta.db` (ver docs/adr/0006). Por eso la
integración no levanta un contenedor: abre un archivo de verdad, lo cierra, lo vuelve a
abrir con otro engine y comprueba que lo escrito sigue ahí, con sus zonas horarias, sus
restricciones y su bitácora. Es la misma base que corre en la imagen.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from apoyo import canales_dobles, orquestador, trabajo
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, StatementError

import acuse.api.app as api_app
from acuse.conciliador import brechas, resumen
from acuse.modelo import EstadoEntrega
from acuse.persistencia import (
    CorridaORM,
    EntregaORM,
    EstadoCorrida,
    EventoORM,
    crear_engine,
    crear_session_factory,
    crear_tablas,
)


@pytest.fixture()
def url_archivo(tmp_path):
    return f"sqlite+pysqlite:///{tmp_path / 'acuse.db'}"


def _abrir(url):
    engine = crear_engine(url)
    crear_tablas(engine)
    return engine, crear_session_factory(engine)


def _entrega(**campos) -> EntregaORM:
    ref = campos.pop("referencia", f"LIQ-{uuid.uuid4().hex[:6]}")
    return EntregaORM(id=uuid.uuid4().hex, trabajo_id=f"T-{ref}", referencia=ref, canal="buzon",
                      idempotencia=campos.pop("idempotencia", f"T-{ref}:{ref}"), **campos)


def test_lo_escrito_sobrevive_a_cerrar_y_reabrir_el_archivo(url_archivo):
    engine, sesiones = _abrir(url_archivo)
    orq = orquestador(sesiones, canales_dobles())
    orq.procesar([trabajo("001", "registro"), trabajo("002", "buzon"), trabajo("003", "nadie")])
    orq.registrar_acuse(["LIQ-002"], actor="receptor")
    with sesiones() as ses:
        foto, lista = resumen(ses), brechas(ses)
    engine.dispose()

    engine2, sesiones2 = _abrir(url_archivo)          # otro engine, el mismo archivo
    with sesiones2() as ses:
        assert (resumen(ses), brechas(ses)) == (foto, lista)
        assert ses.scalar(select(CorridaORM.estado)) is EstadoCorrida.CON_PROBLEMAS
        tipos = list(ses.scalars(select(EventoORM.tipo).order_by(EventoORM.id)))
    assert tipos[-1] == "acusado" and "rechazado" in tipos
    engine2.dispose()


@pytest.mark.parametrize(
    "instante",
    [
        datetime(2026, 3, 8, 3, 30, tzinfo=ZoneInfo("America/New_York")),
        datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=ZoneInfo("America/New_York")),   # hora repetida
        datetime(2026, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Madrid")),
        datetime(2026, 5, 31, 23, 59, 59, 999999, tzinfo=ZoneInfo("America/Mexico_City")),
        datetime(2026, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=-3, minutes=-30))),
        datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
    ],
    ids=["NY-primavera", "NY-hora-repetida", "Madrid-otoño", "CDMX-fin-de-mes", "-03:30", "UTC"],
)
def test_el_archivo_devuelve_el_mismo_instante_en_utc(url_archivo, instante):
    """SQLite no guarda el offset: FechaUTC normaliza al escribir y repone la zona al leer."""
    engine, sesiones = _abrir(url_archivo)
    with sesiones() as ses:
        ses.add(_entrega(referencia="LIQ-T", estado=EstadoEntrega.ENVIADO, enviado_en=instante))
        ses.commit()
    engine.dispose()

    engine2, sesiones2 = _abrir(url_archivo)
    with sesiones2() as ses:
        leido = ses.scalar(select(EntregaORM.enviado_en))
    engine2.dispose()
    assert leido.tzinfo is timezone.utc
    # Se compara contra el instante pasado a UTC: por PEP 495, una hora AMBIGUA (la que se
    # repite en otoño) nunca es «==» a una fecha de otra zona, aunque sea el mismo instante.
    assert leido == instante.astimezone(timezone.utc)
    assert leido.timestamp() == instante.timestamp()


def test_una_fecha_sin_zona_se_guarda_como_utc(url_archivo):
    """Regla de la capa de datos (FechaUTC): lo ingenuo se interpreta como UTC, nunca como hora local."""
    _, sesiones = _abrir(url_archivo)
    with sesiones() as ses:
        ses.add(_entrega(referencia="LIQ-N", enviado_en=datetime(2026, 3, 8, 3, 30)))
        ses.commit()
        assert ses.scalar(select(EntregaORM.enviado_en)) == datetime(2026, 3, 8, 3, 30, tzinfo=timezone.utc)


def test_la_base_impide_dos_entregas_con_la_misma_clave(url_archivo):
    """El índice único es la última defensa aunque la lógica de aplicación se equivoque."""
    _, sesiones = _abrir(url_archivo)
    with sesiones() as ses:
        ses.add(_entrega(referencia="LIQ-A", idempotencia="T-1:LIQ-1"))
        ses.add(_entrega(referencia="LIQ-B", idempotencia="T-1:LIQ-1"))
        with pytest.raises(IntegrityError):
            ses.commit()


def test_la_base_no_acepta_un_estado_que_el_modelo_no_conoce(url_archivo):
    _, sesiones = _abrir(url_archivo)
    with sesiones() as ses:
        ses.add(_entrega(referencia="LIQ-X", estado="ENTREGADO"))
        with pytest.raises(StatementError):
            ses.commit()


def test_un_estado_desconocido_en_el_archivo_no_se_lee_en_silencio(url_archivo):
    """Si alguien escribe a mano un estado que no existe, leerlo revienta: no se reinterpreta."""
    _, sesiones = _abrir(url_archivo)
    with sesiones() as ses:
        ses.add(_entrega(referencia="LIQ-Y"))
        ses.commit()
        ses.execute(text("UPDATE entregas SET estado = 'ENTREGADO'"))
        ses.commit()
    with sesiones() as ses, pytest.raises(LookupError):
        ses.scalar(select(EntregaORM))


def test_crear_tablas_es_idempotente_sobre_un_archivo_con_datos(url_archivo):
    engine, sesiones = _abrir(url_archivo)
    orquestador(sesiones).procesar([trabajo("010")])
    crear_tablas(engine)
    crear_tablas(engine)
    with sesiones() as ses:
        assert ses.scalar(select(EntregaORM.referencia)) == "LIQ-010"


def test_un_acuse_sin_entrega_queda_en_la_bitacora_sin_llave_foranea_rota(url_archivo):
    _, sesiones = _abrir(url_archivo)
    with sesiones() as ses:
        ses.execute(text("PRAGMA foreign_keys = ON"))
    assert orquestador(sesiones).registrar_acuse(["LIQ-NADIE"], actor="receptor") == 0
    with sesiones() as ses:
        evento = ses.scalar(select(EventoORM))
    assert (evento.tipo, evento.entrega_id, evento.actor) == ("acuse_sin_entrega", None, "receptor")


# ------------------------------------------------- la API con ACUSE_BD en archivo


@pytest.fixture()
def api_en_archivo(monkeypatch, url_archivo):
    monkeypatch.setattr(api_app, "URL_BD", url_archivo)
    yield api_app
    monkeypatch.undo()
    api_app._construir(vaciar=True)            # deja la app como la encuentran las demás pruebas


def test_con_archivo_la_api_conserva_el_historico_y_no_reusa_folios(api_en_archivo):
    primero = api_en_archivo._construir(vaciar=True)
    assert primero.siembra["trabajos"] == 30 and primero.siguiente_folio == 31

    segundo = api_en_archivo._construir()       # reinicio del proceso sobre el mismo archivo
    assert segundo.siembra == {}                # no se vuelve a sembrar encima del histórico
    assert segundo.siguiente_folio == 31        # y la numeración continúa
    with segundo.session_factory() as ses:
        assert resumen(ses)["total"] == 30
