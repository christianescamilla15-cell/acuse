# Acuse

[![ci](https://github.com/christianescamilla15-cell/acuse/actions/workflows/ci.yml/badge.svg)](https://github.com/christianescamilla15-cell/acuse/actions/workflows/ci.yml)
[![seguridad](https://github.com/christianescamilla15-cell/acuse/actions/workflows/seguridad.yml/badge.svg)](https://github.com/christianescamilla15-cell/acuse/actions/workflows/seguridad.yml)

**Orquestador de liquidación con acuse verificable.**

> ### Una entrega sin acuse verificable no es una entrega.

Acuse es un orquestador que reparte trabajos de liquidación por varios canales y **sólo
los da por entregados cuando el receptor lo confirma**. No es un framework de mensajería:
es un modelo de entrega que hace estructuralmente imposible el fallo más caro y más
aburrido de las integraciones — el **éxito silencioso**.

---

## El problema — el éxito silencioso

Un sistema falla ruidosamente y alguien lo arregla el mismo día. Un sistema que **reporta
éxito sin haber logrado nada** sigue reportando éxito durante semanas. Las tres formas de
abajo son el mismo fallo con tres disfraces: el proceso mide **lo que él hizo**, no **lo
que ocurrió**.

Los números son ilustrativos, pero las formas son reales y se repiten.

### a) `enviado ≠ recibido`

El emisor deja el archivo en un almacenamiento compartido, marca `entregado = true` y
cierra la corrida. Del otro lado, el consumidor que debía leer ese almacenamiento **nunca
se construyó**: quedó como una fase posterior del proyecto y nadie volvió a mirarlo.

```
Tablero del emisor        1,180 liquidaciones · 1,180 entregadas · 100 %
Realidad del receptor     48 objetos apilados en el almacenamiento · 0 leídos
Antigüedad del más viejo  62 días
Cómo se descubrió         un reclamo del área de negocio, no una alarma
```

Nada falló. Ningún reintento se disparó, ninguna alarma sonó, ninguna métrica se puso en
rojo: el emisor **midió su propia escritura** y la llamó entrega. El síntoma apareció a
dos meses y tres equipos de distancia de la causa.

### b) Ruteo por omisión

El canal de salida se decide por un campo **opcional** de la carga. Si el productor lo
puebla, la liquidación viaja al canal correcto. Si no lo puebla, el código cae en un
`else` que apunta al canal más común, porque "casi siempre es ese".

```
Liquidaciones del periodo          1,040
Con el campo opcional poblado        728  → canal correcto
Con el campo ausente o vacío         312  → canal por omisión (equivocado)
Errores registrados                    0
Alertas emitidas                       0
```

Las 312 se procesaron. Se procesaron **en el lugar equivocado**, con acuse del receptor
equivocado, y el destinatario legítimo no supo que existían. Un `else` bien intencionado
convirtió un dato faltante en una decisión de negocio.

### c) `verde ≠ hizo algo`

Un proceso programado corre todas las madrugadas, no encuentra insumo, no tiene nada que
hacer y termina con código de salida `0`. El tablero lo pinta verde. Está verde porque
**no hizo nada**, y llevaba semanas sin hacer nada.

```
Corridas consecutivas exitosas          41
Duración media                        2.3 s   (una corrida real toma ~4 min)
Registros procesados en esas 41          0
Tiempo sin procesar                 6 semanas
Señal de alarma disponible             ninguna: "éxito" es el estado esperado
```

Un proceso sin insumo no es un proceso exitoso: es un proceso **que no se ejecutó**.
Mientras "terminó bien" y "procesó algo" sean la misma métrica, la diferencia es invisible.

---

## Cómo lo resuelve Acuse

| Patología | Decisión de diseño en Acuse |
|---|---|
| El emisor se declara a sí mismo entregado | `ENVIADO` y `ACUSADO` son **estados distintos**. `Entrega.entregado` devuelve `True` **sólo** si el estado es `ACUSADO` — deliberadamente no incluye `ENVIADO`. |
| El acuse no tiene autor | El acuse exige `acuse_por`: **quién** confirmó. Sin actor no hay acuse; una confirmación anónima es una suposición con timestamp. |
| Enviado hace semanas y nadie confirma | Cada entrega lleva `plazo_acuse_s` por canal. Vencido el plazo pasa a `SIN_ACUSE`: un estado **de problema**, no un limbo silencioso. |
| El canal se elige por un campo ausente | El enrutamiento es por **reglas declaradas**. Si ninguna casa, el enrutador lanza `SinRutaAplicable` y la entrega queda `RECHAZADO` con el motivo. **No existe canal por omisión.** |
| Dos reglas que se pisan, resultado impredecible | El `Enrutador` **valida la ambigüedad al construirse**: dos reglas con la misma condición y la misma prioridad son un `ValueError`, no una carrera. |
| Una corrida sin insumo se ve igual que una exitosa | La corrida reporta **qué hizo**, no sólo que terminó. Cero trabajos procesados es un resultado reportable y distinguible, no un éxito. |
| El agujero entre "enviado" y "confirmado" no se mide | `Brecha` por canal: `enviados`, `acusados`, `sin_acuse` y la antigüedad del más viejo. `hay_brecha` es la pregunta que el emisor nunca se hizo solo. |
| Reintentos que duplican liquidaciones | Clave de `idempotencia`: misma clave, misma entrega. Reintentar es seguro; por eso se puede reintentar de verdad. |

---

## El modelo de estados

```
                          ┌──────────────┐
                          │  PENDIENTE   │  aceptada, aún no se intenta
                          └──────┬───────┘
                                 │  despachar
                 ┌───────────────┼────────────────┐
                 │               │                │
        el canal │       el canal acepta          │ no hay regla
         rechaza │               │                │ que lo cubra
                 ▼               ▼                ▼
          ┌─────────────┐  ┌───────────┐   ┌──────────────┐
          │   FALLIDO   │  │  ENVIADO  │   │  RECHAZADO   │
          └─────────────┘  └─────┬─────┘   └──────────────┘
                                 │
                 ┌───────────────┴────────────────┐
                 │                                │
   el RECEPTOR   │                                │  vence plazo_acuse_s
   confirma      ▼                                ▼  sin confirmación
          ┌─────────────┐                  ┌──────────────┐
          │   ACUSADO   │  ← entrega       │  SIN_ACUSE   │
          └─────────────┘                  └──────────────┘
```

**La línea que sostiene todo el proyecto está entre `ENVIADO` y `ACUSADO`.**

- `ENVIADO` es una afirmación **del emisor**: "lo puse en el canal". Es honesta y es
  insuficiente. Es exactamente lo que los sistemas del apartado (a) llamaban "entregado".
- `ACUSADO` es una afirmación **del receptor**: "lo tengo". Sólo el receptor puede
  producirla, y queda firmada con `acuse_por` y `acusado_en`.
- El emisor **no puede acusar sus propios envíos**. Si pudiera, el estado no aportaría
  información: sería el mismo dato contado dos veces.

Los conjuntos del modelo hacen operativa la distinción:

```python
ESTADOS_ABIERTOS  = {PENDIENTE, ENVIADO}                    # aún deben algo
ESTADOS_PROBLEMA  = {SIN_ACUSE, FALLIDO, RECHAZADO}         # exigen a un humano
```

`ENVIADO` está en **abiertos**, no en terminados. Un envío sin acuse es trabajo pendiente,
no trabajo hecho.

---

## Los dos números

Todo tablero de integración muestra un número de entregas. Acuse muestra **dos**, y la
distancia entre ellos es el producto:

| Número | Qué significa | Quién lo afirma | Se puede inflar solo |
|---|---|---|---|
| **Entregado según el emisor** | `ENVIADO + ACUSADO` — salió por el canal | El emisor | **Sí**: sube con reintentos, con duplicados y con receptores caídos |
| **Entregado confirmado** | `ACUSADO` — el receptor lo tiene | El receptor | **No**: alguien del otro lado tuvo que responder |

```
Canal          emisor dice   confirmado   brecha   más antiguo
──────────────────────────────────────────────────────────────
sincrono            412          412          0          —
asincrono           388          361         27      1 h 42 m
lote                240            0        240      3 d 06 h   ← aquí vive el fallo (a)
```

Un solo número siempre se ve bien. **La brecha es el producto.** Un tablero que sólo puede
mostrar el primero es el tablero que decía `100 %` mientras 48 objetos se apilaban.

---

## Canales

Un canal es la forma de entrega, y cada uno **demuestra una cosa distinta del modelo**.
Los canales de esta implementación están **simulados** (ver más abajo): el objeto de
estudio es el protocolo de acuse, no el transporte.

| Canal | Modo | Cuándo llega el acuse | Qué demuestra |
|---|---|---|---|
| **Síncrono** | `SINCRONO` | En la misma llamada. El receptor responde o no hay envío. | El caso fácil: aquí `ENVIADO` y `ACUSADO` casi coinciden. Sirve de **línea base** — si la brecha del síncrono no es cero, el problema es otro. |
| **Asíncrono** | `ASINCRONO` | Después, por devolución del receptor contra el orquestador. | El caso real: la entrega **vive en `ENVIADO`** un rato. Aquí `plazo_acuse_s` y `SIN_ACUSE` hacen su trabajo, y aquí aparece la patología (a) si el receptor no existe. |
| **Lote** | `ASINCRONO` | Después y **agrupado**: el receptor acusa el lote, y el acuse baja a cada trabajo. | Que el acuse escala sin volverse por-archivo. También el peor escenario: un lote sin consumidor produce **una** brecha grande y visible en vez de N silencios. |

El campo `Trabajo.lote` es el que agrupa; el modo del canal sigue siendo `ASINCRONO`,
porque un lote es un asíncrono con acuse colectivo, no una tercera física de entrega.

---

## Cómo ejecutar

Requiere Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # sólo ejecución (lo mismo que lleva la imagen)

uvicorn acuse.api.app:app --reload --port 8000
```

Abrir el tablero:

```
http://127.0.0.1:8000/
```

La especificación viva de la API la publica el propio servicio en `/docs`.

Pruebas (ver la sección [Pruebas](#pruebas)):

```bash
pip install -r requirements-dev.txt
pytest -q
```

Con contenedor:

```bash
docker build -t acuse .
docker run --rm -p 8000:8000 acuse
```

---

## Escenario de la demo

El tablero arranca vacío a propósito: **un sistema sin datos no debe verse igual que uno
sano**. El recorrido está pensado para durar tres minutos y para que cada control pruebe
una de las tres patologías.

1. **Encolar trabajos.** Se crean liquidaciones con atributos (tipo, destino, moneda…) y
   se enrutan por reglas. En el detalle de cada una queda la **explicación del ruteo**: qué
   regla casó y por qué. El ruteo deja de ser folclore y pasa a ser un dato.

2. **Encolar un trabajo que ninguna regla cubre.** No cae en el canal más común: sale
   `RECHAZADO`, en rojo, con el motivo y con la lista de reglas que se evaluaron.
   → **Demuestra (b): ruteo por omisión.** El dato faltante produce un rechazo ruidoso, no
   una decisión inventada.

3. **Despachar.** Todo lo `PENDIENTE` pasa a `ENVIADO`. Fíjese en el tablero: el contador
   de "entregado según el emisor" sube, y el de **confirmado sigue en cero**. La brecha se
   abre a la vista.
   → **Demuestra (a): enviado ≠ recibido.** Este es el instante exacto que los sistemas
   rotos reportan como `100 %`.

4. **Acusar como receptor.** Desde el panel del receptor se confirma una entrega — con
   nombre de quien confirma. Sólo entonces pasa a `ACUSADO` y el segundo número se mueve.
   Note que el emisor no tiene ese botón: **no puede acusarse a sí mismo**.

5. **Dejar vencer el plazo** (o forzar el barrido). Lo que lleva `ENVIADO` más de
   `plazo_acuse_s` cae a `SIN_ACUSE` y aparece en el panel de brecha, con la antigüedad del
   más viejo. Nadie tiene que revisar un almacenamiento a mano para enterarse.

6. **Correr sin insumo.** Se dispara una corrida con la cola vacía. **No** reporta éxito:
   reporta explícitamente cero trabajos procesados y lo distingue de una corrida que sí
   hizo algo.
   → **Demuestra (c): verde ≠ hizo algo.**

Al final, el tablero muestra los dos números y la brecha por canal. Ese es el proyecto
entero en una pantalla.

---

## Lo que Acuse NO hace

Decirlo importa tanto como lo anterior: un README que no marca sus fronteras es la misma
clase de afirmación no verificada que el proyecto combate.

- **Los transportes están simulados.** Los canales no hablan con sistemas reales; imitan
  latencia, fallo y confirmación tardía. Lo verificable aquí es el **protocolo de acuse**,
  no la entrega física.
- **No es una cola de mensajes de producción.** No hay particiones, ni replicación, ni
  entrega ordenada, ni garantías de durabilidad ante caída del proceso. Si necesita eso,
  use una cola de verdad — y **aplíquele este modelo encima**, porque una cola tampoco
  sabe si el consumidor entendió lo que leyó.
- **No es un motor de reglas.** El ruteo casa atributos por igualdad exacta, con
  prioridad. Es deliberadamente pequeño: la tesis es que las reglas sean **declaradas y
  auditables**, no expresivas.
- **No reemplaza al receptor.** Acuse no puede confirmar en nombre de nadie. Si el receptor
  no coopera, Acuse no inventa el acuse: **muestra el hueco**. Ese es exactamente el punto,
  y es también su costo (ver [ADR 0002](docs/adr/0002-acuse-verificable.md)).
- **No hay autenticación ni multi-tenencia.** Es una demostración de modelo, no un servicio
  expuesto a terceros.

---

## Arquitectura

C4 en Mermaid (contexto, contenedores y componentes reales), las capas y la máquina de estados:
[docs/arquitectura/README.md](docs/arquitectura/README.md). Un proceso y una imagen: la API (FastAPI) sirve el
tablero del mismo origen y persiste en SQLite (memoria o archivo con `ACUSE_BD`). Las capas no son un dibujo:
`tests/test_arquitectura.py` falla si el dominio importa FastAPI, SQLAlchemy o la persistencia.

Decisiones ([docs/adr/](docs/adr/)):

- [ADR 0001 · Registrar las decisiones](docs/adr/0001-registrar-decisiones.md)
- [ADR 0002 · Acuse verificable del receptor](docs/adr/0002-acuse-verificable.md)
- [ADR 0003 · Ruteo explícito, sin canal por omisión](docs/adr/0003-ruteo-explicito.md)
- [ADR 0004 · La entrega como máquina de estados por eventos](docs/adr/0004-maquina-de-estados-por-eventos.md)
- [ADR 0005 · Cifras control en Decimal y oráculo independiente](docs/adr/0005-cifras-control-y-oraculo-independiente.md)
- [ADR 0006 · SQLite como persistencia](docs/adr/0006-sqlite-como-persistencia.md)

Contrato de la API: [docs/api/openapi.json](docs/api/openapi.json) (generado de la app con
`herramientas/exportar_openapi.py`; una prueba falla si se desincroniza). Operación y reversa: [RUNBOOK.md](RUNBOOK.md).

---

## Pruebas

La compuerta es [docs/reglas.md](docs/reglas.md): **88 reglas de negocio, cada una con sus pruebas**
(`archivo::función`), verificada por `tests/test_reglas_doc.py` — ninguna regla sin prueba, ninguna prueba sin
regla. La cobertura se reporta en el CI; no es la compuerta.

| Capa | Qué prueba | Dónde |
|---|---|---|
| Dominio | Máquina de estados (las 36 combinaciones estado × evento), ruteo, cifras control, bordes de importes (Decimal, redondeo) y de fechas (cambio de horario) | `tests/test_maquina_estados.py`, `test_ruteo.py`, `test_cifras_control.py`, `test_bordes_importes.py`, `test_bordes_fechas.py` |
| Aplicación | Orquestador, conciliador, reenvío idempotente, presupuesto de consultas SQL | `tests/test_orquestador*.py`, `test_conciliador.py`, `test_reenvio_idempotente.py`, `test_presupuesto_consultas.py` |
| Integración | SQLite en archivo real: reabrir, zonas horarias, restricciones de la base | `tests/test_persistencia_sqlite.py` |
| Paridad | Golden generado por un oráculo independiente (centavos enteros) + controles negativos | `tests/test_paridad_golden.py`, `tests/golden/` |
| API y contrato | Endpoints, errores, contrato versionado y Schemathesis contra la app en proceso | `tests/test_api*.py`, `test_contrato_*.py` |
| Arquitectura | Imports por capa, con controles negativos del verificador | `tests/test_arquitectura.py` |
| E2E | El recorrido de la demo contra uvicorn como proceso, con reinicio sobre el mismo archivo | `tests/e2e/` |

```bash
pytest -q                                        # todo (≈ 30 s; incluye E2E y Schemathesis)
python tests/golden/generar_golden.py --revisar  # el golden coincide con su oráculo
python herramientas/exportar_openapi.py --revisar
```

---

## Seguridad

Política y canal de reporte en [SECURITY.md](SECURITY.md). Cada cambio pasa CodeQL (Python y JavaScript), Semgrep
(bloqueante), Trivy (dependencias y Dockerfile), gitleaks sobre toda la historia, una revisión de confidencialidad y
SBOM; cada escaneo debe probar que analizó algo. La imagen corre sin root y sin herramientas de pruebas, y el CI la
construye y la arranca. **Acuse no tiene autenticación**: es una demostración, no la expongas a terceros.

---

## Licencia

MIT. Ver [LICENSE](LICENSE).
