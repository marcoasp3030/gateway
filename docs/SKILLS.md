# Skills do agente — catálogo e pedidos (Fase 4)

Especificação das Skills que o agente do Skyone usa para consultar o catálogo e montar o pré-pedido.
Cada Skill é uma chamada HTTP ao gateway. As regras de negócio são validadas no gateway: o agente só
repassa o que o cliente pediu e lê a resposta.

> Situação (10/10/2026): rotas em produção e testadas pelo gateway. As Skills ainda **não foram criadas
> no Skyone**; os nomes e as descrições abaixo são a proposta para criá-las.

## Como chamar

| Item | Valor |
|---|---|
| Endereço base | `https://painel.luxbrain.com.br/gateway` |
| Autenticação | cabeçalho `X-API-Key: <chave>` (a chave fica no cofre do Skyone; nunca no prompt) |
| Chave do By Koji | escopos `ferramentas:catalogo` e `ferramentas:pedidos`, presa ao agente "Atendimento By Koji", sem validade. Valor em `.local/fase4_chave_skills.txt` no servidor |
| Formato | JSON (`Content-Type: application/json`) |
| `conversation_id` | o mesmo que o gateway envia no pacote (campo `conversation_id`). Toda rota exige uma conversa existente desse agente |

A empresa é sempre a da chave. Não existe cabeçalho nem campo para informar a empresa.

## O que o agente já recebe no pacote

A cada turno o gateway envia, sem o agente pedir:

```json
"contexto_modulos": {
  "catalogo": {"itens_ativos": 808},
  "pedidos": {
    "carrinho": { "...": "igual ao campo carrinho das respostas abaixo" },
    "ultimo_pedido": {"numero": "48213", "status": "aguardando_loja", "total": 327.0, "criado_em": "2026-10-10T13:29:00+00:00"}
  }
},
"unidades": [
  {"codigo": "patio_guedala", "nome": "Pátio Guedala", "endereco": "Av. Comendador Adibo Ares, 9 — Morumbi",
   "telefone": null, "horarios": {"seg": [["12:00","15:00"],["19:00","22:00"]]}, "aberta_agora": true, "proxima_abertura": null,
   "formas_pagamento": null, "taxa_entrega": null, "pedido_minimo": null, "area_entrega": null, "aceita_retirada": null,
   "regras": "Horários informados são do salão; horário e condições de delivery ainda a confirmar."}
]
```

- `ultimo_pedido` é nulo se a conversa nunca fechou um pedido.
- Em `unidades`, **nulo significa "não informado"**: o agente não afirma nada sobre o campo e diz que a unidade confirma no retorno do pré-pedido.
- Por isso o agente não precisa chamar `ver_carrinho` no início de cada turno: o carrinho já vem no pacote.

## Formato das respostas

**Sucesso em qualquer escrita** — sempre o carrinho completo atualizado:

```json
{
  "ok": true,
  "carrinho": {
    "conversation_id": "bykoji:can_49fce1f15a43f87f22739d60:5511942717046",
    "unidade": "patio_guedala",
    "modalidade": "retirada",
    "endereco": null,
    "horario": null,
    "observacao": null,
    "maioridade_confirmada": false,
    "itens": [
      {"id": 12, "item_id": 1027, "codigo": "GUE-0138", "nome": "Tempurá Moriawase", "categoria": "Pratos quentes",
       "quantidade": 2, "preco_unitario": 136.0, "subtotal": 272.0, "observacao": null, "alcoolico": false},
      {"id": 13, "item_id": 1053, "codigo": "GUE-0164", "nome": "Sapporo", "categoria": "Cervejas",
       "quantidade": 1, "preco_unitario": 55.0, "subtotal": 55.0, "observacao": null, "alcoolico": true}
    ],
    "numero_itens": 3,
    "subtotal": 327.0,
    "taxa_entrega": null,
    "total": 327.0,
    "pedido_minimo": null,
    "tem_alcoolico": true,
    "faltando": ["maioridade_confirmada"],
    "pode_fechar": false
  }
}
```

- `itens[].id` é o identificador da **linha do carrinho** (usado para alterar e remover); `item_id` é o item do catálogo.
- `taxa_entrega` nula = a unidade não tem taxa cadastrada ou não é entrega; nesse caso o `total` é só o subtotal e o agente diz que a taxa será confirmada pela unidade.
- `faltando` lista o que impede fechar: `itens`, `modalidade`, `endereco`, `maioridade_confirmada`.

**Recusa** — erro com o motivo e o carrinho atual:

```json
{
  "erro": {"codigo": "VALIDATION_ERROR",
           "mensagem": "Item sem preço cadastrado: não entra no pedido. Transfira para a equipe confirmar (motivo pedido_para_confirmar).",
           "detalhes": {"campo": "item_id", "motivo": "sem_preco"}},
  "carrinho": { "...": "carrinho como está" }
}
```

| `motivo` | HTTP | O que o agente faz |
|---|---|---|
| `sem_preco` | 422 | Não promete preço. Transfere: `acao = transferir_humano`, `motivo = pedido_para_confirmar` |
| `item_inativo`, `sem_delivery` | 422 | Avisa que o item não está disponível e oferece alternativa |
| `item_inexistente` | 404 | Busca de novo no catálogo |
| `unidade_diferente` | 422 | Explica que um pedido é de uma unidade só; pergunta se quer trocar de unidade (esvaziar) ou manter |
| `unidade_inexistente` | 404 | Usa um dos códigos de `unidades` |
| `carrinho_cheio` | 422 | Avisa o limite de 50 linhas |
| `linha_inexistente` | 404 | Usa os `id` das linhas do carrinho devolvido |
| `carrinho_vazio`, `falta_modalidade`, `falta_endereco`, `falta_maioridade` | 422 | Pergunta ao cliente o que falta |
| `retirada_indisponivel` | 422 | Oferece entrega |
| `abaixo_do_minimo` | 422 | Informa o `pedido_minimo` e pergunta se quer acrescentar algo |
| `preco_alterado` | 409 | O preço mudou no catálogo: mostra o carrinho atualizado, confirma o novo total e chama `fechar_pedido` de novo |
| `pedido_inexistente` | 404 | O número não é desta conversa |
| `pedido_nao_cancelavel` | 409 | A unidade já decidiu: transfere para a equipe |

Outros erros: 401 (chave ausente ou inválida), 403 (chave sem o escopo), 404 (conversa inexistente), 400 (campo inválido; `detalhes.campo` diz qual).

---

## Skills

### 1. `buscar_catalogo`

**Descrição para o agente:** Procura itens no catálogo de uma unidade. Use sempre antes de citar preço, confirmar que um item existe ou colocar algo no carrinho. Nunca invente item nem preço: só vale o que esta busca devolver. `preco` nulo significa que o item não tem preço cadastrado e não pode entrar em pedido.

`GET /api/v1/catalogo/busca`

| Parâmetro | Obrigatório | Descrição |
|---|---|---|
| `conversation_id` | sim | Conversa atual |
| `q` | não | O que o cliente pediu ("temaki de salmão", "tempura"). Sem acento, sem diferença de maiúsculas e tolerante a erro de digitação. Vazio lista os primeiros itens |
| `unidade` | recomendado | Código da unidade (`morumbi`, `einstein`, `vila_nova_conceicao`, `patio_guedala`). Sem ele, busca em todas |

Retorno (até 20 itens, só ativos, do mais relevante para o menos):

```json
{"itens": [
  {"item_id": 1027, "codigo": "GUE-0138", "unidade": "patio_guedala", "categoria": "Pratos quentes", "nome": "Tempurá Moriawase",
   "descricao": null, "preco": 136.0, "alcoolico": false, "delivery": "a_confirmar", "tipo": "item", "observacao": null},
  {"item_id": 951, "codigo": "GUE-0062", "unidade": "patio_guedala", "categoria": "Temakis — cone", "nome": "Toro",
   "descricao": null, "preco": null, "alcoolico": false, "delivery": "a_confirmar", "tipo": "item", "observacao": "preço sob consulta"}
]}
```

### 2. `ver_carrinho`

**Descrição para o agente:** Mostra o carrinho atual da conversa, o total e o que falta para fechar. Normalmente não é preciso chamar: o carrinho já vem no contexto de cada mensagem e em toda resposta das outras Skills.

`GET /api/v1/carrinho/{conversation_id}` → `{"ok": true, "carrinho": {...}}`

### 3. `adicionar_item`

**Descrição para o agente:** Coloca um item no carrinho. Use o `item_id` devolvido por `buscar_catalogo`. O preço é sempre o do catálogo; não envie preço. O primeiro item define a unidade do pedido.

`POST /api/v1/carrinho/{conversation_id}/itens`

| Campo | Obrigatório | Descrição |
|---|---|---|
| `item_id` | sim | `item_id` do catálogo |
| `quantidade` | não | 1 a 99 (padrão 1) |
| `observacao` | não | Pedido do cliente para o item ("sem cebola"), até 300 caracteres |

Exemplo: `{"item_id": 1027, "quantidade": 2, "observacao": "sem cebola"}`. O mesmo item com a mesma observação soma a quantidade; com observação diferente vira outra linha.

### 4. `alterar_item`

**Descrição para o agente:** Muda a quantidade ou a observação de uma linha do carrinho. Use o `id` da linha (campo `id` em `carrinho.itens`). Quantidade 0 remove a linha.

`PATCH /api/v1/carrinho/{conversation_id}/itens/{id}` — corpo: `{"quantidade": 3}` e/ou `{"observacao": "bem passado"}`.

### 5. `remover_item`

**Descrição para o agente:** Tira uma linha do carrinho. Use o `id` da linha.

`DELETE /api/v1/carrinho/{conversation_id}/itens/{id}`

### 6. `esvaziar_carrinho`

**Descrição para o agente:** Remove todos os itens. Use quando o cliente quiser recomeçar ou trocar de unidade. Depois disso a unidade deixa de estar definida.

`DELETE /api/v1/carrinho/{conversation_id}`

### 7. `definir_entrega`

**Descrição para o agente:** Registra como o cliente quer receber: entrega ou retirada, endereço, horário desejado e observação geral. Também registra a confirmação de maioridade quando houver bebida alcoólica. Envie só os campos que o cliente informou; os outros ficam como estão.

`PUT /api/v1/carrinho/{conversation_id}/entrega`

| Campo | Descrição |
|---|---|
| `unidade` | Código da unidade. Só pode mudar com o carrinho vazio |
| `modalidade` | `delivery` ou `retirada` |
| `endereco` | Endereço completo (obrigatório para `delivery`) |
| `horario` | Texto livre do horário desejado ("hoje às 20h") |
| `observacao` | Observação geral do pedido |
| `maioridade_confirmada` | `true` **somente** depois de o cliente confirmar, com as próprias palavras, que tem 18 anos ou mais |

Exemplo: `{"modalidade": "delivery", "endereco": "Rua Jacarandá, 200, ap. 12 — Morumbi", "horario": "hoje às 20h"}`.

### 8. `fechar_pedido`

**Descrição para o agente:** Fecha o carrinho e envia o pré-pedido para a unidade. Só chame depois de ler o resumo (itens, total, modalidade, endereço) para o cliente e ele confirmar. O pedido ainda **não está confirmado**: a unidade confirma ou recusa, e o cliente é avisado automaticamente. Diga ao cliente o número do pedido e que a unidade vai confirmar.

`POST /api/v1/pedidos` — corpo: `{"conversation_id": "<conversa>"}`

Retorno (HTTP 201):

```json
{
  "ok": true,
  "pedido": {
    "numero": "48213", "status": "aguardando_loja",
    "conversation_id": "bykoji:can_49fce1f15a43f87f22739d60:5511942717046",
    "unidade": "patio_guedala", "modalidade": "retirada", "endereco": null, "horario": null, "observacao": null,
    "itens": [
      {"item_id": 1027, "codigo": "GUE-0138", "nome": "Tempurá Moriawase", "categoria": "Pratos quentes", "quantidade": 2,
       "preco_unitario": 136.0, "subtotal": 272.0, "observacao": null, "alcoolico": false},
      {"item_id": 1053, "codigo": "GUE-0164", "nome": "Sapporo", "categoria": "Cervejas", "quantidade": 1,
       "preco_unitario": 55.0, "subtotal": 55.0, "observacao": null, "alcoolico": true}
    ],
    "subtotal": 327.0, "taxa_entrega": null, "total": 327.0, "tem_alcoolico": true,
    "motivo": null, "cliente_nome": "Ana Souza", "criado_em": "2026-10-10T16:29:00Z", "decidido_em": null
  },
  "carrinho": {"...": "vazio: o carrinho foi fechado"}
}
```

Depois de fechado, o pedido não muda. Se o cliente quiser outra coisa, `adicionar_item` abre um carrinho novo (outro pedido).

### 9. `consultar_pedido`

**Descrição para o agente:** Informa a situação de um pedido desta conversa: aguardando a unidade, confirmado, recusado (com o motivo) ou cancelado.

`GET /api/v1/pedidos/{numero}?conversation_id=<conversa>` → `{"ok": true, "pedido": {...}}`

`status`: `aguardando_loja`, `confirmado`, `recusado`, `cancelado`.

### 10. `cancelar_pedido`

**Descrição para o agente:** Cancela um pedido desta conversa a pedido do cliente. Só funciona enquanto a unidade ainda não confirmou nem recusou. Se já foi decidido, transfira para a equipe.

`POST /api/v1/pedidos/{numero}/cancelar` — corpo: `{"conversation_id": "<conversa>", "motivo": "cliente desistiu"}`

---

## Regras que o gateway garante (o agente não precisa, e não consegue, contornar)

1. Item só entra se estiver ativo, com preço e com `delivery` diferente de `nao`.
2. Um carrinho é de uma unidade só.
3. O preço é sempre o do catálogo: copiado quando o item entra e conferido de novo ao fechar.
4. Carrinho com bebida alcoólica só fecha com `maioridade_confirmada = true`.
5. Entrega exige endereço; `pedido_minimo` e `aceita_retirada` da unidade são respeitados.
6. O pedido só é visto e cancelado pela conversa que o fez, e só antes de a unidade decidir.

## O que acontece depois de `fechar_pedido` (sem ação do agente)

1. A unidade recebe o resumo no WhatsApp de pedidos (se houver número cadastrado) e o pedido aparece no painel.
2. A unidade confirma pelo painel ou respondendo `OK <numero>`; recusa com `NÃO <numero> <motivo>`. Quem decide primeiro vale.
3. O cliente recebe do gateway, na mesma conversa, o aviso de confirmação ou de recusa (com o motivo). Na recusa, os itens voltam para o carrinho.
4. No turno seguinte, `ultimo_pedido.status` já vem atualizado no pacote.

## Orientações para o prompt do agente

- Antes de citar preço ou adicionar item, chame `buscar_catalogo` com a unidade.
- Se o cliente não disse a unidade, pergunte antes de montar o pedido.
- Item com `preco` nulo: não diga valor e não adicione. Transfira com `acao = transferir_humano` e `motivo = pedido_para_confirmar`.
- Com bebida alcoólica no carrinho (`tem_alcoolico = true`), pergunte: "Você confirma que tem 18 anos ou mais?". Só depois da confirmação chame `definir_entrega` com `maioridade_confirmada: true`. Se o cliente negar, remova as bebidas.
- Antes de `fechar_pedido`, leia o resumo e peça a confirmação do cliente.
- Campo nulo em `unidades` (taxa, área de entrega, formas de pagamento, pedido mínimo): diga que a unidade confirma no retorno do pré-pedido.
- Depois de fechar: informe o número e que a unidade vai confirmar. Não diga que o pedido está confirmado nem prometa prazo.
