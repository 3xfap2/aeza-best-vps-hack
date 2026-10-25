#!/usr/bin/env bash
# Развернуть Радар оттока на своём сервере (Ubuntu с Docker) по HTTPS:
#   PUBLIC_HOST=radar.example.com ./deploy/server.sh
# Домен должен указывать на сервер. Своего домена нет — подойдёт адрес вида 203-0-113-10.sslip.io
# (sslip.io отвечает IP из имени), сертификат Let's Encrypt Caddy получит сам.
# Первый запуск создаёт .env со случайными паролями и токенами (повторный — не трогает их, только адреса)
# и конфиг SSO под домен, затем собирает и поднимает все сервисы. Повторный запуск — обновление.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${PUBLIC_HOST:?Укажите домен сервера: PUBLIC_HOST=radar.example.com ./deploy/server.sh}"
secret() { openssl rand -base64 33 | tr -d '/+=' | cut -c1-32; }
set_env() {   # заменить или дописать KEY=VALUE в .env
  if grep -q "^$1=" .env; then sed -i "s#^$1=.*#$1=$2#" .env; else echo "$1=$2" >> .env; fi
}

if [ ! -f .env ]; then
  cat > .env <<EOF
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
set_env PUBLIC_HOST "$HOST"
set_env PUBLIC_URL "https://$HOST"
set_env OIDC_PUBLIC_ISSUER "https://$HOST/dex"

# SSO: Dex должен знать адрес, по которому его видит браузер, и куда вернуть после входа
sed -e "s#http://localhost:5556#https://$HOST#g" \
    -e "s#http://localhost:8080#https://$HOST#g" dex/config.yaml > dex/config.server.yaml

docker compose -f compose.yaml -f compose.server.yaml up -d --build --remove-orphans
docker compose -f compose.yaml -f compose.server.yaml ps
echo "Готово: https://$HOST  ·  Swagger: https://$HOST/api/v1/docs  ·  Grafana: http://$HOST:3000"
