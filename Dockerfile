FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    REFLEX_CHECK_LATEST_VERSION=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY rxconfig.py ./
COPY assets ./assets
COPY qcc_reflex_pilot ./qcc_reflex_pilot

ARG RENDER_EXTERNAL_URL
ENV RENDER_EXTERNAL_URL=${RENDER_EXTERNAL_URL}

RUN reflex init

EXPOSE 10000

CMD ["sh", "-c", "reflex run --env prod --single-port --frontend-port ${PORT:-10000} --backend-port ${PORT:-10000}"]
