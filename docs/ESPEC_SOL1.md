# SOL1 — Avisar a equipe sem parar o atendimento

**Situação:** **aprovada em 11/10/2026**, com as decisões da seção 8 (que valem sobre o texto das seções anteriores onde houver diferença) e o item extra da seção 9. **Nada implementado ainda.**
**Data:** 11/10/2026. Produção: `gateway:2b19ef4`, schema 34. Ordem: G4-a (feito) → OPS1 → **SOL1** → R1 → G4-b → G5/G6 → G3 → B4 → C1 → CFG1-b → carrossel.

---

## 1. O problema (teste do piloto final 7046, 11/10, 15:13–15:20)

O cliente montou um pedido (1 Edamame, Pátio Guedala, retirada), perguntou no meio se a casa faz evento para 50 pessoas e depois tocou em "Finalizar pedido". O agente respondeu `transferir_humano` com motivo `evento_ou_encomenda`. O gateway fez o que essa ação manda: pausou o bot por 60 min e pôs a conversa em "aguardando atendente". O "Ok" seguinte do cliente ficou sem resposta e **o pedido não foi fechado**: o carrinho ficou aberto.

Uma dúvida sobre evento não deveria travar a venda que estava acontecendo. Falta uma ação intermediária: **avisar a equipe e seguir atendendo**.

---

## 2. A ação nova: `notificar_equipe`

### 2.1 Retorno do agente

```json
{"resposta": ["Anotei seu pedido de 1 Edamame para retirada 😊", "Sobre o evento, já avisei nossa equipe; eles falam com você por aqui."],
 "acao": "notificar_equipe",
 "motivo": "evento_ou_encomenda",
 "solicitacao": {"tipo": "evento", "data": "25 de dezembro", "pessoas": 50, "local": null, "observacao": "Cliente só queria saber se fazem."},
 "botoes": {"opcoes": [{"id": "finalizar_pedido", "titulo": "Finalizar pedido"}]}}
```

| Campo | Regra |
|---|---|
| `acao` | `notificar_equipe`. Aceita `resposta` (os balões saem normalmente) e `botoes` |
| `motivo` | Um dos motivos do catálogo do CFG1 (seção 3.2 de `ESPEC_CFG1.md`). Obrigatório. Fora do catálogo → grava `fora_do_escopo` e guarda o texto recebido em `motivo_informado` |
| `solicitacao.tipo` | `evento`, `encomenda`, `orcamento`, `parceria`, `outro` (desconhecido vira `outro`) |
| `solicitacao.data` | Texto como o cliente disse, até 80 caracteres ("25 de dezembro", "sábado à noite") |
| `solicitacao.pessoas` | Inteiro de 1 a 100000 |
| `solicitacao.local` | Texto até 160 |
| `solicitacao.observacao` | Texto até 500 |

- Todos os campos de `solicitacao` são opcionais; campo inválido é **ignorado**, sem erro (como o `nome_cliente`). `solicitacao` ausente também é aceita: a equipe é avisada só com o motivo e o link da conversa.
- Campos fora da lista são descartados. O gateway não inventa nem completa dado.
- Sem `resposta` (lista vazia), a ação vale mesmo assim: registra e avisa, sem balão.

### 2.2 O que o gateway faz

1. **Grava a solicitação**, ligada à conversa, ao contato e ao agente.
2. **Envia um alerta** ao destino de transbordo da empresa (hoje `whatsapp_alerta_transbordo`), pela mesma caixa de avisos internos.
3. **Não pausa o bot** e **não** marca "aguardando atendente". Os balões saem como em qualquer turno.
4. Emite o webhook de saída `solicitacao.criada` (ou `solicitacao.atualizada`).

Tudo isso acontece quando o turno é concluído. Resposta descartada por refazer não registra nada.

Texto do alerta:

```
📋 SOLICITAÇÃO PARA A EQUIPE — evento
Cliente: Jussara Santos — +55 11 94271-7046
Data: 25 de dezembro · Pessoas: 50
Obs.: Cliente só queria saber se fazem.
O bot continua atendendo esta conversa.
Painel: https://painel…
```

### 2.3 Uma solicitação aberta por assunto

- Enquanto houver solicitação **aberta ou em andamento** da mesma conversa com o mesmo `motivo`, uma nova `notificar_equipe` **atualiza a existente**: campos que vieram preenchidos substituem os antigos; campo nulo não apaga o que já havia.
- **O alerta não é repetido.** Exceção proposta (decisão 2): se a atualização trouxe dado novo e o último alerta saiu há mais de 30 min, sai um alerta de atualização.
- Depois de concluída ou cancelada, uma nova ação abre outra solicitação.
- O pacote do agente ganha `contexto_modulos.solicitacoes`: as abertas desta conversa (`motivo`, `tipo`, dados, `status`, `ha_segundos`). Assim o agente sabe que a equipe já foi avisada e só complementa.

### 2.4 `transferir_humano` continua existindo

Pausa o bot e põe a conversa em "aguardando atendente", como hoje. É o caminho de `pediu_humano`, `reclamacao`, `pagamento` e `pedido_para_confirmar`.

| Motivo | Ação esperada do agente |
|---|---|
| `pediu_humano`, `reclamacao`, `pagamento`, `pedido_para_confirmar` | `transferir_humano` (pausa) |
| `evento_ou_encomenda` | `notificar_equipe` (não pausa) |
| `restricao_alimentar`, `fora_do_escopo`, `assunto_proibido` | Qualquer das duas, conforme o prompt base |

### 2.5 Proteção no gateway

Se o agente devolver `transferir_humano` com motivo `evento_ou_encomenda` **e houver carrinho aberto com itens**, o gateway trata como `notificar_equipe`: registra a solicitação, avisa a equipe, envia os balões e **não pausa**.

- O turno guarda `acao = notificar_equipe` e `acao_original = transferir_humano`.
- Conta em `GET /metricas`, bloco novo `solicitacoes`: `{"criadas", "atualizadas", "convertidas", "alertas_enviados"}`.
- Sem carrinho com itens, a transferência vale como hoje.
- No teste de 11/10, isso teria mantido o bot ativo no turno 823; o "Ok" seria respondido e o pedido poderia ser fechado.

---

## 3. Dados

Tabela `solicitacoes` (migração aditiva, próximo número livre):

| Coluna | Regra |
|---|---|
| `id`, `tenant_id` | |
| `numero` | sequencial curto por empresa, para a equipe citar ("S-0012") |
| `conversation_id`, `contato_id`, `agente_id` | vínculo; acompanham a anonimização |
| `motivo`, `motivo_informado` | catálogo e, se houver, o texto recebido |
| `tipo` | `evento`, `encomenda`, `orcamento`, `parceria`, `outro` |
| `dados` | JSON: `data`, `pessoas`, `local`, `observacao` |
| `status` | `aberta` → `em_andamento` → `concluida`; `cancelada` a partir de qualquer um antes de concluída |
| `origem` | `agente` ou `convertida` (proteção da 2.5) |
| `criado_em`, `atualizado_em`, `alertado_em` | |
| `atendido_por`, `concluido_em`, `nota` | quem assumiu, quando terminou, anotação interna (até 500) |

- `avisos_internos` ganha o tipo `solicitacao`.
- `turnos` ganha `acao_original`.
- Anonimização do contato: `dados`, `nota` e `motivo_informado` são apagados.

---

## 4. Configuração

Comportamento novo, com a herança de sempre (plataforma → segmento → empresa → agente) e dentro das versões do CFG1, no grupo `transbordo`:

| Nome | Tipo | Padrão | Efeito |
|---|---|---|---|
| `notificar_equipe` | `on` / `off` | `off` na plataforma; `on` no segmento `delivery_restaurante` (decisão 4) | Desligado: a ação é tratada como `nenhuma` (os balões saem, nada é gravado nem avisado) e a proteção da 2.5 não atua |

- O pacote informa ao agente se o recurso está ligado, para ele não prometer que avisou a equipe quando não vai avisar (bloco `recursos`, que entra no R1).
- Sem destino de alerta cadastrado: a solicitação é gravada e aparece no painel; o aviso fica `simulado`.
- O alerta sai de verdade só com envio real no agente do canal, como os demais avisos internos.

---

## 5. Rotas `/admin/v1` para o painel

| Rota | Uso |
|---|---|
| `GET /tenants/{t}/solicitacoes` | Lista. Filtros: `status` (vários, por vírgula), `motivo`, `tipo`, `desde`, `ate`, `busca` (número, nome, telefone), `limite`, `cursor`. Mais recente primeiro |
| `GET /tenants/{t}/solicitacoes/contagem` | Para o alerta do painel |
| `GET /tenants/{t}/solicitacoes/{id}` | Detalhe |
| `PATCH /tenants/{t}/solicitacoes/{id}` | `{"status"?, "nota"?}`; exige `X-Actor`; qualquer papel |

```json
{"id": 12, "numero": "S-0012", "status": "aberta", "motivo": "evento_ou_encomenda", "tipo": "evento", "origem": "agente",
 "dados": {"data": "25 de dezembro", "pessoas": 50, "local": null, "observacao": "Cliente só queria saber se fazem."},
 "cliente": {"nome": "Jussara Santos", "telefone": "+5511942717046", "contato_id": "ct_…"},
 "conversation_id": "bykoji:can_…:5511942717046",
 "conversa": {"estado": "bot", "pedido_aberto": null},
 "alerta": {"status": "enviado", "em": "2026-10-11T18:17:20Z"},
 "criado_em": "2026-10-11T18:17:18Z", "atualizado_em": "2026-10-11T18:17:18Z",
 "atendido_por": null, "concluido_em": null, "nota": null}
```

- Contagem: `{"abertas": 2, "em_andamento": 1, "mais_antiga_em": "…"}`.
- Mudança de status inválida (voltar de concluída, por exemplo) → 409 com o status atual, como nos pedidos. `em_andamento` grava `atendido_por` com o `X-Actor`.
- `GET /tenants/{t}/conversas` ganha `solicitacao_aberta: {"id", "numero", "tipo", "status"}` ou `null`.
- Auditoria: `solicitacao.criada`, `solicitacao.atualizada`, `solicitacao.status`.

---

## 6. Bloco e testes

Um bloco só (SOL1), com parada e relatório.

- `notificar_equipe` grava, alerta uma vez, envia os balões e **não** pausa nem marca aguardando; o turno seguinte é respondido normalmente.
- Segunda ação com o mesmo motivo atualiza a mesma solicitação e não repete o alerta; campo nulo não apaga dado; depois de concluída, abre outra.
- Validação de `solicitacao`: campo inválido ignorado, tipo desconhecido vira `outro`, motivo fora do catálogo vira `fora_do_escopo` com o texto guardado; `solicitacao` ausente aceita.
- Proteção: `transferir_humano` + `evento_ou_encomenda` + carrinho com itens → convertido, não pausa, conta em `convertidas`; sem carrinho ou com carrinho vazio → transfere como hoje; outros motivos nunca convertem.
- `transferir_humano` com `pediu_humano`, `reclamacao`, `pagamento`, `pedido_para_confirmar` continua pausando (regressão).
- Recurso desligado: ação tratada como `nenhuma`, nada gravado; proteção não atua.
- Resposta descartada por refazer não cria solicitação; só a resposta que sai cria.
- Alerta: texto com nome, telefone, dados e link; sem destino → `simulado`; envio de teste não envia; falha tenta de novo como os outros avisos internos.
- Pacote: `contexto_modulos.solicitacoes` com as abertas; vazio sem nenhuma.
- Rotas: filtros, paginação, contagem, mudança de status válida e inválida, `atendido_por`.
- Isolamento: solicitação de outra empresa → 404; contagem não mistura; conversa de outra empresa não aparece.
- Anonimização limpa os dados da solicitação.

---

## 7. Decisões que preciso de você

1. **Telefone no alerta:** inteiro (proposta, porque a equipe vai ligar ou escrever para o cliente) ou mascarado, como no alerta de transbordo de hoje?
2. **Alerta de atualização:** quando o cliente completa os dados depois (ex.: informa o local), mandar um segundo alerta, no máximo um a cada 30 min (proposta), ou nunca repetir e deixar só no painel?
3. **Proteção da 2.5:** só para `evento_ou_encomenda` com carrinho com itens (como você descreveu), ou também quando há **pedido em aberto** na conversa (aguardando a loja, em preparo)?
4. **Padrão:** `notificar_equipe` nasce desligado na plataforma e ligado no segmento de delivery (proposta), ou desligado em tudo até você ligar no By Koji?
5. **Quem atualiza a solicitação no painel:** qualquer papel, inclusive `membro` (proposta), ou só admin?
6. **Tipos de solicitação:** `evento`, `encomenda`, `orcamento`, `parceria`, `outro` servem?

---

## 8. Decisões do Marco (11/10/2026) — aprovada

1. **Telefone no alerta:** inteiro, porque o destino é a equipe interna. Mascarado só quando o destino de transbordo estiver marcado como **grupo externo** — configuração nova `transbordo_destino_externo` (sim/não, padrão não), no grupo `transbordo`.
2. **Alerta de atualização:** sim, no máximo **1 a cada 30 min por solicitação**, e só quando um **campo novo** for preenchido (campo que estava vazio; trocar o valor de um campo já preenchido não gera alerta).
3. **Proteção (muda a 2.5):** `transferir_humano` com motivo `evento_ou_encomenda` **ou `catering`** vira `notificar_equipe` quando houver **carrinho com itens ou pedido ativo** na conversa (`aguardando_loja`, `confirmado`, `em_preparo`, `pronto_retirada`, `saiu_entrega`). A conversão fica no log e nas métricas, com `acao_original` e `acao_aplicada`.
   - `catering` entra no catálogo de motivos de transferência do CFG1 (seção 3.2 de `ESPEC_CFG1.md`), ligado por padrão.
4. **Padrão:** `notificar_equipe` desligado na plataforma, ligado no segmento `delivery_restaurante`. Na implantação, conferir que o By Koji herda ligado (origem `segmento`).
   - O agente do By Koji está na versão 1 do segmento. Ligar o padrão exige criar a versão 2 do segmento e mover o agente para ela (procedimento da seção 14.3 de `ESPEC_CFG1.md`), comparando a configuração efetiva antes e depois.
5. **Atualização da solicitação no painel:** qualquer papel (`plataforma`, `admin`, `membro`).
6. **Tipos:** `evento`, `catering`, `encomenda`, `orcamento`, `parceria`, `outro`.

## 9. Item extra do SOL1: toques contraditórios no mesmo lote

No teste de 11/10 o cliente tocou em "Entrega" e, um segundo depois, em "Retirada"; os dois chegaram no mesmo lote e o agente seguiu o primeiro.

**Regra:** quando o lote tiver 2 ou mais cliques em opções da **mesma mensagem** de botões ou de lista, fica no lote **só o último clique**.

| Ponto | Como fica |
|---|---|
| O que sai | Os cliques anteriores da mesma mensagem saem de `mensagens_do_lote` e do texto `message` |
| O que fica registrado | O clique mantido ganha `interacao.substituiu: ["entrega"]`, com os ids das opções descartadas, na ordem em que foram tocadas. `ultima_interacao` é a do clique mantido |
| Cliques em mensagens diferentes | Nada muda: ficam todos |
| Clique + texto digitado | Nada muda: fica tudo, na ordem |
| Histórico e banco | As mensagens dos toques descartados continuam gravadas e aparecem no painel; só não vão ao agente como parte do lote |
| "Mesma mensagem" | A mesma mensagem de origem do clique (o identificador que o clique cita), enviada pelo gateway nesta conversa |
| Liga/desliga | Não tem: vale sempre, com ou sem `notificar_equipe` |

```json
"mensagens_do_lote": [
  {"enviada_em": "2026-10-11T15:14:42-03:00", "tipo": "texto", "texto": "Retirada",
   "interacao": {"tipo": "botao", "id": "retirada", "atual": true, "substituiu": ["entrega"]}}],
"message": "Retirada"
```

Testes (além dos da seção 6):
- "Entrega" e depois "Retirada" no mesmo lote → fica só "Retirada", com `substituiu: ["entrega"]`.
- Três toques na mesma mensagem → fica o último, com os dois anteriores em `substituiu`.
- Cliques em mensagens diferentes no mesmo lote → ficam os dois, sem `substituiu`.
- Clique + texto digitado no mesmo lote → fica tudo.
- Dois toques na mesma opção → fica um, com `substituiu` trazendo o id repetido.
- Lista: o mesmo comportamento dos botões.
- As mensagens descartadas continuam no histórico da conversa.
