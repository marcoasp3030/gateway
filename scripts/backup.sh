#!/bin/sh
# Backup diário (OPS1): Postgres + snapshot do Redis + documentos (ARQ1, já cifrados com AES) + manifesto; cifrado com a chave
# pública e enviado ao destino externo. A chave mestra dos documentos (ARQUIVOS_CHAVE_MESTRA) NUNCA vai no mesmo pacote: a cópia do
# .env dentro dele sai sem essa linha (decisão do Marco, 21/10/2026); a chave fica guardada à parte, com o Marco.
# Sem destino configurado, faz só o backup local e registra "local" (o /health/ops fica 503 até o destino existir).
# Nunca envia nada sem cifrar. Uso: sh scripts/backup.sh   (cron diário 04:30, America/Sao_Paulo)
set -u
. "$(dirname "$0")/ops/comum.sh"
mkdir -p "$BASE/diario" "$ESTADO"
exec 9>"$ESTADO/backup.lock"; flock -n 9 || { registro "backup: já há um em andamento; saindo"; exit 0; }
INICIO=$(date -u '+%Y-%m-%dT%H:%M:%SZ'); TS=$(date -u '+%Y%m%dT%H%M%SZ'); DIR="$BASE/diario/$TS"; TMP=$(mktemp -d "$ESTADO/tmp.XXXXXX")
PASSO=inicio
falhar() {
    registro "backup FALHOU no passo $PASSO: $1"
    rm -rf "$TMP"
    # Falha ao cifrar ou enviar: a cópia local já está completa e fica guardada. Antes disso, não deixa pasta pela metade.
    case "$PASSO" in cifrar|enviar|limpeza) ;; *) rm -rf "$DIR";; esac
    gravar_execucao backup falha "$INICIO" "{\"passo\":\"$PASSO\",\"motivo\":\"$1\"}"
    printf '{"resultado":"falha","passo":"%s","motivo":"%s","em":"%s"}\n' "$PASSO" "$1" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$ESTADO/ultimo_backup.json"
    sh "$RAIZ/scripts/alerta.sh" disparar backup critico "falha de backup" "Passo: $PASSO · $1" >/dev/null
    echo "BACKUP FALHOU no passo $PASSO: $1"; exit 1
}
trap 'falhar "interrompido"' INT TERM
mkdir -p "$DIR" || falhar "não consegui criar a pasta do backup"

PASSO=postgres
ANTES=$(contagens "") || falhar "não consegui ler o banco"
tentativa=0
while :; do
    tentativa=$((tentativa+1))
    compose exec -T -e OPS_BANCO="$BANCO_PRODUCAO" postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$OPS_BANCO"' > "$DIR/gateway.dump" 2>"$TMP/pg.err" || falhar "pg_dump terminou com erro"
    DEPOIS=$(contagens "") || falhar "não consegui ler o banco"
    # Contagem igual antes e depois = o manifesto descreve exatamente o que está no arquivo.
    [ "$ANTES" = "$DEPOIS" ] && { EXATAS=true; break; }
    [ "$tentativa" -ge 3 ] && { EXATAS=false; break; }
    ANTES=$DEPOIS
done
[ -s "$DIR/gateway.dump" ] || falhar "o arquivo do banco saiu vazio"
N_TABELAS=$(compose exec -T postgres pg_restore --list < "$DIR/gateway.dump" 2>/dev/null | grep -c 'TABLE DATA')
[ "${N_TABELAS:-0}" -gt 20 ] || falhar "o arquivo do banco não pôde ser lido de volta"

PASSO=redis
compose exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --rdb /data/.backup.rdb >/dev/null 2>&1 && cat /data/.backup.rdb; r=$?; rm -f /data/.backup.rdb; exit $r' > "$DIR/redis.rdb" 2>/dev/null || falhar "o snapshot do Redis falhou"
[ "$(head -c 5 "$DIR/redis.rdb")" = "REDIS" ] || falhar "o snapshot do Redis não é válido"

PASSO=documentos
# Volume `arquivos` (ARQ1): os documentos já estão cifrados (AES-256-GCM, chave por documento); sem a chave mestra não se lê nada.
compose exec -T gateway tar -cf - -C /dados arquivos > "$DIR/arquivos.tar" 2>"$TMP/tar.err" || falhar "não consegui copiar o volume dos documentos"
tar -tf "$DIR/arquivos.tar" >/dev/null 2>&1 || falhar "a cópia dos documentos não pôde ser lida de volta"
N_DOCS=$(tar -tf "$DIR/arquivos.tar" | grep -c '/anx_[0-9a-f]*$' || true)

PASSO=manifesto
# Cópia do .env para o pacote cifrado, SEM a chave mestra dos documentos.
grep -v '^[[:space:]]*ARQUIVOS_CHAVE_MESTRA=' "$ARQ_ENV" > "$TMP/.env"; chmod 600 "$TMP/.env"
SHA_DUMP=$(sha256sum "$DIR/gateway.dump" | cut -d' ' -f1); SHA_RDB=$(sha256sum "$DIR/redis.rdb" | cut -d' ' -f1); SHA_ENV=$(sha256sum "$TMP/.env" | cut -d' ' -f1)
SHA_DOCS=$(sha256sum "$DIR/arquivos.tar" | cut -d' ' -f1)
cat > "$DIR/manifesto.json" <<JSON
{"feito_em":"$INICIO","commit":"$(cd "$RAIZ" && git rev-parse --short HEAD 2>/dev/null)","contagens_exatas":$EXATAS,
 "contagens":$DEPOIS,"contagens_antes":$ANTES,"tabelas_com_dados":$N_TABELAS,
 "arquivos":{"gateway.dump":{"bytes":$(stat -c %s "$DIR/gateway.dump"),"sha256":"$SHA_DUMP"},
             "redis.rdb":{"bytes":$(stat -c %s "$DIR/redis.rdb"),"sha256":"$SHA_RDB"},
             "arquivos.tar":{"bytes":$(stat -c %s "$DIR/arquivos.tar"),"sha256":"$SHA_DOCS","documentos":$N_DOCS},
             "env":{"bytes":$(stat -c %s "$TMP/.env"),"sha256":"$SHA_ENV","sem":"ARQUIVOS_CHAVE_MESTRA"}}}
JSON
chmod 600 "$DIR"/*; chmod 700 "$DIR"

RESULTADO=local; ENVIO='null'; FALTA=$(falta_para_enviar)
if [ -z "$FALTA" ]; then
    PASSO=cifrar
    NOME="gateway-backup-$TS.tar.gpg"; GNUPGHOME="$TMP/gnupg"; mkdir -m 700 "$GNUPGHOME"; export GNUPGHOME
    # O .env vai dentro do pacote cifrado (sem ele o banco restaurado não lê as credenciais guardadas).
    tar -cf - -C "$DIR" gateway.dump redis.rdb arquivos.tar manifesto.json -C "$TMP" .env 2>/dev/null \
        | gpg --batch --yes --no-tty --quiet --trust-model always --recipient-file "$(cfg BACKUP_CHAVE_PUBLICA)" --encrypt --output "$TMP/$NOME" 2>"$TMP/gpg.err" \
        || falhar "não consegui cifrar (confira a chave pública)"
    [ -s "$TMP/$NOME" ] || falhar "o arquivo cifrado saiu vazio"
    BYTES=$(stat -c %s "$TMP/$NOME"); SHA_CIF=$(sha256sum "$TMP/$NOME" | cut -d' ' -f1)
    PASSO=enviar
    PASTAS="diario"
    [ "$(TZ=America/Sao_Paulo date +%u)" = "7" ] && PASTAS="$PASTAS semanal"
    [ "$(TZ=America/Sao_Paulo date +%d)" = "01" ] && PASTAS="$PASTAS mensal"
    for pasta in $PASTAS; do
        rclone_destino copyto "$TMP/$NOME" "$(destino_remoto)/$pasta/$NOME" --retries 3 --low-level-retries 5 >"$TMP/rclone.err" 2>&1 || falhar "o envio para $pasta/ falhou"
        # Conferência no destino: tamanho igual e soma igual (o rclone compara as somas quando o destino as informa).
        REMOTO=$(rclone_destino lsjson "$(destino_remoto)/$pasta/$NOME" 2>/dev/null | grep -o '"Size":[0-9]*' | head -n 1 | cut -d: -f2)
        [ "${REMOTO:-0}" = "$BYTES" ] || falhar "o arquivo em $pasta/ ficou com tamanho diferente"
        rclone_destino check "$TMP" "$(destino_remoto)/$pasta" --one-way --include "$NOME" >"$TMP/check.err" 2>&1 || falhar "a conferência do arquivo em $pasta/ falhou"
    done
    RESULTADO=ok; ENVIO="{\"nome\":\"$NOME\",\"bytes\":$BYTES,\"sha256\":\"$SHA_CIF\",\"pastas\":\"$PASTAS\"}"
    printf '%s\n' "$ENVIO" > "$DIR/envio.json"; chmod 600 "$DIR/envio.json"
else
    registro "backup: envio DESLIGADO, só cópia local. Falta configurar: $FALTA"
fi

PASSO=limpeza
# Na VPS ficam os 3 backups diários mais recentes (sem cifrar, permissão 600).
ls -1d "$BASE"/diario/*/ 2>/dev/null | sort -r | tail -n +4 | while read -r velho; do rm -rf "$velho"; done
# Backups de implantação: apaga os de mais de 30 dias, mas mantém sempre os 5 mais recentes.
ls -1dt "$BASE"/pre-*/ 2>/dev/null | tail -n +6 | while read -r velho; do
    [ -n "$(find "$velho" -maxdepth 0 -mtime +30 2>/dev/null)" ] && rm -rf "$velho"
done
rm -rf "$TMP"

DETALHE="{\"pasta\":\"$TS\",\"bytes\":$(stat -c %s "$DIR/gateway.dump"),\"contagens_exatas\":$EXATAS,\"envio\":$ENVIO,\"falta\":\"$FALTA\"}"
gravar_execucao backup "$RESULTADO" "$INICIO" "$DETALHE"
printf '{"resultado":"%s","pasta":"%s","em":"%s","falta":"%s"}\n' "$RESULTADO" "$TS" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$FALTA" > "$ESTADO/ultimo_backup.json"
date +%s > "$ESTADO/ultimo_backup_ok"
sh "$RAIZ/scripts/alerta.sh" normalizar backup "falha de backup" >/dev/null
if [ "$RESULTADO" = ok ]; then registro "backup OK: $TS enviado ($PASTAS)"; echo "BACKUP OK: $DIR · enviado para: $PASTAS"
else registro "backup LOCAL: $TS (envio desligado)"; echo "BACKUP LOCAL: $DIR · ENVIO DESLIGADO, falta configurar: $FALTA"; fi
