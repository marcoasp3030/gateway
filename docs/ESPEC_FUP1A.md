# FUP1-a — Follow-up: pendência, agendamento e cancelamento

**Situação: proposta para revisão do Marco, 21/10/2026. Nada implementado.**
- **Base:** `docs/ANALISE_COMPORTAMENTO_1.md`, seção 6 (proposta de 13/10), e as decisões F1 a F8 do Marco.
- **Depende do ENC1-a** (atendimento identificado), que já está implantado.
- **FUP1-b:** gerar a mensagem pelo Skyone, enviar e métricas. Vem depois, junto com o ENC1-b (inatividade).

## 1. Decisões já tomadas (F1–F8)

| # | Decisão |
|---|---|
| F1 | Escopo padrão: **só pedido em andamento**. "Pergunta pendente" fica como opção |
| F2 | 1º follow-up aos **15 min**, 2º **60 min depois do 1º**. Máximo padrão **2**, configurável de 0 a 2 |
| F3 | Loja fechada: **não enviar (cancela)**. A opção "adiar para a abertura" existe, desligada |
| F4 | Janela padrão **09:00–22:00**, sempre combinada com o horário da unidade |
| F5 | No turno de follow-up, **só Skills de consulta** (FUP1-b) |
| F6 | Opt-out vale **só para follow-up**; os avisos do pedido continuam |
| F7 | Opt-out pelo agente **e** por frases diretas, detectadas no gateway |
| F8 | Depois do último follow-up sem resposta, o atendimento encerra por inatividade (E2, ENC1-b) |
| — | `follow_up_ativo` começa **desligado** em todos os agentes |

## 2. O que entra no FUP1-a (sem enviar nada)

O FUP1-a monta e guarda a fila de follow-ups e cancela no momento certo. **Ele não chama o Skyone e não manda mensagem.** Quando um follow-up vence, o gateway confere tudo de novo (janela, unidade, pedido, opt-out, atendimento) e registra o que **teria** feito: estado `ensaio_enviaria` ou `cancelado`, com o motivo.

Com isso dá para ligar o FUP1-a no By Koji e ver, com tráfego real, quando e por que ele dispararia, antes de existir envio. O envio entra no FUP1-b, que troca `ensaio_enviaria` por gerar e enviar.

### 2.1 Pendência declarada pelo agente

O retorno do agente aceita, num turno normal, um campo opcional:

```json
"pendencia": {"tipo": "confirmar_pedido", "pergunta": "Confirma este resumo?"}
```

- **`tipo`** (fechado): `escolher_item`, `quantidade`, `entrega`, `confirmar_pedido` (escopo pedido em andamento) e `pergunta_geral` (só no escopo "pergunta pendente").
- **`pergunta`:** texto curto de até 200 caracteres, guardado só para o FUP1-b usar no pacote. Não é instrução executável.
- **Inválido ou ausente:** não arma nada, e a resposta segue normal (não é recusada).

O Marco ajusta o prompt e o "Interpretar Resposta" para devolver o campo. Sem ele, nada é agendado.

### 2.2 Quando o gateway agenda

O gateway arma o 1º follow-up (`previsto_em` = envio + 15 min) só quando tudo isto vale, conferido sob a trava da conversa no fim do turno:
1. o agente tem `follow_up_ativo = on` e `follow_up_maximo` ≥ 1;
2. a resposta saiu **inteira** (todas as partes `enviada`, nenhuma `incerta`) e com `acao` `nenhuma`;
3. veio uma `pendencia` válida, do tipo permitido pelo escopo;
4. **no escopo "pedido em andamento"**, o módulo de pedidos confirma pelo gancho novo `pendencia_valida`: carrinho aberto com itens, ou resumo aguardando confirmação, e nenhum pedido fechado neste atendimento depois do último carrinho;
5. o atendimento está aberto, sem atendente (nem aguardando atendente, nem pausado);
6. o contato não pediu para parar (opt-out);
7. o contato está abaixo do limite do dia, `follow_up_limite_dia` (padrão 2, somando agentes da empresa).

Arma de novo só com nova pendência enviada, e uma pendência nova substitui a anterior. Reação, silêncio e transferência não armam nem renovam.

### 2.3 Quando o gateway cancela (na hora)

Cancela, com motivo registrado:
- **qualquer mensagem do cliente** (`cliente_respondeu`), inclusive clique, anexo e áudio;
- **pedido fechado** ou cancelado no atendimento (`pedido_fechado`);
- **transferência**, assumir pelo atendente ou pausa (`com_atendente`). Devolver ao bot **não** rearma;
- **atendimento encerrado** (`encerrado`);
- **agente, empresa ou canal desligado**, ou `follow_up_ativo` desligado (`desligado`);
- **opt-out** (`opt_out`);
- **nova pendência** (`substituido`).

### 2.4 Quando vence (FUP1-a: só ensaio)

O agendador confere a cada 30 s os vencidos. Sob a trava da conversa, revalida 2.2 e 2.3 e mais:
- **janela:** a interseção de `follow_up_janela` (padrão 09:00–22:00, todos os dias) com o horário da unidade do carrinho, ou da empresa se não houver unidade, no fuso da unidade. Fora dela → `cancelado` com `fora_da_janela` (F3: cancela). Com `follow_up_fora_unidade = adiar_abertura`, que vem desligado, adia até a próxima abertura, no máximo 24 h, e revalida tudo;
- **idade máxima da pendência:** 24 h;
- **tudo certo:** `ensaio_enviaria` (FUP1-a) ou `gerando` (FUP1-b). O 2º follow-up (+60 min) nasce só quando o 1º for **enviado** de verdade, então no FUP1-a não há 2º.

### 2.5 Opt-out (F6, F7)

- **Tabela `follow_up_opt_out`:** por empresa e contato, com origem (`agente`, `frase`, `painel`), data e actor. Vale entre atendimentos e agentes, só para follow-up (avisos do pedido continuam).
- **Pelo agente:** campo `opt_out_follow_up: true` no retorno.
- **Por frase, no gateway:** a mensagem do cliente que é **só** um pedido para parar marca opt-out e cancela tudo. Exemplos: "não quero mais mensagens", "para de mandar mensagem", "pare", "parar". Frase com mais conteúdo ("não quero mais esse item") **não** conta. A lista fica fechada no código, com testes de casos que contam e casos que não contam.
- **Reativar:** só pelo painel (admin ou plataforma), auditado. Uma resposta do cliente nunca reativa sozinha.

## 3. Configuração (comportamentos do agente, herdados como os demais)

| Campo | Padrão | Faixa |
|---|---|---|
| `follow_up_ativo` | `off` | `on`/`off` |
| `follow_up_escopo` | `pedido_em_andamento` | `pedido_em_andamento`, `pergunta_pendente` |
| `follow_up_maximo` | 2 | 0–2 (0 = desligado) |
| `follow_up_primeiro_min` | 15 | 5–1440 |
| `follow_up_segundo_min` | 60 | 5–1440 (depois do envio do 1º) |
| `follow_up_janela` | 09:00–22:00 todos os dias | grade semanal no formato dos horários da unidade |
| `follow_up_fora_unidade` | `cancelar` | `cancelar`, `adiar_abertura` |
| `follow_up_limite_dia` | 2 | 0–5 por contato e por dia, na empresa |

Vale no segmento ou modelo, na empresa e no agente, como os outros comportamentos, com origem em `configuracao_efetiva`. O modelo `delivery_restaurante` recebe os padrões com `follow_up_ativo = off`. **O By Koji não muda** enquanto ninguém ligar.

## 4. Dados (migração 050)

- **`follow_up_tarefas`:**
  - **Campos:** `id`, empresa, `atendimento_id`, `conversation_id`, `agente_id`, `ordinal` (1 ou 2), `pendencia` (tipo e pergunta), `previsto_em`, `estado`, `motivo`, `request_id` do turno que armou, `criado_em`, `atualizado_em`.
  - **Estados:** `agendado`, `cancelado`, `ensaio_enviaria`, `gerando`, `enviado`, `falhou`, `incerto`; os quatro últimos são do FUP1-b.
  - **Unicidade:** uma tarefa `agendado` por atendimento. Índice por `previsto_em`, só das agendadas.
- **`follow_up_opt_out`:** (empresa, contato) é a chave, mais origem, `criado_em`, actor e `removido_em`.
- **`turnos.pendencia`:** a pendência aceita naquele turno, para auditoria.
- **Fila no Redis:** não muda. O agendador lê as vencidas direto do Postgres (são poucas), então nada se perde numa queda do Redis.

## 5. Pacote e retorno do Skyone

- **Pacote:** ganha `follow_up: {"ativo", "escopo"}` em `recursos`, para o agente saber se vale declarar a pendência, e `cliente.opt_out_follow_up` (booleano). No By Koji desligado, `recursos.follow_up.ativo` vem `false`. O campo é novo no pacote e entra no retrato como a única diferença.
- **Retorno:** dois campos novos opcionais, `pendencia` (2.1) e `opt_out_follow_up` (2.5).
- **Instrução para o prompt (proposta):** "Quando terminar o turno esperando uma resposta do cliente para continuar o pedido (escolha, quantidade, entrega ou confirmação do resumo), declare `pendencia`. Não declare em saudação, agradecimento, assunto pessoal ou quando o pedido já foi fechado. Se o cliente pedir para não receber mais mensagens, devolva `opt_out_follow_up: true`."

## 6. API do painel

- **`GET …/conversas/{id}/follow-ups`:** tarefas do atendimento atual e dos anteriores (estado, previsto, motivo).
- **`POST …/conversas/{id}/follow-ups/cancelar`** `{"motivo"?}`: admin e membro; auditado.
- **`GET/PUT/DELETE …/contatos/{id}/opt-out-follow-up`:** ver, registrar (membro pode) e reativar (admin e plataforma).
- **Lista de conversas:** ganha `follow_up: {"estado", "previsto_em"}` quando há tarefa agendada.
- **Webhooks de saída:** `follow_up.agendado`, `follow_up.cancelado`, `follow_up.ensaio` (sem texto). `follow_up.enviado` fica para o FUP1-b.

## 7. Testes

- Relógio controlado.
- Arma só com pendência válida e resposta inteira enviada.
- Não arma com silêncio, reação, transferência, `acao` diferente de `nenhuma`, opt-out ou limite do dia.
- Cancela por cada um dos motivos de 2.3.
- Corrida entre a mensagem do cliente e o vencimento: a mensagem vence, sob a trava.
- Janela: cancela fora dela, cruzando a meia-noite e no fuso da unidade.
- `adiar_abertura`.
- Idade máxima de 24 h.
- Frases de opt-out que contam e que não contam.
- Queda do worker ou do Redis: nada duplica, porque a tarefa é do Postgres.
- Agente sem o módulo de pedidos: o escopo "pedido em andamento" não arma.
- Retrato do By Koji: só `recursos.follow_up` novo.

## 8. Tamanho

**Médio.** Inclui a migração, o pipeline (armar e cancelar), o agendador (vencidos em ensaio), o gancho do módulo de pedidos, a configuração, o opt-out, o pacote e o retorno, as rotas do painel e os testes.

## 9. Decisões em aberto

| # | Decisão | Proposta |
|---|---|---|
| 1 | FUP1-a só em ensaio (não envia) | Sim: dá para ligar no By Koji e ver com tráfego real o que dispararia, antes do FUP1-b |
| 2 | Quem declara a pendência | O agente, com o campo `pendencia`, e o gateway confere pelo módulo de pedidos. Não inferir pelo "?" no fim do texto |
| 3 | Limite por dia | 2 follow-ups por contato e por dia, somando agentes da empresa; dia no fuso da empresa |
| 4 | Frases de opt-out | Lista fechada no código; só a mensagem inteira conta (o "pare" sozinho conta) |
| 5 | Devolver ao bot rearma? | Não; só uma nova pendência enviada arma |
| 6 | Janela no By Koji | 09:00–22:00 (F4) ∩ horário da unidade do carrinho |
| 7 | Onde o By Koji liga | Só depois do ajuste do prompt (campo `pendencia`); começa em ensaio |

**Ponto de parada:** só especificação. Nada implementado.
