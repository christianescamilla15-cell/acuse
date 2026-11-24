# ADR 0001 · Registrar las decisiones de arquitectura

- Estado: aceptada · Fecha: 2026-11-24

## Contexto
Las decisiones importantes se pierden en commits y conversaciones. Acuse ya tenía dos (acuse verificable y ruteo
explícito) en `docs/decisiones/`, con un formato propio y sin numeración común.

## Decisión
Cada decisión que cambie la estructura, una dependencia mayor o un contrato se registra como ADR numerado en
`docs/adr/`, con contexto, decisión, alternativas y consecuencias. Las dos existentes pasan a ser la 0002 y la 0003
sin cambiar su contenido (sólo la numeración y una línea «Antes:»).

## Consecuencias
Quien llegue al repo entiende por qué el sistema es como es, no sólo cómo es. La regla de negocio que una decisión
introduce se agrega a `docs/reglas.md` con su prueba; el ADR explica el porqué, la tabla prueba el qué.
