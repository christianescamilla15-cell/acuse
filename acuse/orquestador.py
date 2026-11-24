"""Orquestador: toma trabajos, los enruta, los envía y REGISTRA qué pasó con cada uno.

Las tres invariantes que sostiene, en el mismo orden que las tres patologías:

  1. **enviado ≠ recibido.** Poner algo en un canal produce ENVIADO. ACUSADO sólo lo
     produce un acuse con actor: o el canal síncrono que confirma en la misma llamada,
     o `registrar_acuse()` con quien confirmó. El orquestador NUNCA escribe ACUSADO por
     el simple hecho de que el envío haya sido aceptado.
  2. **ruteo por omisión.** El canal lo decide el `Enrutador`, con reglas explícitas.
     Si ninguna aplica, la entrega queda RECHAZADO con el motivo textual visible. No
     hay canal de respaldo, no hay «si no trae el campo, mándalo por acá».
  3. **verde ≠ hizo algo.** Una corrida sin insumo termina en SIN_INSUMO, jamás en
     EXITOSA. Los contadores separan enviados de acusados para que nadie pueda leer
     «se entregaron 40» cuando lo que hubo fueron 40 envíos sin confirmar.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from acuse.canales import AcuseImposible, Canal, ResultadoEnvio, validar_resultado
from acuse.estados import DESPACHABLES, Evento, es_valida, siguiente
from acuse.modelo import EstadoEntrega, ModoCanal, Trabajo, ahora
from acuse.persistencia import CorridaORM, EntregaORM, EstadoCorrida, EventoORM
from acuse.ruteo import Enrutador, SinRutaAplicable

# El contrato del canal (`Canal`, `ResultadoEnvio`) vive en `acuse.canales` y se
# importa, no se redefine: dos definiciones del mismo contrato divergen y el día que
# lo hagan, la diferencia se pagará en entregas perdidas.
__all__ = ["Canal", "ResultadoEnvio", "Orquestador"]

# Una entrega ya viva —ACUSADO o ENVIADO— NO se reintenta: o el receptor la confirmó o
# está esperando ese acuse, y reenviarla sería entregar dos veces lo mismo. Cuáles se
# pueden despachar lo dice la máquina de estados (`acuse.estados.DESPACHABLES`), y todo
# cambio de estado de este módulo pasa por `siguiente()`.


def _nuevo_id() -> str:
    return uuid.uuid4().hex


def _anotar(
    ses: Session,
    entrega_id: str | None,
    tipo: str,
    detalle: str,
    actor: str | None = None,
    momento: datetime | None = None,
) -> None:
    """Agrega una línea a la bitácora. Append-only: nunca se edita ni se borra."""
    ses.add(
        EventoORM(
            entrega_id=entrega_id,
            momento=momento or ahora(),
            tipo=tipo,
            actor=actor,
            detalle=detalle,
        )
    )


class Orquestador:
    """Coordina ruteo, envío y registro. No sabe cómo viaja nada: eso es del canal."""

    def __init__(
        self,
        session_factory,
        enrutador: Enrutador,
        canales: dict[str, Canal],
        *,
        plazo_acuse_s: int = 900,
    ) -> None:
        self.session_factory = session_factory
        self.enrutador = enrutador
        self.canales = canales
        self.plazo_acuse_s = plazo_acuse_s

    # ------------------------------------------------------------------ procesar

    def procesar(self, trabajos: list[Trabajo]) -> CorridaORM:
        """Procesa un lote y devuelve la corrida con su desenlace.

        Devuelve la fila de `corridas` ya persistida (con `expire_on_commit=False`
        sigue siendo legible fuera de la sesión).
        """
        with self.session_factory() as ses:
            corrida = CorridaORM(
                id=_nuevo_id(),
                iniciada_en=ahora(),
                tomados=len(trabajos),
                estado=EstadoCorrida.SIN_INSUMO,
            )
            ses.add(corrida)
            ses.flush()

            # ── Caso 1: no había NADA que procesar ───────────────────────────────
            if not trabajos:
                corrida.estado = EstadoCorrida.SIN_INSUMO
                corrida.detalle = (
                    "no había insumo: esto NO es una corrida exitosa. Se ejecutó el "
                    "proceso y no se procesó ni un solo trabajo. Puede ser normal "
                    "(ventana sin movimiento) o puede ser que quien produce el insumo "
                    "dejó de producirlo; en ambos casos alguien tiene que verlo. "
                    "Terminar en verde con cero procesados es cómo un frente se cae "
                    "durante días sin que nadie se entere."
                )
                corrida.terminada_en = ahora()
                ses.commit()
                return corrida

            enrutados = enviados = acusados = rechazados = fallidos = duplicados = 0
            # Se copian ANTES del try: si hay que revertir, el objeto `corrida` queda
            # fuera de la sesión y leerle atributos ahí ya no es confiable.
            corrida_id, iniciada_en = corrida.id, corrida.iniciada_en

            try:
                for trabajo in trabajos:
                    # Misma clave = misma entrega. Nunca se duplica.
                    clave = f"{trabajo.id}:{trabajo.referencia}"
                    entrega = ses.scalar(
                        select(EntregaORM).where(EntregaORM.idempotencia == clave)
                    )

                    # ── Caso 2: ya está viva (ACUSADO o ENVIADO) ─────────────────
                    if entrega is not None and entrega.estado not in DESPACHABLES:
                        duplicados += 1
                        _anotar(
                            ses,
                            entrega.id,
                            "duplicado_ignorado",
                            f"El trabajo «{trabajo.id}» ya tiene una entrega en estado "
                            f"{entrega.estado.value} con la clave «{clave}». No se "
                            "vuelve a enviar: reintentar lo que ya está en vuelo "
                            "duplica la entrega y descuadra al receptor.",
                        )
                        continue

                    if entrega is None:
                        entrega = EntregaORM(
                            id=_nuevo_id(),
                            trabajo_id=trabajo.id,
                            referencia=trabajo.referencia,
                            canal=None,
                            estado=EstadoEntrega.PENDIENTE,
                            intentos=0,
                            plazo_acuse_s=self.plazo_acuse_s,
                            idempotencia=clave,
                            lote=trabajo.lote,
                            creado_en=ahora(),
                        )
                        ses.add(entrega)
                        ses.flush()
                        _anotar(ses, entrega.id, "creada", f"Entrega creada para «{clave}».")
                    else:
                        # Existía, pero en un estado no vivo (FALLIDO, RECHAZADO,
                        # SIN_ACUSE): se REUSA la misma fila. Un reintento no es una
                        # entrega nueva; si lo fuera, el histórico contaría dos.
                        _anotar(
                            ses,
                            entrega.id,
                            "reintento",
                            f"Reintento sobre la entrega existente (venía de "
                            f"{entrega.estado.value}).",
                        )

                    # ── Ruteo EXPLÍCITO ──────────────────────────────────────────
                    try:
                        decision = self.enrutador.resolver(
                            trabajo.referencia, trabajo.atributos
                        )
                    except SinRutaAplicable as exc:
                        rechazados += 1
                        entrega.canal = None
                        entrega.estado = siguiente(entrega.estado, Evento.SIN_RUTA)
                        entrega.detalle = str(exc)
                        _anotar(ses, entrega.id, "rechazado", str(exc))
                        continue

                    enrutados += 1
                    entrega.canal = decision.canal
                    _anotar(ses, entrega.id, "enrutado", decision.explicacion)

                    canal = self.canales.get(decision.canal)
                    if canal is None:
                        # El ruteo resolvió un canal que nadie implementa. Es una falla
                        # de configuración, no un caso a resolver por omisión.
                        fallidos += 1
                        entrega.estado = siguiente(entrega.estado, Evento.FALLA_CANAL)
                        entrega.detalle = (
                            f"La regla «{decision.regla}» resolvió el canal "
                            f"«{decision.canal}», pero no hay ningún canal registrado con "
                            f"ese nombre. Canales disponibles: "
                            f"{', '.join(sorted(self.canales)) or 'ninguno'}."
                        )
                        _anotar(ses, entrega.id, "fallido", entrega.detalle)
                        continue

                    # Cada canal puede exigir su propio plazo de acuse.
                    plazo = getattr(canal, "plazo_acuse_s", None)
                    if isinstance(plazo, int) and plazo > 0:
                        entrega.plazo_acuse_s = plazo

                    entrega.intentos += 1
                    momento_envio = ahora()
                    acuse_impugnado = ""

                    try:
                        resultado: ResultadoEnvio = canal.enviar(
                            entrega.a_dominio(), dict(trabajo.carga)
                        )
                    except Exception as exc:  # el canal reventó: es FALLIDO, no un hueco
                        fallidos += 1
                        entrega.estado = siguiente(entrega.estado, Evento.FALLA_CANAL)
                        entrega.detalle = (
                            f"El canal «{decision.canal}» lanzó "
                            f"{type(exc).__name__}: {exc}"
                        )
                        _anotar(ses, entrega.id, "fallido", entrega.detalle)
                        continue

                    # Un canal ASÍNCRONO no puede saber si el receptor recibió. Si aun
                    # así devuelve acuse, se le RECHAZA el acuse —no el envío—: la
                    # entrega queda ENVIADO, que es la verdad comprobable, y la
                    # impugnación queda escrita.
                    try:
                        resultado = validar_resultado(canal, resultado)
                    except AcuseImposible as exc:
                        acuse_impugnado = str(exc)
                        _anotar(ses, entrega.id, "acuse_rechazado", acuse_impugnado)

                    # ── Caso 3: el canal NO aceptó ───────────────────────────────
                    if not getattr(resultado, "aceptado", False):
                        fallidos += 1
                        entrega.estado = siguiente(entrega.estado, Evento.FALLA_CANAL)
                        entrega.detalle = (
                            getattr(resultado, "detalle", "")
                            or f"El canal «{decision.canal}» no aceptó el envío."
                        )
                        _anotar(ses, entrega.id, "fallido", entrega.detalle)
                        continue

                    # ¿Hubo acuse en la misma llamada? Si el canal no lo dice, se
                    # deduce de su modo. La deducción es conservadora: sólo un canal
                    # declarado SÍNCRONO puede acusar en el acto.
                    inmediato = getattr(resultado, "acuse_inmediato", None)
                    if inmediato is None:
                        inmediato = getattr(canal, "modo", None) is ModoCanal.SINCRONO
                    if acuse_impugnado:
                        inmediato = False

                    entrega.enviado_en = momento_envio

                    if inmediato:
                        # Sin actor no hay acuse. Si el canal confirma pero no dice
                        # QUIÉN, el actor es el canal mismo — explícito y rastreable,
                        # nunca una cadena vacía.
                        actor = getattr(resultado, "acuse_por", None) or f"canal:{decision.canal}"
                        acusados += 1
                        entrega.estado = siguiente(entrega.estado, Evento.ACUSE_INMEDIATO)
                        entrega.acusado_en = momento_envio
                        entrega.acuse_por = actor
                        entrega.detalle = (
                            getattr(resultado, "detalle", "")
                            or f"Acuse en la misma llamada, confirmado por «{actor}»."
                        )
                        _anotar(ses, entrega.id, "acusado", entrega.detalle, actor=actor)
                    else:
                        # Aceptado ≠ recibido. Se queda ENVIADO hasta que alguien acuse.
                        enviados += 1
                        entrega.estado = siguiente(entrega.estado, Evento.DEPOSITO)
                        entrega.acusado_en = None
                        entrega.acuse_por = None
                        entrega.detalle = (
                            getattr(resultado, "detalle", "")
                            or f"Puesto en el canal «{decision.canal}». Aceptado por el "
                            "canal NO es recibido por el receptor: queda ENVIADO hasta "
                            f"que llegue el acuse (plazo {entrega.plazo_acuse_s}s)."
                        )
                        if acuse_impugnado:
                            entrega.detalle = f"{acuse_impugnado} {entrega.detalle}"
                        _anotar(ses, entrega.id, "enviado", entrega.detalle)

            except Exception as exc:
                # La corrida misma se rompió. Se deshace lo parcial y se deja una fila
                # FALLIDA: una corrida que revienta y no deja rastro es indistinguible
                # de una que nunca se ejecutó.
                ses.rollback()
                fallida = CorridaORM(
                    id=corrida_id,
                    iniciada_en=iniciada_en,
                    terminada_en=ahora(),
                    tomados=len(trabajos),
                    estado=EstadoCorrida.FALLIDA,
                    detalle=(
                        f"La corrida abortó con {type(exc).__name__}: {exc}. Se revirtió "
                        "el lote completo; ninguna entrega quedó a medias."
                    ),
                )
                ses.add(fallida)
                ses.commit()
                raise

            corrida.enrutados = enrutados
            corrida.enviados = enviados
            corrida.acusados = acusados
            corrida.rechazados = rechazados
            corrida.fallidos = fallidos
            corrida.estado, corrida.detalle = self._desenlace(
                enviados=enviados,
                acusados=acusados,
                rechazados=rechazados,
                fallidos=fallidos,
                duplicados=duplicados,
            )
            corrida.terminada_en = ahora()
            ses.commit()
            return corrida

    @staticmethod
    def _desenlace(
        *, enviados: int, acusados: int, rechazados: int, fallidos: int, duplicados: int
    ) -> tuple[EstadoCorrida, str]:
        """Traduce los contadores a un desenlace. Ningún camino lleva a EXITOSA con 0."""
        procesados = enviados + acusados

        if rechazados or fallidos:
            return (
                EstadoCorrida.CON_PROBLEMAS,
                f"{procesados} procesados ({acusados} acusados, {enviados} sin acuse "
                f"todavía), {rechazados} rechazados por falta de regla de ruteo y "
                f"{fallidos} fallidos en el canal. Los {rechazados + fallidos} con "
                "problema traen el motivo en su propio detalle.",
            )

        if procesados == 0:
            # Hubo trabajos, pero ninguno se procesó: todos ya estaban entregados o en
            # vuelo. La corrida no hizo nada, y decirle EXITOSA es exactamente el
            # engaño que este proyecto corrige.
            return (
                EstadoCorrida.SIN_INSUMO,
                f"no había insumo NUEVO: los {duplicados} trabajos recibidos ya tenían "
                "una entrega viva (acusada o esperando acuse). La corrida no procesó "
                "nada; esto NO es una corrida exitosa.",
            )

        return (
            EstadoCorrida.EXITOSA,
            f"{procesados} procesados sin incidencias: {acusados} con acuse confirmado "
            f"y {enviados} en el canal esperando acuse"
            + (f"; {duplicados} ignorados por duplicados." if duplicados else ".")
            + " Ojo: los que están en ENVIADO todavía NO son entregas; el conciliador "
            "los vigila hasta que venza su plazo.",
        )

    # ------------------------------------------------------------- registrar_acuse

    def registrar_acuse(self, referencias: list[str], actor: str) -> int:
        """Registra el acuse del RECEPTOR: pasa ENVIADO → ACUSADO.

        Devuelve cuántas entregas se marcaron. Comparar ese número contra
        `len(referencias)` es lo que revela un acuse que cita referencias que nadie
        envió (queda además en la bitácora como `acuse_sin_entrega`).

        `actor` es obligatorio: un acuse anónimo no es verificable, y un acuse que no
        se puede atribuir a nadie vale lo mismo que no tenerlo.
        """
        if not actor or not actor.strip():
            raise ValueError(
                "No se puede registrar un acuse sin actor. El acuse es la afirmación "
                "de QUIÉN recibió; sin ese dato no es verificable y no se distingue de "
                "que el emisor se dé por bien servido a sí mismo."
            )

        actor = actor.strip()
        if not referencias:
            return 0

        momento = ahora()
        marcadas = 0

        with self.session_factory() as ses:
            for referencia in referencias:
                filas = list(
                    ses.scalars(
                        select(EntregaORM).where(EntregaORM.referencia == referencia)
                    )
                )

                if not filas:
                    _anotar(
                        ses,
                        None,
                        "acuse_sin_entrega",
                        f"Llegó un acuse por la referencia «{referencia}», pero no existe "
                        "ninguna entrega con esa referencia. O el receptor acusó algo "
                        "que no le mandamos, o la referencia viaja distinta de cada lado.",
                        actor=actor,
                        momento=momento,
                    )
                    continue

                for fila in filas:
                    if es_valida(fila.estado, Evento.ACUSE_RECEPTOR):
                        fila.estado = siguiente(fila.estado, Evento.ACUSE_RECEPTOR)
                        fila.acusado_en = momento
                        fila.acuse_por = actor
                        fila.detalle = (
                            f"Acuse recibido de «{actor}». Esto —y sólo esto— convierte "
                            "el envío en una entrega."
                        )
                        _anotar(
                            ses,
                            fila.id,
                            "acusado",
                            fila.detalle,
                            actor=actor,
                            momento=momento,
                        )
                        marcadas += 1
                    elif fila.estado is EstadoEntrega.SIN_ACUSE:
                        # Acuse tardío: llegó después de vencer el plazo. NO se borra el
                        # incumplimiento —pasó de verdad— pero queda registrado que sí
                        # terminó llegando.
                        _anotar(
                            ses,
                            fila.id,
                            "acuse_tardio",
                            f"«{actor}» acusó la referencia «{referencia}» DESPUÉS de que "
                            f"venciera el plazo de {fila.plazo_acuse_s}s. La entrega "
                            "sigue marcada SIN_ACUSE: el incumplimiento ocurrió y "
                            "borrarlo escondería el problema que hay que corregir.",
                            actor=actor,
                            momento=momento,
                        )
                    else:
                        _anotar(
                            ses,
                            fila.id,
                            "acuse_ignorado",
                            f"«{actor}» acusó la referencia «{referencia}», pero la "
                            f"entrega está en {fila.estado.value} y sólo se acusa lo que "
                            "está ENVIADO.",
                            actor=actor,
                            momento=momento,
                        )

            ses.commit()

        return marcadas
