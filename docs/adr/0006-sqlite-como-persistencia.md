# ADR 0006 · SQLite como persistencia, y la integración contra un archivo real

- Estado: aceptada · Fecha: 2026-11-24 (registra una decisión vigente desde la versión 0.1.0)

## Contexto
Acuse es una demostración de un modelo de entrega: un proceso, una imagen, sin servidor de base de datos. La
persistencia (`acuse/persistencia.py`, SQLAlchemy 2) corre en SQLite en memoria por omisión, para que la demo
arranque siempre desde el mismo escenario, y en archivo con `ACUSE_BD=sqlite+pysqlite:///ruta.db` para conservar el
histórico. El programa de calidad pide integración contra «la base real en contenedor»; aquí la base real no es un
servidor.

## Decisión
- Se mantiene SQLite. Agregar PostgreSQL sólo para tener un contenedor en el CI probaría una base que el producto no
  usa.
- La integración se prueba **así**: `tests/test_persistencia_sqlite.py` abre un archivo real, lo cierra y lo reabre
  con otro engine (zonas horarias, índice único de idempotencia, estados validados, esquema idempotente); la E2E
  arranca uvicorn como proceso sobre un archivo y lo reinicia; el job «imagen» del CI reinicia el contenedor con
  `ACUSE_BD` en archivo y exige el mismo total de entregas.
- SQLite no guarda el offset de las fechas: `FechaUTC` normaliza a UTC al escribir y repone la zona al leer.

## Consecuencias
- No hay migraciones: el esquema se crea con `create_all` (idempotente). Un cambio de columnas sobre un archivo con
  histórico exige recrearlo o migrar a mano; está anotado en el RUNBOOK.
- La aplicación no es multi-proceso: los buzones de los canales viven en memoria del proceso (ver README, «Lo que
  Acuse NO hace»).
