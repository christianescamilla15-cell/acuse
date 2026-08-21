"""Escenario reproducible: las tres patologías, sembradas a propósito y visibles.

El objetivo de este módulo es que alguien abra el tablero **sin leer una línea de
código** y vea las tres formas del ÉXITO SILENCIOSO ocurriendo al mismo tiempo. Nada
aquí es aleatorio: mismas entradas, mismos números, siempre. Una demostración cuyo
resultado cambia entre corridas no sirve como evidencia, y la evidencia es el punto.

Qué demuestra cada grupo de :func:`sembrar` (30 trabajos en una corrida):

``10 trabajos → destino "A" → canal «sincrono-a»``
    El caso sano, y está aquí para servir de CONTRASTE. El receptor contesta en la
    misma llamada, así que hay un actor identificable que dijo «lo tengo»: quedan en
    ACUSADO. Son las únicas 10 entregas de verdad del escenario. Todo lo que en este
    grupo es gratis —saber si llegó, quién lo recibió y cuándo— es exactamente lo que
    en los otros dos hay que construir a mano.

``14 trabajos → destino "B" → canal «asincrono-b»``
    ⭐ **Patología 1: enviado ≠ recibido.** Los 14 depósitos son exitosos. El emisor
    puede afirmar, con toda razón desde su lado, que «los mandó». Nadie los consume,
    así que quedan en ENVIADO y ninguno tiene acuse. Ése es el número que un tablero
    ingenuo sumaría a «entregadas»: 24 en vez de 10. La brecha nace aquí y crece
    sola mientras nadie del otro lado lea.

``4 trabajos → destino "C" → canal «lote-c»`` (un solo lote)
    La misma patología, con la deuda de granularidad encima: los 4 viajan en UN solo
    objeto. Consumir ese lote produce 4 acuses de una vez, que es lo que permite
    responder «¿llegó el corte?» con un sí o un no en lugar de con un porcentaje.

``2 trabajos → destino "D" → SIN REGLA``
    ⭐ **Patología 2: ruteo por omisión.** No existe regla comodín, y es deliberado.
    En el defecto real estos 2 se habrían ido por el último canal configurado —o por
    el «de siempre»— sin error y sin aviso. Aquí quedan en RECHAZADO, con canal nulo
    y el motivo textual a la vista. Fallar ruidosamente es más barato que entregar
    al destinatario equivocado.

``1 corrida adicional sin trabajos``
    ⭐ **Patología 3: verde ≠ hizo algo.** Una corrida que no procesó nada termina en
    SIN_INSUMO, no en EXITOSA. Se siembra para que el tablero muestre desde el primer
    segundo que «terminó bien» y «hizo algo» son dos preguntas distintas.

Estado esperado tras :func:`sembrar` — 30 entregas:
ACUSADO 10 · ENVIADO 18 · RECHAZADO 2 · brecha de acuse 18.
"""
from __future__ import annotations

from typing import Any

from acuse.canales import Canal, limpiar_registro, registrar
from acuse.canales.asincrono import CanalAsincrono
from acuse.canales.lote import CanalLote
from acuse.canales.sincrono import CanalSincrono
from acuse.modelo import ReglaRuteo, Trabajo

__all__ = [
    "CANAL_SINCRONO",
    "CANAL_ASINCRONO",
    "CANAL_LOTE",
    "CLAVE_LOTE",
    "canales",
    "generar_trabajos",
    "reglas",
    "sembrar",
    "trabajos_sembrados",
]

# --------------------------------------------------------------------------------------
# Nombres. Son el único punto de contacto entre una regla de ruteo y un canal, así que
# viven en constantes: un canal que se llama distinto de lo que la regla nombra produce
# entregas FALLIDO por una errata, no por un problema real.
# --------------------------------------------------------------------------------------
CANAL_SINCRONO = "sincrono-a"
CANAL_ASINCRONO = "asincrono-b"
CANAL_LOTE = "lote-c"

# Llave con la que el lote viaja DENTRO de la carga. El canal por lote recibe
# `(entrega, carga)` y la entrega no lleva lote, así que si no va en la carga el canal
# tendría que inventárselo — y ésa es exactamente la patología 2 en miniatura.
CLAVE_LOTE = "lote"

# Plazos de acuse. Cortos a propósito para que la demo se pueda «vencer» en un clic,
# pero distintos entre sí: cada canal tiene su propia idea de cuánto es demasiado.
PLAZO_SINCRONO_S = 60
PLAZO_ASINCRONO_S = 900
PLAZO_LOTE_S = 3600

# Reparto del escenario sembrado. 10 + 14 + 4 + 2 = 30.
GRUPO_A = 10
GRUPO_B = 14
GRUPO_C = 4
GRUPO_D = 2
LOTE_SEMBRADO = "corte-C-00001"


# --------------------------------------------------------------------------------------
# Reglas de ruteo
# --------------------------------------------------------------------------------------
def reglas() -> list[ReglaRuteo]:
    """Las tres reglas del escenario. **No hay regla comodín, y es deliberado.**

    Un comodín parece prudencia y es lo contrario: convierte «no sé a dónde va esto»
    en «mándalo por acá», que es la patología 2 escrita en una línea de configuración.
    Sin comodín, un trabajo cuyo destino nadie contempló se detiene y se ve.
    """
    return [
        ReglaRuteo(
            nombre="destino-a-sincrono",
            canal=CANAL_SINCRONO,
            cuando={"destino": "A"},
            prioridad=10,
        ),
        ReglaRuteo(
            nombre="destino-b-asincrono",
            canal=CANAL_ASINCRONO,
            cuando={"destino": "B"},
            prioridad=20,
        ),
        ReglaRuteo(
            nombre="destino-c-lote",
            canal=CANAL_LOTE,
            cuando={"destino": "C"},
            prioridad=30,
        ),
    ]


# --------------------------------------------------------------------------------------
# Canales
# --------------------------------------------------------------------------------------
def canales() -> dict[str, Canal]:
    """Instancia los tres canales del escenario, indexados por nombre.

    Cada uno pasa por :func:`acuse.canales.registrar`, que valida el contrato mínimo
    (nombre, modo y plazo de acuse) **antes** de que alguien dependa de él. Un canal
    sin plazo declarado no podría distinguir «va en camino» de «se perdió», y ése es
    justo el diagnóstico que el conciliador tiene que poder emitir.

    El registro se limpia primero para que reconstruir el escenario (por ejemplo al
    reiniciar la demo) no choque con los canales de la vuelta anterior.
    """
    limpiar_registro()
    instancias = [
        # Receptor sano y sin rechazos: este grupo debe acusarse solo, para que el
        # contraste con los otros dos canales sea inequívoco.
        CanalSincrono(
            nombre=CANAL_SINCRONO,
            receptor_disponible=True,
            tasa_rechazo=0.0,
            plazo_acuse_s=PLAZO_SINCRONO_S,
        ),
        CanalAsincrono(
            nombre=CANAL_ASINCRONO,
            plazo_acuse_s=PLAZO_ASINCRONO_S,
            prefijo="objetos/destino-b",
        ),
        CanalLote(
            nombre=CANAL_LOTE,
            plazo_acuse_s=PLAZO_LOTE_S,
            prefijo="lotes/destino-c",
            clave_lote=CLAVE_LOTE,
        ),
    ]
    return {instancia.nombre: registrar(instancia) for instancia in instancias}


# --------------------------------------------------------------------------------------
# Trabajos
# --------------------------------------------------------------------------------------
def _trabajo(numero: int, destino: str, lote: str | None = None) -> Trabajo:
    """Un trabajo con folio estable. Mismo número ⇒ misma referencia, siempre."""
    referencia = f"LIQ-{numero:05d}"
    carga = {
        "folio": referencia,
        "concepto": "liquidación",
        # Importe estable derivado del folio: reproducible, sin azar.
        "importe": 1000 + numero * 7,
        "moneda": "MXN",
    }
    if lote is not None:
        # El lote va en DOS lugares y ninguno sobra: en el trabajo, porque es un dato
        # de negocio que la entrega hereda y el tablero muestra; y en la carga, porque
        # es la carga —no la entrega— lo que recibe el canal por lote. Si sólo viajara
        # en el trabajo, el canal se quedaría sin saber a qué corte pertenece esto.
        carga[CLAVE_LOTE] = lote

    return Trabajo(
        id=f"TRB-{numero:05d}",
        referencia=referencia,
        # El destino es un atributo EXPLÍCITO del trabajo, no un campo opcional cuya
        # ausencia signifique algo. Si falta, no hay ruta y se nota.
        atributos={"destino": destino},
        carga=carga,
        lote=lote,
    )


def generar_trabajos(
    n: int,
    *,
    desde: int = 1,
    incluir_sin_ruta: bool = False,
    lote: str | None = None,
) -> list[Trabajo]:
    """Genera ``n`` trabajos repartidos cíclicamente entre los destinos.

    Args:
        n: cuántos generar. ``0`` devuelve la lista vacía — que es un insumo legítimo
            y la forma de provocar una corrida SIN_INSUMO.
        desde: primer folio. Quien llama lo va avanzando para no repetir referencias.
        incluir_sin_ruta: agrega el destino ``"D"`` al ciclo. No hay regla que lo cubra,
            así que uno de cada cuatro trabajos terminará en RECHAZADO — visible, con
            motivo, y sin haberse ido por un canal que nadie eligió.
        lote: lote a usar para los trabajos del canal por lote. Por omisión se deriva
            de ``desde``, de modo que cada corrida abre su propio corte en vez de
            anexar a uno que el receptor quizá ya se llevó.
    """
    if n < 0:
        raise ValueError("n no puede ser negativo")

    destinos = ["A", "B", "C", "D"] if incluir_sin_ruta else ["A", "B", "C"]
    lote_del_corte = lote or f"corte-C-{desde:05d}"

    trabajos: list[Trabajo] = []
    for indice in range(n):
        destino = destinos[indice % len(destinos)]
        trabajos.append(
            _trabajo(
                desde + indice,
                destino,
                lote=lote_del_corte if destino == "C" else None,
            )
        )
    return trabajos


# --------------------------------------------------------------------------------------
# Siembra
# --------------------------------------------------------------------------------------
def trabajos_sembrados() -> list[Trabajo]:
    """Los 30 trabajos del escenario, en orden y con folios fijos."""
    trabajos: list[Trabajo] = []
    numero = 1

    for _ in range(GRUPO_A):
        trabajos.append(_trabajo(numero, "A"))
        numero += 1
    for _ in range(GRUPO_B):
        trabajos.append(_trabajo(numero, "B"))
        numero += 1
    for _ in range(GRUPO_C):
        # Un solo lote para los cuatro: un corte, un objeto, un acuse.
        trabajos.append(_trabajo(numero, "C", lote=LOTE_SEMBRADO))
        numero += 1
    for _ in range(GRUPO_D):
        # Destino sin regla. Nadie lo enruta y nadie lo esconde.
        trabajos.append(_trabajo(numero, "D"))
        numero += 1

    return trabajos


def sembrar(orquestador: Any) -> dict:
    """Deja la base en el estado inicial de la demostración.

    Ejecuta DOS corridas, y las dos importan:

      1. Los 30 trabajos. Termina CON_PROBLEMAS por los 2 sin ruta; los otros 28 se
         procesan, pero sólo 10 quedan ACUSADO. Los 18 restantes son la brecha.
      2. Una corrida sin insumo. Termina SIN_INSUMO — nunca EXITOSA — para que el
         tablero muestre desde el arranque que un proceso puede terminar bien y no
         haber hecho absolutamente nada.

    Returns:
        Un resumen de la siembra, incluido ``siguiente_folio`` para que quien siga
        generando trabajos no repita referencias ya usadas.
    """
    trabajos = trabajos_sembrados()
    corrida = orquestador.procesar(trabajos)
    corrida_vacia = orquestador.procesar([])

    return {
        "trabajos": len(trabajos),
        "corrida_id": corrida.id,
        "corrida_estado": corrida.estado.value,
        "corrida_vacia_id": corrida_vacia.id,
        "corrida_vacia_estado": corrida_vacia.estado.value,
        "siguiente_folio": len(trabajos) + 1,
        "lote_sembrado": LOTE_SEMBRADO,
    }
