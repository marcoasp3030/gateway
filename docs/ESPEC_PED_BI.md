# PED-BI — visão de vendas da empresa

**Situação: aprovada pelo Marco em 04/11/2026 (decisões da seção 6) e implementada no gateway; painel pendente ([PAINEL]).**

## 1. O que é

Uma tela "Visão de vendas" para a empresa acompanhar os pedidos de um período: quanto vendeu, o que mais vende, quem mais compra, quando vende, como paga, de onde pede e quanto a operação demora. Cada número vem comparado com o período anterior de mesmo tamanho.

## 2. Regras

- **Venda** é pedido aceito pela loja: `confirmado`, `em_preparo`, `pronto_retirada`, `saiu_entrega`, `concluido`.
  - `aguardando_loja`, `recusado` e `cancelado` não entram na venda. Aparecem à parte, em `fora_da_venda`.
- **Origem:** só `real` por padrão. Teste, piloto e simulado entram só se pedidos em `origem=`.
- **Datas:** `de` e `ate` são dias locais, inclusivos. O fuso é o de São Paulo. A data do pedido é a de criação, inclusive para agendados.
- **Período:** sem datas, valem os últimos 30 dias até hoje. O máximo é 366 dias.
- **Cliente:**
  - é identificado pelo telefone gravado no pedido; sem telefone (canal api), pelo contato da conversa;
  - pedido anonimizado (canal excluído) não tem nenhum dos dois: entra nos totais, mas não no ranking de clientes;
  - **novo** é quem fez a primeira compra no período; **recorrente** é quem já tinha comprado antes.
- **Faturamento** é o total do pedido, com a taxa de entrega. `produtos` é a soma dos itens, e `taxas_entrega` vem à parte.

## 3. Rota

`GET /admin/v1/tenants/{t}/pedidos/indicadores`, para gestor e acima. Supervisor e funcionário recebem 403 `papel_insuficiente`.

| Parâmetro | Padrão | Observação |
|---|---|---|
| `de`, `ate` | últimos 30 dias | `AAAA-MM-DD`; `de` depois de `ate` → 400 `campo: de`; mais de 366 dias → 400 `campo: periodo` |
| `unidade` | todas | código da unidade |
| `origem` | `real` | lista separada por vírgula entre `real,piloto,simulado,teste`; outro valor → 400 `campo: origem` |
| `agrupar` | automático | `dia`, `semana` ou `mes`. Automático: até 62 dias por dia, até 190 por semana, acima disso por mês |
| `sumido_dias` | 30 | de 7 a 365 |

**Resposta:**

| Campo | Conteúdo |
|---|---|
| `periodo` | `de`, `ate`, `dias`, `agrupar`, `fuso`, `anterior` (`de` e `ate` do período comparado) |
| `resumo` / `anterior` | faturamento, pedidos, ticket médio, produtos, taxas de entrega, frete grátis, itens vendidos, clientes (novos e recorrentes), agendados, delivery × retirada, recebidos, `fora_da_venda` (aguardando, recusado, cancelado: pedidos e valor), `perda_pct` (recusados e cancelados sobre os já decididos) |
| `variacao_pct` | variação de faturamento, pedidos, ticket médio, itens, clientes e clientes novos; `null` quando o anterior é zero |
| `evolucao` | `[{inicio, pedidos, faturamento}]`, um item por balde, sem buracos |
| `horarios` | `[{dia_semana (0 = domingo), hora, pedidos, faturamento}]`, só as combinações com venda |
| `produtos` | `itens` (todos os vendidos, até 500: quantidade, faturamento, pedidos, participação, curva ABC, quantidade no período anterior e variação), `total`, `curva` (quantos em A, B e C), `categorias`, `sem_venda` (itens ativos do catálogo sem venda no período: até 100 e o total) |
| `clientes` | `por_valor` e `por_frequencia` (20 primeiros cada: nome, telefone completo, pedidos, faturamento, ticket médio, primeira e última compra no período), `sumidos` (20 de maior valor e o total) |
| `pagamentos` | `[{forma, rotulo, pedidos, faturamento, participacao_pct}]`; sem forma gravada → `nao_informado` |
| `bairros` | os 20 bairros com mais pedidos de delivery: pedidos, faturamento e taxa média |
| `operacao` | `aceite` (criação → confirmado), `preparo` (confirmado → pronto ou saiu) e `total` (criação → concluído), cada um com mediana, 90% dos pedidos e quantidade de pedidos medidos. Agendados ficam fora de preparo e total. `motivos_perda`: os 10 motivos de recusa e cancelamento mais comuns |
| `unidades` | por unidade: pedidos, faturamento, ticket médio e recebidos |

- **Curva ABC:** os produtos que somam até 80% do faturamento são A; até 95%, B; o resto, C.
- **Sumido:** comprou 2 vezes ou mais (em qualquer data até o fim do período) e não compra há `sumido_dias` dias, contados até o fim do período.
- **Dados:** sem migração. O índice `ix_pedidos_origem (tenant_id, origem, criado_em)` já atende o filtro.
- **Auditoria:** é só leitura e não gera auditoria.

## 4. Painel ([PAINEL])

- **Menu:** Pedidos → "Visão de vendas", só para gestor e acima.
- **Filtros:**
  - período, com atalhos (hoje, 7 dias, 30 dias, este mês, mês passado e datas livres);
  - unidade;
  - opção "incluir pedidos de teste/piloto", desligada por padrão.
- **Topo:** cartões com faturamento, pedidos, ticket médio, clientes novos e % de perda, com a seta de variação.
- **Gráficos:**
  - evolução do faturamento e dos pedidos;
  - mapa de calor com o dia da semana e a hora;
  - pizza de pagamento e de delivery × retirada.
- **Abas:**
  - Produtos: ranking com curva ABC, categorias e produtos sem venda;
  - Clientes: por valor, por frequência e sumidos. O telefone abre a conversa.
  - Entrega: bairros;
  - Operação: tempos e motivos de perda;
  - Unidades.
- **Sem venda no período:** estado vazio explicando que pedidos de teste e piloto não entram.

## 5. Testes (gateway)

- Totais, ticket médio, clientes novos e recorrentes, fora da venda e % de perda num cenário conhecido.
- O período anterior e a variação, a evolução sem buracos e os horários.
- Ranking de produtos, curva ABC, categorias e produtos sem venda.
- Clientes com nome e telefone completo; o pedido anonimizado fica fora; o cliente sumido aparece.
- Pagamentos, bairros (mesmo bairro com grafia diferente fica junto), tempos da operação e motivos.
- Origem teste só quando pedida; filtro de unidade; recusas de período e origem; supervisor e funcionário recebem 403.

## 6. Decisões do Marco (04/11/2026)

1. **Quem vê:** gestor e acima.
2. **Cliente no ranking:** nome e telefone completo.
3. **Venda:** confirmado em diante. Aguardando, recusado e cancelado aparecem à parte.
4. **Exportar planilha:** por enquanto não.

## 7. O que não entra agora

- Exportar CSV.
- Cache: as consultas usam índice e o volume é pequeno. Entra se a tela ficar lenta.
- Margem e custo, porque o catálogo não tem custo.

## 8. PED-BI2: os canais juntos (09/11/2026)

O campo novo `canais` junta delivery e retirada (WhatsApp) com o iFood nos mesmos gráficos. Os campos de antes continuam iguais, só com o WhatsApp, para o painel atual não quebrar.

- **Venda:** a mesma regra de cada canal. No WhatsApp, confirmado em diante. No iFood, confirmado em diante; cancelado e aguardando ficam fora.
- **Filtros:** valem os mesmos. O pedido de teste do iFood só entra com `origem` incluindo `teste`. Com `unidade`, o iFood entra só pela loja ligada àquela unidade.

| Campo de `canais` | Conteúdo |
|---|---|
| `disponiveis` | `delivery`, `retirada` e, quando a empresa tem loja do iFood, `ifood` |
| `total` | pedidos, faturamento e ticket médio dos canais somados |
| `resumo` | por canal: pedidos, faturamento, ticket médio e participação no faturamento |
| `evolucao` | `[{inicio, pedidos, faturamento, por_canal}]`, um item por balde |
| `dias_semana` | 7 itens (0 = domingo): pedidos, faturamento e `por_canal` |
| `horas` | 24 itens (0 a 23), no mesmo formato |
| `horarios` | dia × hora × canal, só as combinações com venda (para o mapa de calor com filtro de canal) |
| `pagamentos` | por forma: rótulo, pedidos, faturamento, participação em pedidos e `por_canal` |
| `produtos` | `itens` (até 100, por quantidade): nome, quantidade, faturamento, pedidos, participação e `por_canal`; `total` |

`por_canal` sempre traz os três canais, zerados quando não houve venda.

- **Forma de pagamento do iFood:** conta a forma de maior valor do pedido (um pedido pago em duas formas conta uma vez). Os códigos do iFood viram os mesmos do WhatsApp quando é a mesma forma: `PIX` → `pix`, `CASH` → `dinheiro`. Os outros têm rótulo em português: `credito`, `debito`, `vale_refeicao`, `vale_alimentacao`, `carteira_digital`, `vale_presente` e `outro`. Uma forma desconhecida vira `ifood_<forma>`.
- **Produto:** o mesmo nome, sem diferença de maiúscula ou espaço, é o mesmo produto nos três canais. Nome diferente no iFood (ex.: "Combo 1" × "Combo Um") aparece separado.
- **Painel:** um filtro de canal (Todos, Delivery, Retirada, iFood) que vale para os gráficos de evolução, dia da semana, horário de pico, pagamento e produtos.
