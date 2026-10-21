#!/bin/sh
# Implantação do gateway, na ordem obrigatória. Qualquer passo que falhe interrompe tudo, com mensagem.
#   1. só o que está commitado     2. suíte completa (trava)     3. imagem de volta com tag do commit em uso
#   4. backup conferido            5. build + tag do commit      6. migração com a imagem antiga no ar
#   7. troca dos serviços          8. conferência (saudáveis, mesma imagem, saúde 200)
# Uso: sh scripts/implantar.sh <rotulo-do-bloco>     ex.: sh scripts/implantar.sh cfg1a1
# Nunca faz prune. Não mexe em postgres, redis nem em outros serviços da VPS.
set -eu
cd /home/twadmin/gateway
ROTULO=${1:-}
# A implantação roda sempre a suíte inteira: o alvo reduzido só vale para provar a trava.
unset SUITE_ALVO || true
case "$ROTULO" in ''|*[!a-z0-9-]*) echo "IMPLANTAÇÃO BLOQUEADA: informe o rótulo do bloco (letras minúsculas, números e hífen)"; exit 1;; esac
parar() { echo "IMPLANTAÇÃO BLOQUEADA: $1"; exit 1; }

echo "== 1/8 árvore do git"
[ -z "$(git status --porcelain)" ] || { git status --short; parar "há arquivos alterados ou novos fora do commit"; }
COMMIT=$(git rev-parse --short HEAD)

echo "== 2/8 suíte completa"
if ! sh scripts/suite_travada.sh; then parar "a suíte não passou; nada foi construído nem implantado"; fi
[ -z "$(git status --porcelain)" ] || parar "a árvore mudou durante a suíte"
[ "$COMMIT" = "$(git rev-parse --short HEAD)" ] || parar "o commit mudou durante a suíte"

echo "== 3/8 imagem de volta"
EM_USO=""
for c in $(docker compose ps -q gateway worker agendador); do
    i=$(docker inspect "$c" --format '{{.Image}}')
    [ -z "$EM_USO" ] || [ "$EM_USO" = "$i" ] || parar "os serviços não estão todos na mesma imagem"
    EM_USO=$i
done
[ -n "$EM_USO" ] || parar "não achei os contêineres em execução"
VOLTA=$(docker images --no-trunc --format '{{.Repository}}:{{.Tag}} {{.ID}}' | awk -v id="$EM_USO" '$2==id && $1 ~ /^gateway:[0-9a-f]+$/ {print $1; exit}')
[ -n "$VOLTA" ] || parar "a imagem em uso não tem tag gateway:<commit>; crie a tag antes de construir"
echo "   volta: $VOLTA"
[ "$VOLTA" != "gateway:$COMMIT" ] || parar "o commit $COMMIT já é o que está em produção"
# ARQ1: leitor de documentos (serviço próprio). Se já existe, a imagem em uso ganha a tag de volta.
LEITOR_VOLTA=""
LEITOR_ATUAL=$(docker compose ps -q leitor 2>/dev/null | head -n 1)
if [ -n "$LEITOR_ATUAL" ]; then
    LEITOR_VOLTA="gateway-leitor:volta-$COMMIT"
    docker tag "$(docker inspect "$LEITOR_ATUAL" --format '{{.Image}}')" "$LEITOR_VOLTA"
    echo "   volta do leitor: $LEITOR_VOLTA"
fi

echo "== 4/8 backup"
DESTINO=/home/twadmin/backups/gateway/pre-$ROTULO-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -m 700 "$DESTINO"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc gateway' > "$DESTINO/gateway.dump" || parar "o backup falhou"
chmod 600 "$DESTINO/gateway.dump"
TABELAS=$(docker compose exec -T postgres pg_restore --list < "$DESTINO/gateway.dump" | grep -c 'TABLE DATA' || true)
[ "${TABELAS:-0}" -gt 20 ] || parar "o backup não pôde ser lido de volta ($TABELAS tabelas)"
echo "   $DESTINO/gateway.dump ($TABELAS tabelas)"

echo "== 5/8 build"
REG_BUILD=$(mktemp /tmp/build-gateway-XXXXXX.log)
docker compose build -q gateway leitor >"$REG_BUILD" 2>&1 || { tail -n 20 "$REG_BUILD"; parar "o build falhou; os serviços continuam na imagem anterior"; }
rm -f "$REG_BUILD"
docker tag gateway-gateway:latest "gateway:$COMMIT"
docker tag gateway-leitor:latest "gateway-leitor:$COMMIT"
NOVA=$(docker image inspect "gateway:$COMMIT" --format '{{.Id}}')
[ "$NOVA" != "$EM_USO" ] || echo "   aviso: a imagem nova é idêntica à que está em uso"

echo "== 6/8 migração com a imagem antiga no ar"
MIGRACAO=$(docker compose run --rm --no-deps --pull never -T gateway python /app/migrar.py --apply 2>&1) || { echo "$MIGRACAO" | tail -n 20; parar "a migração falhou; os serviços continuam na imagem anterior"; }
echo "$MIGRACAO" | tail -n 2 | sed 's/^/   /'
SAUDE=$(docker compose exec -T gateway python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=10).status)" 2>/dev/null || echo erro)
[ "$SAUDE" = "200" ] || parar "a imagem antiga não respondeu saúde 200 depois da migração (ficou: $SAUDE). Os serviços NÃO foram trocados"

echo "== 7/8 troca dos serviços"
# Imagem esperada pelo vigia (scripts/vigia.sh): contêiner do gateway em outra imagem = implantação por fora, com alerta.
ESPERADA=/home/twadmin/backups/gateway/estado/imagem_implantada
printf '%s gateway:%s\n' "$NOVA" "$COMMIT" > "$ESPERADA"
docker compose up -d --no-deps leitor gateway worker agendador 2>&1 | tail -n 2

echo "== 8/8 conferência"
n=0
while :; do
    n=$((n+1)); sleep 5
    RUINS=0
    for c in $(docker compose ps -q gateway worker agendador); do
        [ "$(docker inspect "$c" --format '{{.State.Health.Status}} {{.Image}}')" = "healthy $NOVA" ] || RUINS=$((RUINS+1))
    done
    [ "$(docker inspect "$(docker compose ps -q leitor | head -n 1)" --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] || RUINS=$((RUINS+1))
    [ "$RUINS" -eq 0 ] && break
    [ "$n" -lt 24 ] || parar "depois de 2 minutos há $RUINS contêiner(es) fora do ar ou na imagem errada. Para voltar: docker tag $VOLTA gateway-gateway:latest && docker compose up -d --no-deps gateway worker agendador && echo \"$EM_USO $VOLTA\" > $ESPERADA"
done
echo "IMPLANTADO: gateway:$COMMIT nos $(docker compose ps -q gateway worker agendador | wc -l) contêineres e gateway-leitor:$COMMIT, todos saudáveis"
echo "   volta: $VOLTA | backup: $DESTINO/gateway.dump"
