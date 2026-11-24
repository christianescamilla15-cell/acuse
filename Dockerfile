# Acuse · imagen de demostración
# Objetivo: que el tablero levante con un solo comando y sin sorpresas.
# Python 3.12, el MISMO que prueba el CI (el job «imagen» lo verifica). Anclada por digest:
# una etiqueta puede reescribirse; Dependabot propone el digest nuevo (sólo parches de 3.12).
FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b

# PYTHONDONTWRITEBYTECODE: nada de .pyc en un contenedor efímero.
# PYTHONUNBUFFERED: los logs salen cuando ocurren, no cuando el búfer se llena
#   — un log tardío es otra forma de "no me enteré a tiempo".
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Las dependencias van primero y en su propia capa: el código cambia mucho
# más seguido que requirements.txt. Sólo las de EJECUCIÓN, con versión exacta:
# pytest y compañía viven en requirements-dev.txt y no entran a la imagen.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Código de la aplicación y tablero estático (la API monta /app/web).
COPY acuse/ ./acuse/
COPY web/ ./web/

# Usuario NO root con UID fijo. Un contenedor de demostración que corre como
# root es exactamente la clase de descuido que este proyecto critica.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin acuse \
    && chown -R acuse:acuse /app
USER acuse

EXPOSE 8000

# El healthcheck pregunta por el tablero, no por el proceso: "el proceso vive"
# es el equivalente contenedorizado de "la corrida terminó en verde".
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "acuse.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
