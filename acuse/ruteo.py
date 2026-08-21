"""Enrutamiento EXPLÍCITO.

Patología que corrige: en integraciones reales el canal se decide por la presencia o
ausencia de un campo opcional. Si el productor olvida poblarlo, el resultado se va por
el canal equivocado y el destinatario nunca se entera — **sin error y sin aviso**.

Aquí un trabajo que no casa con ninguna regla NO cae en un canal por omisión:
se RECHAZA de forma ruidosa y queda registrado con el motivo.
"""
from __future__ import annotations

from dataclasses import dataclass

from acuse.modelo import ReglaRuteo


class SinRutaAplicable(Exception):
    """No hay regla que cubra el trabajo. Es un error, no un caso por omisión."""

    def __init__(self, referencia: str, atributos: dict[str, str], reglas: list[str]) -> None:
        self.referencia = referencia
        self.atributos = atributos
        self.reglas = reglas
        super().__init__(
            f"El trabajo «{referencia}» no casa con ninguna regla de ruteo. "
            f"Atributos: {atributos or '{}'}. Reglas declaradas: {', '.join(reglas) or 'ninguna'}. "
            "No existe canal por omisión: enrutar a ciegas es cómo se pierden entregas."
        )


@dataclass
class Decision:
    canal: str
    regla: str
    explicacion: str


class Enrutador:
    def __init__(self, reglas: list[ReglaRuteo]) -> None:
        # Menor número de prioridad = se evalúa primero. Empate: orden declarado.
        self.reglas = sorted(reglas, key=lambda r: r.prioridad)
        self._validar_ambiguedad()

    def _validar_ambiguedad(self) -> None:
        """Dos reglas con la misma condición y prioridad hacen el ruteo impredecible."""
        vistas: dict[tuple, str] = {}
        for r in self.reglas:
            clave = (tuple(sorted(r.cuando.items())), r.prioridad)
            if clave in vistas:
                raise ValueError(
                    f"Ruteo ambiguo: «{r.nombre}» y «{vistas[clave]}» comparten condición y "
                    "prioridad. Declara prioridades distintas o unifícalas."
                )
            vistas[clave] = r.nombre

    def resolver(self, referencia: str, atributos: dict[str, str]) -> Decision:
        for regla in self.reglas:
            if regla.aplica(atributos):
                casados = ", ".join(f"{k}={v}" for k, v in regla.cuando.items()) or "(sin condición)"
                return Decision(
                    canal=regla.canal,
                    regla=regla.nombre,
                    explicacion=f"Regla «{regla.nombre}» (prioridad {regla.prioridad}) casó con {casados}.",
                )
        raise SinRutaAplicable(referencia, atributos, [r.nombre for r in self.reglas])
