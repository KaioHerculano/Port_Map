#!/bin/bash
set -e

# Sanitiza variáveis de ambiente contra caracteres de quebra de linha (\r) do Windows
APP_ROLE=$(echo "$APP_ROLE" | tr -d '\r')
POSTGRES_HOST=$(echo "$POSTGRES_HOST" | tr -d '\r')
POSTGRES_PORT=$(echo "$POSTGRES_PORT" | tr -d '\r')
DB_ENGINE=$(echo "$DB_ENGINE" | tr -d '\r')

: "${POSTGRES_HOST:=db}"
: "${POSTGRES_PORT:=5432}"

# Verifica se nc está disponível
if ! command -v nc >/dev/null 2>&1; then
    echo "Erro: 'nc' (netcat) não está instalado."
    exit 1
fi

# Aguarda PostgreSQL se DB_ENGINE for postgresql (padrão de produção)
if [[ "$DB_ENGINE" == *"postgresql"* || -z "$DB_ENGINE" ]]; then
    echo "Aguardando banco de dados..."
    echo "Aguardando PostgreSQL em $POSTGRES_HOST:$POSTGRES_PORT..."
    while ! nc -z -w 1 "$POSTGRES_HOST" "$POSTGRES_PORT"; do
        sleep 0.5
    done
    echo "Banco de dados disponível."
fi

case "$APP_ROLE" in

  web)
    echo "Aplicando migrations..."
    python manage.py migrate --noinput

    echo "Coletando arquivos estáticos..."
    python manage.py collectstatic --noinput

    echo "Ajustando permissões de mídia e estáticos..."
    mkdir -p /app/staticfiles /app/media
    chown -R root:root /app/staticfiles /app/media || true
    chmod -R 777 /app/staticfiles /app/media || true

    echo "Iniciando servidor web..."
    exec gunicorn app.wsgi:application --bind 0.0.0.0:8003
    ;;

  worker)
    echo "Iniciando Celery Worker..."
    : "${WORKER_NAME:=worker}"
    exec celery -A app worker \
        --loglevel=info \
        --concurrency=10 \
        --max-tasks-per-child=200 \
        --prefetch-multiplier=2 \
        --hostname="${WORKER_NAME}@%h"
    ;;

  beat)
    echo "Iniciando Celery Beat..."
    exec celery -A app beat --loglevel=info
    ;;

  *)
    echo "APP_ROLE inválido: '$APP_ROLE'. Use: web, worker ou beat."
    exit 1
    ;;
esac
