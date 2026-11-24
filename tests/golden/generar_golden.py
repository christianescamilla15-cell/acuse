#!/usr/bin/env python3
"""Oráculo INDEPENDIENTE de las cifras control: genera los archivos *.cifras.golden.json.

    python3 tests/golden/generar_golden.py            # reescribe los golden desde las entradas
    python3 tests/golden/generar_golden.py --revisar  # sale con 1 si algún golden no está al día

No importa nada de `acuse`: calcula con CENTAVOS ENTEROS (sin Decimal ni float), con su
propio redondeo mitad-hacia-arriba, a partir de la especificación de docs/reglas.md
(sección «Cifras control»). Si el código y este oráculo discrepan, la prueba de paridad
lo detecta y alguno de los dos está mal; por eso no comparten una sola línea.

Las entradas (`*.entrada.json`) traen importes como texto o entero, nunca float.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

CARPETA = Path(__file__).resolve().parent


def centavos(importe: str | int) -> int:
    """Importe → centavos enteros, redondeando el empate lejos del cero."""
    if isinstance(importe, bool) or not isinstance(importe, (str, int)):
        raise ValueError(f"importe no admitido por el oráculo: {importe!r}")
    if isinstance(importe, int):
        return importe * 100
    texto = importe.strip()
    negativo = texto.startswith("-")
    entero, _, fraccion = texto.lstrip("-").partition(".")
    if not entero.isdigit() or (fraccion and not fraccion.isdigit()):
        raise ValueError(f"importe ilegible para el oráculo: {importe!r}")
    escala = 10 ** len(fraccion)
    cociente, resto = divmod(int(entero + fraccion) * 100, escala)
    if 2 * resto >= escala:
        cociente += 1
    return -cociente if negativo and cociente else cociente


def como_texto(cent: int) -> str:
    signo = "-" if cent < 0 else ""
    return f"{signo}{abs(cent) // 100}.{abs(cent) % 100:02d}"


def sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def cifras(entrada: dict) -> dict:
    filas = []
    for registro in entrada["registros"]:
        importe = como_texto(centavos(registro["importe"]))
        canonico = json.dumps([registro["referencia"], importe, registro["moneda"]],
                              ensure_ascii=False, separators=(",", ":"))
        filas.append({"referencia": registro["referencia"], "importe": importe,
                      "moneda": registro["moneda"], "hash": sha256(canonico)})
    filas.sort(key=lambda f: f["referencia"])
    monedas = sorted({f["moneda"] for f in filas})
    return {
        "lote": entrada["lote"],
        "moneda": monedas[0] if len(monedas) == 1 else None,
        "conteo": len(filas),
        "suma": como_texto(sum(centavos(f["importe"]) for f in filas)),
        "hash_lote": sha256("\n".join(f["hash"] for f in filas)),
        "filas": filas,
    }


def destino(entrada: Path) -> Path:
    return entrada.with_name(entrada.name.replace(".entrada.json", ".cifras.golden.json"))


def main() -> int:
    revisar = "--revisar" in sys.argv[1:]
    entradas = sorted(CARPETA.glob("*.entrada.json"))
    if not entradas:
        print("oráculo: no hay entradas *.entrada.json → nada que comparar")
        return 1
    desfasados = 0
    for entrada in entradas:
        esperado = json.dumps(cifras(json.loads(entrada.read_text(encoding="utf-8"))),
                              ensure_ascii=False, indent=2) + "\n"
        salida = destino(entrada)
        if revisar:
            al_dia = salida.is_file() and salida.read_text(encoding="utf-8") == esperado
            desfasados += not al_dia
            print(f"{salida.name}: {'al día' if al_dia else 'DESACTUALIZADO (corre sin --revisar)'}")
        else:
            salida.write_text(esperado, encoding="utf-8")
            print(f"{salida.name}: {json.loads(esperado)['conteo']} filas")
    return 1 if desfasados else 0


if __name__ == "__main__":
    raise SystemExit(main())
