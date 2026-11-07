# IFOOD1 — iFood, Fase 1: financeiro, avaliações e indicadores (só leitura, mais resposta a avaliação)

**Situação: decisões 1, 2 e 4 aprovadas pelo Marco em 06/11/2026; a 3 segue o padrão recomendado (90 dias, ajustável). Implementação em duas partes: IFOOD1-a (conexão e lojas) e IFOOD1-b (coletas e telas, depende da especificação oficial da seção 8).**

## 1. Objetivo

A empresa que vende no iFood vê no painel:

- quanto vendeu pelo iFood;
- quanto o iFood descontou (comissão, taxas, promoções);
- quanto vai receber e quando.

Vê também as avaliações dos clientes, pode respondê-las, e vê os indicadores que o próprio iFood calcula.

Nada aqui recebe, aceita ou despacha pedido do iFood. Também não muda a loja: não abre, fecha nem pausa, não mexe em cardápio nem em preço. Por isso, se o gateway parar, a loja continua funcionando normalmente no iFood.

## 2. Como a empresa se conecta (aplicativo centralizado)

- **Aplicativo:** a Ticway tem **um** aplicativo aprovado no portal de desenvolvedor do iFood.
  - O `clientId` e o `clientSecret` entram pelo painel, só com a chave de plataforma (seção 2a). O `.env` (`IFOOD_CLIENT_ID`, `IFOOD_CLIENT_SECRET`) fica como alternativa. Esses valores nunca aparecem no chat, no repositório nem em log.
- **Token:** o token é **um só para todas as lojas**.
  - Ele é pedido com `grantType=client_credentials` em `POST /authentication/v1.0/oauth/token`.
  - Fica guardado no Redis até perto de vencer, e só um processo renova por vez.
  - Só é renovado ao vencer ou ao receber 401, porque pedir token demais bloqueia o aplicativo.
- **A empresa não digita credencial.** Ela autoriza o aplicativo da Ticway no Portal do Parceiro do iFood, ou a Ticway pede acesso à loja pelo CNPJ e a loja aprova.
- **A plataforma liga a loja à empresa.** `GET /merchant/v1.0/merchants` devolve todas as lojas que autorizaram o aplicativo, de **todas** as empresas clientes. Por isso, quem liga cada loja iFood a uma empresa (e, se quiser, a uma unidade) é só a plataforma (decisão 1).
  - Se um gestor pudesse escolher a loja, poderia ver o financeiro de outra empresa.
- **Saúde da conexão:** cada loja ligada mostra conectada, sem permissão (o iFood devolveu 401/403 para ela) ou desligada.

## 2a. IFOOD1-a, implementada em 06/11/2026

- **Código:** `app/modulos/ifood/cliente.py` (token e chamadas) e `app/modulos/ifood/rotas.py` (lojas).
- **Migração** `065_ifood1a_lojas.sql`: tabela `ifood_lojas`. Cada loja do iFood fica ligada a uma única empresa.
- **Rotas:**
  - `GET /admin/v1/ifood/lojas-autorizadas` (plataforma);
  - `PUT` e `DELETE /admin/v1/tenants/{t}/ifood/lojas/{merchant_id}` (plataforma; o `DELETE` pede o nome da loja como confirmação);
  - `GET /admin/v1/tenants/{t}/ifood/lojas` e `POST …/{merchant_id}/conferir` (gestor).
- **Erros do iFood** voltam com `detalhes.motivo`:
  - 503 `nao_configurado`, quando o `.env` ainda não tem as credenciais;
  - 502 `autenticacao`, `sem_permissao`, `limite`, `indisponivel` ou `formato_inesperado`.
- **Formato das respostas:** o formato da lista de lojas (`id`, `name`, `corporateName`) e do token (`accessToken`, `expiresIn`) foi usado como o iFood documenta. Se a especificação oficial disser outro, a resposta vira `formato_inesperado` e eu ajusto.
- **Credenciais pelo painel (decisão do Marco de 06/11/2026):**
  - `GET` e `PUT /admin/v1/ifood/credenciais`, com o corpo `{"client_id","client_secret"}`.
  - **O `PUT` só aceita a chave de plataforma** (`PANEL_PLATFORM_KEY`). O papel de plataforma enviado com a chave de empresa recebe 403 `papel_sem_credencial` nesta rota, mesmo na fase A do CHAVES1.
  - O gateway pede um token ao iFood com os valores digitados. Se o iFood recusar, a resposta é 400 `credenciais_recusadas` e nada é gravado.
  - Os valores ficam cifrados em `integracao_credenciais` (migração 066) e nunca são devolvidos. A resposta e a auditoria (`ifood.credenciais_alteradas`, sem empresa) trazem só os 4 últimos caracteres do Client ID e quem trocou.
  - As credenciais gravadas pelo painel valem sobre `IFOOD_CLIENT_ID`/`IFOOD_CLIENT_SECRET` do `.env`, que fica como alternativa.
  - O token é guardado separado por aplicativo, então trocar as credenciais não reaproveita o token das antigas.
- **Testes:** `tests/test_ifood1a.py`, com iFood simulado.

## 2b. IFOOD1-b1: pedidos do iFood por webhook, implementada em 07/11/2026

- **Decisões do Marco (07/11/2026):**
  - tudo aparece no painel: pedidos, financeiro, avaliações e indicadores;
  - os pedidos chegam por **webhook**, não por consulta periódica. A consulta periódica é o que mantém a loja "online" no iFood; se ela continuasse depois que a loja fechasse o próprio sistema, entrariam pedidos que ninguém aceita.
- **Formato:** confirmado com uma consulta de leitura na loja de teste, em 07/11. Os eventos trazem `id`, `code`, `fullCode`, `orderId`, `merchantId`, `createdAt` e `salesChannel`. O detalhe vem em `GET /order/v1.0/orders/{id}`.
- **Webhook:** `POST /webhooks/ifood`, pelo endereço `https://painel.luxbrain.com.br/gateway/webhooks/ifood`, que o Marco cadastra no portal do iFood.
  - **Assinatura:** `X-IFood-Signature` é o HMAC-SHA256 do corpo bruto com a chave secreta do aplicativo. Aceita em hexadecimal ou base64.
  - **Respostas:** assinatura errada → 401; sem credenciais → 503; certo → 202.
  - **Gravação:** o evento é gravado uma vez só (por `id`). Loja que não está ligada a nenhuma empresa é ignorada.
  - **Status:** um evento atrasado não faz o status voltar; o cancelamento vale sempre.
  - **Detalhe:** é buscado depois da resposta. Se falhar, conta tentativa (até 10) e o botão "Atualizar" do painel busca de novo.
  - **Sem confirmação nem consulta:** o webhook do iFood não tem confirmação de recebimento, e o gateway não faz consulta periódica.
- **Dados (migração 067):**
  - `ifood_eventos`;
  - `ifood_pedidos`: número, status, se é teste, tipo, canal, valores, pagamentos, itens, id e nome do cliente no iFood e bairro.
  - **Não grava** telefone, documento, e-mail nem endereço do cliente.
- **Painel (gestor):**
  - `GET /admin/v1/tenants/{t}/ifood/pedidos` (filtros `de`, `ate`, `status`, `loja`, `teste`, `limite`, `antes`);
  - `GET …/ifood/pedidos/{order_id}`, com os eventos;
  - `POST …/ifood/pedidos/atualizar`, que busca até 20 detalhes pendentes.
- **Visão de vendas:** `GET …/pedidos/indicadores` ganha o bloco `ifood`.
  - Traz resumo, período anterior, variação, evolução e os 20 produtos que mais venderam no iFood.
  - Venda é confirmado em diante. Pedido de teste do iFood só entra com `origem=…,teste`.
  - O filtro de unidade usa a unidade a que a loja está ligada.
  - O bloco vem `null` se a empresa não tiver loja do iFood.
- **Testes:** `tests/test_ifood1b.py`.
- **Ainda falta (IFOOD1-b2):** financeiro, avaliações e indicadores do iFood. Falta a especificação (Schemas) do Financial v3 e do Review v2. A do Analytics v1 o Marco já mandou.

## 2c. IFOOD1-b2: financeiro do iFood, implementada em 07/11/2026

- **Fonte:** a API Financial Events v3 (`GET /financial/v3.0/merchants/{id}/financial-events`, com `beginDate`, `endDate`, `page` e `size`), conforme a página da documentação que o Marco mandou em 07/11.
  - Cada evento traz o valor (positivo é crédito, negativo é débito), se entra no repasse (`hasTransferImpact`), a data prevista de pagamento, o pedido e a forma de pagamento.
  - **Repasse líquido** é a soma dos eventos com `hasTransferImpact`. Pedido pago direto à loja vem com `false`, mas a comissão dele vem com `true`.
- **Coleta:**
  - em janelas de 7 dias (o iFood aceita até 33), página a página;
  - a primeira coleta pega 90 dias; as seguintes refazem os últimos 8 dias, porque há eventos que chegam atrasados;
  - a API não dá id ao evento, então cada janela coletada substitui os eventos da loja naquelas datas. Coletar de novo corrige sem duplicar;
  - acontece sozinha quando o painel abre e a última coleta passou de 6 h, ou pelo botão "Atualizar" (no máximo a cada 10 min por loja);
  - só uma coleta por loja de cada vez. Se der erro, ele fica gravado e os dados anteriores continuam.
- **Dados (migração 068):**
  - `ifood_fin_eventos`, sem o documento da loja nem dado de cliente;
  - `ifood_coletas`, com a situação de cada coleta.
- **Painel (gestor):**
  - `GET /admin/v1/tenants/{t}/ifood/financeiro?de&ate&loja` (padrão: 30 dias) traz:
    - `resumo`: créditos, débitos, repasse líquido, recebido direto pela loja, pedidos e eventos;
    - `por_tipo`: por nome e descrição do evento;
    - `por_dia` e `por_forma`;
    - `a_receber`: o repasse com data prevista de hoje em diante;
    - `coletas`: por loja, com o último sucesso, o erro e se está coletando.
  - `POST …/ifood/financeiro/atualizar` coleta agora.
  - O detalhe do pedido do iFood ganha `financeiro`: o que o iFood creditou e descontou nele.
- **Testes:** `tests/test_ifood1b.py`.
- **Repasses (API Settlement, página enviada pelo Marco em 07/11):**
  - Chamada: `GET …/settlements?beginPaymentDate&endPaymentDate`, em janelas de 30 dias, até hoje (o iFood não aceita data futura). Sem liquidação no período, o iFood responde 404, que vale como vazio.
  - Gravação: cada título (`closingItems`) é gravado por id em `ifood_repasses` (migração 069), com tipo, produto, valor, status, data de pagamento e período. **Sem os dados bancários.**
  - Coleta: a primeira busca 90 dias; as seguintes, as últimas 5 semanas, porque o título muda de pendente para pago.
  - O financeiro ganha `repasses`: pago, pendente, retido em recebíveis, boletos e renegociado, além dos títulos do período.
- **Ainda falta:** avaliações (Review) e indicadores (Analytics).

## 3. O que é buscado no iFood

As rotas de leitura são as da referência que o Marco enviou (`integracao_ifood_documentacao_completa.md`). Os campos exatos vêm da especificação oficial (seção 8).

| Área | Rotas do iFood | Frequência | Histórico ao ligar |
|---|---|---|---|
| Vendas | `GET /financial/v3.0/merchants/{id}/sales` | a cada 6 h, refazendo os últimos 7 dias | 90 dias |
| Repasses | `GET …/settlements` | a cada 6 h | 90 dias |
| Antecipações | `GET …/anticipations` | a cada 6 h | 90 dias |
| Eventos financeiros | `GET …/financial-events` | a cada 6 h | 90 dias |
| Avaliações | `GET /review/v2.0/merchants/{id}/reviews` (paginado) e `…/summary` | a cada 30 min | 90 dias |
| Indicadores | `POST /analytics/v1.0/merchants/{id}/orders/kpis` (por rede: `/chain/{chainId}/orders/kpis`) | 1 vez por dia, dia anterior | 90 dias |
| Loja | `GET /merchant/v1.0/merchants/{id}` e `…/status` | a cada 30 min | — |

- **Quem busca:** o agendador que já existe, com uma tarefa por loja e área.
- **Ritmo:**
  - uma loja por vez;
  - com intervalo entre chamadas;
  - recuo em 429 e 5xx, e o motivo fica gravado.
- **Gravação:** cada coleta grava por chave do iFood (pedido, repasse, avaliação). Repetir não duplica e corrige o que o iFood revisou.
- **Guardar do nosso lado:** o iFood avisa que a API não é cópia de segurança. O que entra fica guardado aqui, inclusive o JSON original de vendas e repasses, para conferência.

## 4. Dados (migração aditiva, `065_ifood1.sql`)

| Tabela | Conteúdo |
|---|---|
| `ifood_lojas` | `tenant_id`, `merchant_id`, `nome`, `unidade` (opcional), `ativo`, `situacao` (conectada / sem_permissao / desligada), `ligado_por`, `ligado_em` |
| `ifood_vendas` | loja, `order_id` do iFood, data, valor bruto, comissão e taxas, promoções, líquido, forma de pagamento, `bruto` (JSON original) |
| `ifood_repasses` | loja, id do repasse, período, valor, situação, data de pagamento, `bruto` |
| `ifood_antecipacoes` | loja, id, valor, taxa, data, `bruto` |
| `ifood_eventos_financeiros` | loja, id, tipo, valor, data, pedido, `bruto` |
| `ifood_avaliacoes` | loja, `review_id`, nota, comentário, data, situação, resposta e quem respondeu, e o pedido quando o iFood informar |
| `ifood_indicadores` | loja, dia, métrica, valor |
| `ifood_coletas` | loja, área, última coleta, último sucesso, erro (sem token nem dado pessoal) |

**Dados pessoais:**
- Não gravamos telefone nem endereço de cliente iFood.
- Se a avaliação trouxer o nome do cliente, ele aparece só para gestor e acima.
- Os testes usam dados fictícios.

## 5. Rotas do painel

| Rota | O quê | Papel |
|---|---|---|
| `GET /admin/v1/ifood/lojas-autorizadas` | lojas que autorizaram o aplicativo e a quem estão ligadas | plataforma |
| `PUT /admin/v1/tenants/{t}/ifood/lojas/{merchant_id}` | liga a loja à empresa (e unidade); `{"ativo": false}` desliga | plataforma |
| `DELETE /admin/v1/tenants/{t}/ifood/lojas/{merchant_id}` | desliga e apaga os dados guardados daquela loja (com o nome como confirmação, como no CANAL-EXC) | plataforma |
| `GET /admin/v1/tenants/{t}/ifood/lojas` | lojas da empresa e situação de cada coleta | gestor |
| `GET /admin/v1/tenants/{t}/ifood/financeiro?de&ate&loja` | resumo: bruto, taxas, promoções, líquido, ticket médio, por dia; repasses já pagos e a receber; antecipações | gestor |
| `GET /admin/v1/tenants/{t}/ifood/avaliacoes?de&ate&nota&respondida&loja` | lista paginada e resumo (média, distribuição de notas, % respondidas) | gestor |
| `POST /admin/v1/tenants/{t}/ifood/avaliacoes/{review_id}/responder` | responde no iFood (só avaliação não respondida); auditoria `ifood.avaliacao_respondida` | gestor |
| `GET /admin/v1/tenants/{t}/ifood/indicadores?de&ate&loja` | os indicadores do iFood por dia | gestor |
| `POST /admin/v1/tenants/{t}/ifood/coletar` | coleta agora (no máximo 1 a cada 10 min) | gestor |

- **Visão de vendas:** o `GET …/pedidos/indicadores` (PED-BI) ganha um bloco `ifood` no resumo (faturamento bruto, líquido e pedidos do período), para comparar os canais (decisão 4).
  - O ranking de produtos e clientes continua só com os pedidos do nosso canal, porque o financeiro do iFood não traz o cliente com telefone.

## 6. Envio real (resposta a avaliação)

- **É a única escrita no iFood.** Fica atrás de `IFOOD_RESPOSTAS=live` no `.env`. Sem isso, a resposta é gravada como simulada e não sai.
- **Limites e auditoria:** o texto tem de 10 a 300 caracteres. A auditoria grava a avaliação e quem respondeu, sem o texto do cliente.

## 7. Testes

- **Sem iFood de verdade:** os testes usam um iFood simulado (`httpx.MockTransport`) e nunca chamam o iFood real.
- **Token:**
  - um só token para várias lojas;
  - renovação só ao vencer ou com 401, sem pedidos em sequência;
  - o segredo não aparece em log nem em resposta.
- **Isolamento:** a empresa A não vê loja nem dado da empresa B. Uma loja só aparece para a empresa a que a plataforma a ligou.
- **Coleta:**
  - repetida não duplica;
  - correção do iFood atualiza o que já estava gravado;
  - 429 e 5xx recuam e gravam o erro;
  - 403 marca a loja como `sem_permissao`.
- **Números:** o resumo financeiro e de avaliações bate com um cenário conhecido.
- **Resposta a avaliação:** simulada sem `live`; avaliação já respondida é recusada; tudo fica auditado.
- **Papéis:** a plataforma liga as lojas; o gestor vê; supervisor e funcionário recebem 403.

## 8. O que falta para começar

1. **Especificação oficial em JSON (OpenAPI).** As páginas do portal do iFood não abrem do servidor, e o documento enviado só lista as rotas, sem os campos. Preciso do arquivo de cada API, exportado do portal com o login da Ticway: Financial v3, Review v2, Analytics v1, Merchant v1 e Authentication.
   - Salvar em `/home/twadmin/ifood_specs/` na VPS.
   - Esses arquivos não têm segredo.
2. **Credenciais no `.env`.** O Marco grava `IFOOD_CLIENT_ID` e `IFOOD_CLIENT_SECRET` no `.env` da VPS, sem colar no chat.
3. **Permissões do aplicativo.** Confirmar no portal que o aplicativo aprovado tem acesso às áreas Financial, Review, Analytics e Merchant, e que é do tipo **centralizado**.
4. **Uma loja para o primeiro teste.** Uma loja real que autorize o aplicativo, para a primeira coleta de verdade. É só leitura; a resposta a avaliação fica simulada.

## 9. Decisões do Marco (06/11/2026)

1. **Quem liga a loja iFood à empresa:** só a plataforma.
2. **Quem responde avaliação:** gestor e acima.
3. **Histórico ao ligar a loja:** não respondida. Vale o padrão de 90 dias, numa constante fácil de mudar.
4. **Visão de vendas:** mostra o iFood junto, para comparar os canais.

## 10. O que não entra na Fase 1

- Receber, aceitar ou despachar pedidos do iFood (Fase 2).
- Cardápio, preço, estoque, promoções, horários e pausas da loja.
- Entregador do iFood para pedido do WhatsApp (Shipping).
- Juntar o cliente do iFood com o cliente do WhatsApp.
