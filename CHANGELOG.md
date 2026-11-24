# Cambios

Formato: [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) · versiones [SemVer](https://semver.org/lang/es/).

## [0.2.0] · 2026-11-24

### Agregado
- Cifras control por lote (`acuse/cifras.py`): conteo, suma en Decimal y hash por fila con la referencia como llave
  estable; conciliación de lote con diferencias clasificadas; `GET /api/lotes/{lote}/cifras`.
- Máquina de estados explícita por eventos (`acuse/estados.py`); todo cambio de estado pasa por ella.
- Contrato OpenAPI versionado (`docs/api/openapi.json`), probado en proceso con Schemathesis.
- CI (pruebas, golden, contrato, imagen construida y arrancada) y seguridad (CodeQL, Semgrep, Trivy, gitleaks, SBOM).

### Corregido
- El plazo de acuse se medía en horas de pared con fechas de la misma zona: vencía una hora antes o después el día
  del cambio de horario. Ahora se mide en tiempo absoluto y una fecha sin zona es un error.
- Un trabajo repetido en la misma corrida se reintentaba en la misma pasada (dos envíos, dos fallidos contados).
- La API coaccionaba entradas fuera de contrato (`{"incluir_sin_ruta": 0}`, `{"n": false}`); `avance_s` sin tope
  devolvía 500; un GET a un endpoint POST respondía 404 en vez de 405.

### Cambiado
- La imagen sólo instala dependencias de ejecución con versión exacta; pytest y httpx pasan a `requirements-dev.txt`.
- Los ADR pasan de `docs/decisiones/` a `docs/adr/` (0002 y 0003).

## [0.1.0] · 2026-08-21

- Primera versión: orquestador con acuse verificable, ruteo explícito, canales síncrono, asíncrono y por lote,
  conciliador y tablero.
