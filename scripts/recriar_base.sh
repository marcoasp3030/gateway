#!/bin/sh
# Recria SÓ o postgres e o redis (mesmos volumes), para aplicar mudança de configuração do contêiner (ex.: oom_score_adj).
# Ordem: backup conferido → para o agendador → espera a fila esvaziar o que está em processamento → para os workers → fotografa a fila
#        do Redis → recria postgres e redis → espera saudáveis → confere que a fila voltou do AOF → religa workers e agendador → confere tudo.
# O gateway (recepção) continua no ar: durante a troca as chamadas ao banco falham e o canal/varredura repõem depois.
# Uso: sh scripts/recriar_base.sh ensaio      (só confere e mostra o plano; não para nada)
#      sh scripts/recriar_base.sh executar
# Nunca faz prune. Não mexe em gateway, leitor, painel nem outros serviços da VPS.
set -eu
cd /home/twadmin/gateway
MODO=${1:-}
case "$MODO" in ensaio|executar) ;; *) echo "uso: sh scripts/recriar_base.sh ensaio|executar"; exit 2;; esac
. scripts/ops/comum.sh
PREFIXO=gw05
parar() { echo "RECRIAÇÃO INTERROMPIDA: $1"; exit 1; }
agora() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
rcli() { docker compose exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning '"$*"; }
psql_prod() { docker compose exec -T -e B="$BANCO_PRODUCAO" postgres sh -c 'psql -U "$POSTGRES_USER" -d "$B" -At -c "$0"' "$1"; }

# Itens em processamento: pendentes com algum worker (XPENDING) e turnos ainda não concluídos no banco.
em_processamento() {
    n=0
    for t in $(rcli SMEMBERS "$PREFIXO:fila:tenants"); do
        p=$(rcli XPENDING "$PREFIXO:fila:$t" workers 2>/dev/null | head -n 1)
        case "$p" in ''|*[!0-9]*) p=0;; esac
        n=$((n+p))
    done
    turnos=$(psql_prod "SELECT count(*) FROM turnos WHERE etapa IN ('processando','respondido')")
    echo $((n+turnos))
}
# Fotografia da fila no Redis: total de chaves, agendadas, na fila e o tamanho de cada fila por empresa.
fotografia() {
    f="chaves=$(rcli DBSIZE) agendadas=$(rcli ZCARD $PREFIXO:due) na_fila=$(rcli SCARD $PREFIXO:fila:na_fila)"
    for t in $(rcli SMEMBERS "$PREFIXO:fila:tenants" | sort); do f="$f $t=$(rcli XLEN $PREFIXO:fila:$t)"; done
    echo "$f"
}
oom() {
    for s in postgres redis gateway worker agendador leitor; do
        for c in $(docker compose ps -q "$s"); do
            pid=$(docker inspect "$c" --format '{{.State.Pid}}')
            echo "   $s ($(docker inspect "$c" --format '{{.Name}}' | tr -d /)): oom_score_adj=$(cat /proc/$pid/oom_score_adj) (configurado: $(docker inspect "$c" --format '{{.HostConfig.OomScoreAdj}}'))"
        done
    done
}

echo "== 1/9 conferências iniciais ($(agora))"
[ -z "$(git status --porcelain docker-compose.yml)" ] || parar "o docker-compose.yml tem alteração fora do commit"
for s in postgres redis worker agendador; do [ -n "$(docker compose ps -q $s)" ] || parar "serviço $s não está no ar"; done
echo "   em processamento agora: $(em_processamento)"
echo "   fila: $(fotografia)"
echo "   oom_score_adj atual:"; oom
if [ "$MODO" = ensaio ]; then
    echo "ENSAIO: nada foi parado. No modo executar: backup, para agendador e workers, recria postgres e redis, confere a fila e religa."
    exit 0
fi

INICIO=$(date +%s); echo "INÍCIO: $(agora)"
echo "== 2/9 backup"
DESTINO=/home/twadmin/backups/gateway/pre-recriar-base-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -m 700 "$DESTINO"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc gateway' > "$DESTINO/gateway.dump" || parar "o backup falhou (nada foi parado)"
chmod 600 "$DESTINO/gateway.dump"
TABELAS=$(docker compose exec -T postgres pg_restore --list < "$DESTINO/gateway.dump" | grep -c 'TABLE DATA' || true)
[ "${TABELAS:-0}" -gt 20 ] || parar "o backup não pôde ser lido de volta ($TABELAS tabelas); nada foi parado"
echo "   $DESTINO/gateway.dump ($TABELAS tabelas)"

echo "== 3/9 para o agendador (nada novo é distribuído)"
docker compose stop -t 30 agendador >/dev/null 2>&1

echo "== 4/9 espera o que está em processamento terminar (até 120 s)"
n=0
while :; do
    q=$(em_processamento); [ "$q" -eq 0 ] && break
    n=$((n+1)); [ "$n" -le 24 ] || { docker compose start agendador >/dev/null 2>&1; parar "ainda há $q item(ns) em processamento depois de 120 s; agendador religado, nada recriado"; }
    sleep 5
done
echo "== 5/9 para os workers"
docker compose stop -t 90 worker >/dev/null 2>&1
[ "$(em_processamento)" -eq 0 ] || { docker compose start worker agendador >/dev/null 2>&1; parar "item em processamento apareceu ao parar os workers; religados, nada recriado"; }
sleep 2   # o AOF grava a cada segundo (appendfsync everysec)
ANTES=$(fotografia); echo "   fila antes: $ANTES"

echo "== 6/9 recria postgres e redis (mesmos volumes)"
PARADA=$(date +%s)
docker compose up -d --no-deps --force-recreate -t 60 postgres redis 2>&1 | tail -n 2 | sed 's/^/   /'

echo "== 7/9 espera saudáveis (até 120 s)"
n=0
while :; do
    ok=0
    for s in postgres redis; do [ "$(docker inspect "$(docker compose ps -q $s)" --format '{{.State.Health.Status}}')" = healthy ] && ok=$((ok+1)); done
    [ "$ok" -eq 2 ] && break
    n=$((n+1)); [ "$n" -le 24 ] || parar "postgres/redis não ficaram saudáveis em 120 s (workers e agendador continuam parados; backup em $DESTINO)"
    sleep 5
done
VOLTA=$(date +%s)
DEPOIS=$(fotografia); echo "   fila depois: $DEPOIS"
[ "$ANTES" = "$DEPOIS" ] || parar "a fila do Redis não voltou igual do AOF (antes: $ANTES | depois: $DEPOIS); workers e agendador continuam parados"
[ "$(psql_prod 'SELECT max(versao) FROM schema_migrations')" -gt 0 ] || parar "o banco não respondeu"

echo "== 8/9 religa workers e agendador"
docker compose start worker agendador >/dev/null 2>&1
n=0
while :; do
    ruins=0
    for c in $(docker compose ps -q gateway worker agendador); do [ "$(docker inspect "$c" --format '{{.State.Health.Status}}')" = healthy ] || ruins=$((ruins+1)); done
    [ "$ruins" -eq 0 ] && break
    n=$((n+1)); [ "$n" -le 24 ] || parar "$ruins contêiner(es) do gateway fora do ar 2 min depois de religar"
    sleep 5
done
SAUDE=$(docker compose exec -T gateway python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=10).status)" 2>/dev/null || echo erro)
[ "$SAUDE" = 200 ] || parar "saúde do gateway: $SAUDE"
FIM=$(date +%s)

echo "== 9/9 conferência"
echo "   oom_score_adj efetivo:"; oom
ERROS=$(docker compose logs --since "$(date -u -d @$INICIO '+%Y-%m-%dT%H:%M:%S')" postgres redis gateway worker agendador 2>&1 | grep -ciE 'error|fatal|panic|traceback' || true)
echo "   linhas com erro nos logs desde o início: $ERROS"
echo "RECRIADO: início $(date -u -d @$INICIO '+%H:%M:%S') UTC · fim $(date -u -d @$FIM '+%H:%M:%S') UTC · total $((FIM-INICIO)) s · banco e Redis fora por $((VOLTA-PARADA)) s · backup: $DESTINO"
