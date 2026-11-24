# ADR 0004 · La entrega es una máquina de estados explícita, por eventos

- Estado: aceptada · Fecha: 2026-11-24
- Relacionada: ADR 0002 (acuse verificable), ADR 0003 (ruteo explícito)

## Contexto
Los seis estados de `EstadoEntrega` existían desde el principio, pero las transiciones eran asignaciones sueltas
(`entrega.estado = EstadoEntrega.X`) repartidas entre el orquestador y el conciliador. Funcionaban, y aun así nada
en el código decía cuáles eran válidas: no se podía probar que las inválidas fueran imposibles, sólo que los caminos
conocidos no las producían. El modelo entero (ADR 0002) descansa en una transición —ENVIADO → ACUSADO— que sólo el
receptor puede provocar; dejarla implícita es dejar el corazón del proyecto sin contrato.

## Alternativas
1. **Tabla de parejas (origen, destino).** Simple, pero insuficiente: SIN_ACUSE → ACUSADO es legítimo si se reenvía
   por un canal síncrono que confirma en la llamada, e ilegítimo si es un acuse tardío del receptor (el
   incumplimiento ocurrió y no se borra). La misma pareja, dos respuestas.
2. **Tabla (estado, evento) → estado** (elegida). Seis eventos (`SIN_RUTA`, `FALLA_CANAL`, `DEPOSITO`,
   `ACUSE_INMEDIATO`, `ACUSE_RECEPTOR`, `VENCIMIENTO`) y una tabla de 18 transiciones válidas.
3. **Biblioteca de máquinas de estados.** Una dependencia para 18 entradas de un diccionario, en una capa (dominio)
   que hoy sólo usa la biblioteca estándar.

## Decisión
`acuse/estados.py` declara la tabla como dato y `siguiente(estado, evento)` es el único camino para cambiar de
estado; lo que no está en la tabla lanza `TransicionInvalida` con los eventos válidos desde ese estado. ACUSADO es
terminal y nadie vuelve a PENDIENTE. El reintento no es un evento propio: desde FALLIDO, RECHAZADO o SIN_ACUSE se
vuelve a despachar con los mismos eventos que desde PENDIENTE (`DESPACHABLES`).

## Consecuencias
- `tests/test_maquina_estados.py` escribe la tabla esperada a mano, independiente del código, y prueba las 36
  combinaciones (18 válidas, 18 inválidas); además intenta las inválidas por las operaciones públicas.
- Agregar un estado o un evento obliga a decidir su fila en la tabla y en la prueba: no puede colarse una
  transición sin que alguien la escriba.
- El comportamiento previo no cambió: las 49 pruebas originales pasan sin tocarse.
