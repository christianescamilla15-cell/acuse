"""Máquina de estados de la entrega: qué EVENTO lleva de qué estado a cuál.

Antes de esta pieza las transiciones vivían repartidas como asignaciones sueltas en el
orquestador y en el conciliador. Funcionaban, pero ninguna línea de código decía cuáles
eran válidas, así que tampoco se podía probar que las inválidas fueran imposibles. Aquí
la tabla es un dato y todo cambio de estado pasa por :func:`siguiente`.

Las transiciones se definen por evento, no por pareja de estados, porque el mismo par
puede ser válido por un camino e inválido por otro: SIN_ACUSE → ACUSADO es legítimo si
se REENVÍA por un canal síncrono que confirma en la llamada, pero NO si llega un acuse
tardío del receptor (el incumplimiento ocurrió y no se borra; ver
`Orquestador.registrar_acuse`).

    PENDIENTE ─┬─ SIN_RUTA ──────────► RECHAZADO ─┐
               ├─ FALLA_CANAL ───────► FALLIDO ───┤  (se pueden reintentar:
               ├─ DEPOSITO ──────────► ENVIADO    │   vuelven a despacharse
               └─ ACUSE_INMEDIATO ───► ACUSADO    │   con los mismos eventos)
                                                  │
    ENVIADO ───┬─ ACUSE_RECEPTOR ────► ACUSADO    │
               └─ VENCIMIENTO ───────► SIN_ACUSE ─┘

    ACUSADO es terminal: ningún evento lo saca de ahí.
"""
from __future__ import annotations

import enum

from acuse.modelo import EstadoEntrega


class Evento(str, enum.Enum):
    SIN_RUTA = "SIN_RUTA"                  # ninguna regla de ruteo casa
    FALLA_CANAL = "FALLA_CANAL"            # canal no registrado, revienta o no acepta
    DEPOSITO = "DEPOSITO"                  # el canal acepta, sin acuse (asíncrono)
    ACUSE_INMEDIATO = "ACUSE_INMEDIATO"    # el receptor confirma en la misma llamada
    ACUSE_RECEPTOR = "ACUSE_RECEPTOR"      # el receptor confirma después, con actor
    VENCIMIENTO = "VENCIMIENTO"            # pasó el plazo sin acuse


#: Estados desde los que se puede (re)intentar el despacho. ENVIADO y ACUSADO no: o ya
#: está esperando acuse o ya se entregó, y reenviarlos duplicaría la entrega.
DESPACHABLES: frozenset[EstadoEntrega] = frozenset({
    EstadoEntrega.PENDIENTE,
    EstadoEntrega.FALLIDO,
    EstadoEntrega.RECHAZADO,
    EstadoEntrega.SIN_ACUSE,
})

_DESTINO: dict[Evento, EstadoEntrega] = {
    Evento.SIN_RUTA: EstadoEntrega.RECHAZADO,
    Evento.FALLA_CANAL: EstadoEntrega.FALLIDO,
    Evento.DEPOSITO: EstadoEntrega.ENVIADO,
    Evento.ACUSE_INMEDIATO: EstadoEntrega.ACUSADO,
    Evento.ACUSE_RECEPTOR: EstadoEntrega.ACUSADO,
    Evento.VENCIMIENTO: EstadoEntrega.SIN_ACUSE,
}

_ORIGENES: dict[Evento, frozenset[EstadoEntrega]] = {
    Evento.SIN_RUTA: DESPACHABLES,
    Evento.FALLA_CANAL: DESPACHABLES,
    Evento.DEPOSITO: DESPACHABLES,
    Evento.ACUSE_INMEDIATO: DESPACHABLES,
    # Sólo se acusa lo que está esperando acuse. Ni lo rechazado ni lo fallido llegaron
    # al receptor, y lo vencido conserva su incumplimiento.
    Evento.ACUSE_RECEPTOR: frozenset({EstadoEntrega.ENVIADO}),
    Evento.VENCIMIENTO: frozenset({EstadoEntrega.ENVIADO}),
}

#: Tabla completa (estado, evento) → estado. Lo que no está aquí es una transición inválida.
TRANSICIONES: dict[tuple[EstadoEntrega, Evento], EstadoEntrega] = {
    (origen, evento): _DESTINO[evento]
    for evento, origenes in _ORIGENES.items()
    for origen in origenes
}


class TransicionInvalida(ValueError):
    """Se pidió un cambio de estado que el modelo no permite."""

    def __init__(self, estado: EstadoEntrega, evento: Evento) -> None:
        self.estado = estado
        self.evento = evento
        validos = sorted(e.value for (o, e) in TRANSICIONES if o is estado)
        super().__init__(
            f"Transición inválida: {evento.value} no aplica a una entrega {estado.value}. "
            f"Eventos válidos desde {estado.value}: {', '.join(validos) or 'ninguno (terminal)'}."
        )


def es_valida(estado: EstadoEntrega, evento: Evento) -> bool:
    return (estado, evento) in TRANSICIONES


def siguiente(estado: EstadoEntrega, evento: Evento) -> EstadoEntrega:
    """Estado al que lleva `evento` desde `estado`; si no es válido, lo dice en voz alta."""
    try:
        return TRANSICIONES[(estado, evento)]
    except KeyError:
        raise TransicionInvalida(estado, evento) from None


__all__ = ["DESPACHABLES", "Evento", "TRANSICIONES", "TransicionInvalida", "es_valida", "siguiente"]
