#!/bin/sh
set -eu
cd /home/twadmin/gateway
# A suíte roda na imagem que está em uso; biblioteca nova do requirements (ainda fora da imagem) é instalada aqui na versão fixada.
# Só o compose base (banco bykoji_test): o COMPOSE_FILE do .env inclui o override do banco de produção.
COMPOSE_FILE=docker-compose.yml docker compose -f docker-compose.yml run --rm --no-deps -T -e SUITE_ALVO="${SUITE_ALVO:-}" -v /home/twadmin/gateway:/workspace:ro gateway sh -c 'python -m venv --system-site-packages /tmp/test-venv && python /tmp/test-venv/bin/pip -q install --target /tmp/test-packages pytest $(grep -E "^(holidays|python-dateutil)==" /workspace/app/requirements.txt) && cd /workspace && PYTHONPATH=/tmp/test-packages:/workspace/app:/workspace/tests python /workspace/scripts/test_suite.py'
