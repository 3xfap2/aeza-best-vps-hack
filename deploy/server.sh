#!/usr/bin/env bash
# Развернуть Радар оттока на своём сервере (Ubuntu с Docker):
#   PUBLIC_HOST=203.0.113.10 ./deploy/server.sh
# Первый запуск создаёт .env со случайными паролями и токенами (повторный — не трогает его)
# и конфиг SSO под адрес сервера, затем собирает и поднимает все сервисы. Повторный запуск — обновление.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${PUBLIC_HOST:?Укажите адрес сервера: PUBLIC_HOST=1.2.3.4 ./deploy/server.sh}"
secret() { openssl rand -base64 33 | tr -d '/+=' | cut -c1-32; }

if [ ! -f .env ]; then
  cat > .env <<EOF
PUBLIC_URL=http://$HOST
OIDC_PUBLIC_ISSUER=http://$HOST:5556/dex
ADMIN_LOGIN=admin
ADMIN_PASSWORD=$(secret)
METRICS_TOKEN=$(secret)
INGEST_TOKEN=$(secret)
DB_PASSWORD=$(secret)
OIDC_CLIENT_SECRET=$(secret)
GRAFANA_PASSWORD=$(secret)
EOF
  chmod 600 .env
  echo "Создан .env со случайными секретами — пароль администратора: grep ADMIN_PASSWORD .env"
fi

# SSO: Dex должен знать адрес, по которому его видит браузер, и куда вернуть после входа
sed -e "s#http://localhost:5556#http://$HOST:5556#g" \
    -e "s#http://localhost:8080#http://$HOST#g" dex/config.yaml > dex/config.server.yaml

docker compose -f compose.yaml -f compose.server.yaml up -d --build
docker compose -f compose.yaml -f compose.server.yaml ps
echo "Готово: http://$HOST  ·  Swagger: http://$HOST/api/v1/docs  ·  Grafana: http://$HOST:3000"
