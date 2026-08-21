"""Canal asíncrono: depositar NO es entregar.

⭐ Este canal es el corazón de la demostración.

Simula el depósito de un objeto en un almacenamiento compartido. El depósito
**siempre funciona** —por eso ``aceptado`` es siempre ``True``— y ahí está la
trampa: la operación que el emisor puede observar termina bien SIEMPRE, en las
dos situaciones que le importa distinguir:

  * el receptor está leyendo el buzón y procesando cada objeto, y
  * el receptor lleva semanas sin leer nada.

Desde el lado emisor las dos se ven idénticas: subida exitosa, 100% de éxito,
tablero en verde. Los objetos se apilan en silencio y el síntoma aparece mucho
después y muy lejos de la causa — normalmente cuando alguien del otro lado
pregunta por qué no ha recibido lo del mes pasado.

Por eso este canal devuelve **``acuse_inmediato=False`` SIEMPRE**, sin excepción
ni bandera de configuración que lo permita. No es pesimismo: es que no tiene con
qué saberlo. El acuse sólo puede nacer de :meth:`consumir`, es decir, del acto en
que el receptor efectivamente lee.

**Cómo se ve la falla en la demo:** si nadie llama a :meth:`consumir`, los envíos
se aceptan, quedan en ENVIADO, y :meth:`pendientes` crece sin límite. Al vencer
``plazo_acuse_s`` el orquestador los pasa a SIN_ACUSE y la brecha se vuelve
visible con nombre, canal y antigüedad. El emisor deja de poder decir «yo lo
mandé» como si eso fuera una entrega.
"""
from __future__ import annotations

from datetime import datetime

from acuse.canales import ResultadoEnvio
from acuse.modelo import Entrega, ModoCanal, ahora


class CanalAsincrono:
    """Simula depósito de objetos en un almacenamiento que alguien más debe leer.

    Args:
        nombre: cómo lo eligen las reglas de ruteo.
        plazo_acuse_s: cuánto puede tardar el receptor en leer antes de que la
            entrega se considere SIN_ACUSE. Es el único parámetro que convierte
            «va en camino» en «se perdió».
        prefijo: ruta lógica donde se depositan los objetos, sólo para la bitácora.
    """

    modo = ModoCanal.ASINCRONO

    def __init__(self, nombre: str, plazo_acuse_s: int = 900, prefijo: str = "objetos") -> None:
        if plazo_acuse_s <= 0:
            raise ValueError("plazo_acuse_s debe ser un entero positivo")
        self.nombre = nombre
        self.plazo_acuse_s = int(plazo_acuse_s)
        self.prefijo = prefijo
        #: Todo lo depositado, en orden. Cada elemento lleva su bandera «consumido».
        #: Se conserva el histórico completo: es la evidencia de qué se depositó y cuándo.
        self.buzon: list[dict] = []

    # -- contrato de Canal --------------------------------------------------------------
    def enviar(self, entrega: Entrega, carga: dict) -> ResultadoEnvio:
        momento: datetime = ahora()
        objeto = {
            "objeto": f"{self.prefijo}/{entrega.referencia}.json",
            "referencia": entrega.referencia,
            "entrega_id": entrega.id,
            "trabajo_id": entrega.trabajo_id,
            "carga": dict(carga),
            "depositado_en": momento,
            "consumido": False,
            "consumido_en": None,
        }
        self.buzon.append(objeto)
        return ResultadoEnvio(
            aceptado=True,
            # ⚠️ Constante y deliberado. Depositar no es entregar: nadie del otro lado
            #    ha leído todavía, y este canal no tiene forma de saber si lo hará.
            acuse_inmediato=False,
            acuse_por=None,
            detalle=(
                f"Objeto «{objeto['objeto']}» depositado en «{self.nombre}» "
                f"({momento.isoformat()}). El depósito fue exitoso, pero NO hay acuse: "
                f"queda pendiente de que el receptor lo lea. Pendientes ahora: {self.pendientes()}."
            ),
        )

    # -- el otro lado --------------------------------------------------------------------
    def consumir(self, n: int | None = None) -> list[str]:
        """Simula al receptor leyendo el buzón. Devuelve las referencias consumidas.

        Es el ÚNICO origen legítimo de un acuse en este canal: el orquestador toma
        estas referencias y recién entonces marca ACUSADO. Si nadie llama a este
        método, nada se acusa — que es justo lo que pasa en la falla real.

        Args:
            n: cuántos objetos leer, en orden de depósito (FIFO). ``None`` = todos.
        """
        if n is not None and n < 0:
            raise ValueError("n no puede ser negativo")

        leidas: list[str] = []
        momento = ahora()
        for objeto in self.buzon:
            if n is not None and len(leidas) >= n:
                break
            if objeto["consumido"]:
                continue
            objeto["consumido"] = True
            objeto["consumido_en"] = momento
            leidas.append(objeto["referencia"])
        return leidas

    def pendientes(self) -> int:
        """Objetos depositados que nadie ha leído. Si crece y crece, hay una brecha."""
        return sum(1 for objeto in self.buzon if not objeto["consumido"])

    # -- lecturas de apoyo ----------------------------------------------------------------
    @property
    def depositados(self) -> int:
        """Total histórico depositado, leído o no."""
        return len(self.buzon)

    def referencias_pendientes(self) -> list[str]:
        """Qué está atorado, con nombre. Un número no basta para ir a reclamar."""
        return [o["referencia"] for o in self.buzon if not o["consumido"]]

    def mas_antiguo_pendiente(self) -> datetime | None:
        """Cuándo se depositó lo más viejo sin leer: la antigüedad de la brecha."""
        fechas = [o["depositado_en"] for o in self.buzon if not o["consumido"]]
        return min(fechas) if fechas else None

    def __repr__(self) -> str:  # pragma: no cover - ayuda de diagnóstico
        return (
            f"CanalAsincrono({self.nombre!r}, depositados={self.depositados}, "
            f"pendientes={self.pendientes()})"
        )
