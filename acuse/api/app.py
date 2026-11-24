"""Acuse API — la capa HTTP que hace visible lo que el núcleo ya sabe.

Un tablero puede mentir de tres formas, y esta API está diseñada para que ninguna
quepa:

  1. **enviado ≠ recibido.** ``/api/resumen`` publica los DOS números juntos:
     ``entregado_segun_emisor`` y ``entregado_confirmado``. Publicar sólo el primero
     es la forma más común del fallo; publicarlos juntos convierte la diferencia en
     una cifra que alguien tiene que explicar.
  2. **ruteo por omisión.** Las entregas sin ruta salen en ``/api/entregas`` con
     ``canal: null``, estado RECHAZADO y el motivo textual. No se filtran ni se
     agrupan en un «otros».
  3. **verde ≠ hizo algo.** ``/api/corridas`` devuelve el estado real de cada corrida,
     incluido SIN_INSUMO, y un ``hizo_algo`` explícito. Un tablero que sólo sabe decir
     verde/rojo no puede distinguir «procesé todo» de «no había nada», y por eso nadie
     hace esa pregunta.

Los endpoints ``/api/simular/*`` existen para poder provocar cada patología en vivo,
sin esperar a que ocurra sola.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from acuse import __version__, conciliador
from acuse.canales.lote import LoteDesconocido
from acuse.cifras import cifras_de_lote
from acuse.demo import CANAL_ASINCRONO, CANAL_LOTE, canales, generar_trabajos, reglas, sembrar
from acuse.modelo import Brecha, EstadoEntrega, ahora
from acuse.orquestador import Orquestador
from acuse.persistencia import (
    Base,
    CorridaORM,
    EntregaORM,
    EventoORM,
    crear_engine,
    crear_session_factory,
    crear_tablas,
)
from acuse.ruteo import Enrutador

TESIS = "Una entrega sin acuse verificable no es una entrega."

# Base por omisión en memoria: la demo arranca siempre desde el mismo escenario y no
# deja residuos. `ACUSE_BD` permite apuntarla a un archivo para conservar el histórico.
URL_BD = os.environ.get("ACUSE_BD", "sqlite+pysqlite:///:memory:")

# Origen del tablero en desarrollo.
ORIGENES = ["http://localhost:5173", "http://127.0.0.1:5173"]


# ======================================================================================
# Contexto de la aplicación
# ======================================================================================
class Contexto:
    """Piezas vivas del proceso: base, ruteo, canales y orquestador.

    Los canales guardan estado en memoria (el buzón del receptor), así que reiniciar
    la demo tiene que reconstruirlos junto con la base. Reiniciar sólo una de las dos
    dejaría el tablero contando entregas que ningún buzón respalda.
    """

    def __init__(self) -> None:
        self.engine: Any = None
        self.session_factory: Any = None
        self.canales: dict[str, Any] = {}
        self.orquestador: Orquestador | None = None
        self.siguiente_folio: int = 1
        self.siembra: dict = {}

    @property
    def listo(self) -> bool:
        return self.orquestador is not None


_CONTEXTO = Contexto()


def _bd_vacia(session_factory: Any) -> bool:
    """¿No hay ni una entrega ni una corrida? Entonces no hay escenario que mostrar."""
    with session_factory() as ses:
        entregas = ses.scalar(select(func.count(EntregaORM.id))) or 0
        corridas = ses.scalar(select(func.count(CorridaORM.id))) or 0
    return entregas == 0 and corridas == 0


def _construir(*, vaciar: bool = False) -> Contexto:
    """Levanta base, canales y orquestador. Siembra si la base está vacía."""
    engine = crear_engine(URL_BD)
    if vaciar:
        Base.metadata.drop_all(engine)
    crear_tablas(engine)

    session_factory = crear_session_factory(engine)
    instancias = canales()

    _CONTEXTO.engine = engine
    _CONTEXTO.session_factory = session_factory
    _CONTEXTO.canales = instancias
    _CONTEXTO.orquestador = Orquestador(
        session_factory=session_factory,
        enrutador=Enrutador(reglas()),
        canales=instancias,
    )
    _CONTEXTO.siguiente_folio = 1
    _CONTEXTO.siembra = {}

    if _bd_vacia(session_factory):
        _CONTEXTO.siembra = sembrar(_CONTEXTO.orquestador)
        _CONTEXTO.siguiente_folio = int(_CONTEXTO.siembra.get("siguiente_folio", 1))
    else:
        # Base con historia previa: se continúa la numeración en vez de reusar folios,
        # porque un folio repetido colisionaría con la clave de idempotencia y el
        # trabajo nuevo se descartaría como duplicado sin que nadie lo notara.
        with session_factory() as ses:
            existentes = ses.scalar(select(func.count(EntregaORM.id))) or 0
        _CONTEXTO.siguiente_folio = int(existentes) + 1

    return _CONTEXTO


def contexto() -> Contexto:
    """Contexto ya inicializado. Lo construye en el primer uso si hiciera falta."""
    if not _CONTEXTO.listo:
        _construir()
    return _CONTEXTO


@asynccontextmanager
async def _ciclo_de_vida(app: FastAPI):
    contexto()
    yield


app = FastAPI(
    title="Acuse API",
    version=__version__,
    summary=TESIS,
    description=__doc__,
    lifespan=_ciclo_de_vida,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGENES,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ======================================================================================
# Esquemas de salida
# ======================================================================================
class ErrorSalida(BaseModel):
    """Error con motivo legible: un código sin explicación obliga a adivinar."""

    detail: str = Field(description="Qué pasó y qué hacer.")


class Salud(BaseModel):
    servicio: Literal["Acuse API"] = "Acuse API"
    estado: Literal["ok"] = "ok"
    version: str
    tesis: str = TESIS


class BrechaSalida(BaseModel):
    """Distancia entre «lo mandé» y «lo tienen», por canal."""

    canal: str
    enviados: int = Field(description="Salió por el canal y todavía no tiene acuse.")
    acusados: int = Field(description="El receptor confirmó. Sólo esto son entregas.")
    sin_acuse: int = Field(description="De los enviados, los que ya excedieron su plazo.")
    mas_antiguo_s: int = Field(description="Antigüedad del envío sin acuse más viejo.")
    hay_brecha: bool


class ResumenSalida(BaseModel):
    """Fotografía global. Los dos números viajan JUNTOS a propósito."""

    total: int
    por_estado: dict[str, int]
    entregadas: int
    entregado_segun_emisor: int = Field(
        description="ENVIADO + ACUSADO. Lo que reportaría quien cuenta lo que salió."
    )
    entregado_confirmado: int = Field(
        description="ACUSADO. Lo único que alguien del otro lado confirmó."
    )
    brecha_de_acuse: int
    sin_acuse: int
    con_problema: int
    nota: str
    brechas: list[BrechaSalida]


class EntregaSalida(BaseModel):
    id: str
    trabajo_id: str
    referencia: str
    canal: str | None = Field(description="Nulo cuando no hubo regla que lo enrutara.")
    estado: str
    entregado: bool = Field(description="Verdadero SÓLO si está ACUSADO.")
    intentos: int
    plazo_acuse_s: int
    enviado_en: datetime | None
    acusado_en: datetime | None
    acuse_por: str | None = Field(description="Quién confirmó. Sin actor no hay acuse.")
    detalle: str
    lote: str | None
    creado_en: datetime


class CorridaSalida(BaseModel):
    id: str
    iniciada_en: datetime
    terminada_en: datetime | None
    tomados: int
    enrutados: int
    enviados: int
    acusados: int
    rechazados: int
    fallidos: int
    procesados: int = Field(description="enviados + acusados.")
    estado: str
    hizo_algo: bool = Field(
        description="Falso cuando la corrida terminó sin procesar nada: verde ≠ hizo algo."
    )
    detalle: str


class EventoSalida(BaseModel):
    id: int
    entrega_id: str | None
    momento: datetime
    tipo: str
    actor: str | None
    detalle: str


class RespuestaConsumo(BaseModel):
    canal: str
    referencias: list[str]
    consumidas: int = Field(description="Objetos o lotes que el receptor leyó.")
    acuses_registrados: int = Field(
        description="Entregas que pasaron de ENVIADO a ACUSADO gracias a esta lectura."
    )
    pendientes_restantes: int | None
    actor: str
    nota: str


class FilaCifrasSalida(BaseModel):
    referencia: str = Field(description="Llave estable de la fila (folio de negocio).")
    importe: str = Field(description="Decimal a centavos, como texto (nunca float).")
    moneda: str
    hash: str = Field(description="SHA-256 del JSON canónico [referencia, importe, moneda].")


class CifrasSalida(BaseModel):
    """Cifras control del objeto depositado: lo que el receptor debe poder reproducir."""

    lote: str
    moneda: str | None
    conteo: int
    suma: str = Field(description="Suma de los importes ya redondeados, como texto.")
    hash_lote: str = Field(description="SHA-256 de los hashes de fila ordenados por referencia.")
    consumido: bool = Field(description="Si el receptor ya leyó el lote.")
    filas: list[FilaCifrasSalida]


class RespuestaVencimiento(BaseModel):
    avance_s: int
    marcadas: int
    nota: str
    brechas: list[BrechaSalida]


class RespuestaReinicio(BaseModel):
    reiniciado: bool = True
    siembra: dict
    resumen: ResumenSalida


# ======================================================================================
# Esquemas de entrada
# ======================================================================================
class PeticionCorrida(BaseModel):
    n: int = Field(default=6, ge=0, le=500, description="Cuántos trabajos generar.")
    incluir_sin_ruta: bool = Field(
        default=False,
        description=(
            "Agrega trabajos con un destino que ninguna regla cubre. Terminan en "
            "RECHAZADO, que es lo que debe pasar: no hay canal por omisión."
        ),
    )


class PeticionConsumo(BaseModel):
    canal: str = Field(
        default=CANAL_ASINCRONO,
        description=(
            "Canal cuyo receptor va a leer. Por omisión el asíncrono, que es donde "
            "nace la brecha y por tanto lo que casi siempre se quiere consumir."
        ),
    )
    n: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Cuántos objetos leer (o cuántos lotes, si el canal agrupa). "
            "Nulo = todo lo pendiente."
        ),
    )


class PeticionVencimiento(BaseModel):
    avance_s: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Segundos que avanza el reloj lógico. Por omisión, lo justo para que "
            "venza el plazo más largo que haya en vuelo."
        ),
    )


# ======================================================================================
# Conversiones
# ======================================================================================
def _brecha_salida(brecha: Brecha) -> BrechaSalida:
    return BrechaSalida(
        canal=brecha.canal,
        enviados=brecha.enviados,
        acusados=brecha.acusados,
        sin_acuse=brecha.sin_acuse,
        mas_antiguo_s=brecha.mas_antiguo_s,
        hay_brecha=brecha.hay_brecha,
    )


def _entrega_salida(fila: EntregaORM) -> EntregaSalida:
    return EntregaSalida(
        id=fila.id,
        trabajo_id=fila.trabajo_id,
        referencia=fila.referencia,
        canal=fila.canal,
        estado=fila.estado.value,
        entregado=fila.entregado,
        intentos=fila.intentos,
        plazo_acuse_s=fila.plazo_acuse_s,
        enviado_en=fila.enviado_en,
        acusado_en=fila.acusado_en,
        acuse_por=fila.acuse_por,
        detalle=fila.detalle,
        lote=fila.lote,
        creado_en=fila.creado_en,
    )


def _corrida_salida(fila: CorridaORM) -> CorridaSalida:
    procesados = fila.enviados + fila.acusados
    return CorridaSalida(
        id=fila.id,
        iniciada_en=fila.iniciada_en,
        terminada_en=fila.terminada_en,
        tomados=fila.tomados,
        enrutados=fila.enrutados,
        enviados=fila.enviados,
        acusados=fila.acusados,
        rechazados=fila.rechazados,
        fallidos=fila.fallidos,
        procesados=procesados,
        estado=fila.estado.value,
        hizo_algo=procesados > 0,
        detalle=fila.detalle,
    )


def _evento_salida(fila: EventoORM) -> EventoSalida:
    return EventoSalida(
        id=fila.id,
        entrega_id=fila.entrega_id,
        momento=fila.momento,
        tipo=fila.tipo,
        actor=fila.actor,
        detalle=fila.detalle,
    )


def _resumen_salida(ctx: Contexto) -> ResumenSalida:
    with ctx.session_factory() as ses:
        datos = conciliador.resumen(ses)
        lista = conciliador.brechas(ses)
    return ResumenSalida(**datos, brechas=[_brecha_salida(b) for b in lista])


# ======================================================================================
# Endpoints
# ======================================================================================
@app.get("/health", response_model=Salud, tags=["operación"])
def salud() -> Salud:
    """Vivo o no. Deliberadamente NO afirma que el sistema esté sano.

    Un `/health` en verde dice que el proceso responde, nada más. Si además dijera
    «todo bien», sería otro éxito silencioso: la brecha de acuse se mira en
    `/api/resumen`, no aquí.
    """
    return Salud(version=__version__)


@app.get("/api/resumen", response_model=ResumenSalida, tags=["tablero"])
def obtener_resumen() -> ResumenSalida:
    """Conteo global por estado + la brecha de acuse por canal."""
    return _resumen_salida(contexto())


@app.get("/api/brechas", response_model=list[BrechaSalida], tags=["tablero"])
def obtener_brechas() -> list[BrechaSalida]:
    """Brecha de acuse por canal, como recurso propio.

    Existe además de /api/resumen porque el tablero la refresca por separado tras
    cada simulacion: es el numero que se mueve cuando el receptor consume.
    """
    ctx = contexto()
    with ctx.session_factory() as ses:
        return [_brecha_salida(b) for b in conciliador.brechas(ses)]


@app.get(
    "/api/lotes/{lote}/cifras",
    response_model=CifrasSalida,
    tags=["tablero"],
    responses={404: {"model": ErrorSalida, "description": "No hay objeto depositado para ese lote."}},
)
def obtener_cifras_de_lote(lote: str) -> CifrasSalida:
    """Cifras control (conteo, suma en Decimal y hash por fila) del lote depositado.

    Se calculan sobre lo que el receptor leería del objeto del lote, no sobre lo que el
    emisor cree haber mandado: son las que el emisor tiene que poder igualar con las que
    declaró. La conciliación completa vive en `acuse.conciliador.conciliar_lote`.
    """
    ctx = contexto()
    canal = ctx.canales.get(CANAL_LOTE)
    try:
        if canal is None:
            raise LoteDesconocido(lote, [])
        registros = canal.registros_del_lote(lote)
    except LoteDesconocido as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from None
    cifras = cifras_de_lote(lote, registros)
    return CifrasSalida(
        **{k: v for k, v in cifras.como_dict().items() if k != "filas"},
        consumido=bool(canal.lotes_depositados[lote]["consumido"]),
        filas=[FilaCifrasSalida(**f) for f in cifras.como_dict()["filas"]],
    )


@app.get("/api/entregas", response_model=list[EntregaSalida], tags=["tablero"])
def listar_entregas(
    estado: EstadoEntrega | None = Query(default=None, description="Filtro por estado."),
    canal: str | None = Query(default=None, description="Filtro por canal."),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[EntregaSalida]:
    """Entregas, de la más reciente a la más vieja.

    Las RECHAZADO aparecen con ``canal: null``. No se ocultan ni se reagrupan: una
    entrega que nadie supo enrutar es exactamente la que hay que ver.
    """
    ctx = contexto()
    consulta = select(EntregaORM)
    if estado is not None:
        consulta = consulta.where(EntregaORM.estado == estado)
    if canal is not None:
        consulta = consulta.where(EntregaORM.canal == canal)
    consulta = consulta.order_by(
        EntregaORM.creado_en.desc(), EntregaORM.referencia.desc()
    ).limit(limit)

    with ctx.session_factory() as ses:
        filas = ses.scalars(consulta).all()
    return [_entrega_salida(f) for f in filas]


@app.get("/api/corridas", response_model=list[CorridaSalida], tags=["tablero"])
def listar_corridas(limit: int = Query(default=20, ge=1, le=200)) -> list[CorridaSalida]:
    """Últimas corridas con su desenlace real, SIN_INSUMO incluido.

    Es la vista que impide leer «terminó bien» como «hizo algo».
    """
    ctx = contexto()
    consulta = (
        select(CorridaORM).order_by(CorridaORM.iniciada_en.desc(), CorridaORM.id.desc()).limit(limit)
    )
    with ctx.session_factory() as ses:
        filas = ses.scalars(consulta).all()
    return [_corrida_salida(f) for f in filas]


@app.get("/api/eventos", response_model=list[EventoSalida], tags=["tablero"])
def listar_eventos(
    entrega_id: str | None = Query(default=None, description="Bitácora de una entrega."),
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[EventoSalida]:
    """Bitácora append-only: qué pasó, cuándo y quién lo hizo.

    Incluye los hechos que no corresponden a ninguna entrega conocida —un acuse que
    cita una referencia que nadie envió, por ejemplo—. Descartarlos en silencio sería
    otro éxito silencioso.
    """
    ctx = contexto()
    consulta = select(EventoORM)
    if entrega_id is not None:
        consulta = consulta.where(EventoORM.entrega_id == entrega_id)
    consulta = consulta.order_by(EventoORM.id.desc()).limit(limit)

    with ctx.session_factory() as ses:
        filas = ses.scalars(consulta).all()
    return [_evento_salida(f) for f in filas]


@app.post("/api/simular/corrida", response_model=CorridaSalida, tags=["simulación"])
def simular_corrida(datos: PeticionCorrida = Body(default=PeticionCorrida())) -> CorridaSalida:
    """Genera ``n`` trabajos y los procesa.

    Los que van al canal síncrono se acusan solos; los asíncronos quedan ENVIADO y
    engordan la brecha hasta que alguien los consuma. Con ``incluir_sin_ruta`` se
    añaden trabajos que ninguna regla cubre, para ver el rechazo ruidoso.
    """
    ctx = contexto()
    trabajos = generar_trabajos(
        datos.n,
        desde=ctx.siguiente_folio,
        incluir_sin_ruta=datos.incluir_sin_ruta,
    )
    ctx.siguiente_folio += len(trabajos)
    corrida = ctx.orquestador.procesar(trabajos)
    return _corrida_salida(corrida)


@app.post("/api/simular/corrida-vacia", response_model=CorridaSalida, tags=["simulación"])
def simular_corrida_vacia() -> CorridaSalida:
    """Procesa una lista vacía. Termina SIN_INSUMO, jamás EXITOSA.

    Es la patología 3 en un clic: el proceso corrió, no falló nada, y no hizo nada.
    Un tablero binario lo pintaría de verde y el frente se caería durante días.
    """
    ctx = contexto()
    return _corrida_salida(ctx.orquestador.procesar([]))


@app.post(
    "/api/simular/consumir",
    response_model=RespuestaConsumo,
    tags=["simulación"],
    responses={
        404: {"model": ErrorSalida, "description": "No hay canal con ese nombre."},
        409: {"model": ErrorSalida, "description": "El canal es síncrono: no tiene buzón que consumir."},
    },
)
def simular_consumo(datos: PeticionConsumo) -> RespuestaConsumo:
    """El receptor asíncrono lee su buzón y de ahí —y sólo de ahí— nace el acuse.

    Devuelve cuántas entregas pasaron de ENVIADO a ACUSADO. Ese número es la única
    forma legítima de que la brecha baje: marcarla como resuelta sin que nadie lea
    sería fabricar la evidencia que este proyecto existe para exigir.
    """
    ctx = contexto()
    canal = ctx.canales.get(datos.canal)
    if canal is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No hay canal «{datos.canal}». Registrados: "
                f"{', '.join(sorted(ctx.canales)) or 'ninguno'}."
            ),
        )

    if hasattr(canal, "consumir"):
        # Canal asíncrono simple: un objeto por entrega, FIFO.
        referencias = list(canal.consumir(datos.n))
        unidad = "objeto(s)"
        consumidas = len(referencias)
    elif hasattr(canal, "consumir_lote"):
        # Canal por lote: la unidad que el receptor lee es el corte completo.
        pendientes = sorted(
            lote for lote, objeto in canal.lotes_depositados.items() if not objeto["consumido"]
        )
        if datos.n is not None:
            pendientes = pendientes[: datos.n]
        referencias = []
        for lote in pendientes:
            referencias.extend(canal.consumir_lote(lote))
        unidad = "lote(s)"
        consumidas = len(pendientes)
    else:
        raise HTTPException(
            status_code=409,
            detail=(
                f"El canal «{datos.canal}» es síncrono: su acuse llega en la misma "
                "llamada del envío y no hay buzón que consumir."
            ),
        )

    actor = f"receptor:{datos.canal}"
    acuses = ctx.orquestador.registrar_acuse(referencias, actor=actor)
    pendientes_restantes = canal.pendientes() if hasattr(canal, "pendientes") else None

    if not referencias:
        nota = (
            f"No había nada pendiente en «{datos.canal}». La brecha no baja porque "
            "nadie leyó nada: bajarla sin lectura sería inventar el acuse."
        )
    else:
        nota = (
            f"El receptor leyó {consumidas} {unidad} de «{datos.canal}» y se "
            f"registraron {acuses} acuse(s) con actor «{actor}». Sólo ahora esas "
            "entregas son entregas."
        )
        if acuses < len(referencias):
            nota += (
                f" Ojo: {len(referencias) - acuses} referencia(s) no pasaron a ACUSADO "
                "(ya vencidas, ya acusadas o desconocidas). La bitácora dice cuál fue "
                "el caso de cada una."
            )

    return RespuestaConsumo(
        canal=datos.canal,
        referencias=referencias,
        consumidas=consumidas,
        acuses_registrados=acuses,
        pendientes_restantes=pendientes_restantes,
        actor=actor,
        nota=nota,
    )


@app.post("/api/simular/vencer", response_model=RespuestaVencimiento, tags=["simulación"])
def simular_vencimiento(
    datos: PeticionVencimiento | None = Body(default=None),
) -> RespuestaVencimiento:
    """Adelanta el reloj lógico y marca SIN_ACUSE lo que ya venció.

    No espera a que pase el tiempo real: mueve el instante con el que se evalúa el
    plazo. Las entregas dejan de estar «en camino» y pasan a ser un incumplimiento
    con nombre, canal y antigüedad.

    Marcarlas NO reduce la brecha —siguen contando— porque mirar un problema no lo
    resuelve. Sólo un acuse lo hace.
    """
    ctx = contexto()
    avance = datos.avance_s if datos and datos.avance_s else None

    with ctx.session_factory() as ses:
        if avance is None:
            plazo_maximo = ses.scalar(
                select(func.max(EntregaORM.plazo_acuse_s)).where(
                    EntregaORM.estado == EstadoEntrega.ENVIADO
                )
            )
            avance = int(plazo_maximo or 0) + 1
        momento = ahora() + timedelta(seconds=avance)
        marcadas = conciliador.marcar_vencidas(ses, momento)
        lista = conciliador.brechas(ses, momento)

    return RespuestaVencimiento(
        avance_s=avance,
        marcadas=marcadas,
        nota=(
            f"Reloj adelantado {avance}s: {marcadas} entrega(s) pasaron de ENVIADO a "
            "SIN_ACUSE. La brecha no baja por esto —sigue sin haber acuse—; lo único "
            "que cambia es que ahora el incumplimiento tiene nombre."
        ),
        brechas=[_brecha_salida(b) for b in lista],
    )


@app.post("/api/reiniciar", response_model=RespuestaReinicio, tags=["operación"])
def reiniciar() -> RespuestaReinicio:
    """Vuelve al escenario sembrado: base limpia y buzones vacíos."""
    ctx = _construir(vaciar=True)
    return RespuestaReinicio(siembra=ctx.siembra, resumen=_resumen_salida(ctx))


# ======================================================================================
# Sinónimos de ruta
#
# El tablero se escribió contra estos nombres. Se exponen como sinónimos en lugar de
# renombrar los canónicos —o de dejar que el botón responda 404— porque un botón que
# falla es visible, pero uno que falla y el tablero pinta igual sería, otra vez, la
# misma patología: parece que pasó algo y no pasó nada.
# ======================================================================================
@app.post(
    "/api/simular/lote",
    response_model=CorridaSalida,
    tags=["simulación"],
    include_in_schema=False,
)
def simular_lote(datos: PeticionCorrida = Body(default=PeticionCorrida())) -> CorridaSalida:
    """Sinónimo de `/api/simular/corrida`."""
    return simular_corrida(datos)


@app.post(
    "/api/simular/reiniciar",
    response_model=RespuestaReinicio,
    tags=["operación"],
    include_in_schema=False,
)
def reiniciar_sinonimo() -> RespuestaReinicio:
    """Sinónimo de `/api/reiniciar`."""
    return reiniciar()


# ── Tablero estático ──────────────────────────────────────────────────────────
# El tablero vive en /web y se sirve desde la misma app: una sola cosa que
# levantar, y el navegador consume /api del mismo origen (sin CORS que configurar).
from pathlib import Path as _Path  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

_WEB = _Path(__file__).resolve().parent.parent.parent / "web"
if _WEB.is_dir():
    app.mount("/", StaticFiles(directory=str(_WEB), html=True), name="tablero")
