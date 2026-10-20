# Skills do agente — catálogo, pedidos e unidades

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
    "carrinho": { "...": "igual ao campo carrinho das respostas abaixo", "atualizado_em": "2026-10-10T21:10:48-03:00" },
    "carrinho_expirado": false,
    "itens_expirados": [],
    "ultimo_pedido": {"numero": "48213", "status": "aguardando_loja", "status_descricao": "Aguardando a confirmação da unidade", "total": 327.0, "criado_em": "2026-10-10T13:29:00+00:00", "atualizado_em": "2026-10-10T10:29:00-03:00"},
    "ultimos_pedidos": [{"numero": "48213", "status": "aguardando_loja", "status_descricao": "Aguardando a confirmação da unidade", "unidade": "patio_guedala", "total": 327.0, "atualizado_em": "2026-10-10T10:29:00-03:00"}]
  }
},
"unidades": [
  {"codigo": "patio_guedala", "nome": "Pátio Guedala", "endereco": "Av. Comendador Adibo Ares, 9 — Morumbi",
   "telefone": null, "horarios": {"seg": [["12:00","15:00"],["19:00","22:00"]]}, "aberta_agora": true, "proxima_abertura": null,
   "formas_pagamento": null, "taxa_entrega": null, "pedido_minimo": null, "area_entrega": null, "aceita_retirada": null,
   "regras": "Horários informados são do salão; horário e condições de delivery ainda a confirmar."}
]
```

- `carrinho.atualizado_em`: data e hora da última alteração do carrinho (item ou entrega), no fuso da empresa; `null` quando não há carrinho aberto. Serve para o agente saber se o carrinho é desta conversa ou sobrou de outro dia. Nas respostas das Skills o mesmo campo vem em UTC (`+00:00`).
- `carrinho_expirado`: `true` quando o carrinho anterior ficou parado mais tempo que o prazo do agente (padrão 6 horas) e foi arquivado quando o cliente voltou a escrever. `itens_expirados` traz o que havia: `[{"nome": "Edamame", "quantidade": 2}]`. O `carrinho` já vem vazio. O agente deve **perguntar se o cliente quer refazer o pedido**, e só então adicionar os itens de novo (com `adicionar_item`). O aviso continua nos turnos seguintes até o cliente montar outro carrinho, por no máximo 24 horas. Pedido já fechado nunca expira por esta regra.
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
| `unidade_diferente` | 422 | Explica que um pedido é de uma unidade só; pergunta se quer trocar de unidade (esvaziar) ou manter. `detalhes` traz `unidade_do_carrinho` e `unidade_do_item`. Vem antes do preço: item de outra unidade nunca volta `sem_preco` |
| `item_nao_buscado` | 422 | O `item_id` não veio de `buscar_catalogo` nesta conversa nos últimos 30 minutos: **busca de novo** e usa o id devolvido. Não transfere |
| `item_indisponivel` | 422 | O item não é servido no dia e horário do pedido (`detalhes.motivo_indisponivel`, `feriado`, `resumo`). Em `adicionar_item` e em `fechar_pedido` (aqui com `detalhes.itens`). Diz quando é servido e oferece outro item ou outro horário |
| `unidade_inexistente` | 404 | Usa um dos códigos de `unidades` |
| `carrinho_cheio` | 422 | Avisa o limite de 50 linhas |
| `linha_inexistente` | 404 | Usa os `id` das linhas do carrinho devolvido |
| `carrinho_vazio`, `falta_modalidade`, `falta_endereco`, `falta_maioridade` | 422 | Pergunta ao cliente o que falta |
| `retirada_indisponivel` | 422 | Oferece entrega |
| `abaixo_do_minimo` | 422 | Informa o `pedido_minimo` e pergunta se quer acrescentar algo |
| `preco_alterado` | 409 | O preço mudou no catálogo: mostra o carrinho atualizado, confirma o novo total e chama `fechar_pedido` de novo |
| `pedido_inexistente` | 404 | O número não é desta conversa |
| `horario_passado` | 422 | O horário marcado já passou (ao marcar ou ao fechar): pergunta outro horário ao cliente e chama `definir_entrega` de novo. `detalhes.proxima_abertura` diz quando a unidade abre |
| `horario_fora_funcionamento` | 422 | A unidade não funciona naquele dia e hora: oferece horário dentro de `detalhes.aberta_no_dia` ou a partir de `detalhes.proxima_abertura` |
| `horario_invalido` | 422 | Não deu para ler a hora: pergunta ao cliente um horário claro, ou envia `agendado_para` |
| `horario_muito_distante` | 422 | Agendamento só até 7 dias à frente |
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
| `categorias` | não | `true` → em vez de itens, devolve as categorias da unidade com a quantidade de itens (PED2) |
| `categoria` | não | Nome da categoria → só os itens dela que podem ser pedidos, 10 por página, sempre com preço. Sem acento e sem maiúsculas; "temaki" acha "Temakis — cone" (PED2) |
| `pagina` | não | Página dos itens da categoria (padrão 1, 10 por página). Com `categorias=true`: página das categorias, 9 por página; sem `pagina`, vêm todas (DISP1) |

Com `categorias` ou `categoria`, a `unidade` é necessária; sem ela vale a do carrinho aberto e, sem carrinho, a chamada volta 400 com `detalhes.campo = "unidade"`.

```json
{"unidade": "patio_guedala", "total_itens": 193, "categorias": [{"categoria": "Baterá", "qtd_itens": 6}, {"categoria": "Sashimis", "qtd_itens": 14}]}
```

```json
{"unidade": "patio_guedala", "categoria": "batera", "categorias_encontradas": ["Baterá"], "pagina": 1, "por_pagina": 10, "total": 6, "tem_mais": false,
 "itens": [{"item_id": 812, "codigo": "GUE-0412", "unidade": "patio_guedala", "categoria": "Baterá", "nome": "Baterá Shake", "descricao": null,
            "preco": 58.0, "alcoolico": false, "delivery": "a_confirmar", "tipo": "item", "observacao": null}]}
```

**Disponibilidade (DISP1).** Todo item traz `disponivel` (bool), `indisponivel_motivo` (`dia_da_semana`, `feriado`, `horario` ou nulo) e `disponibilidade` (nulo, ou `{"dias_semana", "exceto_feriado", "horario", "resumo"}`); a resposta traz `data_referencia`, o dia e a hora do pedido usados na conferência (o horário agendado do carrinho; sem ele, agora). Item com `disponivel: false` não entra no carrinho: diga ao cliente quando ele é servido (`disponibilidade.resumo`) e ofereça outro. As categorias vêm na ordem do cardápio, com `total_categorias`, `pagina`, `por_pagina`, `tem_mais` e `tem_cardapio`. Só vale usar em `adicionar_item` um `item_id` devolvido por esta busca nesta conversa nos últimos 30 minutos. Contrato completo: `docs/ESPEC_DISP1.md`, seção 8.

**Opções do item (CAT3-b).** Todo item traz `grupos` (lista vazia quando não tem): `[{"grupo_id", "nome", "obrigatorio", "minimo", "maximo", "repetir", "opcoes": [{"opcao_id", "nome", "preco_extra", "disponivel"}]}]`. Com grupo obrigatório, pergunte a escolha antes de `adicionar_item`. Contrato completo na seção 14.1 de `docs/ESPEC_CAT3.md`.

**Esgotado (CAT3-a).** Item esgotado na hora do pedido vem com `disponivel: false`, `indisponivel_motivo: "esgotado"`, `disponivel_a_partir` (ISO, fuso da unidade: quando volta) e `esgotado_resumo` ("esgotado; volta hoje às 18h", "esgotado hoje"). Esses dois campos só aparecem no item esgotado. `adicionar_item` e `fechar_pedido` recusam com o mesmo `item_indisponivel` de sempre, com `motivo_indisponivel: "esgotado"` e `disponivel_a_partir`. O item já no carrinho entra em `itens_indisponiveis`, com `motivo: "esgotado"`. Pedido agendado para depois da volta: disponível. O agente diz ao cliente quando volta e oferece outro item ou outro horário.

**Ordem e categorias (CAT3-a).** A lista de categorias e os itens de uma categoria seguem a ordem que a empresa definir no painel; sem ordem definida, é a de antes. Categoria renomeada continua sendo achada pelo nome antigo (até 3 nomes). Item arquivado não aparece em nenhuma busca.

Categoria que não existe: `itens` vazio e `sugestoes` com as parecidas. Para "o cardápio completo", chame com `categorias=true` e ofereça as categorias; não liste itens de cabeça.

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

| `opcoes` | não | CAT3-b: escolhas nos grupos do item, só ids da busca: `[{"grupo_id", "opcao_id", "quantidade"?}]`. Obrigatório quando o item tem grupo obrigatório |

Exemplo: `{"item_id": 1027, "quantidade": 2, "observacao": "sem cebola", "opcoes": [{"grupo_id": 12, "opcao_id": 302}]}`. O mesmo item com a mesma observação e as mesmas escolhas soma a quantidade; com observação ou escolhas diferentes vira outra linha. Opções do item: formato, recusas e preço final na seção 14.1 de `docs/ESPEC_CAT3.md`.

### 4. `alterar_item`

**Descrição para o agente:** Muda a quantidade ou a observação de uma linha do carrinho. Use o `id` da linha (campo `id` em `carrinho.itens`). Quantidade 0 remove a linha.

`PATCH /api/v1/carrinho/{conversation_id}/itens/{id}` — corpo: `{"quantidade": 3}` e/ou `{"observacao": "bem passado"}` e/ou `{"opcoes": [...]}` (CAT3-b: substitui todas as escolhas da linha; o preço é refeito).

### 5. `remover_item`

**Descrição para o agente:** Tira uma linha do carrinho. Use o `id` da linha.

`DELETE /api/v1/carrinho/{conversation_id}/itens/{id}`

### 6. `esvaziar_carrinho`

**Descrição para o agente:** Remove todos os itens. Use quando o cliente quiser recomeçar ou trocar de unidade. Depois disso o carrinho volta ao estado inicial completo: sem unidade, modalidade, endereço, horário, observação nem maioridade confirmada. **Pergunte tudo de novo**; não presuma entrega ou retirada do pedido anterior.

`DELETE /api/v1/carrinho/{conversation_id}`

### 7. `definir_entrega`

**Descrição para o agente:** Registra como o cliente quer receber: entrega ou retirada, endereço, horário desejado e observação geral. Também registra a confirmação de maioridade quando houver bebida alcoólica. Envie só os campos que o cliente informou; os outros ficam como estão.

`PUT /api/v1/carrinho/{conversation_id}/entrega`

| Campo | Descrição |
|---|---|
| `unidade` | Código da unidade. Só pode mudar com o carrinho vazio |
| `modalidade` | `delivery` ou `retirada` |
| `endereco` | Endereço completo (obrigatório para `delivery`) |
| `horario` | Horário desejado, em texto ("hoje às 20h", "19:30", "amanhã ao meio-dia", "o quanto antes"). O gateway lê a hora do texto; sem dia, vale hoje. Texto sem hora que não seja "o quanto antes" é recusado (`horario_invalido`) |
| `agendado_para` | Opcional: data e hora do pedido agendado, `AAAA-MM-DDTHH:MM` no fuso da unidade. Quando vem, vale no lugar da leitura do texto. `null` tira o agendamento |
| `observacao` | Observação geral do pedido |
| `maioridade_confirmada` | `true` **somente** depois de o cliente confirmar, com as próprias palavras, que tem 18 anos ou mais |

Exemplo: `{"modalidade": "delivery", "endereco": "Rua Jacarandá, 200, ap. 12 — Morumbi", "horario": "hoje às 20h"}`.

### 8. `fechar_pedido`

**Descrição para o agente:** Fecha o carrinho e envia o pré-pedido para a unidade. Só chame depois de ler o resumo (itens, total, modalidade, endereço) para o cliente e ele confirmar. O pedido ainda **não está confirmado**: a unidade confirma ou recusa, e o cliente é avisado automaticamente. Diga ao cliente o número do pedido e que a unidade vai confirmar.

`POST /api/v1/pedidos` — corpo: `{"conversation_id": "<conversa>"}`

Retorno (HTTP 201). O número do pedido vem na raiz (`numero`) e em `pedido.numero`:

```json
{
  "ok": true,
  "numero": "48213",
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

**Descrição para o agente:** Informa a situação de um pedido desta conversa e por onde ele já passou. Use quando o cliente perguntar pelo pedido ("cadê meu pedido?", "já saiu?"). Se o cliente não disser o número, chame sem número para ver os últimos pedidos da conversa.

**Com número:** `GET /api/v1/pedidos/{numero}?conversation_id=<conversa>` → `{"ok": true, "pedido": {...}}`

```json
{"numero": "59859", "status": "saiu_entrega", "status_descricao": "Saiu para entrega",
 "previsao_min": 40, "previsao_ate": "2026-10-10T22:22:50-03:00", "atualizado_ha_segundos": 310,
 "linha_do_tempo": [
   {"status": "aguardando_loja", "em": "2026-10-10T21:40:12-03:00"},
   {"status": "confirmado", "em": "2026-10-10T21:42:50-03:00"},
   {"status": "saiu_entrega", "em": "2026-10-10T21:58:30-03:00"}],
 "motivo": null, "itens": [...], "total": 102.0, "unidade": "patio_guedala", "modalidade": "delivery"}
```

**Sem número:** `GET /api/v1/pedidos?conversation_id=<conversa>` → `{"ok": true, "pedidos": [...]}` com os 3 últimos, do mais recente para o mais antigo: `numero`, `status`, `status_descricao`, `unidade`, `total`, `atualizado_em`.

| `status` | `status_descricao` |
|---|---|
| `aguardando_loja` | Aguardando a confirmação da unidade |
| `confirmado` | Confirmado pela unidade |
| `em_preparo` | Em preparo |
| `pronto_retirada` | Pronto para retirada (só retirada) |
| `saiu_entrega` | Saiu para entrega (só entrega) |
| `concluido` | Concluído |
| `recusado` | Recusado pela unidade (`motivo` diz por quê) |
| `cancelado` | Cancelado (`motivo`, quando houver) |

- `previsao_ate` só vem quando a unidade informou o tempo ao confirmar; senão é nulo e o agente **não inventa** prazo.
- Datas no fuso da empresa. Só a conversa dona do pedido consegue consultar.
- O cliente recebe sozinho um aviso do gateway a cada mudança de status (aparece no histórico). O agente não precisa repetir o aviso; responde quando o cliente perguntar.
- O pacote já traz `contexto_modulos.pedidos.ultimos_pedidos` (os mesmos 3) e `ultimo_pedido.status_descricao`: muitas vezes dá para responder sem chamar a Skill.

### 10. `cancelar_pedido`

**Descrição para o agente:** Cancela um pedido desta conversa a pedido do cliente. Só funciona enquanto a unidade ainda não confirmou nem recusou. Se já foi decidido, transfira para a equipe.

`POST /api/v1/pedidos/{numero}/cancelar` — corpo: `{"conversation_id": "<conversa>", "motivo": "cliente desistiu"}`

---

### 11. `info_unidades`

**Descrição para o agente:** Consulta os dados das unidades: se está aberta agora, quando abre ou fecha, os horários dos próximos 7 dias, endereço, telefone, se aceita retirada, taxa de entrega, pedido mínimo, formas de pagamento, área de entrega e regras. Use quando o cliente perguntar sobre horário, endereço, entrega ou pagamento, ou antes de afirmar qualquer uma dessas coisas. Campo `null` significa "não informado": não invente; diga que a unidade confirma. Unidade com `ativa: false` não deve ser oferecida.

`GET /api/v1/unidades` — escopo `ferramentas:catalogo` (a chave atual já serve).

| Parâmetro | Obrigatório | Descrição |
|---|---|---|
| `conversation_id` | sim | Conversa atual (precisa ser da empresa e do agente da chave) |
| `unidade` | não | Código da unidade (`morumbi`, `einstein`, `vila_nova_conceicao`, `patio_guedala`). Sem ele, vêm todas: primeiro as ativas, em ordem de nome, depois as desativadas |

Retorno:

```json
{"unidades": [
  {"codigo": "patio_guedala", "nome": "Pátio Guedala", "ativa": true,
   "endereco": "Av. Comendador Adibo Ares, 9", "telefone": null,
   "situacao": {"aberta_agora": false, "motivo": "horario", "fecha_as": null,
                "proxima_abertura": "2026-10-11T12:00-03:00", "pausada_ate": null, "pausa_motivo": null},
   "agenda": [
     {"data": "2026-10-10", "dia": "sab", "origem": "grade", "fechado": false, "faixas": [["12:00","16:00"],["19:00","23:00"]], "motivo": null},
     {"data": "2026-10-12", "dia": "seg", "origem": "excecao", "fechado": true, "faixas": [], "motivo": "feriado"}
   ],
   "aceita_retirada": true, "delivery": null, "taxa_entrega": 12.0, "pedido_minimo": 80.0,
   "formas_pagamento": "Pix, crédito e débito", "area_entrega": null, "regras": null,
   "entrega": {"taxa_modo": "bairro", "taxa_fixa": null, "frete_gratis_acima": 200.0, "tempo_min": 45,
               "bairros_atendidos": 38, "precisa_de": "bairro"},
   "retirada": {"aceita": true, "tempo_min": 20},
   "pagamentos": [{"codigo": "pix", "rotulo": "Pix", "aceita_troco": false, "instrucoes": "Chave: …"}]}
]}
```

| Campo | Significado |
|---|---|
| `ativa` | `false` = unidade desativada: não ofereça |
| `telefone` | Telefone público da unidade (texto livre), ou `null` |
| `situacao.aberta_agora` | `true`, `false` ou `null` (nenhum horário cadastrado: não informado) |
| `situacao.motivo` | `null` (aberta), `horario` (fora do horário), `excecao` (feriado ou data especial), `pausa` (fechada agora pela unidade; ver `pausada_ate` e `pausa_motivo`), `sem_horario`, `inativa` |
| `situacao.fecha_as`, `proxima_abertura`, `pausada_ate` | Data e hora no fuso da unidade, ou `null`. Pausa sem prazo não tem `proxima_abertura` |
| `agenda` | Hoje e os 6 dias seguintes, já com feriados e datas especiais (`origem`: `grade` ou `excecao`). Faixa com fim menor que o início cruza a meia-noite |
| `aceita_retirada` | `true`, `false` ou `null` |
| `delivery` | Se a unidade faz entrega: `true`, `false` ou `null` (não informado: a unidade confirma) |
| `taxa_entrega`, `pedido_minimo` | Valor em reais ou `null` |
| `entrega.taxa_modo` | `fixa`, `bairro`, `cep` ou `null` (não cadastrado). `taxa_fixa` só vem no modo `fixa` ou sem modo |
| `entrega.precisa_de` | `bairro`, `cep` ou `null`: o que perguntar ao cliente para saber a taxa. A lista de bairros não vem (pode ter centenas); `bairros_atendidos` é só a contagem |
| `entrega.frete_gratis_acima` | Subtotal a partir do qual a entrega é grátis, ou `null` |
| `entrega.tempo_min`, `retirada.tempo_min` | Tempo estimado em minutos cadastrado pela unidade, ou `null` (não prometa prazo) |
| `retirada.aceita` | Igual a `aceita_retirada` |
| `pagamentos` | Formas aceitas, na ordem da unidade. Lista vazia = não cadastradas: use `formas_pagamento` (texto) ou diga que a unidade confirma. Só `dinheiro` pode ter `aceita_troco: true`; `instrucoes` pode ser informada ao cliente |
| `formas_pagamento`, `area_entrega`, `regras` | Texto livre cadastrado pela unidade, devolvido como está, ou `null` |

Erros (mesmo envelope das outras Skills): sem chave ou chave inválida 401; chave sem o escopo 403; conversa inexistente, de outra empresa ou de outro agente 404; `unidade` com código que não existe na empresa 404; sem `conversation_id` 400.

A resposta não traz identificador interno, WhatsApp de pedidos nem dados de auditoria. O pacote de cada turno continua trazendo o bloco `unidades` resumido; esta Skill serve para o detalhe (agenda dos próximos dias e situação na hora da pergunta).

Os blocos `entrega`, `retirada` e `pagamentos` existem desde o G4-a (11/10/2026), só informativos: o carrinho ainda não calcula taxa por bairro/CEP nem confere pagamento (isso é o G4-b, junto com a Skill nova `consultar_entrega`, contrato em `docs/ESPEC_GESTAO.md` 11.11, e a troca de `abaixo_do_minimo` por `abaixo_minimo`).

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
- **Nunca diga que o pedido foi fechado, registrado ou enviado sem `fechar_pedido` ter devolvido `ok: true` com `numero` neste turno.** Se o gateway receber esse anúncio sem o fechamento, refaz o turno com `aviso_do_gateway` no pacote e, se repetir, não envia a sua resposta.
- Carrinho com `horario_vencido: true`: o horário guardado já passou. Pergunte outro horário antes de fechar; não repita o horário antigo ao cliente.
- Não marque horário que o cliente não disse. Unidade fechada agora: ofereça os horários a partir de `proxima_abertura` e espere a escolha.
- Depois de fechar: informe o número e que a unidade vai confirmar. Não diga que o pedido está confirmado nem prometa prazo.
