# CAT3 — Gestão completa do cardápio pelo painel

**Situação: proposta para decisão do Marco, 20/10/2026. Nada implementado.** Módulo `catalogo` (opções do item tocam também o `pedidos`). Foto do item fica para o ARQ1. Decisões em aberto na seção 10.

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
