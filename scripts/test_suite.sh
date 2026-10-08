#!/bin/sh
set -eu
cd /home/twadmin/gateway
docker compose run --rm --no-deps -v /home/twadmin/gateway:/workspace:ro gateway sh -c 'python -m venv --system-site-packages /tmp/test-venv && python /tmp/test-venv/bin/pip -q install --target /tmp/test-packages pytest && cd /workspace && PYTHONPATH=/tmp/test-packages:/workspace/app:/workspace/tests python /workspace/scripts/test_suite.py'
