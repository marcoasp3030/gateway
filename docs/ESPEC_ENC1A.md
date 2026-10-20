# ENC1-a — Atendimento identificado: começo, fim e reabertura

**Situação: aprovada pelo Marco em 20/10/2026 (seção 6) e implementada (seção 7).** Base: `docs/ANALISE_COMPORTAMENTO_1.md` (seção 7) e as decisões E1–E5 de 13/10. Junto vem a instrumentação pedida: cada chamada de Skill ligada ao seu turno e a versão do prompt. O ENC1-b (inatividade com e sem follow-up, métricas) vem depois do FUP1-a, como combinado.

---

## 1. Hoje

- A conversa (`conversation_id` = empresa + canal + telefone) é **uma só para sempre**. `encerrada_em`/`desfecho` são marcados pela ação `encerrar` do agente (ou pela limpeza de conversa vazia, 30 min) e **apagados** quando o cliente volta: não sobra registro de onde um atendimento começou e terminou.
- O pacote leva as últimas `history_turns` mensagens da conversa inteira, atravessando dias e pedidos antigos (causa do "assunto velho" na análise nº 1). `cliente.resumo_anterior` existe no contrato mas **nunca é preenchido**.
- Pedido fechado seguido de "obrigado" (com `silencio` ou reação) **não** encerra nada.
- Não há como ligar uma chamada de Skill a um turno com certeza (só pela janela de tempo), nem saber que versão do prompt respondeu.

## 2. O que muda

### 2.1 Atendimento

Tabela nova `atendimentos`: `id` (`at_<hex>`), empresa, `conversation_id`, `numero` (1, 2, 3… dentro da conversa), `agente_id`, `aberto_em`, `motivo_inicio`, `estado`, `encerrado_em`, `motivo_encerramento`, `desfecho`, `resumo`. `conversas.atendimento_id` aponta o atual; `mensagens`, `turnos`, `pedidos`, `solicitacoes` e `skill_registro`/`skill_chamadas` ganham `atendimento_id`.

| Estado | Quando |
|---|---|
| `aberto` | Bot atendendo |
| `em_atendimento_humano` | Conversa aguardando atendente ou pausada por atendente (a transferência não encerra o atendimento; o "#bot"/devolver volta para `aberto`) |
| `encerrado` | Ver 2.2 |

`motivo_inicio`: `primeiro_contato`, `retorno_apos_encerramento`, `reaberto_manual`.

**Abertura:** mensagem do **cliente** numa conversa sem atendimento aberto abre um novo (o anterior nunca é apagado nem reaproveitado). Mensagem do sistema (aviso de status do pedido, fora do horário) e do atendente **não** abrem atendimento.

**Migração:** um atendimento por conversa existente (aberto se a conversa não está encerrada; encerrado com o `desfecho` de hoje se está), com as mensagens, turnos e pedidos ligados a ele.

### 2.2 Encerramento (ENC1-a)

| Regra | Motivo | Decisão |
|---|---|---|
| Agente devolve `acao: encerrar` | `concluido` (com o `desfecho`) | como hoje |
| Pedido fechado **neste atendimento** + mensagem do cliente que é só agradecimento/fechamento ("obrigado", "valeu", 👍…), sem carrinho aberto com itens e sem pergunta pendente do bot | `pedido_concluido` — **na hora**, mesmo que o agente responda com `silencio` ou reação | E3 |
| Pedido fechado neste atendimento e **nenhuma** mensagem do cliente nos 30 min seguintes ao fechamento | `pedido_sem_retorno`, aos 30 min | E3 |
| Conversa vazia (limpeza de hoje, 30 min) | `vazio` | como hoje |
| Encerrar pelo painel | `manual` | novo |

- **Sem despedida** em nenhum desses (E1): o gateway não manda texto nem chama o Skyone para dizer tchau. Na conclusão vale a resposta/reação que o agente já deu.
- **Avisos de status do pedido continuam chegando** depois do encerramento e **não reabrem** o atendimento (E3). Se o cliente responder a um aviso ("obrigado" depois de "pedido pronto"), abre atendimento novo normalmente, e o agente responde como hoje (reação) (E5).
- Encerrar não mexe em carrinho nem pedido. O carrinho continua até o `carrinho_expira_h` de sempre (sem TTL novo).
- Inatividade de 60 min sem follow-up / 30 min depois do último follow-up, com texto opcional (E1, E2): **ENC1-b**.

### 2.3 Pacote do Skyone

Bloco novo na raiz, sempre presente:

```json
"atendimento": {"id":"at_…","numero":3,"novo":true,"motivo_inicio":"retorno_apos_encerramento",
  "anterior":{"numero":2,"motivo_encerramento":"pedido_concluido","desfecho":"pedido_feito","encerrado_em":"2026-10-15T20:41:00-03:00",
              "ha_segundos":86400,"resumo":"Pedido 48213 (retirada, Pátio Guedala, R$ 68,00) — concluído."}}
```

- `novo: true` só no primeiro turno entregue do atendimento (persistido: vale também se o worker cair e o turno for retomado).
- **Histórico só do atendimento atual** (E4), ainda limitado por `history_turns`. O anterior vai **só como `anterior.resumo`**, e `cliente.resumo_anterior` passa a ter o mesmo texto (o campo já existe no contrato e hoje vem sempre nulo).
- `acoes_recentes` e `acoes_ja_executadas`: só do atendimento atual (Skill de ontem não vale como "acabei de fazer").
- Carrinho guardado de atendimento anterior: `contexto_modulos.pedidos.carrinho` continua igual e ganha `de_atendimento_anterior: true` (o agente não oferece sozinho; usa se o cliente quiser continuar). Gancho do módulo de pedidos; agente sem pedidos não vê.
- `retomada` e `tempo_desde_ultima` continuam iguais (compatibilidade), mas não decidem nada.

**Resumo do atendimento anterior** (decisão 2): o gateway monta um texto **factual e curto** (até 300 caracteres) ao encerrar, sem chamar IA: motivo do encerramento, pedido(s) do atendimento com número, modalidade, unidade, total e status, solicitação à equipe aberta, transferência. Sem texto da conversa nem dados pessoais. Opcional (decisão 3): aceitar um `resumo` vindo do agente na resposta com `acao: encerrar`, que substitui o factual.

### 2.4 Instrumentação

- **Skill ligada ao turno sem depender do Skyone** (ajuste do Marco, 16/10): a chamada de Skill traz o `conversation_id`, e há **um turno por vez por conversa** (o pipeline pega a trava da conversa antes de processar, `nucleo/pipeline.py`, com validade de `timeout_s` + 90 s). O gateway grava em `skill_registro.turno_id` e `skill_chamadas.turno_id` o turno `em_andamento` daquela conversa no momento da chamada. Sem turno em andamento (Skill chamada depois de o gateway desistir por tempo, ou chamada manual), grava o último turno da conversa e marca `fora_do_turno=true`. Nada vem do Skyone; nenhuma chamada é recusada por isso. Painel: `GET …/skills/registro?turno_id=`.
- **Versão do prompt:** o retorno do agente aceita o campo **`prompt_versao`** (texto de 1 a 40 caracteres: letras, números, `.`, `-`, `_`; ex.: `"v6.7"`). O Marco faz o prompt devolver o campo e ajusta o Interpretar Resposta e o Retorno do Skyone. O gateway grava em `turnos.prompt_versao` (fora do formato ou ausente = nulo, sem recusar a resposta) e mostra nos turnos e nas métricas (`por_versao_do_prompt`: turnos, transferências, refazer e tempo por versão).

### 2.5 API admin — contrato para o [PAINEL]

- Lista e detalhe de conversas ganham `atendimento: {"id","numero","estado","aberto_em","encerrado_em","motivo_encerramento"}` (o atual). `estado` da conversa continua igual.
- `GET /tenants/{t}/conversas/{id}/atendimentos?cursor=` — histórico: número, início, fim, motivo, desfecho, resumo, quantidade de mensagens, pedidos do atendimento.
- `GET /tenants/{t}/conversas/{id}/mensagens?atendimento=at_…` — filtro por atendimento.
- `POST /tenants/{t}/conversas/{id}/encerrar` `{"motivo":"manual"}` e `POST …/reabrir` — admin e membro da própria empresa; auditados; reabrir só o último atendimento e só se a conversa não tiver outro aberto.
- Webhooks de saída novos: `atendimento.aberto`, `atendimento.encerrado` (sem texto).
- Turnos ganham `prompt_versao`; o registro de Skills ganha `turno_id` e `fora_do_turno`.

### 2.6 O que **não** muda

Assumir/devolver, pausa, transbordo, fila, botões, reações, avisos de pedido, solicitações, `conversation_id`, rotas das Skills.

## 3. By Koji

Muda de propósito: histórico do pacote recortado no atendimento, bloco `atendimento`, encerramento por pedido concluído. O **retrato** (`tests/test_multi1b_retrato.py`) será regravado com estas diferenças listadas campo a campo no relatório (só elas; o resto tem de continuar idêntico). Testes próprios: abertura e encerramento por cada regra (relógio fixo para os 30 min), aviso de pedido depois do encerramento sem reabrir, "obrigado" depois de "pronto" abrindo novo, worker caindo no meio (`novo` persistido), corrida mensagem × encerramento dos 30 min, migração das conversas existentes, Skill dentro e fora do turno (inclusive turno refeito e tempo esgotado), `prompt_versao` válido/inválido/ausente, LGPD (anonimização leva os atendimentos e apaga o resumo).

## 4. Tamanho

Um bloco grande (migração 045 com preenchimento das conversas existentes, pipeline, pacote, varredura de 30 min no agendador, API, instrumentação, testes). Ajuste no Skyone (`prompt_versao` no retorno, instrução sobre `atendimento.novo` e `anterior.resumo`) fica com o Marco; o gateway funciona sem ele.

## 5. Decisões

| # | Decisão | Proposta |
|---|---|---|
| 1 | Transferência encerra o atendimento? | Não: estado `em_atendimento_humano`; devolver ao bot volta para `aberto` |
| 2 | Resumo do atendimento anterior | Factual, montado pelo gateway, até 300 caracteres, sem texto da conversa |
| 3 | Resumo vindo do agente | Aceitar `resumo` opcional no `encerrar`, substituindo o factual |
| 4 | "Agradecimento" que encerra | Mesma lista de fechamentos do AVS1 (`parece_fechamento`), só a mensagem inteira; "obrigado, e o horário?" não encerra |
| 5 | 30 min sem retorno após o pedido | Conta do fechamento do pedido; qualquer mensagem do cliente nesse tempo cancela essa regra (o atendimento segue e cai na inatividade do ENC1-b) |
| 6 | Encerrar/reabrir pelo painel | Admin e membro; reabrir só o último |
| 7 | Ligar Skill ao turno | Pelo turno em andamento da conversa (um por vez); sem turno em andamento, o último turno com `fora_do_turno=true`; nada vem do Skyone |
| 8 | Versão do prompt | Campo `prompt_versao` no retorno do agente (até 40 caracteres); fora do formato vira nulo sem recusar a resposta |
| 9 | Histórico | Só do atendimento atual; o anterior só como resumo (E4) — confirmar que vale também quando o cliente volta em menos de 30 min após um "obrigado" (novo atendimento, histórico curto) |

## 6. Decisões do Marco (20/10/2026)

- 1, 2, 4, 5, 6, 7 e 8: como propostas. `prompt_versao` no formato `v` + números e pontos, até 40 caracteres; fora do formato vira nulo.
- 3: resumo vindo do agente **não, por enquanto**: só o resumo factual do gateway. O campo `resumo` fica previsto no retorno, mas é ignorado.
- 9: sim, vale também na volta em menos de 30 min depois de um "obrigado": atendimento novo, histórico curto e o resumo do anterior com o pedido e o status.

## 7. Implementado (migração 045)

- **Tabela `atendimentos`** e `atendimento_id` em conversas, mensagens, turnos, pedidos, solicitações, `skill_registro` e `skill_chamadas`. As linhas novas nascem ligadas ao atendimento **aberto** da conversa por gatilho no banco, o que vale também para as gravadas pela imagem anterior enquanto a migração é aplicada. A mensagem do sistema que chega com o atendimento encerrado (aviso de status do pedido) fica sem atendimento e é adotada pelo próximo que abrir: o agente vê o aviso a que o cliente responde.
- **Estado:** grava `aberto` ou `encerrado`. `em_atendimento_humano` é derivado da conversa (aguardando atendente ou pausada) na API, sem gravar.
- **Migração das conversas existentes:** um atendimento por conversa, com `motivo_inicio` igual a `migracao`.
  - Conversa aberta: atendimento `aberto`.
  - Conversa encerrada: atendimento `encerrado`, com o desfecho de hoje. O motivo é `vazio` quando o desfecho era `vazia`; senão, `concluido`.
- **Encerramento no turno (E3, decisão 4):**
  - **Quando encerra:** o lote é só agradecimento ou fechamento (a mesma regra do AVS1, aplicada à mensagem inteira); o bot não deixou botões ou lista esperando escolha; e o módulo de pedidos confirma que há pedido não cancelado neste atendimento e nenhum carrinho aberto com itens.
  - **Interpretação de "pergunta pendente":** conta como pendente só a escolha por botões ou lista. O "Posso ajudar em mais alguma coisa?", que o bot costuma mandar depois de fechar o pedido, é respondido pelo "obrigado".
  - **Quando não encerra:** com outra mensagem do cliente já esperando (corrida), e com o agente transferindo, encerrando ou avisando a equipe.
  - **Desfecho:** o do agente; sem ele, `pedido_feito`.
- **Sem retorno em 30 min:** o agendador confere a cada minuto. Encerra o atendimento aberto cujo último pedido foi fechado há mais de 30 min sem nenhuma mensagem do cliente depois. Conversa com atendente fica como está. A conferência se repete sob a trava da conversa.
- **Ganchos novos do módulo de pedidos:** `encerra_no_turno`, `sem_retorno`, `resumo_do_atendimento` e `resumo_dos_atendimentos`. O núcleo continua sem ler as tabelas de pedido.
- **Resumo:** gravado ao encerrar e refeito no pacote, para levar o status atual do pedido. Exemplo: "Encerrado: pedido concluído e cliente agradeceu. Pedido 48213 (retirada, Pátio Guedala, R$ 68,00): confirmado pela unidade."
- **Instrumentação:** a chamada de Skill grava o turno `em_andamento/processando` da conversa, também por gatilho. Sem turno em andamento, grava o último turno da conversa, com `fora_do_turno=true`.
- **Retrato do By Koji:** regravado só com acréscimos (240 linhas novas, nenhuma alterada ou removida):
  - `atendimento` em cada pacote (20);
  - `contexto_modulos.pedidos.carrinho.de_atendimento_anterior=false` (20);
  - `atendimento` em cada conversa da lista do painel (9);
  - `por_versao_do_prompt` nas métricas.

  Nenhum cenário do retrato encerra um atendimento, então histórico, respostas das Skills, mensagens e turnos ficaram idênticos.
- **Testes:** `tests/test_enc1a.py` (7).
