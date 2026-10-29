# PAG1 — forma de pagamento e troco no pedido

Pedido do Marco em 29/10/2026, para o lançamento do delivery do By Koji.

## 1. O que muda

- **O pedido passa a ter forma de pagamento**, que é um dos códigos da lista de formas aceitas da unidade (`unidade_pagamentos`, cadastrada no painel desde o G4-a). O pedido também guarda o `troco_para`, quando há troco.
- **O cliente informa a forma pela Skill `definir_entrega`**, que ganha os campos `forma_pagamento` e `troco_para`. Não há Skill nova.
- **`fechar_pedido` recusa** com `pagamento_nao_informado` quando falta a forma.
- **A forma aparece em quatro lugares:**
  - no carrinho e no pedido devolvidos ao agente (resumo);
  - no aviso NOVO PEDIDO para a unidade;
  - no painel (lista e detalhe do pedido);
  - no evento `pedido.criado` dos webhooks de saída (só o código).
- **Entrega no lançamento:**
  - taxa fixa por unidade (`taxa_entrega`), pedido mínimo e área de entrega como texto, como já é hoje;
  - taxa por bairro ou CEP fica para o G4-b;
  - novo: unidade com `delivery_ativo = false` recusa entrega (`delivery_indisponivel`), como já acontecia com `aceita_retirada = false` e a retirada.

## 2. Regras

| Regra | Onde |
|---|---|
| `forma_pagamento` precisa ser o código de uma forma **ativa** da unidade do carrinho. Fora da lista: `forma_pagamento_invalida`, com `detalhes.formas_aceitas`. | `definir_entrega` e `fechar_pedido` |
| Unidade sem nenhuma forma cadastrada: nenhuma forma é aceita, então o pedido não fecha. O agente transfere para a equipe (`pedido_para_confirmar`). | idem |
| `troco_para` só vale na forma com `aceita_troco` (só `dinheiro` pode ter troco). Em outra forma: `troco_so_em_dinheiro`. | idem |
| `troco_para` precisa ser maior que o total (subtotal + taxa fixa da entrega). Senão: `troco_menor_que_total`, com `detalhes.total`. `troco_para: null` = não precisa de troco. | idem; no fechamento, contra o total final |
| Trocar a forma tira o troco. Trocar de unidade (só com o carrinho vazio) e `esvaziar_carrinho` tiram forma e troco. | carrinho |
| Sem forma: `pagamento_nao_informado`, com `detalhes.formas_aceitas`. | `fechar_pedido` |
| Forma que saiu da lista da unidade depois de escolhida volta a faltar no carrinho e é recusada no fechamento. | idem |
| Unidade com `delivery_ativo = false`: entrega recusada com `delivery_indisponivel`. `null` (não informado) segue como hoje. | `definir_entrega` e `fechar_pedido` |

A ordem das conferências no fechamento continua a de antes: itens e preço, modalidade, endereço, maioridade, retirada ou entrega, mínimo, horário, unidade fechada, itens fora do horário. Por último entra o pagamento.

## 3. Dados

Migração `054_pagamento_do_pedido.sql`, toda aditiva:

- `carrinhos.forma_pagamento` e `carrinhos.troco_para`;
- `pedidos.forma_pagamento`, `pedidos.pagamento_rotulo` (cópia do rótulo no fechamento) e `pedidos.troco_para`.

Os pedidos antigos ficam com `pagamento: null`.

## 4. Contrato das Skills

O texto para o prompt está em `docs/SKILLS.md` (seções 7, 8 e tabela de recusas). Resumo:

- **`definir_entrega`**: `{"forma_pagamento": "pix"}` ou `{"forma_pagamento": "dinheiro", "troco_para": 100}`.
- **Carrinho:** traz `forma_pagamento`, `pagamento_rotulo`, `troco_para`, `troco` (quanto a unidade leva de troco) e `formas_aceitas` (`[{codigo, rotulo, aceita_troco}]`). Em `faltando`, entram `pagamento` e `troco` (troco que ficou menor que o total, por exemplo depois de acrescentar itens).
- **Pedido:** traz `pagamento: {forma, rotulo, troco_para, troco}`.
- **Motivos novos:** `pagamento_nao_informado`, `forma_pagamento_invalida`, `troco_so_em_dinheiro`, `troco_menor_que_total`, `delivery_indisponivel`.

## 5. O que não entra

- Pagamento online (link, Pix com QR code gerado pelo gateway) e confirmação de pagamento: o pedido só registra como o cliente vai pagar.
- Taxa por bairro ou CEP, raio e validação de endereço (G4-b).

## 6. Para o lançamento

Cada unidade precisa ter as formas de pagamento cadastradas no painel (Unidade → Pagamentos). Hoje as 4 unidades do By Koji não têm nenhuma, e sem elas nenhum pedido fecha.
