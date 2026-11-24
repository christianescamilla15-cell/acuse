# Runbook · Acuse

## Dónde corre

**No hay despliegue en la nube ni en ningún servidor.** El artefacto que se entrega es la imagen de contenedor
del `Dockerfile` (Python 3.12, usuario no root, sólo dependencias de ejecución) y se corre localmente, como dice el
README. El CI la construye y la arranca en cada cambio (job `imagen`). Si algún día se publica en un servicio, este
runbook es el lugar donde escribir el pipeline: preview → humo → producción, nunca a mano.

## Arrancar

```bash
docker build -t acuse:$(git rev-parse --short HEAD) .
docker run -d --name acuse -p 8000:8000 acuse:$(git rev-parse --short HEAD)
herramientas/humo.sh http://localhost:8000          # debe terminar en «humo: todo responde»
```

Por omisión la base vive en memoria: cada arranque siembra el mismo escenario (30 entregas, dos corridas). Para
conservar el histórico entre reinicios, apunta `ACUSE_BD` a un archivo que el usuario `acuse` (UID 10001) pueda
escribir, por ejemplo `-e ACUSE_BD=sqlite+pysqlite:////tmp/acuse.db` (sobrevive a `docker restart`, no a
`docker rm`) o un directorio montado con dueño 10001.

## Revertir (ensayado el 2026-11-24)

1. `docker rm -f acuse`
2. `docker run -d --name acuse -p 8000:8000 acuse:<sha anterior>` — cada imagen se etiqueta con su commit.
3. `herramientas/humo.sh http://localhost:8000`

Ensayo del 2026-11-24 en local: imagen de esta rama → humo verde; reversa a `acuse:3bce497` (versión 0.1.0, antes
del programa de calidad) → humo verde salvo **una** comprobación esperada (`GET /api/reiniciar → 404`, el defecto
del tablero que tapaba la API, corregido en 0.2.0); vuelta a la imagen de esta rama → humo verde.

Con `ACUSE_BD` en archivo: el esquema se crea con `create_all` y **no hay migraciones** ([ADR 0006](docs/adr/0006-sqlite-como-persistencia.md)).
Entre 0.1.0 y 0.2.0 el esquema no cambió, así que la reversa puede reutilizar el archivo. Si una versión futura
cambia columnas, la reversa debe usar una copia del archivo anterior al cambio.

## Diagnóstico

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El tablero arranca siempre con las mismas 30 entregas | Base en memoria (por diseño) | Usar `ACUSE_BD` en archivo si se quiere conservar el histórico |
| Con `ACUSE_BD` el contenedor no arranca: `unable to open database file` | El usuario `acuse` (UID 10001) no puede escribir en la ruta | Usar `/tmp/…` o montar un directorio con dueño 10001 |
| `/health` verde pero la brecha no baja | Nadie está consumiendo (por diseño: sólo el acuse del receptor la cierra) | Ver `/api/brechas`; consumir con `/api/simular/consumir` |
| Una corrida terminó `SIN_INSUMO` | No hubo trabajos nuevos (o todos eran duplicados vivos) | Revisar quién produce el insumo; no es un éxito |
| Entregas `RECHAZADO` con canal nulo | Ningún `ReglaRuteo` casa con sus atributos | Declarar la regla que falta (ADR 0003); nunca un canal por omisión |
| `GET` a un endpoint devuelve 405 | El endpoint es `POST` (el tablero ya no tapa la API) | Usar el método del contrato `docs/api/openapi.json` |
| 422 con un cuerpo que «antes funcionaba» | Las peticiones son estrictas desde 0.2.0 (`0` no es booleano, `false` no es entero) | Enviar los tipos del contrato |
| El contenedor queda `unhealthy` | El tablero (`/`) no responde en 2 s | `docker logs acuse`; `herramientas/humo.sh` dice qué parte falla |
