# Capacidade da fila durável (Fase 0.6)

Processos (mesma imagem, `docker-compose.yml`):

| Serviço | Réplicas | Limites | Papel |
|---|---|---|---|
| `gateway` (recepção/API) | 1 | 192 MB, 0,5 CPU | Recebe, valida, grava e agenda. Com `FILA_DURAVEL=off` também processa (caminho atual). |
| `worker` | `WORKERS` (padrão 2) | 256 MB, 0,5 CPU cada | Consome as filas por empresa, chama o agente e envia. Cada um atende até `CONCORRENCIA_POR_WORKER` conversas ao mesmo tempo (padrão 20). |
| `agendador` | 1 (trava de liderança no Redis) | 128 MB, 0,25 CPU | Debounce → fila, reconciliação com o Postgres, webhooks de saída, limpeza, indicadores e alertas. |

Capacidade nominal: `WORKERS × CONCORRENCIA_POR_WORKER` conversas em andamento (2 × 20 = 40). O Skyone processa chamadas em paralelo (10/10 simultâneas, lote em 7,89 s) e o gateway limita a 240 chamadas/min por URL; com respostas de 6–15 s, 40 em andamento cabem nessa cota.

## Como aumentar

1. Conferir os indicadores antes: `GET /admin/v1/fila` (aguardando por empresa, mensagem mais antiga, agentes em andamento, uso da cota por URL) e o `/health` interno. Se a fila espera muito e a cota está folgada, falta worker; se a cota passa de 80%, mais workers não ajudam (o limite é por URL).
2. Conferir a folga da VPS: `free -m` (memória disponível) e `docker stats --no-stream` (uso real dos containers). Cada worker a mais reserva até 256 MB.
3. Ajustar no `.env`: `WORKERS=3` (réplicas) e/ou `CONCORRENCIA_POR_WORKER` (1–200).
4. Aplicar só nos serviços da fila: `docker compose up -d --no-deps worker` (réplicas) ou recriar `worker` (concorrência). A recepção não precisa reiniciar.
5. Repetir o teste de carga (`scripts/bench.py`, ver `docs/API_ADMIN.md`) e registrar no `PROGRESS.md` a mediana e o máximo de espera na fila, sem perda nem duplicidade.

## Alertas

- **Fila atrasada:** mensagem mais antiga esperando na fila de uma empresa por mais de 30 s → evento `fila.atrasada` na auditoria da empresa (agrupado a cada 10 min) e log.
- **Cota alta:** uso da cota de uma URL do Skyone acima de 80% por 5 minutos seguidos → evento `skyone.cota_alta` na auditoria da plataforma (só hash da URL, agrupado a cada 30 min) e log.
