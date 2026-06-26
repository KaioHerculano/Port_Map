FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.3.2 \
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1

WORKDIR /app

# Instala dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    netcat-openbsd \
    media-types \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instala o Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="$POETRY_HOME/bin:$PATH"

# Copia definição de dependências
COPY pyproject.toml poetry.lock* /app/

# Instala dependências Python no escopo global do container (sem virtualenv)
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-root

# Copia o código do projeto
COPY . /app/

# Cria o diretório de staticfiles e media (garante que existam)
RUN mkdir -p /app/staticfiles /app/media
RUN chown -R www-data:www-data /app/staticfiles /app/media
RUN chmod -R 755 /app/staticfiles /app/media

# Configura o Entrypoint
COPY entrypoint.sh /usr/local/bin/

# Remove caracteres invisíveis (\r) do script (correção para Windows)
RUN sed -i 's/\r$//g' /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Informa a porta exposta
EXPOSE 8003

ENTRYPOINT ["entrypoint.sh"]
