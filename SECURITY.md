# Seguridad · Acuse

## Reportar una vulnerabilidad

Escribe a **christianescamilla15@gmail.com** con el asunto «Seguridad: Acuse». Respondo en 72 horas hábiles y
publico el arreglo con su aviso. Por favor no abras un issue público para vulnerabilidades.

## Alcance

Acuse es una demostración: **no tiene autenticación ni multi-tenencia** y sus canales están simulados (ver README,
«Lo que Acuse NO hace»). No lo expongas a terceros tal cual. Sí cuentan como vulnerabilidad: ejecución de código,
lectura de archivos fuera de `web/` a través del tablero, inyección en el tablero (HTML/JS) y cualquier forma de
marcar una entrega como ACUSADO sin pasar por un acuse con actor.

## Qué revisa cada cambio

| Revisión | Herramienta | Bloquea |
|---|---|---|
| Análisis estático (Python y el JavaScript del tablero) | CodeQL + Semgrep | sí (Semgrep) |
| Dependencias, Dockerfile y configuración | Trivy | sí, en CRITICAL/HIGH con arreglo publicado |
| Secretos en toda la historia | gitleaks | sí |
| Datos de terceros | revisión de confidencialidad | sí |
| Inventario de componentes | SBOM (syft, SPDX) | no: se publica con cada versión |
| Imagen de producción | job `imagen`: Python 3.12, usuario no root, sin herramientas de pruebas | sí |

Cada revisión debe probar que analizó algo (archivos o paquetes > 0); un reporte vacío cuenta como fallo.
Las excepciones viven en `.trivyignore`, cada una con fecha de vencimiento y motivo; las supresiones de Semgrep van
en el código con su motivo (hoy una: el escape HTML del tablero, `web/app.js`).
