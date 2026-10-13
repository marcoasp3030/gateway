# CAT2 — Edição do catálogo pelo painel e disponibilidade atual na API admin

**Situação:** decidido pelo Marco em 13/10/2026 e implementado. Este documento é o contrato para o [PAINEL].
**Origem:** pedido do [PAINEL] em `painel_koji/docs/VALIDACAO_CARDAPIO_UX.md`.
**Banco:** sem migração (todas as colunas já existiam).

Todas as rotas ficam em `/admin/v1` (rede interna, chave do painel). `X-Actor` identifica quem fez; `X-Role` (`plataforma`, `admin`, `membro`; ausente = `membro`) decide quem escreve. A empresa do caminho é conferida em toda rota: item ou unidade de outra empresa responde 404.

## 1. O item, como a API admin devolve

O mesmo formato na lista, na leitura individual, no retorno do PATCH e no `item` do 409:

```json
{"item_id": 666, "codigo": "GUE-0001", "unidade": "patio_guedala", "categoria": "Almoço executivo", "nome": "Almoço executivo",
 "descricao": "1 entrada + 1 prato principal + 1 sobremesa", "preco": 124.0, "alcoolico": false, "delivery": "a_confirmar", "tipo": "item",
 "observacao": null, "ativo": true, "atualizado_em": "2026-10-13T01:05:26.123456Z",
 "disponibilidade": {"dias_semana": ["seg","ter","qua","qui","sex"], "exceto_feriado": true, "horario": [], "resumo": "segunda a sexta, exceto feriados"},
 "disponivel_agora": false, "indisponivel_motivo": "feriado", "data_referencia": "2026-10-12T22:05-03:00"}
```

| Campo novo | Valor |
|---|---|
| `disponivel_agora` | `true` / `false`: o item pode ser pedido neste instante? |
| `indisponivel_motivo` | `null` quando disponível. Senão, o primeiro que valer, nesta ordem: `item_inativo` (inativo, ou tipo que não é vendido), `sem_preco`, `sem_delivery` (só do salão), `dia_da_semana`, `feriado`, `horario` |
| `data_referencia` | Instante usado na conferência, no fuso da unidade (`AAAA-MM-DDTHH:MM±HH:MM`) |

- Calculado pela **mesma função da Skill** `buscar_catalogo`: dias da semana, faixas de horário e feriado pelo calendário da unidade (nacional; estadual e municipal pela cidade/UF da unidade; "feriado da empresa"). Ponto facultativo não bloqueia.
- É sempre "agora". Não considera carrinho nem horário agendado (isso é do atendimento), nem estoque, nem se a unidade está aberta ou pausada (isso está em `GET …/unidades`).
- `disponibilidade` continua sendo a regra cadastrada, com o `resumo` em palavras; `null` = sem restrição.

## 2. Rotas

### 2.1 `GET /tenants/{t}/catalogo/itens` — lista (já existia)

Mesmos filtros (`unidade`, `tipo`, `ativo`, `q`, `limite` até 200, `cursor`). Cada item passa a trazer `disponivel_agora`, `indisponivel_motivo` e `data_referencia`. Qualquer papel.

### 2.2 `GET /tenants/{t}/catalogo/itens/{item_id}` — leitura individual (nova)

Devolve o item (seção 1). 404 se não existe ou é de outra empresa. Qualquer papel.

### 2.3 `PATCH /tenants/{t}/catalogo/itens/{item_id}` — edição (ampliada)

Só `X-Role: admin` ou `plataforma` (senão 403). Campos opcionais e independentes; **campo omitido mantém o valor atual**; campo fora da lista é recusado (400).

| Campo | Regra | `null` |
|---|---|---|
| `nome` | Texto não vazio, até 120 caracteres (espaços nas pontas são tirados) | recusado |
| `descricao` | Texto até 300 caracteres | apaga (só espaços também apaga) |
| `preco` | Maior que zero, até 99999,99, no máximo 2 casas decimais. Aceita número ou texto (`36.5`, `"36,50"`). Mais de 2 casas é recusado, não arredondado | deixa o item sem preço: ele sai do cardápio do agente |
| `ativo` | `true` ou `false` (booleano de verdade) | recusado |
| `categoria` | Uma categoria que **já existe na mesma unidade**. Comparada sem acento e sem maiúsculas; grava com a grafia que já existe | recusado |
| `disponibilidade` | `{"dias_semana": [...], "exceto_feriado": bool, "horario": [["HH:MM","HH:MM"]]}` | tira a restrição |
| `atualizado_em` | Opcional. O valor de `atualizado_em` que o painel leu (seção 2.4) | — |

- `unidade`, `item_id`, `codigo`, `tipo`, `alcoolico`, `delivery` e `observacao` não são editáveis aqui (enviar qualquer um → 400).
- Corpo sem nenhum campo editável → 400, `campo: "corpo"`.
- **Resposta 200:** o item completo atualizado (seção 1).
- Mandar o valor que o item já tem não é alteração: nada é gravado, `atualizado_em` não muda e não entra no histórico.
- **Erros de validação:** 400, com o primeiro campo que não serve em `erro.detalhes.campo`. Com um campo inválido, nada é gravado.

```json
{"erro": {"codigo": "VALIDATION_ERROR", "mensagem": "…", "detalhes": {"campo": "preco"}}}
```

### 2.4 Concorrência

Com `atualizado_em` no corpo, se o item mudou depois dessa data o gateway **não grava** e devolve:

```json
HTTP 409
{"erro": {"codigo": "CONFLICT", "mensagem": "O item foi alterado por outra pessoa. Confira os dados atuais e salve de novo.",
          "detalhes": {"campo": "atualizado_em", "motivo": "item_alterado"}},
 "item": { "…o item atual, no formato da seção 1…" }}
```

- Envie o `atualizado_em` exatamente como veio na leitura (com os microssegundos e o fuso). Data sem fuso → 400.
- Sem `atualizado_em`, não há conferência: vale a última gravação.
- Edição em lote: cada PATCH devolve o `atualizado_em` novo do seu item.

### 2.5 `GET /tenants/{t}/catalogo/categorias?unidade=…` — categorias da unidade (nova)

Só leitura; qualquer papel. `unidade` é obrigatória (400 sem ela; 404 se a unidade não existe na empresa).

```json
{"unidade": "patio_guedala", "categorias": [
  {"categoria": "Almoço executivo", "ordem": 1, "qtd_itens": 1, "itens_ativos": 1, "itens_no_cardapio": 1},
  {"categoria": "Entradas quentes", "ordem": 2, "qtd_itens": 10, "itens_ativos": 10, "itens_no_cardapio": 10}]}
```

- `ordem`: a do cardápio importado (posição do primeiro item da categoria), a mesma que o agente usa.
- `qtd_itens`: todos os itens da categoria; `itens_ativos`: os ativos; `itens_no_cardapio`: os que o agente pode vender (ativo, com preço, não exclusivo do salão).
- A categoria ainda não tem identificador próprio: é o nome. Criar, renomear e reordenar ficam para depois.

### 2.6 `POST /tenants/{t}/catalogo/disponibilidade/previa` — prévia sem gravar (nova)

Qualquer papel. Nada é gravado, nem no histórico.

```json
// corpo
{"unidade": "patio_guedala", "disponibilidade": {"dias_semana": ["seg","ter","qua","qui","sex"], "exceto_feriado": true, "horario": [["11:30","15:00"]]}}
// resposta 200
{"unidade": "patio_guedala",
 "disponibilidade": {"dias_semana": ["seg","ter","qua","qui","sex"], "exceto_feriado": true, "horario": [["11:30","15:00"]], "resumo": "segunda a sexta, 11h30–15h, exceto feriados"},
 "resumo": "segunda a sexta, 11h30–15h, exceto feriados",
 "disponivel_agora": false, "indisponivel_motivo": "horario", "data_referencia": "2026-10-13T20:36-03:00"}
```

- Avalia **só a regra** (dia, feriado da unidade, horário); não olha preço nem ativo, porque não é de um item. `indisponivel_motivo`: `dia_da_semana`, `feriado`, `horario` ou `null`.
- `disponibilidade: null` → `resumo: null`, `disponivel_agora: true`.
- 400 com `campo: "disponibilidade"` se a regra não serve; 400 sem `unidade`; 404 se a unidade não existe na empresa; campo a mais → 400.

## 3. Auditoria

Cada PATCH que muda algo grava uma linha em `GET /tenants/{t}/gestao/alteracoes` (`entidade: "catalogo_item"`, `entidade_id`: o `item_id`, `acao: "alterado"`, `actor`: o `X-Actor`), com `antes` e `depois` **só dos campos que mudaram**:

```json
{"actor": "ana", "entidade": "catalogo_item", "entidade_id": "666", "acao": "alterado",
 "antes": {"descricao": null, "preco": 34.0}, "depois": {"descricao": "Vagens de soja no vapor", "preco": 36.5}}
```

Filtro: `?entidade=catalogo_item&entidade_id=<item_id>`. Também entra na auditoria geral (`catalogo.item_alterado`).

## 4. Carrinhos abertos e pedidos

A edição **não mexe** em carrinho aberto nem em pedido.

- **Preço mudou:** o carrinho continua mostrando o preço antigo até o fechamento. O `fechar_pedido` compara com o catálogo, atualiza o carrinho e devolve 409 `preco_alterado`, para o agente confirmar o novo total com o cliente. Nenhum pedido sai com preço velho.
- **Item desativado:** o `fechar_pedido` devolve 422 `item_inativo`. Item que ficou sem preço: `sem_preco`.
- **Nome ou categoria:** o carrinho aberto passa a mostrar o nome novo (lê do catálogo).
- **Pedido já fechado:** guarda o nome, a categoria e o preço de quando foi fechado; edição posterior não o altera.

Os dois primeiros pontos estão cobertos por teste neste bloco (`tests/test_cat2.py`).

## 5. Detalhes de implementação que não estavam nas decisões

Escolhas pequenas feitas para fechar o contrato; qualquer uma pode ser revista:

1. **`preco` com mais de 2 casas** é recusado (400), não arredondado. Teto de 99999,99.
2. **`categoria`** é comparada sem acento e sem maiúsculas e gravada com a grafia existente. Vale categoria de item inativo da mesma unidade.
3. **`indisponivel_motivo` de item que não pode ser vendido** usa os mesmos nomes do `adicionar_item`: `item_inativo`, `sem_preco`, `sem_delivery`.
4. **Rota das categorias:** `GET …/catalogo/categorias?unidade=`, com `unidade` obrigatória.
5. **`atualizado_em`** é opcional no PATCH; sem ele não há conferência de concorrência.

## 6. Limites conhecidos

- **Categoria some quando fica sem itens.** Categoria existe só enquanto algum item da unidade a usa. Mover o último item de uma categoria para outra faz a categoria desaparecer, e não dá para voltar pelo painel (criar categoria ainda não existe). O painel deve avisar antes de mover o último item.
- **A importação por CSV sobrescreve a edição do painel.** O arquivo é o catálogo inteiro: reimportar um CSV antigo desfaz nome, descrição, preço, ativo e categoria editados no painel (a disponibilidade só é tocada se o arquivo trouxer as colunas dela). Item que não está no arquivo é desativado. Quem edita pelo painel precisa tratar o painel como a fonte, ou exportar antes de reimportar; não há exportação ainda.
- **Sem filtros nem contagens no servidor** (decisão 9): a lista continua paginada por `cursor`, até 200 por página.
- `observacao`, `tipo`, `alcoolico` e `delivery` continuam só pelo CSV.

## 7. Testes

`tests/test_cat2.py`: leitura individual; papéis (membro lê e não escreve); empresa errada; todas as validações com `detalhes.campo`; campos independentes; `null` em descrição e preço; item sem preço e inativo saindo da busca do agente; mesma categoria com outra grafia; valor igual não gera alteração; 409 com o item atual; histórico com antes, depois e autor; carrinho aberto com `preco_alterado` e `item_inativo`; pedido fechado preservado; `disponivel_agora` igual ao da Skill (horário, dia, feriado nacional e estadual, ponto facultativo); categorias com ordem e contagens; prévia sem gravar.
