# AN1 — Análise de vendas

**Situação: proposta para decisão do Marco, 16/10/2026. Nada implementado.** Faz parte do módulo `pedidos`: só existe para empresa com o módulo em uso (`em_uso` de `GET /tenants/{t}/modulos`). Decisões em aberto na seção 9, cada uma com recomendação.

---

## 1. Auditoria (só leitura, produção em 16/10)

### 1.1 Que dados de venda existem, e de quais canais

| Canal | Situação |
|---|---|
| WhatsApp (uazapi) e canal API | **Único que gera pedido no gateway**: tabela `pedidos`, criada pela Skill `fechar_pedido` |
| iFood | Nada no gateway. No painel há só uma tela de diagnóstico e a credencial em `integrations`; nenhum pedido guardado |
| 99Food | Nada no gateway. O painel recebe o webhook (`/api/public/food99/webhook`) e guarda o **evento bruto** em `webhook_events` do Supabase dele (id do pedido, tipo, status, payload original). Não vira pedido, não tem itens tratados |
| Site | Não existe |

Conclusão: **pedidos do iFood e do 99Food não entram na tabela de pedidos.** Para a análise ter esses canais, falta um bloco de entrada de pedidos externos (decisão 1). A AN1 nasce preparada para eles (campo `canal` e identidade por canal, seções 2 e 3), mas só com dados do WhatsApp.

Campos de `pedidos` hoje:

| Grupo | Campos | Observação |
|---|---|---|
| Identificação | `numero`, `conversation_id`, `carrinho_id` | `numero` é sorteado (não sequencial) |
| Cliente | `cliente_nome`, `cliente_telefone` (cópia no fechamento), e via conversa: `contato_id` | Não há id de cliente de canal externo |
| Itens | `itens` JSONB: `item_id`, `codigo`, `nome`, `categoria`, `quantidade`, `preco_unitario`, `subtotal`, `observacao`, `alcoolico` | Cópia do momento da venda (1.4) |
| Valores | `subtotal`, `taxa_entrega`, `total` | **Não há desconto**, nem forma de pagamento escolhida |
| Status | `status` (aguardando_loja, confirmado, em_preparo, pronto_retirada, saiu_entrega, concluido, recusado, cancelado), `motivo`, `decidido_em`, `decidido_por`; histórico em `pedido_eventos` (de, para, por, origem, `criado_em`) | |
| Onde e como | `unidade` (código), `modalidade` (delivery/retirada), `endereco`, `horario`, `agendado_para` | Fuso: `tenant_unidades.fuso`, senão o da empresa |
| Horários | `criado_em` (fechamento), `atualizado_em`, `decidido_em`, `agendado_para`; cada passo em `pedido_eventos.criado_em` | |
| Canal | **Não há coluna**: sai da conversa (`conversas.canal` / `tenant_canais.tipo`) | |

### 1.2 Identidade do cliente

- No gateway o cliente é o **contato** (`contatos`, único por empresa + telefone); o pedido chega a ele pela conversa (`conversas.contato_id`). Hoje pedidos do mesmo telefone = mesmo cliente, com segurança.
- **Dá para unir com segurança:** mesmo telefone normalizado (E.164) dentro da mesma empresa; mesmo id de cliente do mesmo marketplace (quando existir).
- **Não dá para unir com segurança:** nome (homônimos, apelidos, "Ana" vs "Ana Paula"); e-mail do marketplace (iFood e 99Food costumam mascarar ou não enviar); telefone do iFood, que em geral é um **número intermediário** (localizador) e não o do cliente. Unir por isso funde pessoas diferentes.
- Proposta: tabela `cliente_identidades(tenant, contato_id, canal, chave, origem)` com união automática só por telefone E.164 igual (WhatsApp, site) ou id do marketplace igual; união manual pelo painel (admin) para os demais casos, com desfazer. Sem união por nome.

### 1.3 Separar teste, piloto e mock dos reais

O pedido não guarda em que condição nasceu. Sinais disponíveis hoje:

| Sinal | Onde | Pega |
|---|---|---|
| Conversa simulada (painel/"Simular") | `conversas.simulada` | 2 pedidos |
| Empresa de teste | `tenants.tenant_teste` (só `bench_passo5`) | 0 |
| Cliente do piloto | `contatos_permitidos` do agente **hoje** (não guarda a lista da época) | 9 |
| Agente em `mock` ou envio `dry` | configuração **atual**, não a da época | — |

**Quantos pedidos reais o By Koji tem: zero.** São 11 pedidos (10/10 a 15/10): 9 dos dois celulares do piloto pelo canal uazapi de teste e 2 simulados. Clientes distintos com pedido: 3.

Proposta (decisão 3): coluna `pedidos.teste` gravada no fechamento (verdadeira se conversa simulada, empresa de teste, agente em `mock`, envio `dry` ou cliente na lista do piloto com o piloto ligado), migração marcando os 11 de hoje como teste, e marcar/desmarcar à mão pelo painel (admin, auditado). A análise ignora `teste=true` por padrão (`incluir_teste=true` mostra).

### 1.4 Itens com nome, categoria e preço do momento da venda

**Confirmado:** cada item de `pedidos.itens` guarda `item_id`, `codigo`, `nome`, `categoria`, `quantidade`, `preco_unitario` e `subtotal` copiados no fechamento (13 itens de 13 têm os quatro campos). Mudar nome, categoria ou preço no catálogo depois não altera pedidos antigos. Não há custo do item (sem margem), nem `tipo` (item/adicional) na cópia.

---

## 2. Regras de cálculo

| Termo | Regra proposta |
|---|---|
| Pedido faturado | status `confirmado`, `em_preparo`, `pronto_retirada`, `saiu_entrega` ou `concluido`. `aguardando_loja` conta à parte como **em aberto** (decisão 4) |
| Faturamento | soma de `total` (itens + taxa de entrega) dos faturados; ao lado, `itens` (subtotal) e `taxa_entrega` separados (decisão 5) |
| Cancelados / recusados | fora do faturamento, contados à parte (quantidade e valor) |
| Data da venda | `criado_em` do pedido no fuso da **unidade** (agendado entra no dia em que foi feito, decisão 6) |
| Ticket médio | faturamento ÷ pedidos faturados |
| Cliente único | contato distinto (seção 1.2) com pedido faturado no período |
| Novo × recorrente | novo = primeiro pedido faturado da vida dentro do período; recorrente = já tinha pedido faturado antes do início |
| Variação | `(atual − anterior) ÷ anterior`; período anterior = mesmo tamanho imediatamente antes (ou "mesmo período do ano passado", `comparar=ano`); nulo quando o anterior é zero |
| Pedido de teste | fora de tudo (seção 1.3) |
| Cliente anonimizado | continua em todos os totais; aparece como "Cliente anonimizado" e não entra em rankings nominais de forma identificável |

---

## 3. Dados novos (migração aditiva)

| Tabela / coluna | Para quê |
|---|---|
| `pedidos.canal` (`whatsapp`, `api`, `ifood`, `99food`, `site`) | Canal na própria linha; migração preenche os de hoje pela conversa |
| `pedidos.teste` BOOLEAN | Seção 1.3 |
| `pedidos.desconto` NUMERIC NULL, `pedidos.canal_pedido_id` TEXT NULL | Para os marketplaces (vazios no WhatsApp) |
| `cliente_identidades` | Seção 1.2 |
| `vendas_dia` | Totais por (empresa, dia local, unidade, canal, modalidade): pedidos, faturamento, itens, taxa, cancelados (qtd e valor), recusados, em aberto, clientes novos |
| `vendas_dia_hora` | Pedidos e faturamento por (empresa, dia, unidade, canal, modalidade, hora local) — para o mapa dia da semana × hora |
| `vendas_dia_item` | Por (empresa, dia, unidade, canal, modalidade, item_id): quantidade, faturamento, nome e categoria da última venda do dia |
| `vendas_pendentes` | Fila de (empresa, dia, unidade) a recalcular |
| `an_config` (por empresa) | Regras do RFM e limites (seção 5) |

---

## 4. Desempenho

- **Totais diários pré-calculados** (`vendas_dia*`), sempre **derivados** de `pedidos`: nunca a fonte da verdade, podem ser apagados e refeitos.
- **Quando atualizam:** criar pedido, mudar status, marcar teste ou anonimizar grava (na mesma transação) a linha `(empresa, dia, unidade)` em `vendas_pendentes`. O agendador, a cada 60 s, recalcula o dia inteiro de cada pendente a partir de `pedidos` (apaga e regrava as linhas do dia: corrige qualquer alteração posterior, inclusive cancelamento dias depois, porque o dia afetado é o do pedido, não o da mudança).
- **Hoje** é calculado ao vivo direto de `pedidos` (o dia corrente nunca espera o agendador); dias anteriores vêm dos totais.
- **Rede de segurança:** toda madrugada (04:00 do fuso da empresa) recalcula os últimos 7 dias; rota de plataforma para recalcular um período (`POST …/analise/recalcular`).
- **Clientes, RFM e ficha:** calculados ao vivo de `pedidos` (com índice por `contato_id`), porque dependem da vida inteira do cliente; volume de um restaurante cabe nisso por muito tempo. Se passar de ~200 mil pedidos por empresa, vira tabela diária própria.
- **Limites:** período máximo 366 dias (série por dia); mapa dia × hora até 92 dias; listas paginadas (máx. 100 por página); exportação até 50 mil linhas por arquivo (acima disso, recusa com `motivo: muitas_linhas` pedindo período menor).

---

## 5. Clientes: RFM

Por cliente, sobre pedidos faturados: **R** = dias desde a última compra; **F** = pedidos nos últimos 365 dias; **M** = faturamento nos últimos 365 dias. Grupos (o primeiro que servir, nesta ordem), com os números em `an_config` (editável por admin e plataforma):

| Grupo | Regra proposta (padrão) |
|---|---|
| Campeões | R ≤ 30 e F ≥ 4 e M ≥ ticket médio da empresa × 4 |
| Fiéis | R ≤ 60 e F ≥ 3 |
| Novos | primeira compra há ≤ 30 dias |
| Em risco | F ≥ 2 e 60 < R ≤ 120 |
| Perdidos | R > 120 |
| Ocasionais | o resto |

O agrupamento é sempre "hoje" (não depende do período escolhido); a lista do grupo respeita unidade e canal.

---

## 6. Rotas — contrato para o [PAINEL]

Todas em `/admin/v1/tenants/{t}/analise/…`, com `X-Tenant-Scope` (outra empresa → 404) e **404 `modulo_desligado`** quando a empresa não tem `pedidos` em uso.

**Parâmetros comuns:** `inicio` e `fim` (datas `AAAA-MM-DD`, inclusivas, no fuso de cada unidade), `comparar` (`anterior` padrão, `ano`, `nao`), `unidade` (código, várias separadas por vírgula), `canal`, `modalidade`, `incluir_teste` (padrão `false`). Erros: 400 `{"campo":"inicio","motivo":"periodo_longo"|"ordem"|"formato"}`.

Valor com variação: `{"valor": 1234.5, "anterior": 1000.0, "variacao": 0.2345}`.

### a) Visão geral — `GET /analise/visao-geral`

```json
{"periodo":{"inicio":"2026-10-01","fim":"2026-10-15","anterior":{"inicio":"2026-09-16","fim":"2026-09-30"}},
 "faturamento":{"valor":0,"anterior":0,"variacao":null},"itens":{…},"taxa_entrega":{…},
 "pedidos":{…},"ticket_medio":{…},"clientes_unicos":{…},"clientes_novos":{…},"clientes_recorrentes":{…},
 "cancelados":{"quantidade":{…},"valor":{…}},"recusados":{"quantidade":{…},"valor":{…}},"em_aberto":{"quantidade":0,"valor":0},
 "serie":[{"dia":"2026-10-01","faturamento":0,"pedidos":0,"ticket_medio":null}],
 "mapa":[{"dia_semana":1,"hora":19,"pedidos":0,"faturamento":0}],
 "atualizado_em":"…"}
```

`mapa`: dia da semana ISO (1 = segunda) × hora local (0–23), só células com pedido.

### b) Canais — `GET /analise/canais`

```json
{"canais":[{"canal":"whatsapp","faturamento":{…},"pedidos":{…},"ticket_medio":{…},"participacao":0.0,
            "serie":[{"dia":"…","faturamento":0,"pedidos":0}]}],
 "modalidades":[{"modalidade":"delivery","faturamento":{…},"pedidos":{…},"ticket_medio":{…},"participacao":0.0}],
 "unidades":[{"codigo":"patio_guedala","nome":"Pátio Guedala","faturamento":{…},"pedidos":{…},"ticket_medio":{…},"participacao":0.0}]}
```

### c) Produtos — `GET /analise/produtos`

Parâmetros extras: `ordem` (`quantidade` | `faturamento` | `menos_vendidos`), `categoria`, `limite` (até 100), `cursor`.

```json
{"itens":[{"item_id":123,"codigo":"GUE-0001","nome":"Edamame","categoria":"Entradas quentes","quantidade":{…},"faturamento":{…},
           "participacao":0.0,"classe_abc":"A","tendencia":"alta"|"queda"|"estavel"|null}],
 "proximo_cursor":null}
```

- `GET /analise/produtos/sem-venda`: itens ativos, com preço e que podem ser pedidos (mesma conta do `tem_cardapio`) sem nenhuma venda no período, por unidade, paginado.
- `GET /analise/produtos/tendencias`: `{"em_alta":[…],"em_queda":[…]}` contra o período anterior, só itens com ≥ 5 unidades vendidas em um dos dois períodos; ordena pela variação de quantidade.
- `GET /analise/produtos/categorias`: por categoria: quantidade, faturamento (com variação), participação.
- Curva ABC: por faturamento acumulado no período — A até 80%, B até 95%, C o resto (limites em `an_config`).
- Nome e categoria exibidos: os da última venda no período (o item pode ter mudado no catálogo depois).

### d) Clientes — `GET /analise/clientes`

Parâmetros extras: `ordem` (`valor` | `frequencia` | `recente`), `grupo` (`campeoes`, `fieis`, `novos`, `em_risco`, `perdidos`, `ocasionais`), `limite`, `cursor`.

```json
{"grupos":[{"grupo":"campeoes","rotulo":"Campeões","clientes":0,"faturamento":0}],
 "itens":[{"contato_id":"ct_…","nome":"Ana W","telefone":"5511****7046","anonimizado":false,"grupo":"fieis",
           "pedidos":3,"faturamento":0,"ticket_medio":0,"ultima_compra":"2026-10-14","canal_preferido":"whatsapp"}],
 "proximo_cursor":null}
```

- **Ficha:** `GET /analise/clientes/{contato_id}` — **só admin e plataforma** (membro → 403): nome, telefone completo, canais, pedidos (lista com número, data, unidade, canal, modalidade, status, total), canal preferido, unidade preferida, itens favoritos (top 5 por quantidade), ticket médio, primeira e última compra, grupo RFM com R/F/M.
- **Regras do RFM:** `GET`/`PUT /analise/config` (admin e plataforma) — números da seção 5 e limites da curva ABC.

### e) Atendimento IA — `GET /analise/atendimento`

Conversas reais (não simuladas) com mensagem do cliente no período, filtrável por unidade (preferida da conversa) e canal:

```json
{"conversas":{…},"com_pedido":{…},"conversao":{…},
 "resolvidas_pelo_agente":{…},"transferidas":{…},"transferencias_por_motivo":[{"motivo":"pediu_humano","rotulo":"Cliente pediu atendente","quantidade":0}],
 "tempo_resposta_ms":{"p50":0,"p90":0},"tempo_ate_pedido_min":{"p50":0}}
```

- `com_pedido`: conversa com pedido faturado criado no período; `conversao` = com_pedido ÷ conversas.
- `transferidas`: conversa que entrou em "aguardando atendente" no período; `resolvidas_pelo_agente` = as demais.
- `tempo_resposta_ms`: `turnos.total_ms` (da última mensagem do cliente até a resposta enviada), turnos `ok`.

### f) Exportação — `GET /analise/{lista}/exportar?formato=csv|xlsx`

`lista` = `pedidos`, `produtos`, `produtos/sem-venda`, `categorias`, `clientes`, `canais`, `serie`. Mesmos filtros; arquivo com cabeçalho em português, valores em reais com ponto decimal no CSV (`;` como separador, UTF-8 com BOM, para abrir no Excel) e número no xlsx. Telefone mascarado sempre (inclusive para admin; a ficha é a única com telefone completo). Exportação vai para a auditoria (`analise.exportada`, com lista e período). Limite: 50 mil linhas.

### LGPD

- Telefone **sempre** mascarado (`5511****7046`) em listas e exportações; completo só na ficha, para admin e plataforma.
- Cliente anonimizado: `"nome":"Cliente anonimizado","telefone":null,"anonimizado":true`, sem ficha (404), e continua contando em todos os totais, grupos e rankings de produto.
- Nenhum endereço, observação ou texto de conversa nas rotas de análise.

---

## 7. Fora do AN1

Entrada de pedidos do iFood, 99Food e site (bloco próprio, decisão 1); custo e margem; forma de pagamento usada; metas; envio de relatório por e-mail/WhatsApp.

---

## 8. Blocos

| Bloco | Conteúdo | Tamanho |
|---|---|---|
| **AN1-a** | Migração (`canal`, `teste`, `desconto`, `canal_pedido_id`, tabelas `vendas_*`, `an_config`), marcação de teste no fechamento + os 11 de hoje, totais diários com fila e recálculo, visão geral e canais | Grande |
| **AN1-b** | Produtos (rankings, sem venda, tendências, categorias, ABC) e atendimento IA | Médio |
| **AN1-c** | Clientes (rankings, RFM configurável, ficha), identidades de cliente, exportação CSV/xlsx | Médio |

Cada um com testes de cálculo (pedidos fixos com resultado conhecido, inclusive fuso, virada de dia, cancelamento dias depois, anonimizado e teste), de escopo (`X-Tenant-Scope`, módulo desligado) e de LGPD (máscara em lista e exportação, ficha por papel).

---

## 9. Decisões

| # | Decisão | Proposta |
|---|---|---|
| 1 | iFood, 99Food e site não existem no gateway | AN1 só com WhatsApp e API, já com o campo `canal`; entrada de pedidos externos num bloco próprio depois (o webhook do 99Food hoje vai para o painel, não para o gateway) |
| 2 | Identidade do cliente entre canais | União automática só por telefone E.164 ou id do marketplace; união manual pelo painel; nunca por nome |
| 3 | Pedidos de teste | Coluna `teste` gravada no fechamento + os 11 de hoje marcados; marcação manual pelo painel; análise sem teste por padrão |
| 4 | O que é faturado | Confirmado em diante; `aguardando_loja` à parte como "em aberto" |
| 5 | Faturamento com ou sem taxa de entrega | Com (total), mostrando itens e taxa separados |
| 6 | Dia da venda | Dia do fechamento no fuso da unidade (não o do agendamento) |
| 7 | Grupos RFM | Os da seção 5, editáveis por empresa |
| 8 | xlsx | Biblioteca `openpyxl` fixada no `requirements.txt` (precisa entrar na linha de pip do `scripts/test_suite.sh`); alternativa: o gateway só dá CSV e o painel gera o xlsx |
| 9 | Ficha do cliente | Só admin e plataforma; membro vê a lista mascarada |
| 10 | Quem vê a análise | Todos os papéis da empresa veem os números; exportação só admin e plataforma |
| 11 | Ordem | Depois de ENC1/FUP1, ou antes? Hoje não há pedido real para analisar: proposta é AN1-a logo após a entrada do primeiro cliente real ou junto com a entrada de pedidos externos |
