# Acuse · imagen de demostración
# Objetivo: que el tablero levante con un solo comando y sin sorpresas.
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE: nada de .pyc en un contenedor efímero.
# PYTHONUNBUFFERED: los logs salen cuando ocurren, no cuando el búfer se llena
#   — un log tardío es otra forma de "no me enteré a tiempo".
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ACUSE_WEB=/app/web

WORKDIR /app

# Las dependencias van primero y en su propia capa: el código cambia mucho
# más seguido que requirements.txt.
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
