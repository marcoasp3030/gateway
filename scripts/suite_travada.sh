#!/bin/sh
# Trava da suíte: só devolve 0 se a suíte inteira rodou e passou.
# Bloqueia (saída 1, com o motivo) em: teste falhando, erro (inclusive de coleta), teste pulado,
# nenhum teste executado, resumo ausente ou saída diferente de zero do pytest.
# Uso: sh scripts/suite_travada.sh        (a implantação usa assim, sempre a suíte inteira)
#      SUITE_ALVO=tests/arquivo.py sh scripts/suite_travada.sh   (só para provar a trava)
#      SUITE_MINIMO=252 sh scripts/implantar.sh <rotulo>         (exige pelo menos 252 testes passando)
set -u
cd /home/twadmin/gateway
REGISTRO=$(mktemp /tmp/suite-gateway-XXXXXX.log)
sh scripts/test_suite.sh >"$REGISTRO" 2>&1
CODIGO=$?
RESUMO=$(grep -E '^(=+ )?([0-9]+ (passed|failed|error|errors|skipped|xfailed|xpassed|warnings?|deselected)[, ]*)+ ?in [0-9.]+s|^(=+ )?no tests ran' "$REGISTRO" | tail -n 1)
bloquear() {
    echo "TRAVA DA SUÍTE: BLOQUEADO — $1"
    echo "  resumo: ${RESUMO:-(sem resumo do pytest)}"
    echo "  saída do pytest: $CODIGO"
    echo "  registro completo: $REGISTRO"
    grep -E '^(FAILED|ERROR|SKIPPED)' "$REGISTRO" | head -n 20 | sed 's/^/  /'
    exit 1
}
[ "$CODIGO" -eq 0 ] || bloquear "a suíte terminou com erro (saída $CODIGO)"
[ -n "$RESUMO" ] || bloquear "não há resumo do pytest (a suíte não rodou até o fim)"
PASSARAM=$(echo "$RESUMO" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || true)
[ -n "$PASSARAM" ] && [ "$PASSARAM" -gt 0 ] || bloquear "nenhum teste passou (0 testes executados)"
if echo "$RESUMO" | grep -qE '[0-9]+ (failed|errors?)'; then bloquear "há teste falhando ou com erro"; fi
if echo "$RESUMO" | grep -qE '[0-9]+ (skipped|xfailed|xpassed|deselected)'; then bloquear "há teste pulado (banco de testes indisponível?)"; fi
# SUITE_MINIMO=N: a suíte precisa ter passado em pelo menos N testes (para garantir que os testes novos do bloco rodaram).
case "${SUITE_MINIMO:-}" in ''|*[!0-9]*) ;; *) [ "$PASSARAM" -ge "$SUITE_MINIMO" ] || bloquear "passaram $PASSARAM testes; o mínimo exigido é $SUITE_MINIMO";; esac
echo "TRAVA DA SUÍTE: LIBERADO — $RESUMO"
rm -f "$REGISTRO"
exit 0
