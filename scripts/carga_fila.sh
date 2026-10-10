#!/bin/sh
# Pilha isolada do teste de carga e de falhas da fila durável (docker-compose.carga.yml).
# Não usa o .env nem o banco/Redis da produção. Ver docs/CAPACIDADE.md.
#   scripts/carga_fila.sh construir                    imagem gateway-carga a partir de ./app
#   scripts/carga_fila.sh subir <workers> [concorrencia] [atraso_mock_s]
#   scripts/carga_fila.sh rodar [--conversas 200 --tenants 2 --rotulo nome]
#   scripts/carga_fila.sh worker-matar                 mata (SIGKILL) um worker e o mantém parado
#   scripts/carga_fila.sh redis-cair [segundos]        derruba o Redis e sobe de novo
#   scripts/carga_fila.sh estado | derrubar
set -eu
cd "$(dirname "$0")/.."
DIR="$PWD/.local/carga"
ENVF="$DIR/carga.env"
dc() { CARGA_ENV="$ENVF" CARGA_DADOS="$DIR" docker compose --env-file "$ENVF" -f docker-compose.carga.yml "$@"; }
aleatorio() { python3 -c "import secrets;print(secrets.token_hex(24))"; }

case "${1:-}" in
construir)
  docker build -q -t gateway-carga ./app >/dev/null && echo "imagem gateway-carga pronta" ;;
subir)
  mkdir -p "$DIR/redis"; umask 077
  PG=$(aleatorio); RD=$(aleatorio)
  cat > "$ENVF" <<FIM
CARGA_IMAGEM=${CARGA_IMAGEM:-gateway-carga}
CARGA_PG_SENHA=$PG
CARGA_REDIS_SENHA=$RD
WORKERS=${2:-2}
CONCORRENCIA_POR_WORKER=${3:-20}
MOCK_ATRASO_S=${4:-8}
FILA_DURAVEL=on
CARGA_ISOLADA=1
DATABASE_URL=postgresql://carga:$PG@postgres:5432/carga
REDIS_URL=redis://:$RD@redis:6379/0
PANEL_SERVICE_KEY=$(aleatorio)
API_KEY_PEPPER=$(aleatorio)
ENCRYPTION_KEY=$(python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
FIM
  dc up -d --wait postgres redis
  # Migrações explícitas, como na produção.
  dc run --rm -T --no-deps --entrypoint python carga migrar.py --apply
  dc up -d --wait gateway worker agendador
  dc ps --format '{{.Name}} {{.Status}}' ;;
rodar)
  shift; dc run --rm -T --no-deps carga "$@" ;;
worker-matar)
  alvo=$(dc ps -q worker | head -1)
  docker kill "$alvo" >/dev/null
  # Parado de propósito: quem assume é o outro worker.
  docker stop -t 0 "$alvo" >/dev/null 2>&1 || true
  echo "worker morto: $(docker inspect -f '{{.Name}}' "$alvo")" ;;
redis-cair)
  dc kill redis >/dev/null 2>&1; echo "Redis derrubado por ${2:-10} s"
  sleep "${2:-10}"; dc up -d --wait redis >/dev/null 2>&1; echo "Redis de volta" ;;
estado)
  dc ps -a --format '{{.Name}} {{.Status}}'
  docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' $(dc ps -q) ;;
derrubar)
  dc down --remove-orphans
  # Só os dados deste teste (AOF do Redis da pilha isolada).
  docker run --rm -v "$DIR":/d redis:7-alpine sh -c 'rm -rf /d/redis' ; rm -f "$ENVF" ;;
*)
  sed -n '2,10p' "$0"; exit 2 ;;
esac
