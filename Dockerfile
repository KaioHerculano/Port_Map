# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.3.2 \
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1

WORKDIR /app

# Instala ferramentas necessárias para compilar dependências com cache do apt
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl

# Instala o Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="$POETRY_HOME/bin:$PATH"

# Cria virtualenv dedicado para o projeto
RUN python3 -m venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$POETRY_HOME/bin:$PATH"

# Copia apenas as definições de dependências
COPY pyproject.toml poetry.lock* /app/

# Instala dependências em /opt/venv usando cache do Poetry
RUN --mount=type=cache,target=/root/.cache/pypoetry \
    poetry config virtualenvs.create false \
    && poetry install --only main --no-root

# Estágio final limpo (runtime)
FROM python:3.12-slim AS runner

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/app \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Instala apenas dependências essenciais de runtime (sem compiladores nem headers de desenvolvimento)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    netcat-openbsd \
    media-types \
    curl \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Copia o ambiente virtual com as dependências instaladas
COPY --from=builder /opt/venv /opt/venv

# Cria diretórios de arquivos estáticos e de mídia
RUN mkdir -p /app/staticfiles /app/media \
    && chown -R www-data:www-data /app \
    && chmod -R 755 /app/staticfiles /app/media

# Configura o entrypoint
COPY entrypoint.sh /usr/local/bin/
RUN sed -i 's/\r$//g' /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh

# Copia o código do projeto com as permissões corretas
COPY --chown=www-data:www-data . /app/

# Informa a porta exposta
EXPOSE 8003

USER www-data

ENTRYPOINT ["entrypoint.sh"]
