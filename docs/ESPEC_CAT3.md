# CAT3 — Gestão completa do cardápio pelo painel

**Situação: aprovada em 20/10/2026 (seção 12). CAT3-a implementado (seção 13); CAT3-b implementado (seção 14: contrato final das Skills e do painel). CAT3-c a seguir.** Módulo `catalogo` (opções do item tocam também o `pedidos`). Foto do item fica para o ARQ1. Decisões em aberto na seção 10.

---

## 1. Hoje (só leitura)

- **Itens:** `catalogo_itens` (id, unidade, categoria em texto, nome, descrição, preço, `codigo` único na empresa, `tipo` item/adicional/opcao_executivo/regra, alcoólico, delivery, ativo, observação, disponibilidade por dia/feriado/horário). Não há criação de item pelo painel: só o CSV cria; o painel (CAT2) edita item existente (`PATCH …/catalogo/itens/{id}`, com `atualizado_em` → 409).
- **Categorias:** não existem como cadastro. São o texto `categoria` de cada item; a ordem é a do primeiro item importado (menor id) e a categoria some quando fica sem itens. Ordem do item dentro da categoria: id.
- **Códigos:** prefixo da unidade + 4 dígitos (`GUE-0001`, `MOR-…`, `EIN-…`, `VNC-…`).
- **By Koji (produção, 20/10):** 888 itens em 4 unidades (808 ativos), 19 a 39 categorias por unidade; preço só no Pátio Guedala (193). **364 nomes distintos entre os 808 ativos**: o mesmo prato está repetido por unidade, sem ligação entre as cópias.
- **Esgotado:** **não existe**. Não há `item_em_falta` nem coluna; o mais próximo é a recusa `item_indisponivel` (DISP1, com `motivo_indisponivel` = dia_da_semana/feriado/horario) e o `ativo=false`.
- **Adicionais:** são itens próprios (`tipo=adicional`, ex.: "Adicional de azeite trufado"), sem ligação com o prato; não há grupos de opções.
- **Importação:** `POST …/catalogo/importar` (CSV, prévia com `simulacao=true`). Casa por `codigo`; o que mudou no arquivo **sobrescreve** a edição do painel; item que sumiu do arquivo fica inativo (CAT2, limite conhecido).
- **Auditoria:** `gestao_alteracoes` (`GET …/gestao/alteracoes`) já registra antes/depois das edições do CAT2.

## 2. Itens

| Operação | Rota | Regras |
|---|---|---|
| Criar | `POST …/catalogo/itens` | Unidade, categoria (id), nome, preço opcional, tipo, descrição, alcoólico, delivery, disponibilidade. Código gerado (2.1) |
| Duplicar | `POST …/catalogo/itens/{id}/duplicar` `{"unidade"?, "categoria_id"?, "nome"?}` | Cópia com código novo; nasce **inativo** por padrão (decisão 2), para revisar antes de aparecer ao agente |
| Editar | `PATCH …/catalogo/itens/{id}` (CAT2) | + `codigo`, `categoria_id`, `posicao`, `aplicar_em` (seção 4) |
| Arquivar / desarquivar | `POST …/itens/{id}/arquivar` · `…/desarquivar` | **Nunca apaga** (pedidos guardam a cópia). Arquivado some do agente e da lista padrão do painel (`?arquivados=true` mostra) e não pode ser pedido |
| Mover de categoria | `PATCH` com `categoria_id` (e `posicao` opcional) | Mesma unidade; entra no fim da categoria se `posicao` não vier |
| Ordenar | `PUT …/catalogo/categorias/{id}/ordem` `{"itens":[id,…]}` | Lista completa da categoria (o que faltar vai para o fim) |

Arquivar ≠ desativar: `ativo=false` continua sendo "pausado" (volta a qualquer momento e fica visível no painel); `arquivado_em` tira do dia a dia. Migração: nada é arquivado automaticamente.

### 2.1 Código do item

- Gerado no padrão atual: prefixo da unidade + próximo número livre com 4 dígitos (`GUE-0224`). Prefixo novo por unidade: `tenant_unidades.prefixo_codigo` (2 a 4 letras), preenchido na migração com o que já existe (GUE, MOR, EIN, VNC); unidade nova recebe as 3 primeiras letras do código, sem repetir.
- Editável (formato atual `^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$`, único na empresa → 409 `codigo_em_uso`). Trocar código não afeta pedidos antigos (guardam a cópia) nem o carrinho (usa o id).

## 3. Categorias

Cadastro novo `catalogo_categorias` (id, unidade, nome, posição, arquivada_em) e `catalogo_itens.categoria_id`. A migração cria uma categoria por (unidade, texto) na ordem de hoje e liga os itens; o texto `categoria` do item continua preenchido (cópia do nome) para a busca, os pedidos e a imagem anterior.

| Operação | Rota | Regras |
|---|---|---|
| Listar | `GET …/catalogo/categorias?unidade=` (já existe) | Passa a vir do cadastro, com `id`, `posicao`, `arquivada` e contagens; categoria vazia não some mais |
| Criar | `POST …/catalogo/categorias` `{"unidade","nome","posicao"?}` | Nome único na unidade (sem diferença de maiúsculas/acentos) |
| Renomear | `PATCH …/catalogo/categorias/{id}` `{"nome"}` | Atualiza o texto `categoria` dos itens dela. Pedidos antigos mantêm o nome da época (cópia). A busca do agente guarda os **nomes anteriores** (até 3) como sinônimos: `categoria=<nome antigo>` ainda encontra |
| Reordenar | `PUT …/catalogo/categorias/ordem` `{"unidade","categorias":[id,…]}` | Ordem própria por unidade; vale para o painel e para a lista de categorias do agente |
| Arquivar | `POST …/categorias/{id}/arquivar` `{"itens":"arquivar"\|"mover","destino"?}` | Não arquiva com item ativo sem dizer o que fazer com eles |

## 4. Mesmo prato em várias unidades

Proposta (decisão 3): **prato** = ligação entre os itens das unidades que são o mesmo produto.

- Tabela `catalogo_pratos` (id, código comum `codigo_prato` — ex.: `P-0042` —, nome de referência). `catalogo_itens.prato_id` liga cada item da unidade ao prato. O item de cada unidade **continua existindo** (preço, disponibilidade, esgotado e categoria podem ser diferentes por unidade).
- Migração: **não liga nada sozinha.** Rota de sugestão `GET …/catalogo/pratos/sugestoes` agrupa itens de unidades diferentes com nome normalizado igual (hoje seriam no máximo 364 grupos); o admin confirma no painel (`POST …/catalogo/pratos` com os itens).
- Editar com alcance: `PATCH …/catalogo/itens/{id}` aceita `"aplicar_em": "este" | "todas" | ["morumbi","einstein"]`. Campos que podem ser aplicados: nome, descrição, preço, tipo, alcoólico, delivery, ativo, disponibilidade, opções. **Nunca** aplicados: unidade, código, categoria e posição (cada unidade tem as suas). Resultado por unidade (seção 7).
- Copiar para outra unidade: `POST …/catalogo/copiar` `{"itens":[id…] | "categoria_id", "destino": "morumbi" | ["…"], "ligar": true}` — cria os itens na unidade destino (código novo com o prefixo dela, categoria de mesmo nome criada se faltar, inativos por padrão), já ligados ao mesmo prato. Item que já tem cópia ligada no destino é pulado (`ja_existe`).

## 5. Esgotado

- Coluna `catalogo_itens.esgotado_ate` (TIMESTAMPTZ) e `esgotado_motivo` (texto curto opcional, interno).
- `POST …/itens/{id}/esgotar` `{"ate": "fim_do_dia" | "AAAA-MM-DDTHH:MM" }` — "fim do dia" = 23:59:59 no fuso da unidade; `POST …/itens/{id}/repor` volta antes da hora. **Volta sozinho**: não há tarefa; o item é esgotado enquanto `esgotado_ate > agora`. Aceita `aplicar_em` (todas as unidades do prato).
- Agente (reaproveita o DISP1, sem campo novo de recusa): na busca o item vem `disponivel: false` com `motivo_indisponivel: "esgotado"` e `disponivel_a_partir` (a hora em que volta); `adicionar_item`/`fechar_pedido` recusam com o mesmo `item_indisponivel` de hoje e `motivo_indisponivel: "esgotado"`. Item já no carrinho aparece em `itens_indisponiveis`. Esgotado com pedido agendado para depois da volta: disponível (vale a hora do pedido, como no DISP1).
- Painel: `disponivel_agora` (CAT2) passa a considerar esgotado; filtro `?esgotados=true`.

## 6. Opções do item (grupos de complementos)

### 6.1 Cadastro

- `catalogo_grupos` (id, item_id, nome, `obrigatorio`, `minimo`, `maximo`, posição, ativo) e `catalogo_opcoes` (id, grupo_id, nome, `preco_extra` ≥ 0, posição, ativo, `codigo` opcional).
- Regras: `0 ≤ minimo ≤ maximo ≤ 20`; obrigatório ⇔ `minimo ≥ 1`; até 10 grupos por item e 30 opções por grupo; mesma opção escolhida mais de uma vez só se o grupo permitir (`repetir: true`, ex.: "2× molho").
- Rotas: `GET/PUT …/catalogo/itens/{id}/opcoes` (o conjunto inteiro de grupos e opções do item, com `atualizado_em` → 409) e `aplicar_em` para os itens do mesmo prato.
- Os adicionais que hoje são itens (`tipo=adicional`) continuam como estão; o By Koji decide se vira opção (decisão 7).

### 6.2 Contrato das Skills (para o prompt e as Skills no Skyone)

**`buscar_catalogo`** — cada item passa a trazer (sempre, lista vazia quando não há):

```json
"grupos": [
  {"grupo_id": 12, "nome": "Escolha o molho", "obrigatorio": true, "minimo": 1, "maximo": 1, "repetir": false,
   "opcoes": [{"opcao_id": 301, "nome": "Tarê", "preco_extra": 0.0, "disponivel": true},
              {"opcao_id": 302, "nome": "Trufado", "preco_extra": 6.0, "disponivel": true}]}
]
```

Só grupos e opções ativos. `preco` do item continua sendo o preço base; o texto `resumo` do item ganha "(escolher: molho)" quando há grupo obrigatório.

**`adicionar_item`** — campo novo opcional `opcoes`:

```json
{"item_id": 123, "quantidade": 2, "observacao": "sem cebolinha",
 "opcoes": [{"grupo_id": 12, "opcao_id": 302}, {"grupo_id": 15, "opcao_id": 410, "quantidade": 2}]}
```

**`alterar_item`** — aceita `opcoes` (substitui todas as escolhas da linha).

Recusas novas (422, `erro.detalhes.motivo`, com `grupo_id` e o `grupo` completo em `detalhes` para o agente perguntar ao cliente):

| Motivo | Quando |
|---|---|
| `opcao_obrigatoria` | Faltou escolher em grupo obrigatório |
| `opcoes_acima_do_maximo` | Mais escolhas que o máximo do grupo |
| `opcoes_abaixo_do_minimo` | Menos que o mínimo (grupo opcional com mínimo > 0 não existe; este cobre grupo com mínimo ≥ 2) |
| `opcao_inexistente` | `opcao_id`/`grupo_id` não é deste item, ou inativo |
| `opcao_repetida` | Mesma opção mais de uma vez em grupo sem `repetir` |

Mesmo item com escolhas diferentes vira **linhas diferentes** do carrinho (como a observação diferente hoje); com escolhas iguais, soma a quantidade.

**Carrinho e pedido** — cada linha/item passa a ter:

```json
{"item_id":123,"nome":"Temaki","quantidade":2,"preco_unitario":36.0,"preco_base":30.0,
 "opcoes":[{"grupo":"Escolha o molho","opcao":"Trufado","quantidade":1,"preco_extra":6.0}],"subtotal":72.0}
```

`preco_unitario` = base + extras (o que o cliente paga por unidade); `preco_base` e `opcoes` com nome e preço **copiados** no momento (o pedido não muda se a opção mudar depois). Item sem opções: `opcoes: []` e `preco_base = preco_unitario`. Texto do pedido para a unidade ("🍱 NOVO PEDIDO") lista as opções abaixo de cada item. `fechar_pedido` revalida grupos e preços como já faz com o preço do item (`preco_alterado`).

**O que o prompt precisa saber:** antes de `adicionar_item`, se o item tem grupo obrigatório, perguntar a escolha ao cliente (ou mostrar as opções como botões/lista); repassar `grupo_id`/`opcao_id` devolvidos pela busca, nunca nomes; diante de `opcao_obrigatoria`/`opcoes_acima_do_maximo`, usar o `grupo` dos detalhes para perguntar de novo.

## 7. Operações em lote

`POST …/catalogo/lote` (só admin e plataforma):

```json
{"itens":[101,102,103],"acao":"reajustar","percentual":7.5}        // ou "valor": 2.00 (soma); arredonda em centavos, mínimo 0,01
{"itens":[…],"acao":"mover","categoria_id":44}
{"itens":[…],"acao":"pausar"} | "ativar" | "arquivar" | "desarquivar" | "esgotar" (com "ate")
```

Resposta por item: `{"resultados":[{"item_id":101,"ok":true,"antes":{"preco":30.0},"depois":{"preco":32.25}},{"item_id":103,"ok":false,"motivo":"sem_preco"}]}`. Prévia sem gravar com `"simulacao": true`. Até 500 itens por chamada; cada item numa transação própria (um que falha não desfaz os outros), auditoria única com a lista.

## 8. Importação segura

- **Exportar:** `GET …/catalogo/exportar?formato=csv|xlsx&unidade=` — mesmas colunas do CSV de hoje + `categoria_posicao`, `posicao`, `codigo_prato`, `esgotado_ate`, `arquivado` e uma aba/arquivo `opcoes` (item, grupo, opção, preço extra, mínimo, máximo). xlsx com `openpyxl` (já aprovado).
- **Importar com prévia:** `POST …/catalogo/importacoes` (arquivo CSV ou xlsx) → grava a importação **pendente** e devolve as diferenças: `novos`, `alterados` (campo a campo, antes → depois), `arquivados` (no cardápio e fora do arquivo), `sem_mudanca`, `erros` por linha. Nada é aplicado.
- **Aplicar:** `POST …/catalogo/importacoes/{id}/aplicar` (admin/plataforma), só se nada mudou no cardápio desde a prévia (senão 409 `cardapio_mudou`, gerar prévia nova). Pendente expira em 24 h.
- **Fim do "reimportar apaga a edição do painel":** cada campo do item guarda quando foi editado pelo painel (`editado_no_painel_em` por campo). Na prévia, campo editado no painel **depois** do arquivo exportado aparece como **conflito**, e o padrão é **manter o do painel**; o admin escolhe por item ou "usar o do arquivo em todos". Item fora do arquivo vai para "arquivar?" desmarcado por padrão (nunca arquiva sem marcar).
- O `POST …/catalogo/importar` de hoje continua (compatibilidade) e passa a recusar sobrescrever campo editado no painel sem `forcar=true`.

## 9. Auditoria e permissões

- Tudo em `gestao_alteracoes` (entidade `catalogo_item`, `catalogo_categoria`, `catalogo_prato`, `catalogo_opcoes`, `catalogo_lote`, `catalogo_importacao`), com antes/depois e o actor da sessão; lote e importação com a lista de itens.
- Permissões: **membro só lê**. Admin e plataforma: criar, duplicar, editar, arquivar, mover, ordenar, categorias, pratos, cópia entre unidades, opções, lote, importar. **Esgotar/repor** (decisão 9): proposta também para **membro** — é operação do dia a dia da cozinha.
- Escopo: tudo sob `X-Tenant-Scope`; item, categoria, prato e importação de outra empresa → 404.

## 10. Decisões

| # | Decisão | Proposta |
|---|---|---|
| 1 | Arquivar × desativar | Dois estados: `ativo=false` = pausado (visível no painel); arquivado = fora do dia a dia. Nenhum apaga |
| 2 | Item duplicado/copiado nasce | Inativo, para revisar antes de o agente oferecer |
| 3 | Mesmo prato nas unidades | Cadastro de **prato** com código comum (`P-0042`), cada unidade com o seu item ligado; ligação só confirmada pelo admin (com sugestão por nome) |
| 4 | O que "aplicar a todas" leva | Nome, descrição, preço, tipo, alcoólico, delivery, ativo, disponibilidade, opções; nunca código, categoria e posição |
| 5 | Renomear categoria | Atualiza os itens; guarda até 3 nomes antigos como sinônimos na busca; pedidos antigos mantêm o nome da época |
| 6 | Esgotado | `esgotado_ate` com volta automática pela hora; "fim do dia" no fuso da unidade; motivo `esgotado` no `item_indisponivel` de hoje (sem motivo novo) |
| 7 | Adicionais que hoje são itens | Continuam; o By Koji decide caso a caso se viram opção de grupo |
| 8 | Conflito na importação | Mantém o do painel por padrão; admin escolhe por item; nunca arquiva sem marcar |
| 9 | Quem pode esgotar/repor | Admin, plataforma **e membro** (operação da cozinha); o resto do item só admin e plataforma |
| 10 | Limites | 10 grupos por item, 30 opções por grupo, máximo 20 escolhas por grupo; lote até 500 itens |

## 11. Blocos

| Bloco | Conteúdo | Tamanho |
|---|---|---|
| **CAT3-a** | Migração (categorias, prato, posições, prefixo, esgotado, arquivado), itens (criar, duplicar, arquivar, código), categorias, pratos e cópia entre unidades com `aplicar_em`, esgotado no agente (busca, carrinho, fechamento), lote, auditoria e permissões | Grande |
| **CAT3-b** | Opções do item: cadastro, `buscar_catalogo`/`adicionar_item`/`alterar_item`, recusas novas, carrinho e pedido com opções e preço final, texto para a unidade, retrato do By Koji (só os campos novos mudam: `grupos: []`, `opcoes: []`, `preco_base`) | Grande |
| **CAT3-c** | Exportação CSV/xlsx, importação com prévia, conflitos e aplicação confirmada | Médio |

O CAT3-b muda o contrato das Skills: o prompt e as Skills no Skyone precisam do ajuste da seção 6.2 **antes** de o By Koji cadastrar grupos (sem grupos, nada muda para o agente).

## 12. Decisões do Marco (20/10/2026)

As 10 da seção 10, como propostas, com estes destaques:

- **3 (prato):** cadastro de prato com código comum; a ligação é sempre confirmada pelo admin. Para as combinações que já existem, o painel ganha uma tela de **sugestões de ligação**, por nome, com confirmar ou ignorar em lote. A rota que lista as sugestões está na seção 13.
- **6 (esgotado):** volta automática pela hora, sem tarefa no agendador.
- **9 (permissão):** membro pode esgotar e repor. Criar, arquivar, preço, lote e importação continuam só com admin e plataforma.
- **CAT3-a:** o retrato do By Koji continua idêntico para o agente. Nada muda na busca nem nas Skills enquanto ninguém criar prato, esgotar item ou mudar a ordem.

## 13. CAT3-a implementado — contrato para o [PAINEL]

Migração 046. Base: `/admin/v1/tenants/{t}/catalogo`. Os erros vêm no envelope de sempre, com `detalhes.campo` e, quando há, `detalhes.motivo`. Item, categoria e prato de outra empresa dão 404.

**Permissões:**
- **Admin e plataforma:** todas as escritas.
- **Membro:** lê tudo e pode **esgotar e repor**. Nas outras escritas recebe 403.

### 13.1 Item no painel

O `GET …/itens`, o `GET …/itens/{id}`, o PATCH e as rotas abaixo devolvem o item do CAT2 com os campos novos:

```json
{"…CAT2…": "…", "categoria_id": 12, "posicao": null, "prato": {"id": 3, "codigo": "P-0003", "nome": "Edamame"},
 "esgotado_ate": "2026-10-20T21:00:00+00:00", "esgotado_motivo": "acabou", "arquivado_em": null, "arquivado": false}
```

- `disponivel_agora` e `indisponivel_motivo` passam a considerar `esgotado` e o item arquivado (`item_inativo`).
- `posicao` nula: o item segue a ordem pelo nome.

**Filtros do `GET …/itens`:**
- **`arquivados`:** sem ele, os arquivados ficam fora; `arquivados=true` traz só os arquivados.
- **`esgotados`:** `esgotados=true` traz os esgotados agora; `false`, os não esgotados.
- **`categoria_id`** e **`prato_id`**: filtram por categoria e por prato.

### 13.2 Itens

| Rota | Corpo | Resposta |
|---|---|---|
| `POST …/itens` | `{"unidade","categoria_id","nome", "preco"?, "descricao"?, "observacao"?, "tipo"?="item", "alcoolico"?=false, "delivery"?="sim", "ativo"?=true, "disponibilidade"?, "codigo"?, "posicao"?}` | 201, o item. Código gerado: prefixo da unidade + próximo número (`GUE-0224`). 409 `codigo_em_uso` |
| `POST …/itens/{id}/duplicar` | `{"unidade"?, "categoria_id"?, "nome"?}` | 201, a cópia: **inativa**, código novo. Em outra unidade, entra na categoria de mesmo nome, criada se faltar |
| `PATCH …/itens/{id}` | O do CAT2 + `tipo`, `alcoolico`, `delivery`, `observacao`, `codigo`, `categoria_id`, `posicao`, `aplicar_em` | O item, com `aplicado_em` quando houve alcance: `[{"unidade","item_id","ok","alterados":[…]} \| {…,"ok":false,"motivo"}]` |
| `POST …/itens/{id}/arquivar` · `…/desarquivar` | — | O item |
| `PUT …/categorias/{id}/ordem` | `{"itens":[id,…]}` (o que faltar vai para o fim) | `{"categoria_id","itens":[{"item_id","posicao","posicao_anterior"}]}` |
| `POST …/itens/{id}/esgotar` (membro pode) | `{"ate"?: "fim_do_dia" (padrão) \| "AAAA-MM-DDTHH:MM", "motivo"?, "aplicar_em"?}` | O item (+ `aplicado_em`). `ate` no fuso da unidade, no futuro e até 30 dias; senão 400 `passado`/`longe_demais` |
| `POST …/itens/{id}/repor` (membro pode) | `{"aplicar_em"?}` | O item |

**`aplicar_em`** aceita `"este"` (padrão), `"todas"` ou uma lista de unidades, e vale para os itens do mesmo prato.
- **Leva:** nome, descrição, preço, tipo, alcoólico, delivery, ativo e disponibilidade, além do esgotado nas rotas de esgotar e repor.
- **Nunca leva:** código, categoria e posição.
- **400 `sem_prato`:** o item não está ligado a um prato.
- **400 `unidade_sem_item_do_prato`:** a lista traz unidade sem item do prato, com `unidades` nos detalhes.
- **Concorrência:** o 409 por `atualizado_em` do CAT2 continua valendo.

### 13.3 Categorias

| Rota | Corpo | Resposta |
|---|---|---|
| `GET …/categorias?unidade=&arquivadas=` | — | `{"unidade","categorias":[{"id","unidade","nome","categoria","posicao","ordem","nomes_anteriores","arquivada","arquivada_em","qtd_itens","itens_ativos","itens_no_cardapio","itens_arquivados"}]}`. Primeiro as categorias com posição; as demais na ordem do cardápio importado. Categoria vazia continua na lista |
| `POST …/categorias` | `{"unidade","nome","posicao"?}` | 201. 409 `nome_em_uso`, com nomes comparados sem acento nem maiúsculas |
| `PATCH …/categorias/{id}` | `{"nome"}` | A categoria. Os itens recebem o nome novo; `nomes_anteriores` guarda até 3, que a busca do agente ainda reconhece |
| `PUT …/categorias/ordem` | `{"unidade","categorias":[id,…]}` | `{"unidade","categorias":[{"id","posicao"}]}`. Vale para o painel e para o agente |
| `POST …/categorias/{id}/arquivar` | `{"itens"?: "arquivar" \| "mover", "destino"?}` | A categoria + `itens_movidos` e `itens_arquivados`. Com itens e sem `itens`: 409 `categoria_com_itens` (`qtd_itens`) |
| `POST …/categorias/{id}/desarquivar` | — | A categoria. Os itens arquivados com ela continuam arquivados |

### 13.4 Pratos e sugestões de ligação

| Rota | Corpo | Resposta |
|---|---|---|
| **`GET …/pratos/sugestoes?ignoradas=&limite=100&cursor=`** | — | `{"total","grupos":[{"chave","nome","unidades":[…],"prato":null\|{"id","codigo","nome"},"conflito":null\|"varios_na_unidade","itens":[{"item_id","codigo","unidade","nome","categoria","preco","ativo","prato_id"}]}],"proximo_cursor"}` |
| `POST …/pratos/sugestoes/confirmar` | `{"grupos":[{"chave","itens"?:[id,…]}]}` (até 400) | `{"resultados":[{"chave","ok":true,"prato":{…},"itens_ligados":[…]} \| {"chave","ok":false,"motivo"}]}` |
| `POST …/pratos/sugestoes/ignorar` | `{"chaves":[…]}` | `{"resultados":[{"chave","ok"}]}` |
| `POST …/pratos/sugestoes/restaurar` | `{"chaves":[…]}` | `{"restauradas":[…]}` |
| `GET …/pratos?q=&limite=&cursor=` · `GET …/pratos/{id}` | — | `{"id","codigo","nome","criado_em","itens":[{"item_id","codigo","unidade","nome","preco","ativo","arquivado","esgotado_ate"}]}` |
| `POST …/pratos` | `{"itens":[id,…],"nome"?}` | 201, o prato (`P-0001`…) |
| `PATCH …/pratos/{id}` | `{"nome"}` | O prato |
| `POST …/pratos/{id}/itens` | `{"itens":[…]}` | O prato |
| `DELETE …/pratos/{id}/itens/{item_id}` | — | O prato |
| `DELETE …/pratos/{id}` | — | `{"ok":true}`. 409 `prato_com_itens` |

**Sugestões:**
- **Como são montadas:** itens não arquivados de **unidades diferentes** com o mesmo nome. A comparação ignora acento, maiúsculas e pontuação. Entram só grupos com algum item sem prato e no máximo um prato no grupo.
- **Chave:** o nome normalizado. As sugestões vêm em ordem de chave; o `cursor` é a última chave da página.
- **`varios_na_unidade`:** a unidade aparece duas vezes no grupo. O admin escolhe os `itens`; sem a escolha, o resultado vem `ok:false` com esse motivo.
- **Ignorada:** a sugestão volta sozinha se aparecer item novo com o mesmo nome.

**Motivos de recusa ao ligar itens:**
- **`item_ja_ligado`:** o item já está em outro prato.
- **`unidade_repetida`:** cada unidade entra uma vez no prato.
- **`sugestao_inexistente`** e **`item_fora_do_grupo`**.

### 13.5 Copiar e lote

| Rota | Corpo | Resposta |
|---|---|---|
| `POST …/copiar` | `{"itens":[…]` ou `"categoria_id"`, `"destino": "morumbi" \| […]`, `"ligar"?: true}` | `{"resultados":[{"item_id","destino","ok","novo_item_id","codigo"} \| {…,"ok":false,"motivo":"ja_existe"\|"mesma_unidade"}]}`. Cópias inativas, com o código do destino e a categoria de mesmo nome. Com `ligar`, ficam no prato da origem, criado se faltar |
| `POST …/lote` | `{"itens":[…≤500],"acao","simulacao"?}` | `{"simulacao","resultados":[{"item_id","ok","antes","depois"} \| {"item_id","ok":false,"motivo"}]}` |

**Ações do lote:**
- **`reajustar`:** com `percentual` (−90 a 500) ou `valor` (soma, pode ser negativo). Arredonda em centavos, mínimo 0,01; item sem preço dá `sem_preco`.
- **`mover`:** com `categoria_id`; item de outra unidade dá `unidade_diferente`.
- **`pausar`, `ativar`, `arquivar`, `desarquivar`.**
- **`esgotar`:** com `ate` e `motivo`.
- **`repor`.**

Cada item roda numa transação própria, e o lote grava um único registro no histórico.

### 13.6 Histórico e agente

- **Histórico:** tudo vai para `GET /tenants/{t}/gestao/alteracoes`, com as entidades `catalogo_item`, `catalogo_categoria`, `catalogo_prato` e `catalogo_lote`, e também para a auditoria.
- **Prefixo de código da unidade:** `prefixo_codigo` aparece na lista de unidades do painel. Foi a única diferença no retrato do By Koji e não é vista pelo agente.
- **Agente:**
  - item arquivado não aparece;
  - esgotado vem como no `docs/SKILLS.md`;
  - categorias e itens seguem a ordem definida no painel e, sem ela, a de antes;
  - categoria renomeada é achada pelo nome antigo.
- **Testes:** `tests/test_cat3a.py` (6).

## 14. CAT3-b implementado (migração 047)

### 14.1 Contrato final para o prompt e as Skills (substitui o rascunho da seção 6.2)

Enquanto nenhum grupo for cadastrado, nada muda para o agente além dos campos novos: `grupos: []` na busca, `opcoes: []` e `preco_base` nas linhas.

**Diferenças em relação ao rascunho da 6.2:**
- As escolhas no carrinho e no pedido trazem também `grupo_id` e `opcao_id`, que o `alterar_item` usa.
- O `resumo` do item não ganhou "(escolher: …)": o agente usa `grupos[].obrigatorio`.
- `disponivel` da opção é sempre `true` por enquanto (não há esgotado de opção).

**1. `buscar_catalogo`:** cada item traz sempre `grupos`, com lista vazia quando o item não tem grupos. Só vêm grupos e opções ativos.
```json
"grupos": [
  {"grupo_id": 12, "nome": "Escolha o molho", "obrigatorio": true, "minimo": 1, "maximo": 1, "repetir": false,
   "opcoes": [{"opcao_id": 301, "nome": "Tarê", "preco_extra": 0.0, "disponivel": true},
              {"opcao_id": 302, "nome": "Trufado", "preco_extra": 6.0, "disponivel": true}]},
  {"grupo_id": 15, "nome": "Adicionais", "obrigatorio": false, "minimo": 0, "maximo": 3, "repetir": true,
   "opcoes": [{"opcao_id": 410, "nome": "Cream cheese", "preco_extra": 4.0, "disponivel": true}]}
]
```
- **`obrigatorio`:** vale `true` quando `minimo` ≥ 1.
- **`maximo`:** total de escolhas no grupo, somando as quantidades.
- **`repetir: true`:** a mesma opção pode vir com `quantidade` > 1 ("2× cream cheese").
- **`preco`:** continua sendo o preço base do item.

**2. `adicionar_item`:** campo opcional `opcoes`, só com ids. Nunca mande nomes.
```json
{"item_id": 123, "quantidade": 2, "observacao": "sem cebolinha",
 "opcoes": [{"grupo_id": 12, "opcao_id": 302}, {"grupo_id": 15, "opcao_id": 410, "quantidade": 2}]}
```
- **Formato:** `quantidade` da opção vai de 1 a 20 (padrão 1); no máximo 50 escolhas por chamada.
- **Grupos sem escolha:**
  - grupo obrigatório sem escolha é recusado;
  - grupo opcional pode ficar sem escolha.
- **Linhas do carrinho:**
  - mesmo item com a mesma observação e as **mesmas escolhas** soma a quantidade;
  - escolhas diferentes viram **outra linha**.

**3. `alterar_item`:** `PATCH /api/v1/carrinho/{conversation_id}/itens/{id}`
- `{"opcoes": [...]}` **substitui todas** as escolhas da linha, e o preço é refeito.
- `{"opcoes": []}` tira todas; num grupo obrigatório isso dá recusa.
- Pode vir junto com `quantidade` e `observacao`.

**4. Recusas novas:** HTTP 422, com `erro.detalhes.motivo`, `campo: "opcoes"`, `item_id`, `grupo_id` e o **`grupo` completo**, no mesmo formato da busca. No `alterar_item` e no `fechar_pedido` vem também `linha_id`.

| Motivo | Quando | O que o agente faz |
|---|---|---|
| `opcao_obrigatoria` | Grupo obrigatório sem escolha. No `fechar_pedido`, quando o grupo foi criado depois de o item entrar no carrinho | Pergunta ao cliente (pode mandar as opções do `grupo` como botões ou lista) e chama de novo (`alterar_item` com `linha_id` no fechamento) |
| `opcoes_acima_do_maximo` | Mais escolhas que o `maximo` do grupo | Pergunta quais manter |
| `opcoes_abaixo_do_minimo` | Escolheu algo, mas menos que o `minimo` (grupo com mínimo ≥ 2) | Pergunta o que falta |
| `opcao_inexistente` | `grupo_id` ou `opcao_id` não é deste item ou está inativo (sem `grupo` quando o próprio grupo não existe) | Busca de novo e usa só os ids devolvidos |
| `opcao_repetida` | A mesma opção com quantidade > 1 num grupo sem `repetir` | Pergunta de novo |

O carrinho atual vem junto, como em toda recusa.

**5. Carrinho, pedido e preço final:** cada linha ou item traz `preco_base` e `opcoes`.
```json
{"id": 88, "item_id": 123, "nome": "Edamame", "quantidade": 2, "preco_base": 34.0, "preco_unitario": 48.0, "subtotal": 96.0,
 "opcoes": [{"grupo_id": 12, "grupo": "Escolha o molho", "opcao_id": 302, "opcao": "Trufado", "quantidade": 1, "preco_extra": 6.0},
            {"grupo_id": 15, "grupo": "Adicionais", "opcao_id": 410, "opcao": "Cream cheese", "quantidade": 2, "preco_extra": 4.0}]}
```
- **Preço da linha:** `preco_unitario` = `preco_base` + soma de (`preco_extra` × `quantidade`). É o que o cliente paga por unidade. `subtotal` = `preco_unitario` × `quantidade`.
- **Item sem opções:** vem com `opcoes: []` e `preco_base` = `preco_unitario`.
- **Cópia:** nome e preço das opções ficam copiados no momento da escolha. O pedido fechado não muda se a opção mudar depois.
- **`fechar_pedido` revalida:**
  - extra mudou: `preco_alterado` (409), com o carrinho já refeito;
  - opção saiu do cardápio: `opcao_inexistente`;
  - grupo obrigatório novo: `opcao_obrigatoria`.
- **Texto do pedido para a unidade:** as escolhas aparecem abaixo de cada item. Exemplo:
  ```
  3× Edamame (R$ 49,00) = R$ 147,00
     + Trufado (Escolha o molho) +R$ 7,00
     + 2× Cream cheese (Adicionais) +R$ 4,00
  ```

**6. O que o prompt precisa saber:**
1. Antes de `adicionar_item`, olhe os `grupos` do item. Se houver grupo obrigatório, pergunte a escolha ao cliente; pode mostrar as opções como botões ou lista.
2. Repasse `grupo_id` e `opcao_id` devolvidos pela busca, nunca nomes.
3. Diante de uma recusa de opção, use o `grupo` dos detalhes para perguntar de novo.
4. Ao confirmar o pedido, cite as escolhas e o `preco_unitario` / `subtotal` do carrinho. Não some preços por conta própria.

### 14.2 Painel

| Rota | Corpo | Resposta |
|---|---|---|
| `GET …/catalogo/itens/{id}/opcoes` | — | `{"item_id","atualizado_em","grupos":[{"id","nome","obrigatorio","minimo","maximo","repetir","posicao","ativo","opcoes":[{"id","nome","preco_extra","codigo","posicao","ativo"}]}]}`. Qualquer papel lê |
| `PUT …/catalogo/itens/{id}/opcoes` (admin e plataforma) | `{"grupos":[{"id"?, "nome", "minimo"=0, "maximo"=1, "repetir"?=false, "ativo"?=true, "opcoes":[{"id"?, "nome", "preco_extra"=0, "codigo"?, "ativo"?=true}]}], "atualizado_em"?, "aplicar_em"?}` | O conjunto novo |

O `PUT` funciona assim:
- **Conjunto inteiro:** a ordem da lista é a posição. Com `id`, atualiza mantendo o id; sem `id`, cria; o que não vier é apagado. Carrinhos e pedidos têm a cópia, e o fechamento confere de novo.
- **Limites:** até 10 grupos; de 1 a 30 opções por grupo; 0 ≤ `minimo` ≤ `maximo` ≤ 20, com `maximo` ≥ 1; `preco_extra` ≥ 0.
- **Erros:** 400 com `campo` no formato `grupos[n].opcoes[m].preco_extra`. Grupo de outro item dá 400 `grupo_de_outro_item`.
- **Concorrência:** `atualizado_em` diferente do atual dá 409 `opcoes_alteradas`, com o conjunto atual em `opcoes`.
- **`aplicar_em`:** aceita `"todas"` ou uma lista de unidades. Copia o conjunto para os itens do mesmo prato, substituindo o deles, e devolve `aplicado_em`.
- **Histórico:** fica em `gestao_alteracoes`, com a entidade `catalogo_opcoes`.

### 14.3 Retrato do By Koji

Só os campos novos, todos acrescentados (35 linhas, nenhuma alterada):
- `grupos: []` em cada item da busca (7);
- `opcoes: []` e `preco_base` nas linhas de carrinho do pacote e das respostas das Skills;
- `opcoes: []` e `preco_base` nos itens do pedido.

Testes: `tests/test_cat3b.py` (4).

