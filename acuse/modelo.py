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
        if self.estado is not EstadoEntrega.ENVIADO or self.enviado_en is None:
            return False
        return (momento or ahora()) - self.enviado_en > timedelta(seconds=self.plazo_acuse_s)


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
