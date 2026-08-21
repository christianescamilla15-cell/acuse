# ADR-001 · La entrega la confirma el receptor, no el emisor

- **Estado:** aceptada
- **Fecha:** 2026-08-21
- **Ámbito:** modelo de entrega (`acuse.modelo`), máquina de estados, tablero
- **Relacionada:** ADR-002 (ruteo explícito)

---

## Contexto

El orquestador reparte trabajos de liquidación por canales heterogéneos: unos responden en
la misma llamada, otros aceptan el trabajo y contestan después, otros reciben lotes.

En todos ellos, la pregunta operativa es una sola: **¿el receptor tiene la liquidación?**

La forma habitual de responderla es medir el acto del emisor. El emisor escribe en el
canal, no hay excepción, y marca `entregado = true`. Esa medición es barata, siempre está
disponible y **es la causa raíz de la patología que este proyecto ataca**: el éxito
silencioso.

Tres observaciones sobre el fallo, tomadas de integraciones reales y con las cifras
neutralizadas:

1. Un emisor reportó `1,180 / 1,180 entregadas · 100 %` durante 62 días. Del otro lado
   había **48 objetos apilados** en el almacenamiento y ningún consumidor: la pieza que
   debía leerlos nunca se construyó. Ninguna alarma podía sonar, porque **nada falló**: el
   emisor hizo exactamente lo que sabía hacer y midió exactamente eso.

2. El síntoma apareció **lejos de la causa**: por un reclamo de negocio, dos meses y tres
   equipos después. El costo de un fallo silencioso no es el fallo; es la distancia entre
   el momento en que ocurre y el momento en que alguien se entera.

3. Cuando finalmente se investigó, el sistema **no tenía cómo responder la pregunta**. No
   existía un dato que dijera "el receptor lo tiene". Había que abrir el almacenamiento a
   mano y contar. Un dato que sólo se obtiene manualmente no existe operativamente.

El problema, entonces, no es que el emisor mienta. Es que **el emisor no es una fuente con
autoridad sobre la recepción**, y todo el diseño estaba construido sobre su palabra.

---

## Opciones evaluadas

### Opción 1 — Confiar en el emisor (el estado del arte del fallo)

`ENVIADO` es el estado terminal de éxito. Si la escritura en el canal no lanzó excepción,
la entrega se da por buena.

- ✔ Cero costo. Cero coordinación. Funciona el primer día.
- ✔ El número siempre se ve bien — y ese es justamente el problema.
- ✘ Mide el acto del emisor, no el hecho de la recepción: **responde otra pregunta**.
- ✘ Es indistinguible del caso en que el receptor no existe. Los 48 objetos apilados son
  el resultado exacto de esta opción.
- ✘ No admite alarma posible: no hay señal que encender, porque no hay fallo que detectar.

**Descartada.** Es la patología, no una alternativa a ella.

### Opción 2 — Reintentos ciegos con reglas de tiempo

Si pasado un plazo "algo parece mal", se reenvía. Backoff, N intentos, cola de muertos.

- ✔ Recupera fallos **transitorios** de transporte, que existen y son reales.
- ✔ No requiere nada del receptor.
- ✘ Reintentar es una respuesta, no una medición: sigue sin haber un dato que diga
  "llegó". Al final de los N intentos el sistema sigue sin saber si el receptor lo tiene.
- ✘ Contra un receptor inexistente, **multiplica el daño**: en el caso observado habría
  producido 48 objetos por número de intentos, todos igual de silenciosos.
- ✘ Sin idempotencia del lado receptor, un reintento contra un receptor lento produce
  liquidaciones duplicadas — se cambia un fallo silencioso por otro peor y con costo
  contable.

**Descartada como mecanismo principal.** Sobrevive como complemento: los reintentos son
útiles **una vez que existe** una señal que distinga "no llegó" de "no lo sé".

### Opción 3 — Acuse explícito del receptor (elegida)

El receptor confirma, contra el orquestador, que tiene la liquidación. `ENVIADO` y
`ACUSADO` son estados **distintos**, y sólo el segundo cuenta como entrega.

- ✔ Mide **el hecho**, no el acto. Responde la pregunta que importa.
- ✔ El acuse lleva autor (`acuse_por`) y momento (`acusado_en`): es auditable, no una
  inferencia con fecha.
- ✔ La ausencia de acuse es un **dato positivo y consultable** — la brecha por canal —, no
  una ausencia de evidencia.
- ✔ El emisor pierde la capacidad de declararse exitoso a sí mismo. Es una pérdida
  deseable.
- ✘ **Exige cooperación del receptor.** Sin contraparte que confirme, no hay acuse.
- ✘ Añade un estado y un plazo por canal (ver Consecuencias).

### Opción 4 — Confirmación por sondeo del destino

El orquestador verifica por su cuenta que el artefacto llegó: lista el destino, cuenta
objetos, consulta un extremo del receptor.

- ✔ No exige que el receptor implemente nada nuevo: se le observa desde fuera.
- ✔ Detecta el caso "el archivo no está" sin coordinación previa.
- ✘ **Verifica presencia, no procesamiento.** El objeto puede estar ahí y no haberse
  leído nunca — que es literalmente el caso de los 48 objetos apilados. Un sondeo del
  destino los habría contado y declarado presentes.
- ✘ Requiere que el orquestador tenga acceso de lectura al destino, lo que invierte la
  frontera de confianza y no siempre es posible.
- ✘ Acopla el orquestador a la forma física de cada canal: un sondeo distinto por
  transporte, cada uno con su propia manera de mentir.

**Descartada como sustituto.** Útil como red de seguridad **secundaria** en canales donde
el receptor no puede acusar; nunca como definición de entrega.

---

## Decisión

**Adoptamos el acuse explícito del receptor (Opción 3) como única definición de entrega.**

En concreto:

1. `EstadoEntrega` separa `ENVIADO` (afirmación del emisor) de `ACUSADO` (afirmación del
   receptor). Son estados distintos y no colapsan jamás.

2. `Entrega.entregado` devuelve `True` **sólo** con `ACUSADO`. La exclusión de `ENVIADO`
   es deliberada y está anotada en el código para que ningún cambio futuro la "arregle".

3. `ENVIADO` pertenece a `ESTADOS_ABIERTOS`, no a los terminales. Un envío sin confirmar
   es trabajo pendiente.

4. Un acuse sin `acuse_por` no es un acuse. Se exige el actor: una confirmación anónima es
   una suposición con marca de tiempo.

5. Cada entrega lleva `plazo_acuse_s`. Vencido el plazo sin confirmación, la entrega pasa a
   `SIN_ACUSE`, que vive en `ESTADOS_PROBLEMA`. **El silencio se convierte en un estado
   nombrado**, no en un limbo.

6. El sistema publica **dos números** —lo que el emisor envió y lo que el receptor
   confirmó— y la `Brecha` entre ambos por canal, con la antigüedad del más viejo. Un solo
   número siempre se ve bien; la brecha es la que informa.

7. Se conserva la clave de `idempotencia` para que reintentar sea seguro, de modo que la
   Opción 2 pueda usarse encima de esta decisión sin duplicar liquidaciones.

---

## Consecuencias

### Positivas

- Existe una respuesta consultable a "¿el receptor lo tiene?", con autor y momento.
- El caso "el consumidor no existe" se vuelve **imposible de confundir con éxito**: las
  entregas se acumulan en `ENVIADO` y luego caen a `SIN_ACUSE` con antigüedad creciente.
- El síntoma se acerca a la causa: la brecha aparece en el plazo del canal, no cuando
  alguien reclama.
- Los reintentos dejan de ser ciegos: se reintenta lo que está en `SIN_ACUSE`, no todo.
- La distinción es didáctica. Un tablero con dos números explica el problema a alguien que
  no lo ha vivido.

### Negativas — y son reales

- **Exige cooperación del receptor.** Es el costo mayor. Si la contraparte no puede o no
  quiere implementar el acuse, el modelo no funciona: sólo mostrará una brecha permanente.
  Eso es más honesto que un `100 %` falso, pero no entrega la liquidación. En esos casos
  hay que negociar el acuse como parte del contrato de integración, o aceptar la Opción 4
  degradada y **documentar** que se verifica presencia, no procesamiento.
- **Añade un estado más** a la máquina. `ENVIADO` deja de ser terminal, y todo el código
  que consumía "entregado" debe revisarse. La migración desde un sistema que ya reporta
  entregas cambia el número hacia abajo — políticamente incómodo, aunque el número anterior
  fuera falso.
- **Obliga a definir un plazo por canal**, y ese plazo es una decisión de negocio, no
  técnica. Un plazo corto genera `SIN_ACUSE` falsos y ruido que erosiona la confianza en la
  alarma; uno largo retrasa la detección. No hay valor por omisión defendible: hay que
  medir el comportamiento real de cada canal y ajustarlo. Un plazo mal puesto convierte
  esta decisión en otra fuente de ruido.
- **Aparece un modo de fallo nuevo:** el acuse falso. Un receptor puede acusar y no
  procesar. El modelo lo acota (queda el autor registrado) pero no lo elimina; sólo mueve
  la responsabilidad a un lugar donde tiene nombre.
- **Más superficie:** hay que exponer y proteger el extremo de acuse, y la trazabilidad
  crece en almacenamiento.

### Neutras

- El canal síncrono queda con una brecha estructuralmente cercana a cero. Eso está bien: es
  la **línea base** contra la cual se leen los demás.
- El acuse por lote baja la confirmación a cada trabajo del lote. Menos tráfico, misma
  semántica.

---

## Criterios de reversión

Esta decisión se revisa —no antes— si se cumple alguno de los siguientes:

1. **Sin contraparte, de forma estructural.** Se demuestra que ningún receptor del
   ecosistema puede emitir acuse y que no es negociable en el contrato de integración. En
   ese escenario se degrada a la Opción 4 (sondeo del destino) **con la limitación escrita
   en el tablero**: "presencia verificada, procesamiento no verificado". Nunca se vuelve a
   la Opción 1.
2. **Ruido insostenible.** Si durante 90 días corridos más del 30 % de los `SIN_ACUSE`
   resultan falsos positivos por plazos mal calibrados, el problema es el plazo, no el
   modelo: primero se recalibra por canal con datos, y sólo si la calibración es imposible
   se revisa el mecanismo.
3. **Redundancia demostrada.** Si el transporte adoptado ofrece un acuse de extremo a
   extremo con autor y semántica de procesamiento —no de recepción física—, Acuse debe
   consumir ese acuse en lugar de mantener uno propio. Duplicar la señal es peor que no
   tenerla.

Explícitamente **no** son motivo de reversión: que la brecha se vea mal en un tablero, que
el número de entregas baje al migrar, ni que el receptor tarde en implementar su parte.
Esos son el resultado esperado de medir bien algo que antes se medía mal.
