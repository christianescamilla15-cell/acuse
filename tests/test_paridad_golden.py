"""Paridad contra un oráculo independiente: el conciliador reproduce los archivos golden.

Los `tests/golden/*.cifras.golden.json` los genera `tests/golden/generar_golden.py`, que
no importa nada de `acuse` (centavos enteros, redondeo propio). Aquí los trabajos de cada
entrada recorren el camino real —Orquestador → Enrutador → CanalLote— y las cifras se
recalculan del lado receptor con `acuse.conciliador`. Deben ser IGUALES al golden.

El listón lo ponen los controles negativos: una alteración mínima (un centavo, una fila
de más o de menos) tiene que romper la paridad; si no la rompiera, la prueba no probaría
nada.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from acuse.api.app import app
from acuse.canales.lote import CanalLote
from acuse.cifras import EstadoConciliacion, TipoDiferencia, cifras_de_lote
from acuse.conciliador import cifras_declaradas, conciliar_lote
from acuse.demo import LOTE_SEMBRADO, trabajos_sembrados
from acuse.modelo import ReglaRuteo, Trabajo
from acuse.orquestador import Orquestador
from acuse.ruteo import Enrutador

from apoyo import RAIZ_REPO, nueva_bd

GOLDEN = RAIZ_REPO / "tests" / "golden"
CASOS = ["lote_bordes", "lote_sembrado"]


def _leer(nombre: str) -> dict:
    return json.loads((GOLDEN / nombre).read_text(encoding="utf-8"))


def _trabajos(entrada: dict) -> list[Trabajo]:
    lote = entrada["lote"]
    return [
        Trabajo(
            id=f"T-{r['referencia']}",
            referencia=r["referencia"],
            atributos={"destino": "C"},
            carga={"importe": r["importe"], "moneda": r["moneda"], "lote": lote},
            lote=lote,
        )
        for r in entrada["registros"]
    ]


def _por_el_camino_real(trabajos: list[Trabajo]) -> CanalLote:
    canal = CanalLote("lote-c")
    orq = Orquestador(nueva_bd(), Enrutador([ReglaRuteo("c-lote", "lote-c", {"destino": "C"})]), {"lote-c": canal})
    corrida = orq.procesar(trabajos)
    assert corrida.enviados == len(trabajos)        # todos salieron: nada se quedó en el camino
    return canal


@pytest.mark.parametrize("caso", CASOS)
def test_el_conciliador_reproduce_el_golden(caso):
    entrada = _leer(f"{caso}.entrada.json")
    golden = _leer(f"{caso}.cifras.golden.json")
    trabajos = _trabajos(entrada)
    canal = _por_el_camino_real(trabajos)

    declaradas = cifras_declaradas(entrada["lote"], trabajos)
    resultado = conciliar_lote(declaradas, canal.registros_del_lote(entrada["lote"]))

    assert declaradas.como_dict() == golden          # el emisor declara lo mismo que el oráculo
    assert resultado.recibidas.como_dict() == golden  # y el receptor reproduce lo mismo
    assert resultado.estado is EstadoConciliacion.CUADRA


def test_el_golden_esta_al_dia_con_su_oraculo():
    salida = subprocess.run(
        [sys.executable, str(GOLDEN / "generar_golden.py"), "--revisar"],
        capture_output=True, text=True, check=False,
    )
    assert salida.returncode == 0, salida.stdout + salida.stderr
    assert salida.stdout.count("al día") == len(CASOS)


def test_la_entrada_del_lote_sembrado_es_la_de_la_demo():
    """Si la demo cambia sus importes, el golden del lote sembrado debe regenerarse."""
    entrada = _leer("lote_sembrado.entrada.json")
    demo = [
        {"referencia": t.referencia, "importe": t.carga["importe"], "moneda": t.carga["moneda"]}
        for t in trabajos_sembrados() if t.lote == LOTE_SEMBRADO
    ]
    assert entrada["lote"] == LOTE_SEMBRADO
    assert demo == entrada["registros"]


def test_la_api_publica_las_cifras_del_lote_sembrado_igual_que_el_golden():
    golden = _leer("lote_sembrado.cifras.golden.json")
    with TestClient(app) as cliente:
        assert cliente.post("/api/reiniciar").status_code == 200
        respuesta = cliente.get(f"/api/lotes/{LOTE_SEMBRADO}/cifras")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo.pop("consumido") is False
    assert cuerpo == golden


# ------------------------------------------------------------- controles negativos


def _recibidos_alterados(alterar) -> tuple:
    entrada = _leer("lote_bordes.entrada.json")
    golden = _leer("lote_bordes.cifras.golden.json")
    trabajos = _trabajos(entrada)
    registros = copy.deepcopy(_por_el_camino_real(trabajos).registros_del_lote(entrada["lote"]))
    alterar(registros)
    return golden, conciliar_lote(cifras_declaradas(entrada["lote"], trabajos), registros)


def _un_centavo_menos(registros):
    """LIQ-G-0005 es «2.675» → 2.68; el receptor la ve como «2.665» → 2.67."""
    i = next(i for i, (referencia, _) in enumerate(registros) if referencia == "LIQ-G-0005")
    registros[i] = ("LIQ-G-0005", {"importe": "2.665", "moneda": "MXN"})


def _una_fila_de_mas(registros):
    registros.append(("LIQ-INTRUSA", {"importe": "0.00", "moneda": "MXN"}))


def _otra_moneda_en_una_fila(registros):
    referencia, carga = registros[0]
    registros[0] = (referencia, {**carga, "moneda": "USD"})


CONTROLES = {
    "un-centavo": (_un_centavo_menos, TipoDiferencia.IMPORTE_DISTINTO),
    "una-fila-menos": (list.pop, TipoDiferencia.FALTANTE),
    "una-fila-de-mas": (_una_fila_de_mas, TipoDiferencia.SOBRANTE),
    "otra-moneda-en-una-fila": (_otra_moneda_en_una_fila, None),
}


@pytest.mark.parametrize("control", list(CONTROLES), ids=list(CONTROLES))
def test_una_alteracion_minima_rompe_la_paridad(control):
    alterar, tipo = CONTROLES[control]
    if tipo is None:
        # Una fila en otra moneda hace al lote recibido inconciliable: se rechaza en voz alta.
        with pytest.raises(ValueError, match="mezcla monedas"):
            _recibidos_alterados(alterar)
        return
    golden, resultado = _recibidos_alterados(alterar)
    assert resultado.recibidas.como_dict() != golden
    assert resultado.recibidas.hash_lote != golden["hash_lote"]
    assert resultado.estado is EstadoConciliacion.DESCUADRA
    assert [d.tipo for d in resultado.diferencias] == [tipo]


def test_reordenar_las_filas_no_rompe_la_paridad():
    golden, resultado = _recibidos_alterados(list.reverse)
    assert resultado.recibidas.como_dict() == golden
    assert resultado.cuadra


def test_el_codigo_y_el_oraculo_coinciden_fila_por_fila():
    """Diagnóstico fino: si la paridad global se rompe, esto dice en qué importe."""
    entrada = _leer("lote_bordes.entrada.json")
    golden = {f["referencia"]: f for f in _leer("lote_bordes.cifras.golden.json")["filas"]}
    for registro in entrada["registros"]:
        (fila,) = cifras_de_lote("x", [(registro["referencia"], registro)]).como_dict()["filas"]
        assert fila == golden[registro["referencia"]], registro
