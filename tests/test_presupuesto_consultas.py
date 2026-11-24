"""Presupuesto de consultas: cuánto le cuesta a la base cada operación, según el tamaño.

Es el «benchmark» de este repo, pero determinista: en lugar de medir segundos (que
varían por máquina y vuelven intermitente al CI) cuenta sentencias SQL. Atrapa la
regresión que importa: un tablero que hace una consulta por fila (N+1) o una corrida
cuyo costo por trabajo crece sin que nadie lo note.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event

from acuse.conciliador import brechas, marcar_vencidas, resumen
from acuse.modelo import ahora
from acuse.persistencia import crear_engine, crear_session_factory, crear_tablas

from apoyo import orquestador, trabajo

TAMANOS = [10, 60, 240]


class Contador:
    def __init__(self, engine) -> None:
        self.total = self.selects = 0
        event.listen(engine, "before_cursor_execute", self._contar)

    def _contar(self, conn, cursor, sentencia, parametros, contexto, varias) -> None:
        self.total += 1
        self.selects += sentencia.lstrip().upper().startswith("SELECT")

    def reiniciar(self) -> None:
        self.total = self.selects = 0


def _escenario(n: int):
    engine = crear_engine("sqlite+pysqlite:///:memory:")
    crear_tablas(engine)
    sesiones = crear_session_factory(engine)
    orq = orquestador(sesiones)
    trabajos = [trabajo(f"{i:04d}", "buzon" if i % 2 else "registro") for i in range(n)]
    return sesiones, orq, trabajos, Contador(engine)


@pytest.mark.parametrize("n", TAMANOS)
def test_procesar_cuesta_lo_mismo_por_trabajo(n):
    _, orq, trabajos, contador = _escenario(n)
    orq.procesar(trabajos)
    assert contador.selects == n                   # una búsqueda de idempotencia por trabajo
    assert contador.total <= 6 * n + 4             # alta, bitácora y estado: costo lineal


@pytest.mark.parametrize("n", TAMANOS)
def test_el_tablero_cuesta_una_consulta_sin_importar_el_tamano(n):
    sesiones, orq, trabajos, contador = _escenario(n)
    orq.procesar(trabajos)
    for medir in (brechas, resumen):
        contador.reiniciar()
        with sesiones() as ses:
            medir(ses)
        assert (contador.total, contador.selects) == (1, 1), medir.__name__


@pytest.mark.parametrize("n", TAMANOS)
def test_marcar_vencidas_lee_una_vez_y_escribe_por_vencida(n):
    sesiones, orq, trabajos, contador = _escenario(n)
    orq.procesar(trabajos)
    contador.reiniciar()
    with sesiones() as ses:
        marcadas = marcar_vencidas(ses, ahora() + timedelta(days=1))
    assert marcadas == n // 2
    assert contador.selects == 1
    assert contador.total <= marcadas + 3


@pytest.mark.parametrize("n", TAMANOS)
def test_registrar_acuse_busca_una_vez_por_referencia(n):
    _, orq, trabajos, contador = _escenario(n)
    orq.procesar(trabajos)
    contador.reiniciar()
    orq.registrar_acuse([t.referencia for t in trabajos], actor="receptor")
    assert contador.selects == n
    assert contador.total <= 2 * n + 2
