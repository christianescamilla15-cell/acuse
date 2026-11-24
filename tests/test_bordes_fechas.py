"""Bordes de fechas: el plazo de acuse se mide en tiempo absoluto.

Defecto que fijan (encontrado en la semana 3): `Entrega.vencida` restaba dos fechas con
la MISMA zona y Python las resta como horas de pared. La madrugada del cambio de horario
una entrega vencía una hora antes (primavera) o una hora después (otoño). Ahora toda
resta pasa por UTC (`acuse.modelo.a_utc`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from apoyo import nueva_bd, sembrar_entrega

from acuse.conciliador import brechas, marcar_vencidas
from acuse.modelo import Entrega, EstadoEntrega, a_utc

NUEVA_YORK = ZoneInfo("America/New_York")
MADRID = ZoneInfo("Europe/Madrid")
CDMX = ZoneInfo("America/Mexico_City")
TOKIO = ZoneInfo("Asia/Tokyo")


def _enviada(enviado_en: datetime, plazo_s: int, estado=EstadoEntrega.ENVIADO) -> Entrega:
    return Entrega(
        id="E-1", trabajo_id="T-1", referencia="LIQ-1", canal="buzon",
        estado=estado, plazo_acuse_s=plazo_s, enviado_en=enviado_en,
    )


# (caso, enviado_en, momento, plazo_s, ¿vencida?) — el «real» es el tiempo absoluto transcurrido.
CASOS_HORARIO = [
    # Primavera en Nueva York: 01:30 EST → 03:30 EDT son 2 h de pared y 1 h real.
    ("NY primavera, 1 h real = plazo: no vence",
     datetime(2026, 3, 8, 1, 30, tzinfo=NUEVA_YORK), datetime(2026, 3, 8, 3, 30, tzinfo=NUEVA_YORK), 3600, False),
    ("NY primavera, 1 h real > plazo de 59:59: vence",
     datetime(2026, 3, 8, 1, 30, tzinfo=NUEVA_YORK), datetime(2026, 3, 8, 3, 30, tzinfo=NUEVA_YORK), 3599, True),
    # Otoño en Nueva York: la 01:30 se repite; fold=1 es la segunda (EST), 1 h real después.
    ("NY otoño, misma hora de pared y 1 h real: vence con plazo de 30 min",
     datetime(2026, 11, 1, 1, 30, tzinfo=NUEVA_YORK),
     datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=NUEVA_YORK), 1800, True),
    ("NY otoño, 01:00 EDT → 01:59 EST son 1 h 59 min reales",
     datetime(2026, 11, 1, 1, 0, tzinfo=NUEVA_YORK),
     datetime(2026, 11, 1, 1, 59, fold=1, tzinfo=NUEVA_YORK), 7139, True),
    # Primavera en Madrid: 01:30 CET → 03:30 CEST, 1 h real.
    ("Madrid primavera, 1 h real = plazo: no vence",
     datetime(2026, 3, 29, 1, 30, tzinfo=MADRID), datetime(2026, 3, 29, 3, 30, tzinfo=MADRID), 3600, False),
    ("Madrid otoño, 02:30 CEST → 02:30 CET (fold) es 1 h real",
     datetime(2026, 10, 25, 2, 30, tzinfo=MADRID),
     datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=MADRID), 3599, True),
    # Ciudad de México ya no cambia de horario (desde 2022): el antiguo domingo de abril son 2 h reales.
    ("CDMX sin horario de verano: 2 h de pared = 2 h reales",
     datetime(2026, 4, 5, 1, 30, tzinfo=CDMX), datetime(2026, 4, 5, 3, 30, tzinfo=CDMX), 7199, True),
    ("CDMX sin horario de verano: plazo de 2 h exactas no vence",
     datetime(2026, 4, 5, 1, 30, tzinfo=CDMX), datetime(2026, 4, 5, 3, 30, tzinfo=CDMX), 7200, False),
    # Zonas distintas: enviado en Tokio, revisado en CDMX, 15 min reales.
    ("zonas distintas, mismo instante + 15 min",
     datetime(2026, 6, 1, 9, 0, tzinfo=TOKIO), datetime(2026, 5, 31, 18, 15, tzinfo=CDMX), 900, False),
    ("zonas distintas, mismo instante + 15 min y 1 s",
     datetime(2026, 6, 1, 9, 0, tzinfo=TOKIO), datetime(2026, 5, 31, 18, 15, 1, tzinfo=CDMX), 900, True),
]


@pytest.mark.parametrize(
    ("enviado_en", "momento", "plazo_s", "vencida"),
    [c[1:] for c in CASOS_HORARIO],
    ids=[c[0] for c in CASOS_HORARIO],
)
def test_el_plazo_se_mide_en_tiempo_absoluto_aun_con_cambio_de_horario(enviado_en, momento, plazo_s, vencida):
    assert _enviada(enviado_en, plazo_s).vencida(momento) is vencida


@pytest.mark.parametrize(
    ("desplazamiento", "vencida"),
    [
        (timedelta(seconds=-1), False),
        (timedelta(0), False),                      # en el límite exacto todavía no vence
        (timedelta(microseconds=1), True),          # un microsegundo después, sí
        (timedelta(seconds=1), True),
        (timedelta(days=400), True),
    ],
    ids=["un-segundo-antes", "limite-exacto", "un-microsegundo-despues", "un-segundo-despues", "un-año-despues"],
)
def test_el_limite_del_plazo_es_estricto(desplazamiento, vencida):
    enviado = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    plazo = 900
    assert _enviada(enviado, plazo).vencida(enviado + timedelta(seconds=plazo) + desplazamiento) is vencida


@pytest.mark.parametrize(
    "estado", [e for e in EstadoEntrega if e is not EstadoEntrega.ENVIADO], ids=lambda e: e.value
)
def test_solo_una_entrega_enviado_puede_vencer(estado):
    enviado = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _enviada(enviado, 1, estado=estado).vencida(enviado + timedelta(days=30)) is False


def test_sin_fecha_de_envio_no_vence():
    entrega = _enviada(datetime(2026, 1, 1, tzinfo=timezone.utc), 1)
    entrega.enviado_en = None
    assert entrega.vencida() is False


@pytest.mark.parametrize(
    "ingenua",
    [datetime(2026, 3, 8, 3, 30), datetime(2026, 1, 1)],
    ids=["madrugada-del-cambio", "año-nuevo"],
)
def test_una_fecha_sin_zona_no_se_adivina(ingenua):
    with pytest.raises(ValueError, match="no trae zona horaria"):
        a_utc(ingenua)
    with pytest.raises(ValueError, match="no trae zona horaria"):
        _enviada(datetime(2026, 1, 1, tzinfo=timezone.utc), 60).vencida(ingenua)


@pytest.mark.parametrize(
    "zona", [timezone.utc, NUEVA_YORK, MADRID, CDMX, TOKIO, timezone(timedelta(hours=5, minutes=45))],
    ids=["UTC", "Nueva-York", "Madrid", "CDMX", "Tokio", "+05:45"],
)
def test_a_utc_conserva_el_instante(zona):
    local = datetime(2026, 7, 4, 23, 59, 59, 999999, tzinfo=zona)
    utc = a_utc(local)
    assert utc.tzinfo is timezone.utc
    assert utc == local                       # el mismo instante, sólo otra forma de escribirlo


# ------------------------------------------------------ el conciliador mide igual


def test_el_conciliador_marca_vencidas_en_tiempo_absoluto_con_reloj_local():
    """Revisión con la hora de Nueva York la mañana del cambio: 1 h real, no 2."""
    sesiones = nueva_bd()
    enviado = datetime(2026, 3, 8, 1, 30, tzinfo=NUEVA_YORK)
    sembrar_entrega(sesiones, EstadoEntrega.ENVIADO, referencia="LIQ-NY", enviado_en=enviado, plazo_acuse_s=3600)
    momento = datetime(2026, 3, 8, 3, 30, tzinfo=NUEVA_YORK)

    with sesiones() as ses:
        (brecha,) = brechas(ses, momento)
        assert brecha.mas_antiguo_s == 3600
        assert brecha.sin_acuse == 0
        assert marcar_vencidas(ses, momento) == 0
    with sesiones() as ses:
        assert marcar_vencidas(ses, momento + timedelta(seconds=1)) == 1


def test_el_conciliador_rechaza_un_momento_sin_zona():
    sesiones = nueva_bd()
    with sesiones() as ses, pytest.raises(ValueError, match="no trae zona horaria"):
        brechas(ses, datetime(2026, 3, 8, 3, 30))
    with sesiones() as ses, pytest.raises(ValueError, match="no trae zona horaria"):
        marcar_vencidas(ses, datetime(2026, 3, 8, 3, 30))
