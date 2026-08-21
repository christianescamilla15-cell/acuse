"""Pruebas del orquestador.

Cada prueba fija una de las tres patologías. No comprueban «que no truene»:
comprueban que el sistema se NIEGUE a reportar como entregado algo que nadie
confirmó, que se niegue a enrutar por omisión, y que se niegue a llamar exitosa a
una corrida que no procesó nada.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from acuse.canales import ResultadoEnvio
from acuse.modelo import EstadoEntrega, ModoCanal, ReglaRuteo, Trabajo
from acuse.orquestador import Orquestador
from acuse.persistencia import (
    CorridaORM,
    EntregaORM,
    EstadoCorrida,
    EventoORM,
    crear_engine,
    crear_session_factory,
    crear_tablas,
)
from acuse.ruteo import Enrutador

# ---------------------------------------------------------------- dobles de prueba


class CanalDoble:
    """Canal de prueba. Cumple el contrato de `acuse.canales.Canal`."""

    def __init__(
        self,
        nombre: str,
        modo: ModoCanal,
        *,
        aceptado: bool = True,
        acuse_inmediato: bool | None = None,
        acuse_por: str | None = None,
        detalle: str = "",
        explota: bool = False,
        plazo_acuse_s: int = 900,
    ) -> None:
        self.nombre = nombre
        self.modo = modo
        self.aceptado = aceptado
        # None = el canal no fuerza nada y responde según su modo.
        self.acuse_inmediato = acuse_inmediato
        self.acuse_por = acuse_por
        self.detalle = detalle
        self.explota = explota
        self.plazo_acuse_s = plazo_acuse_s
        self.enviados: list[str] = []

    def enviar(self, entrega, carga: dict) -> ResultadoEnvio:
        if self.explota:
            raise RuntimeError("el canal se cayó a media llamada")
        self.enviados.append(entrega.referencia)

        if not self.aceptado:
            return ResultadoEnvio(
                aceptado=False,
                detalle=self.detalle or f"El canal «{self.nombre}» no admitió el envío.",
            )

        inmediato = (
            self.acuse_inmediato
            if self.acuse_inmediato is not None
            else self.modo is ModoCanal.SINCRONO
        )
        return ResultadoEnvio(
            aceptado=True,
            acuse_inmediato=inmediato,
            acuse_por=(self.acuse_por or f"receptor:{self.nombre}") if inmediato else None,
            detalle=self.detalle,
        )


REGLAS = [
    ReglaRuteo(
        nombre="al-registro-en-linea",
        canal="registro",
        cuando={"destino": "registro"},
        prioridad=10,
    ),
    ReglaRuteo(
        nombre="al-buzon-por-lote",
        canal="buzon",
        cuando={"destino": "buzon"},
        prioridad=20,
    ),
]


@pytest.fixture()
def sesiones():
    """SQLite en memoria, esquema limpio por prueba."""
    engine = crear_engine("sqlite+pysqlite:///:memory:")
    crear_tablas(engine)
    return crear_session_factory(engine)


@pytest.fixture()
def canales():
    return {
        # Confirma en la misma llamada: el receptor responde en línea.
        "registro": CanalDoble("registro", ModoCanal.SINCRONO, acuse_por="sistema-de-registro"),
        # Deja el archivo y se va: el acuse llegará después… o no llegará.
        "buzon": CanalDoble("buzon", ModoCanal.ASINCRONO),
    }


def _orq(sesiones, canales, **kwargs) -> Orquestador:
    return Orquestador(sesiones, Enrutador(REGLAS), canales, **kwargs)


def _entregas(sesiones) -> list[EntregaORM]:
    with sesiones() as ses:
        return list(ses.scalars(select(EntregaORM).order_by(EntregaORM.referencia)))


def _tipos_evento(sesiones) -> list[str]:
    with sesiones() as ses:
        return list(ses.scalars(select(EventoORM.tipo).order_by(EventoORM.id)))


def _trabajo(sufijo: str, destino: str) -> Trabajo:
    return Trabajo(
        id=f"T-{sufijo}",
        referencia=f"LIQ-{sufijo}",
        atributos={"destino": destino},
        carga={"importe": 100},
    )


# --------------------------------------------------------- 1. enviado ≠ recibido


def test_canal_sincrono_deja_acusado(sesiones, canales):
    """El canal confirma en la misma llamada: eso SÍ es una entrega."""
    corrida = _orq(sesiones, canales).procesar([_trabajo("001", "registro")])

    (entrega,) = _entregas(sesiones)
    assert entrega.estado is EstadoEntrega.ACUSADO
    assert entrega.entregado is True
    assert entrega.acusado_en is not None
    # Sin actor no hay acuse: el registro dice QUIÉN confirmó.
    assert entrega.acuse_por == "sistema-de-registro"

    assert corrida.estado is EstadoCorrida.EXITOSA
    assert (corrida.acusados, corrida.enviados) == (1, 0)


def test_canal_asincrono_deja_enviado_y_no_entregado(sesiones, canales):
    """El canal aceptó el archivo. Nadie confirmó nada. NO es una entrega."""
    corrida = _orq(sesiones, canales).procesar([_trabajo("002", "buzon")])

    (entrega,) = _entregas(sesiones)
    assert entrega.estado is EstadoEntrega.ENVIADO
    # El corazón del proyecto: aceptado por el canal ≠ entregado.
    assert entrega.entregado is False
    assert entrega.a_dominio().entregado is False
    assert entrega.enviado_en is not None
    assert entrega.acusado_en is None
    assert entrega.acuse_por is None

    # Los contadores tampoco los mezclan.
    assert (corrida.acusados, corrida.enviados) == (0, 1)


def test_a_un_canal_asincrono_que_dice_haber_acusado_se_le_rechaza_el_acuse(sesiones):
    """Un canal ASÍNCRONO no puede saber si el receptor recibió: su acuse no vale.

    Se le rechaza el ACUSE, no el envío: la entrega queda ENVIADO —que es la verdad
    comprobable— y la impugnación queda escrita en la bitácora.
    """
    mentiroso = CanalDoble(
        "buzon", ModoCanal.ASINCRONO, acuse_inmediato=True, acuse_por="yo-mismo"
    )
    _orq(sesiones, {"buzon": mentiroso}).procesar([_trabajo("003", "buzon")])

    (entrega,) = _entregas(sesiones)
    assert entrega.estado is EstadoEntrega.ENVIADO
    assert entrega.entregado is False
    assert entrega.acuse_por is None
    assert entrega.acusado_en is None
    assert "acuse_rechazado" in _tipos_evento(sesiones)


# ------------------------------------------------------------ 2. ruteo por omisión


def test_trabajo_sin_regla_queda_rechazado_con_motivo(sesiones, canales):
    """Sin regla que lo cubra NO hay canal por omisión: se rechaza ruidosamente."""
    huerfano = Trabajo(id="T-004", referencia="LIQ-004", atributos={"destino": "inexistente"})

    corrida = _orq(sesiones, canales).procesar([huerfano])

    (entrega,) = _entregas(sesiones)
    assert entrega.estado is EstadoEntrega.RECHAZADO
    assert entrega.canal is None                      # no cayó en ningún canal
    assert entrega.entregado is False
    assert "no casa con ninguna regla" in entrega.detalle
    assert "LIQ-004" in entrega.detalle               # el motivo es legible, no un código

    assert corrida.estado is EstadoCorrida.CON_PROBLEMAS
    assert corrida.rechazados == 1
    assert "rechazado" in _tipos_evento(sesiones)


def test_atributo_faltante_no_cae_en_un_canal_cualquiera(sesiones, canales):
    """El caso exacto de la patología: el productor no pobló el campo."""
    sin_atributo = Trabajo(id="T-005", referencia="LIQ-005", atributos={})

    _orq(sesiones, canales).procesar([sin_atributo])

    (entrega,) = _entregas(sesiones)
    assert entrega.estado is EstadoEntrega.RECHAZADO
    assert canales["registro"].enviados == []
    assert canales["buzon"].enviados == []


def test_canal_resuelto_pero_no_registrado_es_fallido(sesiones):
    """La regla resolvió un canal que nadie implementa: falla visible, no silencio."""
    corrida = _orq(sesiones, {}).procesar([_trabajo("006", "registro")])

    (entrega,) = _entregas(sesiones)
    assert entrega.estado is EstadoEntrega.FALLIDO
    assert "no hay ningún canal registrado" in entrega.detalle
    assert corrida.estado is EstadoCorrida.CON_PROBLEMAS
    assert corrida.fallidos == 1


def test_canal_que_revienta_deja_fallido_no_hueco(sesiones):
    canales = {"buzon": CanalDoble("buzon", ModoCanal.ASINCRONO, explota=True)}
    corrida = _orq(sesiones, canales).procesar([_trabajo("007", "buzon")])

    (entrega,) = _entregas(sesiones)
    assert entrega.estado is EstadoEntrega.FALLIDO
    assert "RuntimeError" in entrega.detalle
    assert corrida.fallidos == 1


def test_canal_que_no_acepta_deja_fallido(sesiones):
    canales = {"buzon": CanalDoble("buzon", ModoCanal.ASINCRONO, aceptado=False)}
    corrida = _orq(sesiones, canales).procesar([_trabajo("008", "buzon")])

    (entrega,) = _entregas(sesiones)
    assert entrega.estado is EstadoEntrega.FALLIDO
    assert entrega.intentos == 1
    assert corrida.estado is EstadoCorrida.CON_PROBLEMAS


# ------------------------------------------------------------- 3. verde ≠ hizo algo


def test_lista_vacia_es_sin_insumo_no_exitosa(sesiones, canales):
    """Corrió y no procesó nada. Eso NO es éxito."""
    corrida = _orq(sesiones, canales).procesar([])

    assert corrida.estado is EstadoCorrida.SIN_INSUMO
    assert corrida.estado is not EstadoCorrida.EXITOSA
    assert corrida.tomados == 0
    assert "NO es una corrida exitosa" in corrida.detalle
    assert corrida.terminada_en is not None
    assert _entregas(sesiones) == []


# ------------------------------------------------------------------ idempotencia


def test_procesar_dos_veces_no_duplica(sesiones, canales):
    """Misma clave = misma entrega. Ni dos filas ni dos envíos."""
    lote = [_trabajo("009", "buzon")]
    orq = _orq(sesiones, canales)

    primera = orq.procesar(lote)
    segunda = orq.procesar(lote)

    entregas = _entregas(sesiones)
    assert len(entregas) == 1
    assert entregas[0].idempotencia == "T-009:LIQ-009"
    assert entregas[0].intentos == 1                       # se envió UNA vez
    assert len(canales["buzon"].enviados) == 1

    assert primera.estado is EstadoCorrida.EXITOSA
    # La segunda corrida no procesó nada nuevo: tampoco puede llamarse exitosa.
    assert segunda.estado is EstadoCorrida.SIN_INSUMO
    assert "duplicado_ignorado" in _tipos_evento(sesiones)


def test_reintento_de_una_fallida_reusa_la_misma_entrega(sesiones):
    """Un reintento no crea una entrega nueva: el histórico contaría dos."""
    canal = CanalDoble("buzon", ModoCanal.ASINCRONO, aceptado=False)
    canales = {"buzon": canal}
    orq = _orq(sesiones, canales)
    lote = [_trabajo("010", "buzon")]

    orq.procesar(lote)
    canal.aceptado = True
    orq.procesar(lote)

    entregas = _entregas(sesiones)
    assert len(entregas) == 1
    assert entregas[0].estado is EstadoEntrega.ENVIADO
    assert entregas[0].intentos == 2
    assert "reintento" in _tipos_evento(sesiones)


# --------------------------------------------------------------- registrar_acuse


def test_registrar_acuse_sin_actor_lanza_valueerror(sesiones, canales):
    orq = _orq(sesiones, canales)
    orq.procesar([_trabajo("011", "buzon")])

    with pytest.raises(ValueError) as error:
        orq.registrar_acuse(["LIQ-011"], actor="")

    assert "sin actor" in str(error.value)

    with pytest.raises(ValueError):
        orq.registrar_acuse(["LIQ-011"], actor="   ")

    # Y nada cambió: un acuse anónimo no mueve un solo registro.
    (entrega,) = _entregas(sesiones)
    assert entrega.estado is EstadoEntrega.ENVIADO


def test_registrar_acuse_pasa_enviado_a_acusado(sesiones, canales):
    orq = _orq(sesiones, canales)
    orq.procesar([_trabajo("012", "buzon"), _trabajo("013", "buzon")])

    marcadas = orq.registrar_acuse(["LIQ-012"], actor="receptor-liquidacion")

    assert marcadas == 1
    por_referencia = {e.referencia: e for e in _entregas(sesiones)}

    acusada = por_referencia["LIQ-012"]
    assert acusada.estado is EstadoEntrega.ACUSADO
    assert acusada.entregado is True
    assert acusada.acuse_por == "receptor-liquidacion"
    assert acusada.acusado_en is not None

    # La que nadie acusó sigue exactamente donde estaba.
    assert por_referencia["LIQ-013"].estado is EstadoEntrega.ENVIADO
    assert por_referencia["LIQ-013"].entregado is False


def test_acuse_de_referencia_desconocida_no_inventa_entregas(sesiones, canales):
    """Se cuenta lo marcado, no lo pedido: comparar ambos revela el desajuste."""
    orq = _orq(sesiones, canales)
    orq.procesar([_trabajo("014", "buzon")])

    marcadas = orq.registrar_acuse(["LIQ-014", "LIQ-QUE-NADIE-ENVIO"], actor="receptor")

    assert marcadas == 1
    assert len(_entregas(sesiones)) == 1
    assert "acuse_sin_entrega" in _tipos_evento(sesiones)


def test_una_corrida_que_revienta_deja_rastro_y_no_se_traga_el_error(sesiones, canales):
    """Una corrida que aborta sin dejar fila es indistinguible de una que no corrió."""

    class EnrutadorRoto:
        def resolver(self, referencia, atributos):
            raise TypeError("el enrutador está mal construido")

    orq = Orquestador(sesiones, EnrutadorRoto(), canales)

    with pytest.raises(TypeError):
        orq.procesar([_trabajo("016", "buzon")])

    with sesiones() as ses:
        corridas = list(ses.scalars(select(CorridaORM)))
        # El lote se revirtió completo: ninguna entrega a medias.
        assert list(ses.scalars(select(EntregaORM))) == []

    assert len(corridas) == 1
    assert corridas[0].estado is EstadoCorrida.FALLIDA
    assert "TypeError" in corridas[0].detalle


def test_la_bitacora_conserva_la_secuencia_completa(sesiones, canales):
    orq = _orq(sesiones, canales)
    orq.procesar([_trabajo("015", "buzon")])
    orq.registrar_acuse(["LIQ-015"], actor="receptor")

    assert _tipos_evento(sesiones) == ["creada", "enrutado", "enviado", "acusado"]
