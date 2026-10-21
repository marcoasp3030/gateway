#!/bin/sh
set -eu
cd /home/twadmin/gateway
# A suíte roda na imagem que está em uso; biblioteca nova do requirements (ainda fora da imagem) é instalada aqui na versão fixada.
# Só o compose base (banco bykoji_test): o COMPOSE_FILE do .env inclui o override do banco de produção.
# ARQ1: o leitor de documentos tem testes próprios, na imagem dele (bibliotecas compiladas que o contêiner do gateway não carrega
# a partir de /tmp). Rodam antes da suíte principal, sem banco e sem rede de produção; falha aqui interrompe a suíte.
docker build -q -t gateway-leitor:teste leitor >/dev/null
docker run --rm --read-only --tmpfs /tmp --user 10002 -v /home/twadmin/gateway:/workspace:ro gateway-leitor:teste sh -c 'python -m pip -q --disable-pip-version-check install --target /tmp/p pytest >/dev/null && cd /workspace && PYTHONPATH=/tmp/p:/workspace/leitor:/workspace/tests python -m pytest -q -p no:cacheprovider -rs leitor/tests' > /tmp/leitor-testes.log 2>&1 || { cat /tmp/leitor-testes.log; exit 1; }
if grep -qE '[0-9]+ (skipped|failed|error)' /tmp/leitor-testes.log; then cat /tmp/leitor-testes.log; exit 1; fi
echo "leitor: $(tail -n 1 /tmp/leitor-testes.log)"
COMPOSE_FILE=docker-compose.yml docker compose -f docker-compose.yml run --rm --no-deps -T -e SUITE_ALVO="${SUITE_ALVO:-}" -v /home/twadmin/gateway:/workspace:ro gateway sh -c 'python -m venv --system-site-packages /tmp/test-venv && python /tmp/test-venv/bin/pip -q install --target /tmp/test-packages pytest $(grep -E "^(holidays|python-dateutil|python-multipart)==" /workspace/app/requirements.txt) && cd /workspace && PYTHONPATH=/tmp/test-packages:/workspace/app:/workspace/tests python /workspace/scripts/test_suite.py'
