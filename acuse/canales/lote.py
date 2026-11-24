"""Canal por lote: un objeto por lote, no uno por registro.

Igual que el asíncrono —depositar sigue sin ser entregar, y el acuse sigue
naciendo sólo cuando el receptor lee— pero corrige una deuda distinta y muy
concreta: **un objeto por registro**.

Cuando un proceso deposita un objeto por cada renglón, el receptor recibe N
eventos donde el negocio esperaba 1. Las consecuencias son las de siempre y
todas silenciosas: el receptor procesa N veces, los conteos de un lado y del
otro nunca cuadran, y el acuse queda partido en N pedazos, de modo que nadie
puede responder la única pregunta que importa —«¿llegó el corte de hoy?»— con
un sí o un no. Agrupando por ``Trabajo.lote`` la unidad de entrega vuelve a ser
la unidad de negocio: un lote, un objeto, un acuse.

**El lote es obligatorio y explícito.** Si la carga no trae lote, este canal
NO inventa uno ni manda el trabajo a un lote «general»: rechaza el envío. Un
canal que decide el destino por la ausencia de un campo es exactamente el
defecto de ruteo por omisión — el productor olvida poblar el campo, nadie ve un
error, y el resultado termina donde no debía.
"""
from __future__ import annotations

from datetime import datetime

from acuse.canales import ResultadoEnvio
from acuse.modelo import Entrega, ModoCanal, Trabajo, ahora


class LoteDesconocido(KeyError):
    """Se quiso consumir un lote que nunca se depositó."""

    def __init__(self, lote: str, conocidos: list[str]) -> None:
        self.lote = lote
        self.conocidos = conocidos
        super().__init__(
            f"No hay objeto depositado para el lote «{lote}». "
            f"Lotes conocidos: {', '.join(conocidos) or 'ninguno'}. "
            "Acusar un lote inexistente sería fabricar evidencia."
        )


def carga_de_lote(trabajo: Trabajo, clave_lote: str = "lote") -> dict:
    """Arma la carga que espera :class:`CanalLote` a partir del trabajo.

    El lote viaja DENTRO de la carga porque el canal recibe ``(entrega, carga)``
    y la entrega no lo conoce. Hacerlo explícito evita que el canal lo adivine.
    """
    if trabajo.lote is None:
        raise ValueError(
            f"El trabajo «{trabajo.referencia}» no declara lote y va a un canal por lote. "
            "Agruparlo bajo un lote inventado rompería el conteo del receptor."
        )
    return {**trabajo.carga, clave_lote: trabajo.lote}


class CanalLote:
    """Deposita UN objeto por lote y acumula en él las referencias del lote.

    Args:
        nombre: cómo lo eligen las reglas de ruteo.
        plazo_acuse_s: cuánto puede tardar el receptor en leer el lote.
        prefijo: ruta lógica del objeto, sólo para la bitácora.
        clave_lote: llave de la carga donde viene el lote.
    """

    modo = ModoCanal.ASINCRONO

    def __init__(
        self,
        nombre: str,
        plazo_acuse_s: int = 3600,
        prefijo: str = "lotes",
        clave_lote: str = "lote",
    ) -> None:
        if plazo_acuse_s <= 0:
            raise ValueError("plazo_acuse_s debe ser un entero positivo")
        self.nombre = nombre
        self.plazo_acuse_s = int(plazo_acuse_s)
        self.prefijo = prefijo
        self.clave_lote = clave_lote
        #: lote -> objeto depositado. UNA entrada por lote, por más trabajos que lleguen.
        self._objetos: dict[str, dict] = {}

    # -- lecturas ------------------------------------------------------------------------
    @property
    def lotes_depositados(self) -> dict[str, dict]:
        """Los objetos depositados, uno por lote. ``len(...)`` = objetos, no registros."""
        return dict(self._objetos)

    def pendientes(self) -> int:
        """Lotes depositados que el receptor no ha leído."""
        return sum(1 for o in self._objetos.values() if not o["consumido"])

    def referencias_del_lote(self, lote: str) -> list[str]:
        """Qué trabajos viajan dentro de ese objeto."""
        if lote not in self._objetos:
            raise LoteDesconocido(lote, sorted(self._objetos))
        return list(self._objetos[lote]["referencias"])

    def registros_del_lote(self, lote: str) -> list[tuple[str, dict]]:
        """(referencia, carga) de cada registro del objeto, tal como lo leería el receptor.

        Es la entrada para recalcular las cifras control del lado receptor
        (`acuse.conciliador.conciliar_lote`). Se devuelven copias: leer no altera el objeto.
        """
        if lote not in self._objetos:
            raise LoteDesconocido(lote, sorted(self._objetos))
        return [(r["referencia"], dict(r["carga"])) for r in self._objetos[lote]["registros"]]

    def mas_antiguo_pendiente(self) -> datetime | None:
        fechas = [o["depositado_en"] for o in self._objetos.values() if not o["consumido"]]
        return min(fechas) if fechas else None

    # -- contrato de Canal ----------------------------------------------------------------
    def enviar(self, entrega: Entrega, carga: dict) -> ResultadoEnvio:
        lote = carga.get(self.clave_lote) if isinstance(carga, dict) else None
        if lote is None:
            lote = getattr(entrega, "lote", None)

        if not isinstance(lote, str) or not lote.strip():
            # Ruteo por omisión: se rechaza en voz alta en lugar de inventar un lote.
            return ResultadoEnvio(
                aceptado=False,
                detalle=(
                    f"La carga de «{entrega.referencia}» no declara «{self.clave_lote}». "
                    "Este canal agrupa por lote y NO usa uno por omisión: agrupar bajo un "
                    "lote inventado desplaza el registro a un corte que no le toca y nadie "
                    "vería el error. Puebla el lote en el trabajo (ver carga_de_lote)."
                ),
            )

        momento: datetime = ahora()
        objeto = self._objetos.get(lote)

        if objeto is not None and objeto["consumido"]:
            # El receptor ya se llevó ese corte. Reabrirlo en silencio haría que su
            # acuse cubriera registros que él nunca vio.
            return ResultadoEnvio(
                aceptado=False,
                detalle=(
                    f"El lote «{lote}» ya fue consumido por el receptor "
                    f"({objeto['consumido_en'].isoformat()}). Anexar «{entrega.referencia}» "
                    "quedaría cubierto por un acuse anterior que no lo incluía. "
                    "Abre un lote nuevo."
                ),
            )

        if objeto is None:
            objeto = {
                "lote": lote,
                "objeto": f"{self.prefijo}/{lote}.json",
                "referencias": [],
                "registros": [],
                "depositado_en": momento,
                "actualizado_en": momento,
                "consumido": False,
                "consumido_en": None,
            }
            self._objetos[lote] = objeto
            nota = "Se creó el objeto del lote"
        else:
            nota = "Se anexó al objeto ya existente del lote"

        if entrega.referencia not in objeto["referencias"]:
            objeto["referencias"].append(entrega.referencia)
            objeto["registros"].append(
                {
                    "referencia": entrega.referencia,
                    "entrega_id": entrega.id,
                    "trabajo_id": entrega.trabajo_id,
                    "carga": {k: v for k, v in carga.items() if k != self.clave_lote},
                }
            )
        objeto["actualizado_en"] = momento

        return ResultadoEnvio(
            aceptado=True,
            # ⚠️ Igual que el asíncrono simple: depositar no es entregar.
            acuse_inmediato=False,
            acuse_por=None,
            detalle=(
                f"{nota} «{objeto['objeto']}» en «{self.nombre}» ({momento.isoformat()}). "
                f"El lote lleva {len(objeto['referencias'])} registro(s) y UN solo objeto. "
                "Sin acuse: falta que el receptor lea el lote."
            ),
        )

    # -- el otro lado ------------------------------------------------------------------------
    def consumir_lote(self, lote: str) -> list[str]:
        """Simula al receptor leyendo un lote completo.

        Devuelve TODAS las referencias que viajaban en ese objeto, para que el
        orquestador acuse cada entrega. Un lote = un acuse que cubre N registros;
        eso es lo que permite responder «¿llegó el corte?» con un sí o un no.

        Consumir dos veces el mismo lote devuelve una lista vacía: el acuse ya se
        emitió y repetirlo inflaría el conteo del receptor.
        """
        if lote not in self._objetos:
            raise LoteDesconocido(lote, sorted(self._objetos))

        objeto = self._objetos[lote]
        if objeto["consumido"]:
            return []

        objeto["consumido"] = True
        objeto["consumido_en"] = ahora()
        return list(objeto["referencias"])

    def __repr__(self) -> str:  # pragma: no cover - ayuda de diagnóstico
        return (
            f"CanalLote({self.nombre!r}, lotes={len(self._objetos)}, "
            f"pendientes={self.pendientes()})"
        )
