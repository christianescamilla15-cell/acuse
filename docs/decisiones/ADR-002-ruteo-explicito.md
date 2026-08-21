# ADR-002 · Ruteo explícito: no existe canal por omisión

- **Estado:** aceptada
- **Fecha:** 2026-08-21
- **Ámbito:** enrutamiento (`acuse.ruteo`), `ReglaRuteo`, estado `RECHAZADO`
- **Relacionada:** ADR-001 (acuse verificable del receptor)

---

## Contexto

Cada trabajo de liquidación debe salir por un canal. La elección depende de atributos del
trabajo: tipo de documento, destino, moneda, si viene por lote, si es una corrección.

En integraciones reales esa elección casi siempre se implementa igual: una cadena de
condiciones sobre campos de la carga, terminada en un `else` que apunta al canal más
frecuente. El `else` se escribe con la mejor intención — "hay que mandarlo a algún lado" —
y con un supuesto que nadie verificó: **que el campo que decide siempre viene poblado.**

Lo observado, con las cifras neutralizadas:

```
Liquidaciones del periodo             1,040
Con el campo opcional poblado           728   → canal correcto
Con el campo ausente o vacío            312   → canal por omisión (equivocado)
Errores registrados                       0
Alertas emitidas                          0
Tiempo hasta detectarlo             5 semanas (por reclamo del receptor legítimo)
```

Las 312 no fallaron. **Se procesaron correctamente en el lugar equivocado**: el canal las
aceptó, el receptor de ese canal las acusó, y el destinatario legítimo nunca supo que
existían. Con el ADR-001 puesto, esas 312 aparecen como `ACUSADO`: el acuse era genuino, y
venía del receptor equivocado.

Ahí está el punto fino: **el acuse verifica la recepción, no la pertinencia.** El ADR-001
no cubre esta patología. Necesita la suya.

Tres lecturas del fallo:

1. Un campo **opcional** que decide el canal no es opcional: es obligatorio y está mal
   declarado. La opcionalidad la puso el esquema; la obligatoriedad la puso el negocio.
2. El `else` convierte **la ausencia de un dato en una decisión de negocio**, sin dejar
   rastro de que hubo una decisión. Un valor ausente y un valor explícito producen el mismo
   registro.
3. "Casi siempre es ese canal" es una estadística, no una regla. Al aplicarla como regla,
   el sistema acierta el 70 % de las veces y **no puede decir cuáles fueron el otro 30 %**.

---

## Opciones evaluadas

### Opción 1 — Omisión al canal más común

Si ninguna condición casa, se usa el canal de mayor volumen histórico.

- ✔ Nunca se atora nada. Cero trabajos bloqueados, cero intervención humana.
- ✔ Es lo que ya hace todo el mundo, así que no sorprende a nadie.
- ✘ **Es la patología.** Produce exactamente las 312 liquidaciones mal enrutadas, en
  silencio y con acuse válido.
- ✘ El error es indetectable desde el emisor: no hay excepción, no hay métrica anómala, no
  hay diferencia entre un acierto y un desastre.
- ✘ Corromper datos en el sistema de registro equivocado es **más caro** que no entregar:
  no entregar se reintenta; entregar mal hay que revertirlo, y revertir toca a terceros.

**Descartada.**

### Opción 2 — Inferir el canal por los campos presentes

Si falta el campo que decide, deducir el canal a partir de otros: si trae `lote`, va a
lotes; si trae un identificador de sesión, va a síncrono; y así.

- ✔ Rescata trabajos que de otro modo se detendrían, sin tocar al productor.
- ✔ Suena razonable, y las primeras 200 pruebas dan bien.
- ✘ **La heurística es invisible en el resultado.** Un trabajo enrutado por inferencia y
  uno enrutado por regla producen el mismo registro: cuando falla, no hay forma de separar
  ambas poblaciones para medir el daño.
- ✘ Las heurísticas se acumulan. Cada caso raro añade una condición, y en un año nadie
  puede decir por qué un trabajo concreto fue a donde fue. El ruteo se vuelve folclore.
- ✘ Es la Opción 1 con más pasos: sigue **inventando el dato faltante**, sólo que con más
  autoconfianza. Su tasa de acierto es alta, que es justo lo que la hace peligrosa: falla
  poco y falla callada.

**Descartada.**

### Opción 3 — Reglas declaradas con rechazo ruidoso (elegida)

El ruteo se declara como una lista de reglas explícitas —condición, canal, prioridad— y un
trabajo que no casa con ninguna **no se enruta**: se rechaza, con motivo.

- ✔ La decisión de canal es un **dato auditable**, no un efecto de la ejecución.
- ✔ Un dato faltante produce un rechazo visible en vez de una entrega equivocada.
- ✔ La cobertura de reglas es inspeccionable **antes** de correr nada.
- ✘ Un trabajo legítimo no contemplado se detiene y exige intervención humana.
- ✘ Alguien tiene que mantener el catálogo de reglas.

---

## Decisión

**Adoptamos reglas declaradas con rechazo ruidoso (Opción 3). No existe canal por
omisión.**

En concreto:

1. `ReglaRuteo(nombre, canal, cuando, prioridad)` declara el ruteo como **dato**, no como
   flujo de control. `cuando` casa atributos por **igualdad exacta**; deliberadamente no
   hay expresiones, negaciones ni comodines: una regla que no se puede leer en voz alta no
   se puede auditar.

2. Si ninguna regla casa, `Enrutador.resolver` lanza `SinRutaAplicable` y la entrega queda
   en `RECHAZADO` —un estado de `ESTADOS_PROBLEMA`—, no en un canal cualquiera. La
   excepción **carga el diagnóstico completo**: la referencia, los atributos recibidos y
   las reglas que se evaluaron. Quien lo lea sabe qué faltó sin abrir el código.

3. Toda decisión de ruteo devuelve una `Decision` con `regla` y `explicacion`: qué regla
   casó, con qué prioridad y qué atributos coincidieron. **El porqué viaja con el
   resultado.** No se reconstruye después leyendo logs.

4. El `Enrutador` **valida la ambigüedad al construirse**: dos reglas con la misma
   condición y la misma prioridad son un `ValueError` inmediato. Un ruteo que depende del
   orden de declaración es un ruteo que cambiará solo el día que alguien reordene la lista.

5. Prioridad menor se evalúa primero, y los empates entre condiciones distintas se resuelven
   por orden declarado. La regla general se declara con prioridad alta (se evalúa al
   final); nunca como `else`.

6. **Una regla `cuando={}` es legal y es una elección explícita**, no una omisión: casa con
   todo y debe declararse con prioridad alta y nombre propio. La diferencia con la Opción 1
   no es el efecto, es que **está escrita, tiene autor y aparece en la explicación** de cada
   trabajo que cae en ella. Un catchall visible es defendible; uno implícito no.

---

## Consecuencias

### Positivas

- La patología del campo opcional desaparece: 312 trabajos sin el dato producen 312
  `RECHAZADO` con motivo, en el primer minuto, en vez de 312 entregas equivocadas
  descubiertas a las 5 semanas.
- El ruteo se puede revisar sin ejecutar: la lista de reglas **es** la especificación.
- Cada entrega explica su propio canal. Las disputas sobre "por qué se fue por ahí" se
  resuelven mirando el registro, no reconstruyendo el estado de la máquina.
- Los conflictos entre reglas se detectan al arrancar, no en producción y a medias.
- Cierra el hueco que el ADR-001 no cubre: el acuse prueba **que llegó**; las reglas
  prueban **que llegó a quien debía**.

### Negativas

- **Trabajos legítimos se detienen.** Una regla faltante bloquea entregas reales hasta que
  alguien la declare. El costo es real y se paga en operación; el criterio es que detener
  una liquidación es reversible y entregarla al receptor equivocado no siempre lo es.
- **`RECHAZADO` necesita dueño.** Un estado de problema que nadie atiende es otra pila
  silenciosa, con mejor nombre. Debe verse en el tablero y tener responsable, o esta
  decisión sólo mueve el silencio de sitio.
- **El catálogo de reglas se mantiene.** Cada tipo nuevo de liquidación exige declarar su
  regla antes de producirla. Ese trabajo antes no existía — porque lo hacía el `else`, mal.
- **La igualdad exacta es rígida.** Un atributo con `"MX"` y otro con `"mx"` no casan, y el
  resultado es un rechazo. Es incómodo, y es preferible a un emparejamiento laxo que casa
  de más: la normalización pertenece al productor, no al enrutador.
- **Fricción con productores externos.** Un emisor acostumbrado a que "siempre pasa algo"
  verá rechazos que antes no veía. Es la misma conversación del ADR-001 y es igual de
  incómoda.

### Neutras

- El coste de resolución es lineal en el número de reglas. Con catálogos de este tamaño no
  hay nada que optimizar, y la legibilidad vale más que el índice.

---

## Criterios de reversión

1. **Rechazos crónicos por operación, no por diseño.** Si el `RECHAZADO` se vuelve el
   estado normal porque el catálogo no se mantiene, el problema es de propiedad del
   catálogo, no del modelo. La respuesta es asignar dueño y alarma — **no** reintroducir el
   `else`.
2. **Explosión combinatoria.** Si el número de reglas crece hasta ser inauditable (orden de
   cientos, con condiciones que nadie puede leer completas), se revisa el lenguaje de
   condiciones —jerarquías, agrupaciones— manteniendo intactas las dos propiedades
   irrenunciables: **decisión explicada** y **sin ruteo por omisión**.
3. **Canal único, de forma permanente.** Si el sistema queda con un solo canal posible, el
   enrutador sobra: se declara una regla catchall con nombre y se documenta. Sigue sin
   haber omisión — hay una regla, escrita.

Explícitamente **no** son motivo de reversión: que un trabajo se detenga, que un productor
se queje del rechazo, ni que el catálogo exija mantenimiento. Ese mantenimiento es el
trabajo que el `else` estaba ocultando.
