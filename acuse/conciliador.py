"""Conciliador: mide la distancia entre «lo mandé» y «lo tienen».

Sin esta capa el sistema puede estar 100% en verde mientras nada llega al otro lado:
el emisor apila envíos, cada uno termina «bien», y el síntoma aparece días después y
lejos de la causa. Aquí esa distancia tiene un número, un canal y una antigüedad.

Tres decisiones que hacen que la medición no se pueda maquillar:

  * Una entrega marcada SIN_ACUSE **sigue contando** en la brecha. Si sólo contáramos
    las ENVIADO vencidas, `marcar_vencidas()` haría desaparecer la brecha con sólo
    mirarla: el reporte mejoraría sin que nadie recibiera nada.
  * `mas_antiguo_s` se mide desde el envío. Un canal puede tener pocas entregas sin
    acuse y estar gravemente roto si la más vieja lleva semanas.
  * `resumen()` publica los DOS números —según el emisor y confirmado— uno junto al
    otro. Publicar sólo el primero es la forma más común de este fallo.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from acuse.modelo import Brecha, EstadoEntrega, a_utc, ahora
from acuse.persistencia import EntregaORM, EventoORM

__all__ = ["brechas", "marcar_vencidas", "resumen"]

# Lo que salió por un canal y todavía nadie confirmó.
_SIN_CONFIRMAR = (EstadoEntrega.ENVIADO, EstadoEntrega.SIN_ACUSE)


def brechas(session: Session, momento: datetime | None = None) -> list[Brecha]:
    """Brecha de acuse por canal, ordenada por nombre de canal.

    Semántica de cada campo (coherente y estable a propósito):

      * `enviados`  = salió por el canal y NO tiene acuse (ENVIADO + SIN_ACUSE).
      * `acusados`  = el receptor confirmó (ACUSADO). Sólo esto son entregas.
      * `sin_acuse` = de los `enviados`, los que ya excedieron su plazo
                      (ENVIADO vencidas + las ya marcadas SIN_ACUSE).
      * `mas_antiguo_s` = segundos desde el envío más viejo que sigue sin acuse.

    Así `sin_acuse ⊆ enviados` siempre, y `marcar_vencidas()` no altera ni un número:
    marcar el problema no es resolverlo.

    Las entregas RECHAZADO no aparecen aquí porque no tienen canal — nunca llegaron a
    salir. Se ven en `resumen()`, que no deja fuera ningún estado.

    `momento` puede venir en cualquier zona horaria; la antigüedad se mide en tiempo
    absoluto (UTC), así que un cambio de horario no la alarga ni la acorta.
    """
    momento = a_utc(momento or ahora())

    filas = session.scalars(
        select(EntregaORM).where(
            EntregaORM.canal.is_not(None),
            EntregaORM.estado.in_((EstadoEntrega.ACUSADO, *_SIN_CONFIRMAR)),
        )
    ).all()

    conteos: dict[str, dict[str, int]] = defaultdict(
        lambda: {"enviados": 0, "acusados": 0, "sin_acuse": 0, "mas_antiguo_s": 0}
    )

    for fila in filas:
        canal = fila.canal or ""
        acumulado = conteos[canal]

        if fila.estado is EstadoEntrega.ACUSADO:
            acumulado["acusados"] += 1
            continue

        acumulado["enviados"] += 1

        # Vencida por plazo, o ya marcada como incumplida en una pasada anterior.
        if fila.estado is EstadoEntrega.SIN_ACUSE or fila.vencida(momento):
            acumulado["sin_acuse"] += 1

        if fila.enviado_en is not None:
            antiguedad = int((momento - a_utc(fila.enviado_en)).total_seconds())
            if antiguedad > acumulado["mas_antiguo_s"]:
                acumulado["mas_antiguo_s"] = antiguedad

    return [
        Brecha(
            canal=canal,
            enviados=datos["enviados"],
            acusados=datos["acusados"],
            sin_acuse=datos["sin_acuse"],
            mas_antiguo_s=datos["mas_antiguo_s"],
        )
        for canal, datos in sorted(conteos.items())
    ]


def marcar_vencidas(session: Session, momento: datetime | None = None) -> int:
    """Pasa a SIN_ACUSE las entregas ENVIADO cuyo plazo de acuse ya venció.

    Devuelve cuántas se marcaron. Es idempotente: una segunda pasada devuelve 0 porque
    las anteriores ya salieron de ENVIADO, pero siguen contando en la brecha.

    El plazo es el contrato: pasado ese tiempo sin confirmación, la entrega deja de
    estar «en camino» y pasa a ser un incumplimiento con nombre propio. Que el estado
    cambie solo, sin que nadie levante la mano, es justo lo que evita que el problema
    se descubra semanas después.
    """
    momento = a_utc(momento or ahora())

    candidatas = session.scalars(
        select(EntregaORM).where(
            EntregaORM.estado == EstadoEntrega.ENVIADO,
            EntregaORM.enviado_en.is_not(None),
        )
    ).all()

    marcadas = 0
    for fila in candidatas:
        if not fila.vencida(momento):
            continue

        transcurridos = int((momento - a_utc(fila.enviado_en)).total_seconds())
        exceso = transcurridos - fila.plazo_acuse_s
        fila.estado = EstadoEntrega.SIN_ACUSE
        fila.detalle = (
            f"Sin acuse: se envió por «{fila.canal}» hace {transcurridos}s y el plazo "
            f"convenido es de {fila.plazo_acuse_s}s, excedido por {exceso}s. El envío "
            "fue aceptado por el canal, pero nadie del lado receptor confirmó haberlo "
            "recibido; mientras eso no ocurra NO es una entrega."
        )
        session.add(
            EventoORM(
                entrega_id=fila.id,
                momento=momento,
                tipo="sin_acuse",
                actor="conciliador",
                detalle=fila.detalle,
            )
        )
        marcadas += 1

    # Commit explícito: dejar la mitad marcada y la otra mitad no es peor que no
    # marcar nada, porque el reporte quedaría inconsistente sin avisar.
    session.commit()
    return marcadas


def resumen(session: Session) -> dict:
    """Fotografía global. Publica los DOS números que hacen visible el engaño.

    * `entregado_segun_emisor` = ENVIADO + ACUSADO. Lo que el emisor reportaría si
      contara «lo que salió» como entregado. Es el número que suele viajar a los
      tableros y el que oculta el problema.
    * `entregado_confirmado` = ACUSADO. Lo único que alguien del otro lado confirmó.

    La diferencia entre ambos es `brecha_de_acuse`. Si es distinta de cero, el sistema
    está reportando como entregado algo que nadie ha dicho haber recibido.
    """
    por_estado_bruto = dict(
        session.execute(
            select(EntregaORM.estado, func.count(EntregaORM.id)).group_by(EntregaORM.estado)
        ).all()
    )
    # Todos los estados presentes, incluso en cero: un estado ausente del reporte es un
    # estado que nadie vigila.
    por_estado = {
        estado.value: int(por_estado_bruto.get(estado, 0)) for estado in EstadoEntrega
    }

    total = sum(por_estado.values())
    confirmado = por_estado[EstadoEntrega.ACUSADO.value]
    segun_emisor = confirmado + por_estado[EstadoEntrega.ENVIADO.value]
    brecha = segun_emisor - confirmado

    return {
        "total": total,
        "por_estado": por_estado,
        # «Cuántas realmente entregadas»: sólo las que el receptor confirmó.
        "entregadas": confirmado,
        "entregado_segun_emisor": segun_emisor,
        "entregado_confirmado": confirmado,
        "brecha_de_acuse": brecha,
        "sin_acuse": por_estado[EstadoEntrega.SIN_ACUSE.value],
        "con_problema": (
            por_estado[EstadoEntrega.SIN_ACUSE.value]
            + por_estado[EstadoEntrega.FALLIDO.value]
            + por_estado[EstadoEntrega.RECHAZADO.value]
        ),
        "nota": (
            f"{segun_emisor} figurarían como entregadas si contáramos lo que salió; "
            f"sólo {confirmado} tienen acuse. Diferencia: {brecha}."
            if brecha
            else f"Sin brecha: las {confirmado} que figuran como entregadas tienen acuse."
        ),
    }
