#!/bin/sh
# Instala (ou atualiza) as três tarefas do OPS1 no crontab do usuário, sem tocar nas outras linhas dele.
# Horários de São Paulo: backup 04:30 todo dia; teste de restauração domingo 05:00; vigia a cada 2 min.
# (gravados em UTC, que é o relógio do cron desta VPS; o Brasil não tem horário de verão, então a conta é fixa: +3 h).
# Uso: sh scripts/ops/instalar_cron.sh          (instala)      sh scripts/ops/instalar_cron.sh remover
set -eu
RAIZ=/home/twadmin/gateway; LOG=/home/twadmin/backups/gateway/estado/cron.log
MARCA_I="# >>> gateway OPS1 (não editar à mão; use scripts/ops/instalar_cron.sh)"; MARCA_F="# <<< gateway OPS1"
mkdir -p "$(dirname "$LOG")"
ATUAL=$(crontab -l 2>/dev/null | awk -v i="$MARCA_I" -v f="$MARCA_F" '$0==i {pula=1} !pula {print} $0==f {pula=0}')
if [ "${1:-}" = remover ]; then printf '%s\n' "$ATUAL" | crontab -; echo "tarefas do OPS1 removidas do crontab"; exit 0; fi
{ [ -n "$ATUAL" ] && printf '%s\n' "$ATUAL"
  cat <<CRON
$MARCA_I
# O cron desta VPS roda em UTC e não aceita fuso por tarefa: 07:30 UTC = 04:30 em São Paulo; domingo 08:00 UTC = 05:00.
30 7 * * * /bin/sh $RAIZ/scripts/backup.sh >> $LOG 2>&1
0 8 * * 0 /bin/sh $RAIZ/scripts/teste_restauracao.sh >> $LOG 2>&1
*/2 * * * * /bin/sh $RAIZ/scripts/vigia.sh >> $LOG 2>&1
$MARCA_F
CRON
} | crontab -
echo "crontab atualizado:"; crontab -l | awk -v i="$MARCA_I" -v f="$MARCA_F" '$0==i {mostra=1} mostra {print} $0==f {mostra=0}'
