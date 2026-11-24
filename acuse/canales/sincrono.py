"""Canal síncrono: el receptor contesta en la misma llamada.

Este canal **sí sabe si llegó**. Habla con un receptor que responde en el acto,
así que la respuesta ya es el acuse: hay un actor identificable del otro lado que
dijo «lo tengo». Por eso —y sólo por eso— puede devolver ``acuse_inmediato=True``.

Es el caso fácil, y sirve de contraste: todo lo que en este canal es trivial
(saber si llegó, saber quién lo recibió, saber cuándo) es exactamente lo que en
el canal asíncrono hay que construir a mano. Cuando un equipo copia la lógica del
canal síncrono hacia uno asíncrono sin construir el acuse, nace el éxito
silencioso.

**Determinismo.** La tasa de rechazo se sortea con un hash criptográfico de la
referencia, no con ``random`` ni con el ``hash()`` interno de Python (que va
salado por proceso y cambia entre corridas). Motivo: una demo cuyo resultado
cambia entre ejecuciones no sirve como evidencia, y la evidencia es el punto.
La misma referencia produce siempre el mismo veredicto.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from acuse.canales import ResultadoEnvio
from acuse.modelo import Entrega, ModoCanal, ahora


def _sorteo(referencia: str, sal: str = "") -> float:
    """Número estable en [0, 1) derivado de la referencia. Sin azar, reproducible."""
    digest = hashlib.blake2b(f"{sal}|{referencia}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


class CanalSincrono:
    """Simula un receptor que responde en la misma llamada.

    Args:
        nombre: cómo lo eligen las reglas de ruteo.
        receptor_disponible: si el receptor está en pie. Caído ⇒ el envío no se acepta.
        tasa_rechazo: proporción de referencias que el receptor rechaza por contenido
            (0.0 = ninguna, 1.0 = todas). Determinista por referencia.
        plazo_acuse_s: por contrato del registro; en la práctica no se usa, porque
            el acuse de este canal nunca queda pendiente.
    """

    modo = ModoCanal.SINCRONO

    def __init__(
        self,
        nombre: str,
        receptor_disponible: bool = True,
        tasa_rechazo: float = 0.0,
        plazo_acuse_s: int = 60,
    ) -> None:
        if not 0.0 <= tasa_rechazo <= 1.0:
            raise ValueError("tasa_rechazo debe estar entre 0.0 y 1.0")
        if plazo_acuse_s <= 0:
            raise ValueError("plazo_acuse_s debe ser un entero positivo")
        self.nombre = nombre
        self.receptor_disponible = receptor_disponible
        self.tasa_rechazo = float(tasa_rechazo)
        self.plazo_acuse_s = int(plazo_acuse_s)
        #: Referencias que el receptor confirmó. Es la bitácora del lado receptor.
        self.recibidos: list[str] = []
        #: Intentos que no fueron aceptados, con su motivo.
        self.rechazados: list[tuple[str, str]] = []

    # -- identidad del actor que acusa -------------------------------------------------
    @property
    def actor(self) -> str:
        """Quién firma el acuse. Sin actor, un acuse es una suposición."""
        return f"receptor:{self.nombre}"

    def rechaza(self, referencia: str) -> bool:
        """Veredicto determinista del receptor para esa referencia."""
        if self.tasa_rechazo <= 0.0:
            return False
        return _sorteo(referencia, sal=self.nombre) < self.tasa_rechazo

    # -- contrato de Canal --------------------------------------------------------------
    def enviar(self, entrega: Entrega, carga: dict) -> ResultadoEnvio:
        referencia = entrega.referencia
        momento: datetime = ahora()

        if not self.receptor_disponible:
            motivo = (
                f"El receptor «{self.nombre}» no respondió: está fuera de servicio. "
                "El envío NO se aceptó, así que la entrega es FALLIDA y reintentable. "
                "No se marca nada como entregado."
            )
            self.rechazados.append((referencia, motivo))
            return ResultadoEnvio(aceptado=False, detalle=motivo)

        if self.rechaza(referencia):
            motivo = (
                f"El receptor «{self.nombre}» rechazó la referencia «{referencia}» "
                "tras inspeccionarla. Es un rechazo del contenido, no una caída del canal: "
                "reintentar sin corregir la carga vuelve a fallar igual."
            )
            self.rechazados.append((referencia, motivo))
            return ResultadoEnvio(aceptado=False, detalle=motivo)

        self.recibidos.append(referencia)
        return ResultadoEnvio(
            aceptado=True,
            acuse_inmediato=True,
            acuse_por=self.actor,
            detalle=(
                f"El receptor «{self.nombre}» confirmó la recepción de «{referencia}» "
                f"en la misma llamada ({momento.isoformat()}). Hay acuse porque hay "
                "alguien identificable del otro lado que respondió."
            ),
        )

    def __repr__(self) -> str:  # pragma: no cover - ayuda de diagnóstico
        estado = "disponible" if self.receptor_disponible else "CAÍDO"
        return f"CanalSincrono({self.nombre!r}, receptor={estado}, recibidos={len(self.recibidos)})"
