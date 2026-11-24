# ADR 0005 · Cifras control por lote, en Decimal, comparadas contra un oráculo independiente

- Estado: aceptada · Fecha: 2026-11-24
- Relacionada: ADR 0002 (acuse verificable)

## Contexto
El acuse del receptor prueba que un lote **llegó**; no prueba que llegó **completo**. Un receptor puede confirmar
«recibí el corte» sobre un objeto al que le falta una fila o en el que un importe cambió de centavo. En
liquidación eso se resuelve con cifras control: conteo, suma e identificación de cada fila, que ambos lados deben
reproducir. Además, los importes de la demo viajaban como enteros y nada impedía un `float`.

## Decisión
1. `acuse/cifras.py` (dominio, sólo biblioteca estándar) calcula por lote: conteo, suma en `Decimal` y SHA-256 por
   fila sobre el JSON canónico `[referencia, importe a 2 decimales, moneda]`; el hash del lote es el de los hashes
   de fila ordenados por referencia. La **llave estable es la referencia** (folio de negocio), no el UUID de la
   entrega ni el orden de llegada.
2. Redondeo a centavos **mitad hacia arriba** (`ROUND_HALF_UP`, el redondeo comercial), fila por fila; la suma se
   hace sobre lo ya redondeado, que es lo que ve el receptor. Se descartó el bancario (`ROUND_HALF_EVEN`): no es el
   que espera nadie que concilie a mano.
3. Se rechaza lo que obligaría a inventar: `float` (ya perdió precisión), booleano, importe ausente (no vale
   cero), monedas mezcladas, referencias repetidas. Un lote sin filas concilia como `SIN_REGISTROS`, no `CUADRA`.
4. La conciliación (`conciliador.conciliar_lote`) recalcula del lado receptor y clasifica cada diferencia:
   FALTANTE, SOBRANTE, IMPORTE_DISTINTO, MONEDA_DISTINTA.
5. **Oráculo independiente:** `tests/golden/generar_golden.py` implementa la misma especificación con centavos
   enteros y su propio redondeo, sin importar nada de `acuse`, y genera los `*.cifras.golden.json`. La prueba de
   paridad exige igualdad exacta y tiene controles negativos (un centavo, una fila de más o de menos).

## Consecuencias
- Dos implementaciones que no comparten código tienen que coincidir en 31 filas de bordes (empates, trampas de
  float, negativos, cero con signo, montos grandes, referencia no ASCII) y en el lote sembrado de la demo.
- La API publica las cifras del lote depositado (`GET /api/lotes/{lote}/cifras`) con los importes como texto.
- Cambiar la especificación exige cambiar las dos implementaciones y regenerar el golden (`--revisar` en el CI).
