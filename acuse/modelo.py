"""Modelo de Acuse.

Tesis: **una entrega sin acuse verificable no es una entrega.**

Nace de una patología real y medible en integraciones asíncronas: el emisor marca
`entregado = True` porque él subió el archivo, y nadie del otro lado confirma que lo
haya leído. Los archivos se apilan en silencio y el síntoma aparece lejos de la causa.

Dos decisiones de diseño lo cierran:
  1. ENVIADO y ACUSADO son estados DISTINTOS. Sólo el receptor produce el acuse.
  2. El enrutamiento es explícito: un trabajo que no casa con ninguna regla se RECHAZA
     de forma ruidosa. Nunca cae en un canal por omisión.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def a_utc(momento: datetime) -> datetime:
    """El mismo instante, expresado en UTC. Una fecha sin zona NO se adivina: es un error.

    Trampa real que esto cierra: Python resta dos `datetime` con el MISMO `tzinfo` como
    horas de pared, ignorando el cambio de horario. Enviado a la 01:30 y revisado a las
    03:30 de la madrugada en que se adelanta el reloj dan «2 h» aunque sólo pasó 1 h, y
    una entrega vence antes de tiempo (o, en otoño, después). Los plazos de acuse se
    miden en tiempo absoluto, así que toda resta pasa antes por UTC.
    """
    if momento.tzinfo is None or momento.utcoffset() is None:
        raise ValueError(
            f"La fecha {momento.isoformat()} no trae zona horaria. Un instante sin zona no "
            "se puede comparar contra un plazo: adivinarle una zona es mover el plazo."
        )
    return momento.astimezone(timezone.utc)


class EstadoEntrega(str, enum.Enum):
    PENDIENTE = "PENDIENTE"      # aceptada, aún no se intenta
    ENVIADO = "ENVIADO"          # el emisor la puso en el canal · NO es entrega
    ACUSADO = "ACUSADO"          # el RECEPTOR confirmó · esto sí es entrega
    SIN_ACUSE = "SIN_ACUSE"      # se envió y venció el plazo sin confirmación
    FALLIDO = "FALLIDO"          # el canal rechazó el envío
    RECHAZADO = "RECHAZADO"      # no se pudo enrutar: no hay regla que lo cubra


ESTADOS_ABIERTOS = {EstadoEntrega.PENDIENTE, EstadoEntrega.ENVIADO}
ESTADOS_PROBLEMA = {EstadoEntrega.SIN_ACUSE, EstadoEntrega.FALLIDO, EstadoEntrega.RECHAZADO}


class ModoCanal(str, enum.Enum):
    SINCRONO = "SINCRONO"        # el acuse llega en la misma llamada
    ASINCRONO = "ASINCRONO"      # el acuse llega después, por devolución del receptor


@dataclass(frozen=True)
class ReglaRuteo:
    """Regla EXPLÍCITA. Sin coincidencia no hay canal por omisión."""
    nombre: str
    canal: str
    cuando: dict[str, str]       # atributos del trabajo que deben coincidir exactamente
    prioridad: int = 100

    def aplica(self, atributos: dict[str, str]) -> bool:
        return all(atributos.get(k) == v for k, v in self.cuando.items())


@dataclass
class Trabajo:
    """Una liquidación a entregar."""
    id: str
    referencia: str                       # identificador de negocio (folio, documento…)
    atributos: dict[str, str] = field(default_factory=dict)
    carga: dict = field(default_factory=dict)
    lote: str | None = None
    creado_en: datetime = field(default_factory=ahora)


@dataclass
class Entrega:
    """El registro que hace imposible confundir «envié» con «llegó»."""
    id: str
    trabajo_id: str
    referencia: str
    canal: str | None
    estado: EstadoEntrega = EstadoEntrega.PENDIENTE
    intentos: int = 0
    plazo_acuse_s: int = 900
    enviado_en: datetime | None = None
    acusado_en: datetime | None = None
    acuse_por: str | None = None          # QUIÉN confirmó: sin actor no hay acuse
    detalle: str = ""
    idempotencia: str = ""                # misma clave = misma entrega, no duplica

    @property
    def entregado(self) -> bool:
        """Sólo ACUSADO cuenta como entregado. Deliberadamente NO incluye ENVIADO."""
        return self.estado is EstadoEntrega.ACUSADO

    def vencida(self, momento: datetime | None = None) -> bool:
        """ENVIADO y con MÁS de `plazo_acuse_s` transcurridos (en el límite exacto, aún no)."""
        if self.estado is not EstadoEntrega.ENVIADO or self.enviado_en is None:
            return False
        transcurrido = a_utc(momento or ahora()) - a_utc(self.enviado_en)
        return transcurrido > timedelta(seconds=self.plazo_acuse_s)


@dataclass
class Brecha:
    """Lo que el emisor cree entregado y el receptor no ha confirmado."""
    canal: str
    enviados: int
    acusados: int
    sin_acuse: int
    mas_antiguo_s: int

    @property
    def hay_brecha(self) -> bool:
        return self.sin_acuse > 0
