# DISP1 — Disponibilidade de item por dia e feriado · paginação de categorias

**Situação:** especificação para aprovação. **Nada implementado.**
**Data:** 12/10/2026. Produção: `gateway:7784585`, schema 39.

## 1. O que o teste mostrou e o que existe hoje

- 12/10 (feriado), 20:36: o agente ofereceu, adicionou e fechou o "Almoço executivo" do Pátio Guedala (pedido 12873). A restrição "segunda a sexta, exceto feriados" só existe em texto, na descrição ou na observação do item.
- O catálogo tem 4 itens assim, um por unidade (`EIN-0001`, `MOR-0001`, `GUE-0001`, `VNC-0001`); o do Morumbi diz "terça a sexta". Só o do Pátio Guedala tem preço.
- **Não existe rota de criar ou editar item.** O catálogo entra só por CSV (o arquivo é o catálogo inteiro) e o painel só lista. O "contrato admin de criação/edição" precisa nascer aqui (seção 2.5).
- `categorias=true` devolve todas as categorias de uma vez (18 no Pátio Guedala), em ordem alfabética, sem página.

---

## 2. DISP1 — disponibilidade do item

### 2.1 Campo novo no item

`catalogo_itens.disponibilidade` (JSON). **Nulo = sem restrição** (todo item de hoje).

```json
{"dias_semana": ["seg", "ter", "qua", "qui", "sex"], "exceto_feriado": true, "horario": [["11:30", "15:00"]]}
```

| Chave | Regra |
|---|---|
| `dias_semana` | Lista de `seg ter qua qui sex sab dom`. Ausente ou vazia = todos os dias |
| `exceto_feriado` | `true` = não vale em feriado. Ausente = `false` |
| `horario` | Faixas `[["HH:MM","HH:MM"]]` em que o item é servido (proposta, decisão 1). Ausente = o dia todo |

**Feriado**, para esta regra, é o mesmo do `calendario` do CAL1 para a unidade do item: nacional, estadual e municipal da cidade da unidade, e o "feriado da empresa". Ponto facultativo (Carnaval, Cinzas) **não** conta.

### 2.2 Em que dia se confere

No dia e hora **do pedido**: o `agendado_para` do carrinho, se houver; senão, agora (fuso da unidade). Pedido feito no domingo para segunda ao meio-dia pode levar o almoço executivo; pedido feito na segunda à noite, não.

### 2.3 `buscar_catalogo`

O item restrito **continua aparecendo**, marcado (proposta, decisão 2), para o agente poder explicar em vez de dizer que não existe:

```json
{"item_id": 666, "nome": "Almoço executivo", "preco": 124.0, "…": "…",
 "disponivel_hoje": false,
 "indisponivel_motivo": "feriado",
 "disponibilidade": {"dias_semana": ["seg","ter","qua","qui","sex"], "exceto_feriado": true, "horario": [["11:30","15:00"]],
                     "resumo": "segunda a sexta, 11h30–15h, exceto feriados"}}
```

- `disponivel_hoje`: sempre presente em todo item (`true` para item sem restrição).
- `indisponivel_motivo`: `dia_da_semana`, `feriado` ou `horario`; nulo quando disponível.
- `disponibilidade`: nulo para item sem restrição.
- Itens indisponíveis vão para o fim da lista. No modo `categorias`, a contagem `qtd_itens` não muda (o item existe no cardápio).
- "Hoje" aqui é o dia do pedido (seção 2.2); o campo `data_referencia` na resposta diz qual dia foi usado.

### 2.4 Carrinho e pedido

| Onde | O que muda |
|---|---|
| `adicionar_item` | Item fora da disponibilidade → 422, motivo **`item_indisponivel_hoje`**, com `detalhes`: `motivo_indisponivel` (`dia_da_semana`/`feriado`/`horario`), `data_referencia`, `disponibilidade` (com `resumo`) e `feriado` (nome, quando for o caso). Carrinho não muda |
| `alterar_item` | Não confere (mudar quantidade ou observação de algo que já está no carrinho) |
| `definir_entrega` | Aceita o horário normalmente; se com o novo horário algum item ficar fora da disponibilidade, o carrinho volta com esse item marcado `disponivel: false` |
| Carrinho (respostas e pacote) | Cada item ganha `disponivel` (bool). Havendo item indisponível: `pode_fechar: false` e `faltando` ganha `"item_indisponivel"` |
| `fechar_pedido` | Confere de novo. Item indisponível → 422 `item_indisponivel_hoje`, com `detalhes.itens` (linhas afetadas). Nenhum pedido é criado |

### 2.5 Contrato admin

- **Rota nova** `PATCH /admin/v1/tenants/{id}/catalogo/itens/{item_id}` — corpo `{"disponibilidade": {...} | null}`. Só este campo (edição completa de item continua sendo do G5). Valida dias, faixas e formato; audita; entra no histórico de alterações.
- `GET /admin/v1/tenants/{id}/catalogo/itens` passa a devolver `disponibilidade` e `disponivel_hoje`.
- **CSV:** três colunas novas e **opcionais** no fim — `dias_semana` (`seg-sex` ou `seg,ter,qua`), `exceto_feriado` (`sim`/`nao`), `horario` (`11:30-15:00`, vários separados por `;`). Arquivo com o cabeçalho antigo continua aceito e **não apaga** a disponibilidade já cadastrada. Com as colunas, célula vazia = sem restrição.
- Papel: admin e plataforma (proposta, decisão 5).

### 2.6 Migração dos itens existentes

Migração 040, aditiva: coluna `disponibilidade` nula em todos. **Nenhum item muda de comportamento** até alguém cadastrar. Os 4 itens de almoço executivo do By Koji ficam para você cadastrar pelo painel — ou eu preencho na implantação, com sua ordem (decisão 4).

---

## 3. Paginação de categorias

`GET /api/v1/catalogo/busca?categorias=true&unidade=…&pagina=N`

```json
{"unidade": "patio_guedala", "total_itens": 193, "total_categorias": 18,
 "pagina": 1, "por_pagina": 9, "tem_mais": true,
 "categorias": [{"categoria": "Sashimis", "qtd_itens": 14}, {"categoria": "Temakis — cone", "qtd_itens": 12}]}
```

- **9 por página** (proposta, decisão 6): sobra uma linha das 10 da lista do WhatsApp para "Ver mais categorias". 18 categorias = 2 páginas.
- `total_categorias`, `pagina`, `por_pagina`, `tem_mais` sempre presentes. Página além do fim: `categorias` vazio, `tem_mais: false`.
- **Sem `pagina`** (chamada de hoje): devolve todas, como hoje, com `pagina: null` e `tem_mais: false` (proposta, decisão 7) — não muda nada para quem ainda não atualizou a Skill.
- **Ordem** (decisão 8): hoje é alfabética, e por isso sashimi e temaki caíram depois de licores. Proposta: ordem do cardápio (a ordem em que as categorias aparecem no arquivo importado), que costuma pôr o que mais vende na frente.
- O código da Skill `buscar_catalogo` muda uma linha (enviar `pagina` também com `categorias`); mando o código completo junto com a implantação.

---

## 4. Testes

- Disponibilidade: dia permitido e não permitido; feriado nacional, estadual e municipal da cidade da unidade; ponto facultativo não bloqueia; faixa de horário; item sem restrição (regressão); unidade sem cidade (só nacional).
- Dia do pedido: carrinho agendado para dia permitido aceita o item hoje indisponível; mudar o horário para dia não permitido marca o item e tira o `pode_fechar`; `fechar_pedido` recusa e não cria pedido.
- Busca: `disponivel_hoje` sempre presente; indisponível no fim; resumo em palavras.
- Admin: PATCH (válido, inválido, nulo limpa, outra empresa → 404, papel); CSV com e sem as colunas novas; CSV antigo não apaga a disponibilidade.
- Categorias: 9 por página, `tem_mais`, última página, página além do fim, sem `pagina` devolve todas, ordem.
- Teste do contrato das respostas e isolamento entre empresas.

## 5. Decisões que preciso de você

1. **Faixa de horário no item** (`horario`): entra agora (proposta — o almoço executivo foi vendido às 20h36, e isso estaria errado mesmo sem feriado), ou só dia da semana e feriado?
2. **Item indisponível na busca:** aparece marcado com `disponivel_hoje: false` e o motivo (proposta), ou some da resposta?
3. **Dia de referência:** o dia do pedido (`agendado_para`, senão hoje), como proposto, ou sempre hoje?
4. **Itens existentes:** todos sem restrição; os 4 almoços executivos você cadastra pelo painel (proposta), ou eu preencho na implantação? Se eu preencher: Einstein, Pátio Guedala e Vila Nova Conceição seg–sex, Morumbi ter–sex, todos `exceto_feriado`; faltaria você me dizer a faixa de horário do almoço de cada unidade.
5. **Quem edita a disponibilidade:** admin e plataforma (proposta), ou qualquer papel?
6. **Categorias por página:** 9 (proposta) ou 10?
7. **Sem `pagina`:** devolve todas, como hoje (proposta), ou já devolve só a primeira página?
8. **Ordem das categorias:** a do cardápio importado (proposta) ou alfabética, como hoje?
9. **Um bloco só** (DISP1 com as duas partes, uma implantação), como proposto, ou a paginação de categorias primeiro, por ser pequena?
