"""Cifras control por lote: conteo, suma en Decimal y hash por fila con llave estable.

El acuse dice QUE el lote llegó; las cifras control dicen QUE llegó COMPLETO e ÍNTEGRO.
Un receptor que confirma «recibí el corte» sin poder reproducir el conteo, la suma y el
hash del emisor está confirmando un objeto, no su contenido.

Reglas (cada una con su prueba en docs/reglas.md):

  * **Importe en Decimal, nunca en float.** Un float ya perdió precisión antes de llegar
    (`2.675` es 2.67499999…) y por eso se RECHAZA, igual que un booleano, un texto con
    separadores de miles o en notación científica, o un valor no finito.
  * **Un importe ausente no vale cero.** Se rechaza: tomarlo por 0 es inventar el dato.
  * **Redondeo a centavos, mitad hacia arriba** (`ROUND_HALF_UP`, se aleja del cero en el
    empate: 0.005 → 0.01 y -0.005 → -0.01), fila por fila. La suma se hace sobre los
    importes YA redondeados, que son los que ve el receptor: así la cifra control cuadra
    con la suma de las filas del archivo. El cero no lleva signo (-0.004 → 0.00).
  * **Una moneda por lote.** No se suman pesos con dólares: un lote mezclado se rechaza.
  * **La llave estable es la referencia** (el folio de negocio), no el id de la entrega
    (un UUID distinto en cada corrida) ni el orden de llegada. Referencias repetidas en
    un mismo lote se rechazan: dos filas con la misma llave no se pueden conciliar.
  * **Hash por fila:** SHA-256 del JSON canónico `[referencia, importe, moneda]`.
    **Hash del lote:** SHA-256 de los hashes de fila ordenados por referencia y unidos
    con salto de línea. Reordenar las filas no lo cambia; alterar cualquiera, sí.
  * **Un lote sin filas no cuadra:** su conciliación es SIN_REGISTROS, no CUADRA. Verde
    con cero registros es la patología 3 con otro nombre.
"""
from __future__ import annotations

import enum
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENTAVO = Decimal("0.01")
_RE_IMPORTE = re.compile(r"^-?\d+(?:\.\d+)?$")
_RE_MONEDA = re.compile(r"^[A-Z]{3}$")


class CifraInvalida(ValueError):
    """Un registro no permite calcular cifras control sin inventar algo."""


def normalizar_importe(valor: object) -> Decimal:
    """Importe exacto, redondeado a centavos con mitad hacia arriba."""
    if isinstance(valor, bool):
        raise CifraInvalida(f"Importe booleano ({valor!r}): no es una cantidad.")
    if isinstance(valor, float):
        raise CifraInvalida(
            f"Importe en float ({valor!r}): un float ya perdió precisión antes de llegar. "
            "Envíalo como texto («2.675») o como Decimal."
        )
    if isinstance(valor, int):
        exacto = Decimal(valor)
    elif isinstance(valor, Decimal):
        exacto = valor
    elif isinstance(valor, str):
        texto = valor.strip()
        if not _RE_IMPORTE.match(texto):
            raise CifraInvalida(
                f"Importe ilegible «{valor}»: se esperan dígitos con punto decimal opcional, "
                "sin separadores de miles, signo + ni notación científica."
            )
        exacto = Decimal(texto)
    else:
        raise CifraInvalida(f"Importe de tipo {type(valor).__name__}: no es una cantidad.")

    if not exacto.is_finite():
        raise CifraInvalida(f"Importe no finito ({valor!r}).")
    try:
        redondeado = exacto.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:      # más dígitos de los que caben en el contexto
        raise CifraInvalida(f"Importe fuera de rango ({valor!r}).") from exc
    # El cero no lleva signo: -0.004 redondea a «-0.00», que es 0.00 escrito distinto y
    # cambiaría el hash de la fila sin que cambie el dinero.
    return redondeado if redondeado != 0 else Decimal("0.00")


def hash_fila(referencia: str, importe: Decimal, moneda: str) -> str:
    canonico = json.dumps([referencia, f"{importe:.2f}", moneda], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def hash_lote(hashes_por_referencia: Mapping[str, str]) -> str:
    unidos = "\n".join(hashes_por_referencia[r] for r in sorted(hashes_por_referencia))
    return hashlib.sha256(unidos.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FilaControl:
    referencia: str
    importe: Decimal
    moneda: str
    hash: str


@dataclass(frozen=True)
class CifrasControl:
    lote: str
    moneda: str | None          # None sólo cuando el lote no tiene filas
    conteo: int
    suma: Decimal
    hash_lote: str
    filas: tuple[FilaControl, ...]   # ordenadas por referencia

    def como_dict(self) -> dict:
        """Forma serializable y estable (importes como texto, nunca como float)."""
        return {
            "lote": self.lote,
            "moneda": self.moneda,
            "conteo": self.conteo,
            "suma": f"{self.suma:.2f}",
            "hash_lote": self.hash_lote,
            "filas": [
                {"referencia": f.referencia, "importe": f"{f.importe:.2f}", "moneda": f.moneda, "hash": f.hash}
                for f in self.filas
            ],
        }


def fila_de(referencia: str, carga: Mapping) -> FilaControl:
    """Fila de control a partir de la referencia y la carga de un trabajo."""
    if not isinstance(referencia, str) or not referencia.strip():
        raise CifraInvalida("Fila sin referencia: sin llave estable no hay con qué conciliarla.")
    if "importe" not in carga or carga["importe"] is None:
        raise CifraInvalida(f"«{referencia}» no trae importe. Un importe ausente no vale cero.")
    moneda = carga.get("moneda")
    if not isinstance(moneda, str) or not _RE_MONEDA.match(moneda):
        raise CifraInvalida(f"«{referencia}» trae moneda {moneda!r}: se espera un código ISO de 3 letras.")
    importe = normalizar_importe(carga["importe"])
    return FilaControl(referencia, importe, moneda, hash_fila(referencia, importe, moneda))


def cifras_de_lote(lote: str, registros: Iterable[tuple[str, Mapping]]) -> CifrasControl:
    """Cifras control de un lote a partir de pares (referencia, carga)."""
    if not isinstance(lote, str) or not lote.strip():
        raise CifraInvalida("Lote sin nombre: no se puede declarar cifras de algo que no se nombra.")
    por_referencia: dict[str, FilaControl] = {}
    for referencia, carga in registros:
        fila = fila_de(referencia, carga)
        if fila.referencia in por_referencia:
            raise CifraInvalida(
                f"La referencia «{fila.referencia}» viene dos veces en el lote «{lote}». Dos filas "
                "con la misma llave no se pueden conciliar: una de las dos sobra."
            )
        por_referencia[fila.referencia] = fila

    monedas = {f.moneda for f in por_referencia.values()}
    if len(monedas) > 1:
        raise CifraInvalida(f"El lote «{lote}» mezcla monedas ({', '.join(sorted(monedas))}): no se suman.")

    filas = tuple(por_referencia[r] for r in sorted(por_referencia))
    return CifrasControl(
        lote=lote,
        moneda=next(iter(monedas)) if monedas else None,
        conteo=len(filas),
        suma=sum((f.importe for f in filas), Decimal("0.00")),
        hash_lote=hash_lote({f.referencia: f.hash for f in filas}),
        filas=filas,
    )


# ------------------------------------------------------------------------- conciliación


class EstadoConciliacion(str, enum.Enum):
    CUADRA = "CUADRA"
    DESCUADRA = "DESCUADRA"
    SIN_REGISTROS = "SIN_REGISTROS"   # no había nada que cuadrar: no es CUADRA


class TipoDiferencia(str, enum.Enum):
    FALTANTE = "FALTANTE"                 # el emisor la declaró y el receptor no la tiene
    SOBRANTE = "SOBRANTE"                 # el receptor la tiene y nadie la declaró
    IMPORTE_DISTINTO = "IMPORTE_DISTINTO"
    MONEDA_DISTINTA = "MONEDA_DISTINTA"


@dataclass(frozen=True)
class Diferencia:
    referencia: str
    tipo: TipoDiferencia
    declarado: str | None
    recibido: str | None


@dataclass(frozen=True)
class ResultadoConciliacion:
    lote: str
    estado: EstadoConciliacion
    declaradas: CifrasControl
    recibidas: CifrasControl
    diferencias: tuple[Diferencia, ...]

    @property
    def cuadra(self) -> bool:
        return self.estado is EstadoConciliacion.CUADRA


def conciliar_cifras(declaradas: CifrasControl, recibidas: CifrasControl) -> ResultadoConciliacion:
    """Compara lo que el emisor declaró contra lo que el receptor puede reproducir."""
    if declaradas.lote != recibidas.lote:
        raise ValueError(
            f"Se quiso conciliar el lote «{declaradas.lote}» contra «{recibidas.lote}»: "
            "comparar lotes distintos no prueba nada."
        )
    emisor = {f.referencia: f for f in declaradas.filas}
    receptor = {f.referencia: f for f in recibidas.filas}
    diferencias: list[Diferencia] = []
    for referencia in sorted(emisor.keys() | receptor.keys()):
        d, r = emisor.get(referencia), receptor.get(referencia)
        if r is None:
            diferencias.append(Diferencia(referencia, TipoDiferencia.FALTANTE, f"{d.importe:.2f}", None))
        elif d is None:
            diferencias.append(Diferencia(referencia, TipoDiferencia.SOBRANTE, None, f"{r.importe:.2f}"))
        elif d.moneda != r.moneda:
            diferencias.append(Diferencia(referencia, TipoDiferencia.MONEDA_DISTINTA, d.moneda, r.moneda))
        elif d.importe != r.importe:
            diferencias.append(
                Diferencia(referencia, TipoDiferencia.IMPORTE_DISTINTO, f"{d.importe:.2f}", f"{r.importe:.2f}")
            )

    if declaradas.conteo == 0 and recibidas.conteo == 0:
        estado = EstadoConciliacion.SIN_REGISTROS
    elif diferencias or declaradas.hash_lote != recibidas.hash_lote:
        estado = EstadoConciliacion.DESCUADRA
    else:
        estado = EstadoConciliacion.CUADRA
    return ResultadoConciliacion(declaradas.lote, estado, declaradas, recibidas, tuple(diferencias))


__all__ = [
    "CENTAVO",
    "CifraInvalida",
    "CifrasControl",
    "Diferencia",
    "EstadoConciliacion",
    "FilaControl",
    "ResultadoConciliacion",
    "TipoDiferencia",
    "cifras_de_lote",
    "conciliar_cifras",
    "fila_de",
    "hash_fila",
    "hash_lote",
    "normalizar_importe",
]
