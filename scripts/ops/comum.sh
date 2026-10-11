# Funções comuns dos scripts de operação (OPS1). Usar com: . "$(dirname "$0")/ops/comum.sh"
# Segredos e destinos vêm do .env da VPS (nunca do repositório). Nada aqui imprime valor de variável.
RAIZ=${GATEWAY_RAIZ:-/home/twadmin/gateway}
ARQ_ENV=${OPS_ENV:-$RAIZ/.env}
BASE=${BACKUP_BASE:-/home/twadmin/backups/gateway}
ESTADO=${OPS_ESTADO:-$BASE/estado}
PATH=/home/twadmin/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH
export PATH
umask 077
# Banco de produção. O POSTGRES_DB do contêiner é o banco de testes do compose base; por isso o nome é explícito.
BANCO_PRODUCAO=${BACKUP_BANCO:-gateway}

# Valor de uma variável do .env (última ocorrência, sem aspas em volta); vazio se não existir.
cfg() {
    [ -r "$ARQ_ENV" ] || { printf '%s' "${2:-}"; return 0; }
    v=$(grep -E "^[[:space:]]*$1=" "$ARQ_ENV" | tail -n 1 | cut -d= -f2- | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/")
    printf '%s' "${v:-${2:-}}"
}

agora_local() { TZ=America/Sao_Paulo date '+%d/%m %H:%M'; }
registro() { mkdir -p "$ESTADO"; printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$ESTADO/ops.log"; }
compose() { ( cd "$RAIZ" && docker compose "$@" ); }

# Grava a execução na tabela ops_execucoes (lida pelo /health/ops). Falha aqui não muda o resultado do script.
# uso: gravar_execucao <backup|restauracao> <ok|local|falha> <inicio ISO> <detalhe JSON>
gravar_execucao() {
    # Ensaio (pasta de backup alternativa): não escreve no registro de produção.
    [ -n "${OPS_SEM_REGISTRO:-}" ] && return 0
    printf '%s' "$4" | compose exec -T -e OPS_BANCO="$BANCO_PRODUCAO" -e OPS_TIPO="$1" -e OPS_RESULTADO="$2" -e OPS_INICIO="$3" postgres sh -c '
        D=$(cat); psql -U "$POSTGRES_USER" -d "$OPS_BANCO" -q -v ON_ERROR_STOP=1 -v tipo="$OPS_TIPO" -v res="$OPS_RESULTADO" -v ini="$OPS_INICIO" -v det="$D" <<SQL
INSERT INTO ops_execucoes(tipo,resultado,inicio,detalhe) VALUES (:'"'"'tipo'"'"',:'"'"'res'"'"',:'"'"'ini'"'"'::timestamptz,:'"'"'det'"'"'::jsonb);
SQL' >/dev/null 2>&1 || registro "aviso: execução não gravada em ops_execucoes ($1/$2)"
}

# Contagem das tabelas principais como JSON, numa leitura só. $1 = nome do banco.
SQL_CONTAGENS="SELECT json_build_object('esquema',(SELECT max(versao) FROM schema_migrations),'tabelas',(SELECT count(*) FROM information_schema.tables WHERE table_schema='public'),'tenants',(SELECT count(*) FROM tenants),'agentes',(SELECT count(*) FROM agentes),'tenant_canais',(SELECT count(*) FROM tenant_canais),'conversas',(SELECT count(*) FROM conversas),'mensagens',(SELECT count(*) FROM mensagens),'turnos',(SELECT count(*) FROM turnos),'pedidos',(SELECT count(*) FROM pedidos),'contatos',(SELECT count(*) FROM contatos))"
contagens() {
    compose exec -T -e OPS_BANCO="${1:-$BANCO_PRODUCAO}" -e OPS_SQL="$SQL_CONTAGENS" postgres sh -c 'psql -U "$POSTGRES_USER" -d "$OPS_BANCO" -At -v ON_ERROR_STOP=1 -c "$OPS_SQL"'
}

# Destino externo configurado? Devolve a lista do que falta (vazia = pronto para enviar).
falta_para_enviar() {
    f=""
    c=$(cfg BACKUP_CHAVE_PUBLICA); { [ -n "$c" ] && [ -r "$c" ]; } || f="$f BACKUP_CHAVE_PUBLICA"
    # BACKUP_RCLONE_REMOTO (um destino do rclone já pronto, ex.: uma pasta) existe só para ensaiar o envio sem o serviço externo.
    if [ -z "$(cfg BACKUP_RCLONE_REMOTO)" ]; then
        for v in BACKUP_S3_ENDPOINT BACKUP_S3_BUCKET BACKUP_S3_ACCESS_KEY BACKUP_S3_SECRET_KEY; do [ -n "$(cfg $v)" ] || f="$f $v"; done
    fi
    command -v rclone >/dev/null 2>&1 || f="$f rclone"
    printf '%s' "${f# }"
}

# rclone com o destino montado só por variáveis de ambiente: nenhuma credencial fica em arquivo de configuração nem na linha de comando.
rclone_destino() {
    RCLONE_CONFIG=/dev/null RCLONE_CONFIG_GWBACKUP_TYPE=s3 RCLONE_CONFIG_GWBACKUP_PROVIDER=Other \
    RCLONE_CONFIG_GWBACKUP_ENDPOINT="$(cfg BACKUP_S3_ENDPOINT)" RCLONE_CONFIG_GWBACKUP_REGION="$(cfg BACKUP_S3_REGIAO)" \
    RCLONE_CONFIG_GWBACKUP_ACCESS_KEY_ID="$(cfg BACKUP_S3_ACCESS_KEY)" RCLONE_CONFIG_GWBACKUP_SECRET_ACCESS_KEY="$(cfg BACKUP_S3_SECRET_KEY)" \
    RCLONE_CONFIG_GWBACKUP_NO_CHECK_BUCKET=true rclone "$@"
}
destino_remoto() {
    r=$(cfg BACKUP_RCLONE_REMOTO); [ -n "$r" ] && { printf '%s' "$r"; return 0; }
    p=$(cfg BACKUP_S3_PREFIXO); printf 'gwbackup:%s%s' "$(cfg BACKUP_S3_BUCKET)" "${p:+/${p#/}}"
}
