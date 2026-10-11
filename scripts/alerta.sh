#!/bin/sh
# Alertas pelo Telegram dos scripts da VPS (OPS1). O gateway tem os seus em app/nucleo/alertas.py, com as mesmas regras:
#   - o mesmo alerta sai no máximo uma vez a cada ALERTA_REPETIR_MIN (padrão 30 min), como lembrete enquanto durar;
#   - quando o problema acaba, sai um "normalizado" com a duração, só se havia alerta aberto;
#   - mensagem sem dado de cliente; sem bot configurado nada é enviado, e o registro local diz por quê.
# Uso: alerta.sh disparar <chave> <aviso|critico> "<título>" ["<detalhe>"]
#      alerta.sh normalizar <chave> ["<título>"]
#      alerta.sh teste
set -u
. "$(dirname "$0")/ops/comum.sh"
DIR=${ALERTA_ESTADO:-$ESTADO/alertas}
mkdir -p "$DIR"
AGORA=${ALERTA_AGORA:-$(date +%s)}
REPETIR=$(( $(cfg ALERTA_REPETIR_MIN 30) * 60 ))
arquivo() { printf '%s/%s' "$DIR" "$(printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '_')"; }
duracao() { m=$(( $1 / 60 )); if [ "$m" -lt 1 ]; then echo "menos de 1 min"; elif [ "$m" -lt 60 ]; then echo "$m min"; else printf '%s h %02d min\n' $(( m / 60 )) $(( m % 60 )); fi; }

# Envia o texto. 0 = saiu; 1 = não saiu (sem bot configurado ou falha). O token nunca vai para a linha de comando nem para o registro.
enviar() {
    registro "alerta: $(printf '%s' "$1" | head -n 1)"
    if [ -n "${ALERTA_SIMULAR:-}" ]; then printf '%s\n---\n' "$1" >> "$ALERTA_SIMULAR"; return 0; fi
    token=$(cfg TELEGRAM_BOT_TOKEN); chat=$(cfg TELEGRAM_CHAT_ID)
    if [ -z "$token" ] || [ -z "$chat" ]; then
        [ -e "$DIR/.desligado.$(date -u +%Y%m%d)" ] || { registro "alertas desligados: TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID não configurados"; : > "$DIR/.desligado.$(date -u +%Y%m%d)"; }
        return 1
    fi
    printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" | curl -fsS -m 15 -o /dev/null -K - \
        --data-urlencode "chat_id=$chat" --data-urlencode "text=$1" --data-urlencode "disable_web_page_preview=true" 2>/dev/null \
        || { registro "alerta não enviado: falha ao falar com o Telegram"; return 1; }
}

case "${1:-}" in
disparar)
    [ $# -ge 4 ] || { echo "uso: alerta.sh disparar <chave> <aviso|critico> <título> [detalhe]"; exit 2; }
    f=$(arquivo "$2"); icone="🔴"; [ "$3" = "aviso" ] && icone="🟡"; detalhe=${5:-}
    if [ ! -e "$f.inicio" ]; then
        echo "$AGORA" > "$f.inicio"; printf '%s' "$4" > "$f.titulo"; echo 0 > "$f.ultimo"
        enviar "$icone GATEWAY — $4${detalhe:+
$detalhe}
$(agora_local)" && echo "$AGORA" > "$f.ultimo"
        echo novo; exit 0
    fi
    ultimo=$(cat "$f.ultimo" 2>/dev/null || echo 0)
    if [ $(( AGORA - ultimo )) -lt "$REPETIR" ]; then echo suprimido; exit 0; fi
    enviar "$icone GATEWAY — $4${detalhe:+
$detalhe}
Continua há $(duracao $(( AGORA - $(cat "$f.inicio") ))).
$(agora_local)" && echo "$AGORA" > "$f.ultimo"
    echo lembrete ;;
normalizar)
    [ $# -ge 2 ] || { echo "uso: alerta.sh normalizar <chave> [título]"; exit 2; }
    f=$(arquivo "$2")
    [ -e "$f.inicio" ] || { echo sem_alerta; exit 0; }
    titulo=${3:-$(cat "$f.titulo" 2>/dev/null || echo "$2")}
    enviar "🟢 GATEWAY — normalizado: $titulo
Durou $(duracao $(( AGORA - $(cat "$f.inicio") ))).
$(agora_local)"
    rm -f "$f.inicio" "$f.ultimo" "$f.titulo"
    echo normalizado ;;
teste)
    if enviar "🟢 GATEWAY — teste de alerta
Se você está lendo isto, o bot e o chat estão certos.
$(agora_local)"; then echo "ALERTA DE TESTE ENVIADO"; else echo "ALERTA DE TESTE NÃO ENVIADO: confira TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env (veja $ESTADO/ops.log)"; exit 1; fi ;;
*) echo "uso: alerta.sh disparar|normalizar|teste"; exit 2 ;;
esac
