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
5. Repetir o teste de carga na pilha isolada (`scripts/carga_fila.sh`, abaixo) e registrar no `PROGRESS.md` a mediana e o máximo de espera na fila, sem perda nem duplicidade.

## Limite por empresa e por agente

Regra: **limite por empresa ≈ cota do fluxo Skyone/min × duração média da chamada em minutos.**

Exemplo (By Koji): 240 chamadas/min × (8 s ÷ 60) ≈ 32 → `max_lotes_simultaneos = 30`. Acima disso as conversas a mais só esperariam a cota.

- `max_lotes_simultaneos` (por empresa, padrão 5): conversas em andamento somando todos os agentes da empresa. Ajuste: `PUT /admin/v1/tenants/{t}/config` com `{"max_lotes_simultaneos": N}` (1–100; nulo volta ao padrão).
- `max_concorrencia` (por agente, padrão 5): conversas em andamento de um agente. Ajuste: `PATCH /admin/v1/tenants/{t}/agentes/{a}` com `{"max_concorrencia": N}`, ou no `/config` da empresa para valer como padrão dos agentes dela.
- Os dois valem ao mesmo tempo. Como a cota do Skyone é por URL (por fluxo, isto é, por agente), o agente só chega perto do teto da empresa se o `max_concorrencia` dele também subir.
- Em 10/10/2026: bykoji com `max_lotes_simultaneos = 30` e o agente “Atendimento By Koji” (`bykoji_atendimento`, um agente e um fluxo Skyone próprio) com `max_concorrencia = 30`. Demais empresas e agentes no padrão 5 e 5 (inclusive o agente `[teste]` de bench do bykoji).
- A capacidade dos workers (`WORKERS × CONCORRENCIA_POR_WORKER`, hoje 40) é o teto de tudo somado.

## Alertas

- **Fila atrasada:** mensagem mais antiga esperando na fila de uma empresa por mais de 30 s → evento `fila.atrasada` na auditoria da empresa (agrupado a cada 10 min) e log.
- **Cota alta:** uso da cota de uma URL do Skyone acima de 80% por 5 minutos seguidos → evento `skyone.cota_alta` na auditoria da plataforma (só hash da URL, agrupado a cada 30 min) e log.

## Teste de carga (10/10/2026, imagem `gateway:7423fac`)

Pilha **isolada** nesta mesma VPS (`docker-compose.carga.yml`): mesma imagem e mesmos limites de memória/CPU da produção, banco e Redis próprios, fila durável ligada só nela, agente **simulado** respondendo em 8 s (`MOCK_ATRASO_S=8`), nenhuma chamada ao Skyone nem ao canal. 200 conversas disparadas ao mesmo tempo em 2 empresas (100 cada), debounce de 1 s, limites por empresa/agente no teto (100) para que o limite fosse a capacidade dos workers.

| Workers × conversas por worker | Espera na fila: mediana | p95 | máxima | Até a resposta: mediana | máxima | Tudo respondido em | Perdidas | Duplicadas |
|---|---|---|---|---|---|---|---|---|
| 2 × 20 (padrão) | 16,9 s | 31,8 s | 32,8 s | 25,9 s | 41,9 s | 48,9 s | 0 | 0 |
| 3 × 20 | 10,2 s | 23,4 s | 23,9 s | 19,5 s | 32,9 s | 40,1 s | 0 | 0 |
| 2 × 50 | 4,9 s | 11,2 s | 11,6 s | 14,8 s | 21,7 s | 24,8 s | 0 | 0 |
| 2 × 100 | 2,2 s | 3,1 s | 3,3 s | 12,2 s | 14,2 s | 20,1 s | 0 | 0 |

“Espera na fila” = do fim do debounce até um worker começar a conversa (`turnos.espera_ms` menos o debounce). A recepção aceitou as 200 mensagens em 4–5 s.

Leitura:

- O limite medido **não é a máquina**: durante as rodadas cada worker ficou em ~47 MB (de 256 MB) e CPU perto de zero, porque o tempo é quase todo espera pelo agente. O que limita é o número de vagas (`WORKERS × CONCORRENCIA_POR_WORKER`): 200 conversas com 40 vagas e agente de 8 s saem em 5 levas.
- Um terceiro worker corta a espera máxima de ~33 s para ~24 s e custa mais ~50 MB reais (256 MB reservados). Aumentar `CONCORRENCIA_POR_WORKER` rende mais e não custa memória; com 100 por worker o agente simulado passou de 8,0 para 8,7 s de mediana (primeiro sinal de disputa de CPU e de conexões com o banco, 5 por worker).
- Com o Skyone real há outro teto, que este teste não exercita: 240 chamadas/min por URL de fluxo. 200 conversas no mesmo fluxo levam pelo menos ~50 s por causa da cota, com qualquer número de workers.
- Em produção valem também os limites por empresa e por agente (padrão 5 e 5): uma empresa sozinha não ocupa mais que 5 vagas.

## Teste de falhas (INV14), mesma pilha isolada

| Falha no meio das 200 conversas | Resultado |
|---|---|
| Redis morto (SIGKILL) por 10 s durante o processamento | 200/200 respondidas, 0 perdidas, 0 duplicadas, 0 partes incertas; terminou em 54,0 s (48,9 s sem falha). |
| Redis morto por 10 s durante a recepção | A recepção devolve erro enquanto o Redis está fora; reenviando a mesma mensagem (mesmo `message_id`, como faz um provedor) ficaram exatamente 200 entradas: 0 perdidas, 0 duplicadas; terminou em 62,5 s. |
| Redis morto por 10 s com o limite padrão de 5 por empresa (40 conversas) | Antes da correção deste bloco a empresa ficava parada 60–90 s (vagas presas); depois, 46–52 s no total (36,8 s sem falha). 0 perdidas, 0 duplicadas. |
| Um worker morto (SIGKILL) e mantido parado | O outro worker seguiu sozinho: 180/200 em 74 s. As 20 conversas que estavam com o worker morto (agente no meio da resposta, nada enviado ao cliente) foram refeitas e respondidas em ~170–180 s. 0 perdidas, 0 duplicadas, 0 partes incertas. |

O tempo de ~3 min no caso do worker morto vem da validade da trava da conversa (`timeout_s + 90 s` = 150 s) mais a reconciliação (a cada 60 s). Não foi encurtado de propósito: assumir a trava de um worker “que parece morto” abriria a chance de responder duas vezes. Em reinício normal isso não acontece, porque o worker termina os lotes antes de sair (`PARADA_GRACIOSA_S`, padrão 75 s).

### INV14 em produção (10/10/2026, `FILA_DURAVEL=on`)

Só no `postman_teste`, com um agente simulado criado para o teste (mock, envio de teste); 600 conversas em fluxo contínuo (8 por segundo, 75 s), limites padrão (5 por empresa). No meio do fluxo: um worker morto (SIGKILL) e mantido parado, e 20 s depois o Redis morto (SIGKILL) por 10 s.

| Medida | Resultado |
|---|---|
| Conversas respondidas | 600/600 |
| Perdidas / duplicadas | 0 / 0 |
| Turnos refeitos, em aberto, com erro, partes incertas | 0 / 0 / 0 / 0 |
| Recusas da recepção com o Redis fora (503), todas reenviadas com o mesmo `message_id` | 370 |
| Tempo total | 85 s (10 s depois do fim do envio) |
| bykoji | Nenhuma mensagem nem turno a mais durante o teste; benchmark simulado depois: 5/5, zero erros |

Como o agente simulado de produção responde na hora (sem `MOCK_ATRASO_S`), nenhum lote estava nas mãos do worker no instante em que ele morreu (0 turnos refeitos). A retomada de um lote cortado no meio ficou provada na pilha isolada (tabela acima).

### Como repetir

```sh
sh scripts/carga_fila.sh construir            # imagem gateway-carga a partir de ./app (não mexe na imagem em uso)
sh scripts/carga_fila.sh subir 2 20 8         # workers, conversas por worker, atraso do agente simulado
sh scripts/carga_fila.sh rodar --conversas 200 --tenants 2 --rotulo 2x20
sh scripts/carga_fila.sh redis-cair 10        # em outro terminal, no meio de uma rodada
sh scripts/carga_fila.sh worker-matar
sh scripts/carga_fila.sh derrubar             # remove a pilha isolada e os dados dela
```

A pilha não publica portas, gera segredos próprios em `.local/carga/` (fora do git) e o script de carga recusa agente que não seja mock/dry.

## Troca para a fila durável

`FILA_DURAVEL` no `.env` decide quem processa: `off` = agendador dentro da recepção (caminho atual); `on` = workers. Os dois caminhos usam a mesma trava por conversa e o mesmo agendamento no Redis, e a recepção e os workers terminam os lotes em andamento antes de parar.

```sh
sh scripts/fila_duravel.sh estado      # chave, containers, indicadores, pendências no banco
sh scripts/fila_duravel.sh ligar       # FILA_DURAVEL=on e recria recepção, workers e agendador
sh scripts/fila_duravel.sh desligar    # volta ao caminho atual
```

Nos primeiros minutos depois de ligar, conferir com `estado`: `health` com `"ligada": true`, `workers_vivos` = `WORKERS`, `agendador_ativo: true`; `entradas pendentes no banco` voltando a 0 depois de cada conversa; `mais antiga (s)` e `mais_antiga_s` abaixo de 30; `turnos em aberto` voltando a 0; `partes incertas` em 0; avisos nos logs em 0. Se algo fugir disso, `desligar`: o que estiver pendente é retomado pelo caminho atual ao subir a recepção.
