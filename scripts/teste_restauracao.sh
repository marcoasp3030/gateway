#!/bin/sh
# Teste de restauração semanal (OPS1): restaura o backup local mais recente num banco temporário, compara com o manifesto,
# apaga o temporário e registra. O banco de produção nunca é tocado. Uso: sh scripts/teste_restauracao.sh  (cron: domingo 05:00)
# Limite: o arquivo cifrado do destino externo só abre com a chave privada, que não fica na VPS; aqui se confere que ele
# existe lá com o tamanho registrado. A prova completa é o ensaio manual (docs/RESTAURACAO_MANUAL.md).
set -u
. "$(dirname "$0")/ops/comum.sh"
exec 9>"$ESTADO/restauracao.lock"; flock -n 9 || { registro "restauração: já há um teste em andamento"; exit 0; }
INICIO=$(date -u '+%Y-%m-%dT%H:%M:%SZ'); BANCO=gateway_restauracao_test; PASSO=inicio
apagar() { compose exec -T -e B="$BANCO" postgres sh -c 'dropdb -U "$POSTGRES_USER" --if-exists "$B"' >/dev/null 2>&1; }
falhar() {
    apagar; registro "teste de restauração FALHOU no passo $PASSO: $1"
    gravar_execucao restauracao falha "$INICIO" "{\"passo\":\"$PASSO\",\"motivo\":\"$1\",\"backup\":\"${NOME:-}\"}"
    printf '{"resultado":"falha","passo":"%s","motivo":"%s","em":"%s"}\n' "$PASSO" "$1" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$ESTADO/ultimo_teste_restauracao.json"
    sh "$RAIZ/scripts/alerta.sh" disparar restauracao critico "falha no teste de restauração" "Passo: $PASSO · $1" >/dev/null
    echo "TESTE DE RESTAURAÇÃO FALHOU no passo $PASSO: $1"; exit 1
}
trap 'falhar "interrompido"' INT TERM
case "$BANCO" in *_test) ;; *) falhar "o banco temporário precisa terminar em _test";; esac

PASSO=localizar
DIR=$(ls -1d "$BASE"/diario/*/ 2>/dev/null | sort -r | head -n 1); DIR=${DIR%/}
[ -n "$DIR" ] && [ -s "$DIR/gateway.dump" ] && [ -s "$DIR/manifesto.json" ] || falhar "não há backup local para testar"
NOME=$(basename "$DIR")
[ "$(sha256sum "$DIR/gateway.dump" | cut -d' ' -f1)" = "$(grep -o '"gateway.dump":{"bytes":[0-9]*,"sha256":"[0-9a-f]*"' "$DIR/manifesto.json" | grep -o '[0-9a-f]\{64\}')" ] || falhar "o arquivo do banco não confere com a soma do manifesto"

PASSO=restaurar
apagar
compose exec -T -e B="$BANCO" postgres sh -c 'createdb -U "$POSTGRES_USER" "$B"' >/dev/null 2>&1 || falhar "não consegui criar o banco temporário"
compose exec -T -e B="$BANCO" postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$B" --no-owner --exit-on-error' < "$DIR/gateway.dump" >/dev/null 2>&1 || falhar "o pg_restore terminou com erro"

PASSO=comparar
ESPERADO=$(tr -d '\n' < "$DIR/manifesto.json" | sed 's/.*"contagens":\({[^}]*}\).*/\1/' | tr -d ' ')
OBTIDO=$(contagens "$BANCO" | tr -d ' ') || falhar "não consegui ler o banco restaurado"
[ -n "$ESPERADO" ] && [ "$ESPERADO" = "$OBTIDO" ] || falhar "as contagens do banco restaurado não batem com o manifesto"
# Leitura de verificação: a conversa mais recente e as mensagens dela precisam estar acessíveis.
compose exec -T -e B="$BANCO" postgres sh -c 'psql -U "$POSTGRES_USER" -d "$B" -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM mensagens WHERE conversation_id=(SELECT conversation_id FROM conversas ORDER BY atualizado_em DESC LIMIT 1)"' >/dev/null 2>&1 || falhar "a leitura de verificação falhou"

PASSO=destino
EXTERNO=nao_configurado
if [ -z "$(falta_para_enviar)" ] && [ -s "$DIR/envio.json" ]; then
    ARQ=$(grep -o '"nome":"[^"]*"' "$DIR/envio.json" | cut -d'"' -f4); BYTES=$(grep -o '"bytes":[0-9]*' "$DIR/envio.json" | cut -d: -f2)
    REMOTO=$(rclone_destino lsjson "$(destino_remoto)/diario/$ARQ" 2>/dev/null | grep -o '"Size":[0-9]*' | head -n 1 | cut -d: -f2)
    [ "${REMOTO:-0}" = "$BYTES" ] || falhar "o arquivo cifrado não está no destino externo com o tamanho registrado"
    EXTERNO=conferido
fi

PASSO=apagar
apagar
compose exec -T -e B="$BANCO" postgres sh -c 'psql -U "$POSTGRES_USER" -d postgres -Atc "SELECT count(*) FROM pg_database WHERE datname='"'"'$B'"'"'"' 2>/dev/null | grep -qx 0 || falhar "o banco temporário não foi apagado"
gravar_execucao restauracao ok "$INICIO" "{\"backup\":\"$NOME\",\"contagens\":$OBTIDO,\"destino_externo\":\"$EXTERNO\"}"
printf '{"resultado":"ok","backup":"%s","destino_externo":"%s","em":"%s"}\n' "$NOME" "$EXTERNO" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$ESTADO/ultimo_teste_restauracao.json"
sh "$RAIZ/scripts/alerta.sh" normalizar restauracao "falha no teste de restauração" >/dev/null
registro "teste de restauração OK: $NOME (destino externo: $EXTERNO)"
echo "TESTE DE RESTAURAÇÃO OK: backup $NOME · contagens iguais às do manifesto · destino externo: $EXTERNO"
