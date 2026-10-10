#!/bin/sh
# Troca do processamento para a fila durável (Fase 0.6, bloco 5) e volta.
#   sh scripts/fila_duravel.sh estado      mostra a chave, os containers e os indicadores
#   sh scripts/fila_duravel.sh ligar       FILA_DURAVEL=on  (workers processam; recepção só recebe)
#   sh scripts/fila_duravel.sh desligar    FILA_DURAVEL=off (volta ao caminho atual)
# A troca recria recepção, workers e agendador com parada graciosa: os lotes em andamento
# terminam antes de cada processo sair. Nada é migrado nem apagado; o que estiver pendente
# é retomado pelo outro caminho (agendamento no Redis + reconciliação pelo Postgres).
set -eu
cd "$(dirname "$0")/.."
SERVICOS="gateway worker agendador"

chave() { sed -n 's/^FILA_DURAVEL=//p' .env | tail -1; }

estado() {
  echo "FILA_DURAVEL no .env: $(chave)"
  docker compose ps --format '{{.Service}} {{.Status}}' $SERVICOS
  # /health interno e indicadores; a chave do painel é lida dentro do container e não aparece.
  docker compose exec -T gateway python -c "
import json,os,urllib.request
def ler(caminho,chave=False):
    r=urllib.request.Request('http://127.0.0.1:8000'+caminho)
    if chave: r.add_header('Authorization','Bearer '+os.environ['PANEL_SERVICE_KEY']);r.add_header('X-Actor','fila_duravel.sh')
    return json.load(urllib.request.urlopen(r,timeout=5))
print('health:',json.dumps(ler('/health').get('fila'),ensure_ascii=False))
f=ler('/admin/v1/fila',True)
print('empresas com fila:',json.dumps(f['empresas'],ensure_ascii=False))
print('agentes em andamento:',json.dumps(f['agentes_em_andamento'],ensure_ascii=False))
"
  # Entradas ainda não processadas (fonte da verdade) e avisos recentes nos logs.
  docker compose exec -T gateway python -c "
import asyncio,os,asyncpg
async def m():
    c=await asyncpg.connect(os.environ['DATABASE_URL'])
    print('entradas pendentes no banco:',await c.fetchval(\"SELECT count(*) FROM mensagens WHERE processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema')\"),
          '| mais antiga (s):',await c.fetchval(\"SELECT coalesce(extract(epoch FROM now()-min(criado_em))::int,0) FROM mensagens WHERE processada_em IS NULL AND direcao='entrada' AND origem IN ('cliente','sistema')\"))
    print('turnos em aberto:',await c.fetchval(\"SELECT count(*) FROM turnos WHERE etapa IN ('processando','respondido')\"),
          '| partes incertas nas últimas 24 h:',await c.fetchval(\"SELECT count(*) FROM mensagens WHERE entrega='incerta' AND criado_em>now()-interval '24 hours'\"))
    await c.close()
asyncio.run(m())
"
  echo "avisos nos últimos 5 min: $(docker compose logs --since 5m $SERVICOS 2>/dev/null | grep -c 'WARNING\|ERROR' || true)"
}

trocar() {
  atual=$(chave)
  [ -n "$atual" ] || { echo "FILA_DURAVEL não está no .env"; exit 1; }
  if [ "$atual" = "$1" ]; then echo "Já está em $1; nada a fazer."; estado; exit 0; fi
  sed -i "s/^FILA_DURAVEL=.*/FILA_DURAVEL=$1/" .env
  echo "FILA_DURAVEL: $atual -> $1; recriando $SERVICOS (parada graciosa, até ~90 s)"
  docker compose up -d --no-deps --wait $SERVICOS
  sleep 8
  estado
}

case "${1:-}" in
  estado) estado ;;
  ligar) trocar on ;;
  desligar) trocar off ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
