#!/usr/bin/env bash
# Prueba de humo de Acuse: sale con 0 sólo si la API y el tablero responden como deben.
#
#   herramientas/humo.sh <url>                    # p. ej. http://localhost:8000
#
# La usan el job «imagen» del CI (contra el contenedor recién construido) y el RUNBOOK.
# Espera hasta HUMO_TIMEOUT segundos (90 por omisión) a que el servicio arranque.
# No escribe nada: sólo hace GET (y un GET a una ruta POST para comprobar el 405).
set -uo pipefail

API="${1:?uso: herramientas/humo.sh <url>}"
TIMEOUT="${HUMO_TIMEOUT:-90}"
API="${API%/}"
fallas=0

ok()    { echo "  ok    $1"; }
falla() { echo "  FALLA $1"; fallas=$((fallas + 1)); }

esperar() {
  local url="$1" limite=$((SECONDS + TIMEOUT))
  until curl -fsS --max-time 5 "$url" >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$limite" ]; then return 1; fi
    sleep 2
  done
}

echo "humo: ${API}"
if esperar "${API}/health"; then
  salud="$(curl -fsS --max-time 10 "${API}/health")"
  if printf '%s' "$salud" | grep -q '"estado":"ok"'; then
    ok "/health estado=ok versión $(printf '%s' "$salud" | sed -n 's/.*"version":"\([^"]*\)".*/\1/p')"
  else
    falla "/health: ${salud:0:200}"
  fi
else
  falla "/health no respondió en ${TIMEOUT} s"
fi

# La tesis en una respuesta: el resumen publica los DOS números (según el emisor y confirmado).
resumen="$(curl -fsS --max-time 10 "${API}/api/resumen" 2>&1)"
if printf '%s' "$resumen" | grep -q '"entregado_segun_emisor":' && printf '%s' "$resumen" | grep -q '"entregado_confirmado":'; then
  ok "/api/resumen publica según el emisor y confirmado (base viva)"
else
  falla "/api/resumen: ${resumen:0:200}"
fi

# Verde ≠ hizo algo: al arrancar se siembra el escenario y queda al menos una corrida registrada.
corridas="$(curl -fsS --max-time 10 "${API}/api/corridas?limit=1" 2>&1)"
if printf '%s' "$corridas" | grep -q '"estado":"'; then ok "/api/corridas tiene corridas"; else falla "/api/corridas: ${corridas:0:200}"; fi

# Contesta la app y no los archivos estáticos: una ruta POST pedida con GET es 405, no 404.
codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${API}/api/reiniciar")"
if [ "$codigo" = "405" ]; then ok "GET /api/reiniciar → 405"; else falla "GET /api/reiniciar → ${codigo}"; fi

if curl -fsS --max-time 10 "${API}/" | grep -qi '<html'; then ok "el tablero se sirve en /"; else falla "el tablero no responde en /"; fi

if [ "$fallas" -gt 0 ]; then
  echo "humo: ${fallas} falla(s)"
  exit 1
fi
echo "humo: todo responde"
