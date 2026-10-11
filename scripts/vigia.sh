#!/bin/sh
# Vigia da VPS (OPS1), a cada 2 minutos pelo cron: funciona mesmo com o gateway fora do ar.
# Confere contêineres, disco, memória e atraso do backup, e chama scripts/alerta.sh (que cuida da repetição e do "normalizado").
set -u
. "$(dirname "$0")/ops/comum.sh"
mkdir -p "$ESTADO/vigia"
exec 9>"$ESTADO/vigia.lock"; flock -n 9 || exit 0
A="$RAIZ/scripts/alerta.sh"
# Condição que precisa se repetir em N conferências seguidas para disparar (evita alerta por um pico).
seguidas() { f="$ESTADO/vigia/$1"; if [ "$2" = sim ]; then n=$(( $(cat "$f" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$f"; [ "$n" -ge "$3" ]; else rm -f "$f"; return 1; fi; }

# 1) Contêineres: parado, reiniciando ou não saudável por 2 conferências seguidas (~2 a 4 min).
for servico in gateway worker agendador postgres redis; do
    ruim=nao; estado=""
    ids=$(compose ps -aq "$servico" 2>/dev/null)
    [ -n "$ids" ] || { ruim=sim; estado="não existe"; }
    for id in $ids; do
        s=$(docker inspect "$id" --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}sem-conferência{{end}}' 2>/dev/null || echo "desconhecido/")
        case "$s" in running/healthy|running/sem-conferência) ;; *) ruim=sim; estado="$s";; esac
    done
    if [ "$servico" = worker ] && [ "$ruim" = nao ]; then
        esperados=$(cfg WORKERS 2); vivos=$(echo "$ids" | grep -c .)
        [ "$vivos" -ge "$esperados" ] || { ruim=sim; estado="$vivos de $esperados no ar"; }
    fi
    if seguidas "servico-$servico" "$ruim" 2; then sh "$A" disparar "servico-$servico" critico "serviço parado: $servico" "Estado: $estado" >/dev/null
    elif [ "$ruim" = nao ]; then sh "$A" normalizar "servico-$servico" "serviço parado: $servico" >/dev/null; fi
done

# 2) Disco: aviso em 80%, crítico em 90%; normaliza abaixo do aviso menos 2 pontos.
uso=$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}'); aviso=$(cfg ALERTA_DISCO_AVISO 80); critico=$(cfg ALERTA_DISCO_CRITICO 90)
if [ "$uso" -ge "$critico" ]; then sh "$A" disparar disco critico "disco acima de $critico%" "Uso: $uso%" >/dev/null
elif [ "$uso" -ge "$aviso" ]; then sh "$A" disparar disco aviso "disco acima de $aviso%" "Uso: $uso%" >/dev/null
elif [ "$uso" -lt $(( aviso - 2 )) ]; then sh "$A" normalizar disco "disco acima do limite" >/dev/null; fi

# 3) Memória: disponível abaixo do limite (padrão 10%) por 3 conferências seguidas (~5 min).
pct=$(awk '/MemTotal/ {t=$2} /MemAvailable/ {d=$2} END {printf "%d", d*100/t}' /proc/meminfo); minimo=$(cfg ALERTA_MEMORIA_PCT 10)
baixa=nao; [ "$pct" -lt "$minimo" ] && baixa=sim
if seguidas memoria "$baixa" 3; then sh "$A" disparar memoria aviso "memória baixa" "Disponível: $pct%" >/dev/null
elif [ "$baixa" = nao ]; then sh "$A" normalizar memoria "memória baixa" >/dev/null; fi

# 4) Backup atrasado: nenhum backup concluído (enviado ou local) nas últimas 26 horas.
ultimo=$(cat "$ESTADO/ultimo_backup_ok" 2>/dev/null || echo 0); horas=$(( ( $(date +%s) - ultimo ) / 3600 ))
if [ "$ultimo" -eq 0 ]; then sh "$A" disparar backup-atrasado critico "backup atrasado" "Nenhum backup concluído ainda." >/dev/null
elif [ "$horas" -ge 26 ]; then sh "$A" disparar backup-atrasado critico "backup atrasado" "Último concluído há $horas h." >/dev/null
else sh "$A" normalizar backup-atrasado "backup atrasado" >/dev/null; fi
exit 0
