"""Persistencia de Acuse: entregas, corridas y bitácora.

El esquema está diseñado para que las tres formas del ÉXITO SILENCIOSO no quepan:

  1. `entregas.estado` distingue ENVIADO de ACUSADO. No hay un booleano `entregado`
     que el emisor pueda poner en `True` por su cuenta: entregado se DERIVA del estado
     y sólo ACUSADO cuenta.
  2. `entregas.canal` es NULL cuando no se pudo enrutar. Una entrega sin canal existe,
     se ve y trae el motivo en `detalle`; no desaparece ni cae en un canal por omisión.
  3. `corridas.estado` incluye SIN_INSUMO. Ver `EstadoCorrida`.

Todo instante se guarda en UTC y se lee con zona horaria (ver `FechaUTC`).
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    create_engine,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from acuse.modelo import Entrega as EntregaDominio
from acuse.modelo import EstadoEntrega, ahora

__all__ = [
    "Base",
    "EstadoCorrida",
    "EntregaORM",
    "CorridaORM",
    "EventoORM",
    "FechaUTC",
    "crear_engine",
    "crear_session_factory",
    "crear_tablas",
    "SessionLocal",
]


class Base(DeclarativeBase):
    """Base propia: este esquema no se mezcla con el de nadie más."""


class EstadoCorrida(str, enum.Enum):
    """Desenlace de una corrida del orquestador.

    ⭐ SIN_INSUMO existe porque «corrió y no procesó nada» NO es éxito.

    El patrón que corrige es concreto: un proceso programado que no encuentra insumo
    termina en verde, la alarma nunca se dispara y el frente se descubre días después,
    cuando alguien pregunta por qué no llegó nada. Verde ≠ hizo algo.

    Por eso SIN_INSUMO es un estado PROPIO y VISIBLE, no un caso particular de EXITOSA:
    quien mire el tablero tiene que poder distinguir «procesé todo lo que había» de
    «no había nada que procesar», y decidir si lo segundo es normal o es una falla de
    quien debía producir el insumo. Un tablero que sólo sabe decir verde/rojo no puede
    hacer esa pregunta, y por eso nadie la hace.
    """

    EXITOSA = "EXITOSA"              # hubo insumo, se procesó y no hubo incidencias
    SIN_INSUMO = "SIN_INSUMO"        # corrió y no procesó nada: NO es éxito
    CON_PROBLEMAS = "CON_PROBLEMAS"  # se procesó, pero hubo rechazados o fallidos
    FALLIDA = "FALLIDA"              # la corrida misma se rompió


class FechaUTC(TypeDecorator):
    """DateTime que SIEMPRE regresa con zona horaria.

    Trampa real: SQLite (y varios motores) no guardan el offset. Al releer se obtiene
    un datetime «ingenuo» y compararlo con `ahora()` —que sí trae zona— revienta con
    TypeError. Eso rompería justo el cálculo de vencimiento, que es el corazón del
    conciliador. Normalizamos a UTC al escribir y reponemos la zona al leer.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class EntregaORM(Base):
    """Fila de `entregas`. Espejo persistente de `acuse.modelo.Entrega`."""

    __tablename__ = "entregas"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trabajo_id: Mapped[str] = mapped_column(String(64), nullable=False)
    referencia: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    # NULL = no se pudo enrutar. La entrega igual existe y dice por qué en `detalle`.
    canal: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    estado: Mapped[EstadoEntrega] = mapped_column(
        SAEnum(EstadoEntrega, native_enum=False, length=16, validate_strings=True),
        nullable=False,
        default=EstadoEntrega.PENDIENTE,
        index=True,
    )
    intentos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plazo_acuse_s: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    enviado_en: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)
    acusado_en: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)
    # QUIÉN confirmó. Sin actor no hay acuse; por eso se guarda junto al instante.
    acuse_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detalle: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Misma clave = misma entrega. El índice único es el que impide el duplicado
    # aunque la lógica de aplicación se equivoque.
    idempotencia: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    lote: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC, nullable=False, default=ahora)

    def a_dominio(self) -> EntregaDominio:
        """Convierte a la dataclass del dominio, dueña de las reglas."""
        return EntregaDominio(
            id=self.id,
            trabajo_id=self.trabajo_id,
            referencia=self.referencia,
            canal=self.canal,
            estado=self.estado,
            intentos=self.intentos,
            plazo_acuse_s=self.plazo_acuse_s,
            enviado_en=self.enviado_en,
            acusado_en=self.acusado_en,
            acuse_por=self.acuse_por,
            detalle=self.detalle,
            idempotencia=self.idempotencia,
        )

    @property
    def entregado(self) -> bool:
        """Delega en el dominio para que la regla viva en UN solo lugar."""
        return self.a_dominio().entregado

    def vencida(self, momento: datetime | None = None) -> bool:
        return self.a_dominio().vencida(momento)

    def __repr__(self) -> str:  # pragma: no cover - ayuda al depurar
        return (
            f"<EntregaORM {self.referencia} canal={self.canal} "
            f"estado={self.estado.value} entregado={self.entregado}>"
        )


class CorridaORM(Base):
    """Fila de `corridas`. Una ejecución del orquestador, con su desenlace.

    `enviados` y `acusados` se cuentan por SEPARADO a propósito: sumarlos en un solo
    contador «entregados» es exactamente el error que este proyecto corrige.
    """

    __tablename__ = "corridas"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    iniciada_en: Mapped[datetime] = mapped_column(FechaUTC, nullable=False, default=ahora)
    terminada_en: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)
    tomados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enrutados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Puestas en el canal y AÚN SIN ACUSE. No incluye las acusadas.
    enviados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Confirmadas por el receptor. Éstas —y sólo éstas— son entregas.
    acusados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rechazados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallidos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estado: Mapped[EstadoCorrida] = mapped_column(
        SAEnum(EstadoCorrida, native_enum=False, length=16, validate_strings=True),
        nullable=False,
        default=EstadoCorrida.SIN_INSUMO,
        index=True,
    )
    detalle: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:  # pragma: no cover - ayuda al depurar
        return (
            f"<CorridaORM {self.id} estado={self.estado.value} tomados={self.tomados} "
            f"acusados={self.acusados} enviados={self.enviados}>"
        )


class EventoORM(Base):
    """Bitácora APPEND-ONLY: qué pasó, cuándo y quién lo hizo.

    Nada en este proyecto actualiza ni borra filas de `eventos`. Una entrega puede
    cambiar de estado varias veces; la bitácora conserva la secuencia completa, que es
    lo único que permite reconstruir por qué algo quedó como quedó.

    La llave primaria es un entero autoincremental a propósito: el orden de la bitácora
    es el orden de inserción, y dos eventos del mismo milisegundo siguen siendo
    distinguibles y ordenables.

    `entrega_id` admite NULL para poder registrar hechos que NO corresponden a ninguna
    entrega conocida — el caso típico es un acuse que llega citando una referencia que
    nadie envió. Descartarlo en silencio sería otro éxito silencioso.
    """

    __tablename__ = "eventos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entrega_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("entregas.id"), nullable=True, index=True
    )
    momento: Mapped[datetime] = mapped_column(FechaUTC, nullable=False, default=ahora)
    tipo: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    actor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detalle: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:  # pragma: no cover - ayuda al depurar
        return f"<EventoORM {self.tipo} entrega={self.entrega_id} actor={self.actor}>"


# `expire_on_commit=False`: tras `commit()` el orquestador devuelve la corrida al que
# la pidió. Con la expiración por omisión, leer `corrida.estado` fuera de la sesión
# lanzaría DetachedInstanceError; el resultado de la corrida no debe depender de que
# la sesión siga viva.
SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)


def crear_engine(url: str = "sqlite+pysqlite:///:memory:", *, echo: bool = False) -> Engine:
    """Crea el engine y deja `SessionLocal` amarrado a él.

    Para SQLite en memoria se fuerza `StaticPool` y `check_same_thread=False`: sin eso
    cada conexión abriría una base VACÍA distinta y las pruebas «pasarían» sobre datos
    que no existen —otro verde que no significa nada.
    """
    argumentos: dict = {"echo": echo, "future": True}
    if url.startswith("sqlite") and ":memory:" in url:
        argumentos["poolclass"] = StaticPool
        argumentos["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **argumentos)
    SessionLocal.configure(bind=engine)
    return engine


def crear_session_factory(engine: Engine) -> sessionmaker:
    """Fábrica de sesiones AMARRADA a un engine concreto.

    Preferible a `SessionLocal` cuando conviven varios engines (pruebas en paralelo,
    una base por caso), porque no depende de un estado global que el último
    `crear_engine` podría haber reconfigurado.
    """
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def crear_tablas(engine: Engine) -> None:
    """Crea el esquema completo. Idempotente."""
    Base.metadata.create_all(engine)
