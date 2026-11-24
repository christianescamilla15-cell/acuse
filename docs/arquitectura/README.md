# Arquitectura · Acuse

Modelo C4: contexto → contenedores → componentes. Los diagramas son texto (Mermaid) y se versionan con el código.
Las capas del último nivel no son un dibujo: `tests/test_arquitectura.py` recorre los imports y falla si alguien
las cruza.

## 1. Contexto

Acuse se para entre quien produce liquidaciones y quien debe recibirlas, y sólo da una entrega por hecha cuando el
receptor la confirma. En esta implementación el productor y los receptores están **simulados** (endpoints
`/api/simular/*` y canales en memoria): lo que se demuestra es el protocolo de acuse, no el transporte.

```mermaid
C4Context
  title Contexto · Acuse
  Person(operador, "Operador de liquidaciones", "Mira el tablero: lo enviado, lo confirmado y la brecha entre ambos")
  System(acuse, "Acuse", "Enruta liquidaciones por reglas explícitas, las entrega por canales y exige acuse del receptor")
  System_Ext(productor, "Productor de liquidaciones", "Genera los trabajos a entregar (simulado: /api/simular/corrida)")
  System_Ext(receptor, "Receptores", "Confirman lo que recibieron, con actor (simulados: canales síncrono, asíncrono y por lote)")
  Rel(operador, acuse, "Consulta y provoca escenarios", "HTTPS")
  Rel(productor, acuse, "Entrega trabajos", "llamada en proceso")
  Rel(acuse, receptor, "Envía o deposita", "canal")
  Rel(receptor, acuse, "Acusa recibo", "devolución con actor")
```

## 2. Contenedores

Un solo proceso y una sola imagen (`Dockerfile`): la API sirve también el tablero, del mismo origen.

```mermaid
C4Container
  title Contenedores · Acuse
  Person(operador, "Operador de liquidaciones")
  System_Boundary(b, "Acuse (una imagen, un proceso)") {
    Container(tablero, "Tablero", "HTML + CSS + JavaScript sin compilación (web/)", "Pinta los dos números, la brecha por canal y las corridas; servido en «/»")
    Container(api, "API", "Python 3.12 · FastAPI · uvicorn (acuse/)", "Ruteo, envío, acuse, conciliación y cifras control; contrato en docs/api/openapi.json")
    ContainerDb(bd, "Base de datos", "SQLite · SQLAlchemy 2", "Entregas, corridas y bitácora append-only; en memoria o en archivo (ACUSE_BD)")
    Container(buzones, "Buzones de los canales", "Memoria del proceso", "Lo depositado por los canales asíncrono y por lote, hasta que el receptor lo lee")
  }
  Rel(operador, tablero, "Usa", "HTTPS")
  Rel(tablero, api, "Lee y simula", "JSON /api, mismo origen")
  Rel(api, bd, "Lee y escribe", "SQL")
  Rel(api, buzones, "Deposita y consume", "en proceso")
```

## 3. Componentes de la API

```mermaid
C4Component
  title Componentes · API de Acuse
  Container_Boundary(api, "API (acuse/)") {
    Component(http, "Endpoints HTTP", "acuse/api/app.py · FastAPI", "Esquemas estrictos de entrada, errores con motivo, tablero sin tapar la API")
    Component(demo, "Escenario sembrado", "acuse/demo.py", "Reglas, canales y los 30 trabajos deterministas de la demo")
    Component(orq, "Orquestador", "acuse/orquestador.py", "Corridas: enruta, envía, registra; idempotencia por trabajo+referencia; SIN_INSUMO")
    Component(conc, "Conciliador", "acuse/conciliador.py", "Brecha por canal, vencimientos, resumen con los dos números y conciliación de lotes")
    Component(ruteo, "Enrutador", "acuse/ruteo.py", "Reglas declaradas por prioridad; sin canal por omisión")
    Component(estados, "Máquina de estados", "acuse/estados.py", "Tabla (estado, evento) → estado; toda transición pasa por aquí")
    Component(cifras, "Cifras control", "acuse/cifras.py", "Conteo, suma en Decimal y hash por fila con llave estable")
    Component(modelo, "Modelo", "acuse/modelo.py", "Entrega, Trabajo, ReglaRuteo, Brecha; tiempo absoluto (UTC)")
    Component(canales, "Canales", "acuse/canales/", "Contrato y registro; síncrono, asíncrono y por lote (simulados)")
    Component(pers, "Persistencia", "acuse/persistencia.py · SQLAlchemy", "ORM, FechaUTC, índice único de idempotencia")
  }
  ContainerDb(bd, "SQLite", "memoria o archivo")
  Rel(http, orq, "procesar / registrar_acuse")
  Rel(http, conc, "brechas / resumen / marcar_vencidas")
  Rel(http, cifras, "cifras del lote depositado")
  Rel(http, demo, "siembra y genera trabajos")
  Rel(orq, ruteo, "resolver")
  Rel(orq, canales, "enviar + validar_resultado")
  Rel(orq, estados, "siguiente()")
  Rel(conc, estados, "siguiente()")
  Rel(conc, cifras, "conciliar_cifras")
  Rel(orq, pers, "entregas, corridas, eventos")
  Rel(conc, pers, "consultas agregadas")
  Rel(pers, bd, "SQL")
```

## 4. Capas (verificadas por `tests/test_arquitectura.py`)

| Capa | Módulos | Puede importar | Terceros permitidos |
|---|---|---|---|
| dominio | `modelo`, `estados`, `ruteo`, `cifras` | sólo el dominio | ninguno (biblioteca estándar) |
| canales | `canales`, `canales.sincrono`, `canales.asincrono`, `canales.lote` | dominio | ninguno |
| infraestructura | `persistencia` | dominio | SQLAlchemy |
| aplicación | `orquestador`, `conciliador` | dominio, canales, infraestructura | SQLAlchemy |
| composición | `demo` | dominio, canales | ninguno |
| presentación | `api`, `api.app` | todas | FastAPI, Pydantic, Starlette, SQLAlchemy |

Un módulo nuevo sin capa asignada también hace fallar la prueba.

## 5. La entrega como máquina de estados

Tabla completa (18 transiciones válidas y 18 inválidas, todas probadas) en `acuse/estados.py` y
`tests/test_maquina_estados.py`; decisión en [ADR 0004](../adr/0004-maquina-de-estados-por-eventos.md).

```mermaid
stateDiagram-v2
  [*] --> PENDIENTE
  PENDIENTE --> RECHAZADO: SIN_RUTA
  PENDIENTE --> FALLIDO: FALLA_CANAL
  PENDIENTE --> ENVIADO: DEPOSITO
  PENDIENTE --> ACUSADO: ACUSE_INMEDIATO
  ENVIADO --> ACUSADO: ACUSE_RECEPTOR
  ENVIADO --> SIN_ACUSE: VENCIMIENTO
  RECHAZADO --> ENVIADO: reintento (mismos eventos de despacho)
  FALLIDO --> ENVIADO: reintento
  SIN_ACUSE --> ENVIADO: reintento
  ACUSADO --> [*]
```

## 6. Decisiones

[`docs/adr/`](../adr/): 0001 registrar decisiones · 0002 acuse verificable del receptor · 0003 ruteo explícito ·
0004 máquina de estados por eventos · 0005 cifras control con oráculo independiente · 0006 SQLite como
persistencia.
