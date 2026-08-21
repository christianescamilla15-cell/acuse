"""Registro de canales.

Un **canal** es el medio por el que una entrega sale del emisor. Su contrato aquí
está diseñado alrededor de una sola pregunta incómoda:

    ¿este canal SABE si el receptor recibió, o sólo sabe que yo solté el paquete?

Los canales síncronos saben (el receptor contesta en la misma llamada). Los
asíncronos NO saben: depositar un objeto en un almacenamiento no es entregar, es
dejarlo ahí. Confundir ambas cosas es el origen del *éxito silencioso*: el emisor
marca la entrega como cumplida porque él subió el archivo, y del otro lado nadie
confirmó nada. El síntoma aparece días después y lejos de la causa.

Por eso el resultado de un envío lleva DOS banderas separadas:

  * ``aceptado``        — el canal admitió el paquete (o falló al admitirlo).
  * ``acuse_inmediato`` — el RECEPTOR confirmó en el acto.

y el invariante duro del módulo:

    ⚠️ ``acuse_inmediato`` sólo puede ser ``True`` en un canal ``SINCRONO``.

Un canal asíncrono que devuelva ``acuse_inmediato=True`` está mintiendo, y esa
mentira es exactamente la que este proyecto existe para hacer imposible. La
verificación no es un comentario: la aplica :func:`validar_resultado`, y todo
orquestador debe pasar por ella (o por :func:`enviar_por`, que ya la incluye).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from acuse.modelo import Entrega, ModoCanal


class CanalInvalido(ValueError):
    """El canal no cumple el contrato mínimo para poder registrarse."""


class CanalDuplicado(ValueError):
    """Ya hay un canal con ese nombre. Sobrescribir en silencio es cómo se pierde trazabilidad."""


class CanalNoRegistrado(KeyError):
    """Se pidió un canal que nadie declaró. Nunca se sustituye por uno por omisión."""

    def __init__(self, nombre: str, disponibles: list[str]) -> None:
        self.nombre = nombre
        self.disponibles = disponibles
        super().__init__(
            f"No hay canal registrado con el nombre «{nombre}». "
            f"Registrados: {', '.join(disponibles) or 'ninguno'}. "
            "No existe canal por omisión: entregar por un canal que nadie eligió "
            "es la misma falla que no entregar."
        )


class AcuseImposible(ValueError):
    """Un canal declaró un acuse que su modo no le permite producir."""


@dataclass(frozen=True)
class ResultadoEnvio:
    """Lo que el canal devuelve tras un intento de envío.

    ``aceptado`` y ``acuse_inmediato`` son campos DISTINTOS a propósito: el primero
    habla del emisor, el segundo del receptor. Colapsarlos en un solo booleano
    ``exito`` es literalmente el defecto que corregimos.
    """

    aceptado: bool
    acuse_inmediato: bool = False
    acuse_por: str | None = None   # QUIÉN confirmó · un acuse sin actor no es un acuse
    detalle: str = ""

    def __post_init__(self) -> None:
        # Un acuse exige un actor identificable. «Se acusó» sin decir quién no es evidencia.
        if self.acuse_inmediato and not self.acuse_por:
            raise AcuseImposible(
                "acuse_inmediato=True sin acuse_por: un acuse sin actor identificable "
                "no es un acuse, es una suposición."
            )
        # No se puede confirmar la recepción de algo que el canal ni siquiera admitió.
        if self.acuse_inmediato and not self.aceptado:
            raise AcuseImposible(
                "acuse_inmediato=True con aceptado=False: el canal no admitió el envío, "
                "nadie pudo haberlo recibido."
            )
        # Un rechazo sin motivo obliga a adivinar la causa, y adivinar es el problema.
        if not self.aceptado and not self.detalle:
            raise ValueError(
                "Un envío no aceptado debe explicar por qué. Un fallo sin motivo "
                "se archiva como ruido y se descubre tarde."
            )


@runtime_checkable
class Canal(Protocol):
    """Contrato mínimo de un canal de entrega."""

    #: Nombre con el que las reglas de ruteo lo eligen.
    nombre: str
    #: Si sabe o no de la recepción en la misma llamada.
    modo: ModoCanal
    #: Segundos que puede tardar un acuse antes de considerarse SIN_ACUSE.
    plazo_acuse_s: int

    def enviar(self, entrega: Entrega, carga: dict) -> ResultadoEnvio:
        """Intenta poner la carga en el canal. NO decide si hubo entrega."""
        ...


#: Canales declarados y disponibles para el ruteo.
REGISTRO: dict[str, Canal] = {}


def validar_resultado(canal: Canal, resultado: ResultadoEnvio) -> ResultadoEnvio:
    """Aplica el invariante que da nombre al proyecto.

    Un canal ASINCRONO no tiene forma física de saber si el receptor leyó: su
    acuse llega después, por devolución. Si dice que acusó en el acto, miente.
    """
    if resultado.acuse_inmediato and canal.modo is not ModoCanal.SINCRONO:
        raise AcuseImposible(
            f"El canal «{canal.nombre}» es {canal.modo.value} y devolvió acuse_inmediato=True. "
            "Un canal asíncrono no puede confirmar la recepción en la misma llamada: "
            "depositar no es entregar."
        )
    return resultado


def registrar(canal: Canal, reemplazar: bool = False) -> Canal:
    """Da de alta un canal. Valida el contrato ANTES de que alguien dependa de él."""
    nombre = getattr(canal, "nombre", None)
    if not isinstance(nombre, str) or not nombre.strip():
        raise CanalInvalido("El canal debe declarar un «nombre» no vacío.")
    if not isinstance(getattr(canal, "modo", None), ModoCanal):
        raise CanalInvalido(f"El canal «{nombre}» debe declarar «modo» como ModoCanal.")
    plazo = getattr(canal, "plazo_acuse_s", None)
    if not isinstance(plazo, int) or isinstance(plazo, bool) or plazo <= 0:
        raise CanalInvalido(
            f"El canal «{nombre}» debe declarar «plazo_acuse_s» como entero positivo: "
            "sin plazo no hay forma de distinguir «va en camino» de «se perdió»."
        )
    if not callable(getattr(canal, "enviar", None)):
        raise CanalInvalido(f"El canal «{nombre}» debe implementar enviar(entrega, carga).")
    if nombre in REGISTRO and not reemplazar:
        raise CanalDuplicado(
            f"Ya existe un canal registrado como «{nombre}». Sustituirlo en silencio "
            "haría que las entregas viajaran por un medio distinto al declarado. "
            "Usa reemplazar=True si es intencional."
        )
    REGISTRO[nombre] = canal
    return canal


def obtener(nombre: str) -> Canal:
    """Devuelve el canal declarado. Si no existe, falla ruidosamente."""
    try:
        return REGISTRO[nombre]
    except KeyError:
        raise CanalNoRegistrado(nombre, sorted(REGISTRO)) from None


def enviar_por(nombre: str, entrega: Entrega, carga: dict) -> ResultadoEnvio:
    """Atajo seguro: resuelve el canal, envía y valida el invariante del acuse."""
    canal = obtener(nombre)
    return validar_resultado(canal, canal.enviar(entrega, carga))


def limpiar_registro() -> None:
    """Vacía el registro. Pensado para pruebas y para el arranque de la demo."""
    REGISTRO.clear()


__all__ = [
    "Canal",
    "ResultadoEnvio",
    "REGISTRO",
    "registrar",
    "obtener",
    "enviar_por",
    "validar_resultado",
    "limpiar_registro",
    "CanalInvalido",
    "CanalDuplicado",
    "CanalNoRegistrado",
    "AcuseImposible",
]
