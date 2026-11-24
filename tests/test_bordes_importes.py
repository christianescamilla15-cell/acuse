"""Bordes de importes: Decimal, redondeo a centavos y lo que NO se acepta como cantidad."""
from __future__ import annotations

from decimal import Decimal

import pytest

from acuse.cifras import CifraInvalida, cifras_de_lote, hash_fila, normalizar_importe

# (entrada, centavos esperados como texto). Las trampas de float están escritas como TEXTO:
# en float, round(2.675, 2) da 2.67 y round(1.005, 2) da 1.0.
REDONDEOS = [
    ("0.005", "0.01"),                # empate: mitad hacia arriba
    ("0.0049", "0.00"),
    ("0.0050000001", "0.01"),
    ("-0.005", "-0.01"),              # el empate se aleja del cero también en negativos
    ("-0.004", "0.00"),               # y el cero no lleva signo
    ("-0.0", "0.00"),
    ("2.675", "2.68"),                # float diría 2.67
    ("1.005", "1.01"),                # float diría 1.00
    ("1.015", "1.02"),
    ("0.125", "0.13"),                # ROUND_HALF_EVEN (bancario) diría 0.12
    ("0.135", "0.14"),
    ("1000", "1000.00"),
    ("  7.1  ", "7.10"),              # espacios alrededor se toleran
    ("0.10", "0.10"),
    ("999999999.995", "1000000000.00"),
    ("123456789012345678901234.555", "123456789012345678901234.56"),
    (1175, "1175.00"),                # entero
    (-3, "-3.00"),
    (0, "0.00"),
    (Decimal("2.675"), "2.68"),       # Decimal exacto
    (Decimal("1E+3"), "1000.00"),     # Decimal en notación científica sí es exacto
    (Decimal("-0.00"), "0.00"),
]


@pytest.mark.parametrize(("entrada", "esperado"), REDONDEOS, ids=[repr(e) for e, _ in REDONDEOS])
def test_el_importe_se_redondea_a_centavos_mitad_hacia_arriba(entrada, esperado):
    resultado = normalizar_importe(entrada)
    assert isinstance(resultado, Decimal)
    assert f"{resultado:.2f}" == esperado
    assert resultado.as_tuple().exponent == -2


INVALIDOS = [
    (2.675, "float"),                 # ya perdió precisión
    (0.1, "float"),
    (True, "booleano"),               # bool es un int en Python: no es una cantidad
    (False, "booleano"),
    ("1,000.00", "ilegible"),         # separador de miles
    ("1.000,50", "ilegible"),
    ("1e3", "ilegible"),              # notación científica en texto
    ("+5", "ilegible"),
    ("", "ilegible"),
    ("   ", "ilegible"),
    ("abc", "ilegible"),
    ("$100", "ilegible"),
    (".5", "ilegible"),
    (Decimal("NaN"), "no finito"),
    (Decimal("Infinity"), "no finito"),
    (Decimal("-Infinity"), "no finito"),
    ("9" * 40, "fuera de rango"),     # no cabe en el contexto de 28 dígitos
    ([1], "tipo list"),
    (None, "tipo NoneType"),
]


@pytest.mark.parametrize(("entrada", "motivo"), INVALIDOS, ids=[repr(e)[:24] for e, _ in INVALIDOS])
def test_lo_que_no_es_una_cantidad_exacta_se_rechaza(entrada, motivo):
    with pytest.raises(CifraInvalida, match=motivo):
        normalizar_importe(entrada)


@pytest.mark.parametrize(
    ("importes", "suma"),
    [
        (["0.005", "0.005", "0.005"], "0.03"),        # se suma lo redondeado: 3 × 0.01
        (["0.004", "0.004", "0.004"], "0.00"),        # (la suma sin redondear daría 0.01)
        (["0.1"] * 10, "1.00"),                       # float daría 0.9999999999999999
        (["100.00", "-100.00"], "0.00"),
        (["-0.005", "0.005"], "0.00"),
        (["1175", "1182", "1189", "1196"], "4742.00"),
        (["999999999.995", "0.005"], "1000000000.01"),
    ],
    ids=["tres-empates", "tres-debajo-del-empate", "diez-decimos", "se-anulan", "empates-opuestos",
         "lote-sembrado", "grande-mas-empate"],
)
def test_la_suma_del_lote_es_la_suma_de_las_filas_redondeadas(importes, suma):
    registros = [(f"LIQ-{i:03d}", {"importe": imp, "moneda": "MXN"}) for i, imp in enumerate(importes)]
    cifras = cifras_de_lote("L", registros)
    assert f"{cifras.suma:.2f}" == suma
    assert cifras.suma == sum((f.importe for f in cifras.filas), Decimal("0"))


@pytest.mark.parametrize(
    ("a", "b"),
    [("2.675", "2.68"), ("1000", "1000.00"), (Decimal("1E+3"), "1000.004"), ("-0.004", "0")],
    ids=["texto-vs-redondeado", "entero-vs-centavos", "cientifica-vs-texto", "cero-negativo-vs-cero"],
)
def test_importes_iguales_al_centavo_dan_el_mismo_hash(a, b):
    assert hash_fila("R", normalizar_importe(a), "MXN") == hash_fila("R", normalizar_importe(b), "MXN")


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("referencia", "R2"), ("importe", Decimal("1.01")), ("moneda", "USD")],
)
def test_cambiar_cualquier_campo_cambia_el_hash_de_la_fila(campo, valor):
    base = {"referencia": "R", "importe": Decimal("1.00"), "moneda": "MXN"}
    assert hash_fila(**base) != hash_fila(**{**base, campo: valor})


def test_el_hash_de_fila_no_se_confunde_con_separadores_en_la_referencia():
    """JSON canónico, no concatenación: «A|1.00» no puede chocar con otra fila."""
    assert hash_fila("A|1.00", Decimal("2.00"), "MXN") != hash_fila("A", Decimal("1.00"), "MXN")
