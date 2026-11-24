"""Contrato contra la app EN PROCESO con Schemathesis: datos generados desde el OpenAPI.

Para cada operación del contrato, Schemathesis genera peticiones válidas e inválidas y
verifica que la app nunca responde 5xx, que cada código de estado y cada cuerpo están
documentados y cumplen su esquema, y que rechaza con 4xx lo que el contrato no admite.

Corre sobre ASGI (sin red ni servidor). Cada caso parte del escenario sembrado.
"""
from __future__ import annotations

import schemathesis
from hypothesis import HealthCheck, settings

from acuse.api.app import app

esquema = schemathesis.openapi.from_asgi("/openapi.json", app)


@esquema.parametrize()
@settings(max_examples=30, deadline=None, suppress_health_check=list(HealthCheck), derandomize=True)
def test_la_api_cumple_su_contrato(case):
    case.call_and_validate()
