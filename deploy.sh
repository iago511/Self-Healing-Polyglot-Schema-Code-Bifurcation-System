#!/usr/bin/env bash

set -euo pipefail

cd /opt/sh-pscbs
: "${APP_IMAGE:?Imagem obrigatoria}"
export APP_IMAGE
docker compose config --quiet
docker compose pull
docker compose up -d --wait --wait-timeout 60


sleep 15
container=$(docker compose ps -q agent)
test -n "$container"
test "$(docker inspect -f '{{.State.Running}}' "$container")" = true
test "$(docker inspect -f '{{.RestartCount}}' "$container")" = 0
printf 'APP_IMAGE=%s\n' "$APP_IMAGE" > .deployed-image
echo "Deployment validado: $APP_IMAG"