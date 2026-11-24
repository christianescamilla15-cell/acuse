"""E2E: el recorrido de la demo contra la API ARRANCADA COMO PROCESO, por HTTP real.

Arranca `uvicorn acuse.api.app:app` en un puerto libre con la base en un archivo SQLite
temporal (`ACUSE_BD`), recorre los pasos del README (las tres patologías y el acuse) con
`httpx`, detiene el proceso, lo vuelve a arrancar sobre el mismo archivo y comprueba que
el histórico sigue ahí. Es lo más cerca de la imagen que se puede estar sin Docker; el
job «imagen» del CI hace lo mismo con el contenedor.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

RAIZ = Path(__file__).resolve().parents[2]
GOLDEN_SEMBRADO = RAIZ / "tests" / "golden" / "lote_sembrado.cifras.golden.json"


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def servidor(url_bd: str) -> Iterator[str]:
    puerto = _puerto_libre()
    proceso = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "acuse.api.app:app", "--host", "127.0.0.1", "--port", str(puerto)],
        cwd=RAIZ,
        env={**os.environ, "ACUSE_BD": url_bd},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{puerto}"
    try:
        limite = time.monotonic() + 30
        while True:
            if proceso.poll() is not None:
                raise AssertionError(f"uvicorn terminó al arrancar:\n{proceso.stdout.read()}")
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > limite:
                raise AssertionError("la API no respondió /health en 30 s")
            time.sleep(0.2)
        yield base
    finally:
        proceso.terminate()
        try:
            proceso.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proceso.kill()


def _sin_antiguedad(resumen: dict) -> dict:
    """La antigüedad avanza con el reloj; todo lo demás debe ser idéntico tras reiniciar."""
    return {**resumen, "brechas": [{k: v for k, v in b.items() if k != "mas_antiguo_s"} for b in resumen["brechas"]]}


def test_recorrido_de_la_demo_y_reinicio_del_proceso(tmp_path):
    url_bd = f"sqlite+pysqlite:///{tmp_path / 'acuse-e2e.db'}"

    with servidor(url_bd) as base, httpx.Client(base_url=base, timeout=10) as api:
        # 0. Arranca sembrado: 10 acusadas, 18 sin acuse, 2 sin ruta, y una corrida SIN_INSUMO.
        resumen = api.get("/api/resumen").json()
        assert (resumen["total"], resumen["entregado_confirmado"], resumen["entregado_segun_emisor"]) == (30, 10, 28)
        assert "<html" in api.get("/").text.lower()                     # el tablero se sirve del mismo origen

        # 1-2. Una corrida con trabajos sin regla: los rechaza con motivo, no los manda a ningún lado.
        corrida = api.post("/api/simular/corrida", json={"n": 8, "incluir_sin_ruta": True}).json()
        assert (corrida["estado"], corrida["rechazados"]) == ("CON_PROBLEMAS", 2)
        rechazadas = api.get("/api/entregas", params={"estado": "RECHAZADO"}).json()
        assert len(rechazadas) == 4 and all(e["canal"] is None for e in rechazadas)

        # 3. Enviado ≠ recibido: la brecha está abierta.
        antes = api.get("/api/resumen").json()
        assert antes["brecha_de_acuse"] > 0

        # 4. El receptor lee su buzón: sólo eso cierra la brecha, y exactamente por lo leído.
        consumo = api.post("/api/simular/consumir", json={"canal": "asincrono-b", "n": 5}).json()
        despues = api.get("/api/resumen").json()
        assert consumo["acuses_registrados"] == 5
        assert despues["brecha_de_acuse"] == antes["brecha_de_acuse"] - 5
        assert despues["entregado_segun_emisor"] == antes["entregado_segun_emisor"]

        # 5. Vence el plazo: el incumplimiento tiene nombre y la brecha NO baja por mirarla.
        vencimiento = api.post("/api/simular/vencer").json()
        assert vencimiento["marcadas"] == despues["brecha_de_acuse"]
        assert sum(b["sin_acuse"] for b in api.get("/api/brechas").json()) == despues["brecha_de_acuse"]

        # 6. Verde ≠ hizo algo: una corrida sin insumo no es EXITOSA.
        vacia = api.post("/api/simular/corrida-vacia").json()
        assert (vacia["estado"], vacia["hizo_algo"]) == ("SIN_INSUMO", False)

        # Cifras control del lote sembrado, iguales al golden del oráculo independiente.
        cifras = api.get("/api/lotes/corte-C-00001/cifras").json()
        cifras.pop("consumido")
        assert cifras == json.loads(GOLDEN_SEMBRADO.read_text(encoding="utf-8"))
        foto = api.get("/api/resumen").json()

    # El proceso se reinicia sobre el mismo archivo: el histórico sigue y no se resiembra.
    with servidor(url_bd) as base, httpx.Client(base_url=base, timeout=10) as api:
        assert _sin_antiguedad(api.get("/api/resumen").json()) == _sin_antiguedad(foto)
        nueva = api.post("/api/simular/corrida", json={"n": 3}).json()
        assert (nueva["estado"], nueva["tomados"]) == ("EXITOSA", 3)       # folios nuevos, no duplicados
        assert api.get("/api/resumen").json()["total"] == foto["total"] + 3
