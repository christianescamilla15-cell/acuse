"""Apoyo común de las pruebas: base real, dobles de canal y entregas sembradas a mano.

Las pruebas nuevas comparten esto para no redefinir el mismo doble en cada archivo. La
base es SQLite: en memoria (`StaticPool`, la misma que usa la demo) o en archivo cuando
la prueba necesita reabrirla (ver `test_persistencia_sqlite.py`).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from acuse.canales import ResultadoEnvio
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

RAIZ_REPO = Path(__file__).resolve().parents[1]

REGLAS = [
    ReglaRuteo(nombre="al-registro", canal="registro", cuando={"destino": "registro"}, prioridad=10),
    ReglaRuteo(nombre="al-buzon", canal="buzon", cuando={"destino": "buzon"}, prioridad=20),
]


class CanalDoble:
    """Canal de prueba conforme a `acuse.canales.Canal`.

    Cuenta cuántas veces se le llamó por referencia: es la evidencia de que un reenvío no
    duplicó el envío.
    """

    def __init__(
        self,
        nombre: str,
        modo: ModoCanal,
        *,
        aceptado: bool = True,
        explota: bool = False,
        plazo_acuse_s: int = 900,
    ) -> None:
        self.nombre = nombre
        self.modo = modo
        self.aceptado = aceptado
        self.explota = explota
        self.plazo_acuse_s = plazo_acuse_s
        self.enviados: list[str] = []

    def enviar(self, entrega, carga: dict) -> ResultadoEnvio:
        self.enviados.append(entrega.referencia)
        if self.explota:
            raise RuntimeError("el canal se cayó a media llamada")
        if not self.aceptado:
            return ResultadoEnvio(aceptado=False, detalle=f"«{self.nombre}» no admitió el envío.")
        inmediato = self.modo is ModoCanal.SINCRONO
        return ResultadoEnvio(
            aceptado=True,
            acuse_inmediato=inmediato,
            acuse_por=f"receptor:{self.nombre}" if inmediato else None,
        )


def nueva_bd(url: str = "sqlite+pysqlite:///:memory:"):
    engine = crear_engine(url)
    crear_tablas(engine)
    return crear_session_factory(engine)


def canales_dobles() -> dict[str, CanalDoble]:
    return {
        "registro": CanalDoble("registro", ModoCanal.SINCRONO),
        "buzon": CanalDoble("buzon", ModoCanal.ASINCRONO),
    }


def orquestador(sesiones, canales=None, **kwargs) -> Orquestador:
    return Orquestador(sesiones, Enrutador(REGLAS), canales or canales_dobles(), **kwargs)


def trabajo(sufijo: str, destino: str = "buzon", **carga) -> Trabajo:
    return Trabajo(
        id=f"T-{sufijo}",
        referencia=f"LIQ-{sufijo}",
        atributos={"destino": destino},
        carga=carga or {"importe": "100.00", "moneda": "MXN"},
    )


def sembrar_entrega(
    sesiones,
    estado: EstadoEntrega,
    *,
    referencia: str | None = None,
    canal: str | None = "buzon",
    enviado_hace_s: int | None = 10_000,
    plazo_acuse_s: int = 60,
    enviado_en: datetime | None = None,
) -> str:
    """Inserta una entrega directamente en un estado dado (para probar transiciones)."""
    referencia = referencia or f"LIQ-{uuid.uuid4().hex[:8]}"
    if enviado_en is None and enviado_hace_s is not None and estado is not EstadoEntrega.PENDIENTE:
        enviado_en = ahora() - timedelta(seconds=enviado_hace_s)
    with sesiones() as ses:
        fila = EntregaORM(
            id=uuid.uuid4().hex,
            trabajo_id=f"T-{referencia}",
            referencia=referencia,
            canal=None if estado is EstadoEntrega.RECHAZADO else canal,
            estado=estado,
            intentos=0 if estado is EstadoEntrega.PENDIENTE else 1,
            plazo_acuse_s=plazo_acuse_s,
            enviado_en=enviado_en,
            acusado_en=ahora() if estado is EstadoEntrega.ACUSADO else None,
            acuse_por="receptor:previo" if estado is EstadoEntrega.ACUSADO else None,
            detalle="sembrada por la prueba",
            idempotencia=f"T-{referencia}:{referencia}",
        )
        ses.add(fila)
        ses.commit()
        return fila.id


def entregas(sesiones) -> list[EntregaORM]:
    with sesiones() as ses:
        return list(ses.scalars(select(EntregaORM).order_by(EntregaORM.referencia)))


def entrega_por_referencia(sesiones, referencia: str) -> EntregaORM:
    with sesiones() as ses:
        return ses.scalars(select(EntregaORM).where(EntregaORM.referencia == referencia)).one()


def tipos_evento(sesiones) -> list[str]:
    with sesiones() as ses:
        return list(ses.scalars(select(EventoORM.tipo).order_by(EventoORM.id)))
