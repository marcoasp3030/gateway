# PEDEXT — Pedidos do iFood e do 99Food na tabela de pedidos

**Situação: proposta para decisão do Marco, 16/10/2026. Só leitura; nada implementado.** Faz parte do módulo `pedidos`. Vem depois de ENC1/FUP1 e antes da AN1 (`docs/ESPEC_AN1.md`, seção 10).

---

## 1. O que existe hoje (levantamento só de leitura)

| | iFood | 99Food |
|---|---|---|
| No gateway | Nada | Nada |
| No painel | Central de testes: token por `client_credentials` (`merchant-api.ifood.com.br/authentication/v1.0/oauth/token`), dados e status da loja, `order/v1.0/events:polling`, detalhe `order/v1.0/orders/{id}`, avaliações. Credenciais (`client_id`, `client_secret`, `merchant_id`) na tabela `integrations` do Supabase do painel | Webhook `/api/public/food99/webhook` grava o **evento bruto** em `webhook_events` (loja, id do pedido, tipo, status, horário, payload original); central de testes em `openapi.99food.com` (token `v1/auth/authtoken/get`, loja, `v1/orders/events:polling`, detalhe `v1/order/order/detail`). Credenciais (`app_id`, `app_secret`, `app_shop_id`) no `integrations` do painel |
| Pedidos guardados | Nenhum | Só eventos brutos (sem itens tratados, sem cliente, sem valores separados) |

## 2. O que a API do iFood permite (documentação oficial do Merchant API)

| Assunto | O que a API dá |
|---|---|
| Receber pedidos | **Polling** de eventos (`GET /order/v1.0/events:polling`, recomendado a cada 30 s) com **confirmação** obrigatória (`POST …/acknowledgment`; evento não confirmado volta no próximo polling). Ou **webhook**, sem confirmação e sem filtro por loja. Os eventos podem vir fora de ordem (ordenar por `createdAt`) |
| Ciclo do pedido | Eventos de colocado, confirmado, pronto para retirada, despachado, concluído e cancelado (códigos exatos a fixar com uma amostra real, seção 7) |
| Detalhe | `GET /order/v1.0/orders/{id}` |
| Tipo e momento | `orderType` (DELIVERY, TAKEOUT, DINE_IN), `orderTiming` (IMMEDIATE, SCHEDULED), `salesChannel` (ex.: IFOOD), `createdAt`, `preparationStartDateTime` |
| Itens | nome, quantidade, `unitPrice`, `options` (adicionais), `observations` (até 1024), `externalCode` (o código do PDV, se a loja cadastrou) |
| Valores | `total.subTotal`, `total.deliveryFee`, `total.benefits`, `total.additionalFees`, `total.orderAmount` (= subTotal + deliveryFee + additionalFees − benefits). Benefícios com patrocínio `IFOOD`, `MERCHANT`, `EXTERNAL`, `CHAIN`: só `MERCHANT` é desconto pago pela loja |
| Pagamento | `payments.prepaid`, `payments.pending`, `payments.methods[].type` (ONLINE/OFFLINE) |
| Cliente | `customer.id`, `name`, `documentNumber` (CPF, só para nota fiscal), `ordersCountOnMerchant`, `phone.number` + `phone.localizer` + `phone.localizerExpiration`. **O telefone é um número intermediário com código localizador**, deixa de ser enviado 3 h após a entrega e pode faltar |
| Endereço | `delivery.deliveryAddress` completo, com coordenadas |
| Ações | Confirmar, despachar, cancelar etc. (fora do PEDEXT, seção 6) |
| Autenticação | OAuth `client_credentials` do aplicativo; a loja autoriza o aplicativo no Portal do Parceiro |

Fontes: [Endpoints](https://developer.ifood.com.br/en-US/docs/food/guides/modules/order/endpoints), [Estrutura do pedido](https://developer.ifood.com.br/en-US/docs/food/guides/modules/order/details), [Polling](https://developer.ifood.com.br/pt-BR/docs/guides/modules/events/polling-overview/), [Webhook](https://developer.ifood.com.br/en-US/docs/guides/modules/events/webhook-overview). Prazos de confirmação, cancelamento automático e exigência de homologação: **a confirmar** no portal antes de implementar.

99Food: o formato do detalhe do pedido não está na documentação pública que encontrei. A fonte confiável são os **eventos reais que o painel já guarda** e uma chamada ao detalhe pela central de testes do painel: proposta é o Marco exportar uma amostra (sem dados pessoais) para fixar o formato num teste.

## 3. Proposta

### 3.1 Como o pedido chega ao gateway

| | Proposta |
|---|---|
| iFood | O **agendador do gateway faz o polling** a cada 30 s por loja autorizada, grava o evento, busca o detalhe, grava/atualiza o pedido e **só então** confirma o evento. Sem webhook no início (o polling tem confirmação e filtro por loja) |
| 99Food | **Mudar o webhook da 99Food para o gateway** (`/webhooks/99food/{integracao}?s=<segredo>`), com polling de reserva a cada 60 s. O painel deixa de ser o destino. Alternativa (decisão 3): o painel continua recebendo e repassa ao gateway |
| Repetição | Evento e pedido idempotentes por (empresa, canal, id do pedido no canal) e id do evento; evento fora de ordem não volta status para trás |

### 3.2 Onde o pedido fica

Na **mesma tabela `pedidos`**, com `canal` = `ifood` ou `99food` e `origem` = `real` (ou `teste` para loja de homologação). Migração aditiva:

- `pedidos.conversation_id` passa a aceitar nulo (pedido de marketplace não tem conversa; a ligação com a conversa continua para os do WhatsApp).
- `pedidos.canal_pedido_id` (id no marketplace) e `pedidos.canal_numero` (o número curto que a loja vê no tablet), com único (empresa, canal, canal_pedido_id).
- `pedidos.desconto` (benefícios `MERCHANT`), `pedidos.subsidio_canal` (benefícios IFOOD/EXTERNAL/CHAIN, informativo), `pedidos.taxas_adicionais`, `pedidos.pago_online` (bool), `pedidos.agendado_para` (já existe).
- `pedidos.cliente_externo_id` (ver 3.4) e `pedidos.loja_externa` (merchant/loja do canal).
- `integracoes_pedidos` (empresa, canal, loja externa, unidade do gateway, credencial cifrada, ativo, último polling) e `pedidos_externos_eventos` (evento bruto mínimo, para auditoria e reprocessamento, apagado após 30 dias).

**Status** (mapa a fixar com a amostra real):

| Marketplace | Gateway |
|---|---|
| colocado | `aguardando_loja` |
| confirmado | `confirmado` |
| em preparo / pronto para retirada | `em_preparo` / `pronto_retirada` |
| despachado | `saiu_entrega` |
| concluído | `concluido` |
| cancelado (pela loja, pelo cliente ou pelo marketplace) | `cancelado`, com o motivo do canal |

**Itens:** cada item com nome, quantidade, preço unitário, subtotal e adicionais (como linhas próprias, `tipo: adicional`, ligadas ao item). `item_id` do nosso catálogo quando o `externalCode` do marketplace bate com o `codigo` do catálogo da unidade; senão nulo (a análise usa o nome). Categoria: a do nosso catálogo quando bateu; senão a do marketplace, se vier.

### 3.3 O que **não** muda com pedido de marketplace

- Nenhum aviso ao cliente pelo WhatsApp (o cliente é do marketplace), nenhum "OK <numero>" da unidade, nenhuma ação de carrinho.
- O agente do WhatsApp não vê pedidos de marketplace (o pacote só traz os pedidos da conversa).
- O gateway **não confirma, despacha nem cancela** no marketplace neste bloco (seção 6): a loja continua usando o gestor do iFood/99Food; o gateway só espelha.

### 3.4 Cliente de marketplace **não se une** ao cliente do WhatsApp

Regra explícita (decisão do Marco de 16/10):

- O telefone do iFood é **intermediário** (localizador) e o do 99Food não é confiável como identidade: **nenhum pedido de marketplace é ligado a um contato do WhatsApp**, nem por telefone, nem por nome.
- O cliente de marketplace é identificado só pelo id do cliente **naquele canal** (`cliente_externo_id` = `ifood:<customer.id>` / `99food:<id>`), guardado como hash (SHA-256 com o segredo da plataforma) para contar recorrência sem guardar o id cru.
- A AN1 conta clientes únicos e recorrência **por canal**; o "cliente único" total é a soma, sabendo que a mesma pessoa pode aparecer uma vez em cada canal.

### 3.5 LGPD

- Guarda: nome do cliente (para a lista de pedidos do painel), id do cliente em hash, itens, valores, status, horários, modalidade, bairro e cidade.
- **Não guarda:** telefone/localizador, CPF, endereço completo, coordenadas, observações dos itens do marketplace além de 200 caracteres (para a cozinha, quando o painel mostrar). Evento bruto guardado sem esses campos.
- Anonimização: pedido de marketplace não tem contato no gateway; um pedido de exclusão chega pelo marketplace. Rota de plataforma para apagar o nome e o hash de um cliente externo.

## 4. O que cadastrar com o By Koji

| Para | O quê | Quem |
|---|---|---|
| iFood | Aplicativo da plataforma (o mesmo `client_id`/`client_secret` que o painel usa, ou um novo) homologado no iFood, se exigido; `merchantId` de **cada loja** do By Koji no iFood; autorização do aplicativo no Portal do Parceiro de cada loja | Marco (portal do iFood) e By Koji (autorização) |
| 99Food | `app_id`, `app_secret` e `app_shop_id` de cada loja; troca do endereço do webhook para o do gateway (decisão 3) | Marco (portal 99Food) |
| Ambos | **De-para loja do marketplace → unidade do gateway** (Pátio Guedala, Morumbi…); códigos dos itens no marketplace iguais ao `codigo` do nosso catálogo (opcional, melhora a análise por produto) | By Koji |
| Gateway | Credenciais só pelo script de credencial (como o `scripts/canal_credencial.py`), cifradas no cofre; nunca pelo chat, nunca copiadas automaticamente do Supabase do painel | Marco roda o script |

## 5. Painel

- Lista de pedidos com filtro `canal` e ícone do canal; pedido de marketplace sem "Confirmar/Recusar" (só espelho), com o número curto do canal.
- Tela de integrações passa a gravar no gateway (rota admin de plataforma) em vez do Supabase, ou o Marco usa o script.

## 6. Fora do PEDEXT

Confirmar, despachar, cancelar e responder pelo gateway (gestor de pedidos próprio); cardápio sincronizado com os marketplaces; avaliações; chat do marketplace; repasses financeiros.

## 7. Blocos

| Bloco | Conteúdo | Tamanho |
|---|---|---|
| PEDEXT-0 | Amostras reais anonimizadas (iFood: um pedido de cada tipo e evento; 99Food: eventos do `webhook_events` e um detalhe) viram fixtures de teste; mapa de status fechado | Pequeno (Marco exporta; eu anonimizo e fixo) |
| PEDEXT-a | Migração, integrações e credenciais pelo script, iFood por polling + detalhe, status, itens, cliente externo em hash, painel com filtro de canal | Grande |
| PEDEXT-b | 99Food (webhook no gateway + polling de reserva), mesmos testes | Médio |

## 8. Decisões

| # | Decisão | Proposta |
|---|---|---|
| 1 | Só espelhar ou também agir no marketplace | Só espelhar no PEDEXT; agir num bloco futuro |
| 2 | iFood por polling ou webhook | Polling (tem confirmação e filtro por loja) |
| 3 | Webhook do 99Food | Apontar para o gateway (o painel deixa de receber); alternativa: o painel repassa |
| 4 | Credenciais | Novas no cofre do gateway, pelo script; o painel deixa de guardar as de produção |
| 5 | Cliente de marketplace | Nunca unido ao do WhatsApp; id do canal em hash; recorrência por canal |
| 6 | Dados pessoais do marketplace | Sem telefone, CPF e endereço completo; bairro e cidade sim |
| 7 | Adicionais | Linhas próprias ligadas ao item (a análise pode somar ou separar) |
| 8 | Loja de homologação | Pedidos com `origem=teste` |

## 9. Aprovado pelo Marco (16/10/2026)

Linha geral aprovada: iFood por polling com confirmação; 99Food por webhook direto no gateway com polling de reserva; só espelhar (sem confirmar nem cancelar no marketplace); cliente de marketplace sem unir ao do WhatsApp, só com o id em hash; credenciais só pelo script; amostra anonimizada dos eventos da 99Food que o painel já guarda para fixar o formato. As 8 decisões da seção 8 aguardam resposta.

## 10. iFood: homologação, tipo de aplicativo e prazos (pesquisa na documentação oficial, 16/10/2026)

| Tema | O que a documentação do iFood Developer diz | Consequência para nós |
|---|---|---|
| Tipo de aplicativo | **Centralizado** (SaaS, na nuvem, acesso às lojas vinculadas, token por `client_credentials` com `clientId`/`clientSecret`, validade de 6 h, **sem refresh token**) ou **distribuído** (instalado no ambiente da loja, acesso só àquela loja, vínculo por `userCode`/`authorizationCode` com refresh token) | **Centralizado**: o gateway é uma plataforma na nuvem para várias empresas. É o mesmo tipo que a central de testes do painel já usa |
| Vínculo da loja | No Portal do Desenvolvedor, em Meus Apps → Permissões, busca-se a loja por ID ou CNPJ e envia-se a solicitação; um gestor da loja aprova no **Portal do Parceiro** (de preferência o perfil "Dono"). Se esse usuário for desativado, a integração para | Pedir ao By Koji quem é o "Dono" no Portal do Parceiro de cada loja, para aprovar |
| Homologação | O portal cria um aplicativo e uma loja **de teste**; o aplicativo de produção só é criado **depois da homologação**, pedida pela aba Suporte (sessão de validação). Cada módulo tem critérios próprios; o iFood testa o aplicativo completo; só conta profissional (CNPJ) | **Precisa homologar**. Desenvolver contra a loja de teste e agendar a validação. **Risco a confirmar com o suporte do iFood:** os critérios do módulo de pedidos costumam exigir as ações (confirmar, despachar); um aplicativo que só espelha pode não ser aceito no módulo de pedidos como está. Se não for, a decisão 1 (só espelhar) precisa ser revista |
| Confirmação | Pedido tem de ser **confirmado em até 8 minutos**; depois disso o iFood **cancela automaticamente**, inclusive retirada. Cancelamentos em excesso geram penalidade e podem fechar a loja temporariamente | Enquanto o gateway só espelha, a loja continua confirmando no Gestor de Pedidos do iFood. O gateway mostra o cancelamento automático como `cancelado` com o motivo |
| Eventos | Polling recomendado a cada 30 s; confirmar (ack) só o que foi processado; eventos guardados até 8 h depois da entrega e podem vir fora de ordem; webhook tenta por até 15 min, sem ack | Polling a cada 30 s com ack depois de gravar (decisão 2); reprocessar por id do evento |
| Dados do cliente | Telefone com localizador, que deixa de ser enviado 3 h depois da entrega; CPF só para nota fiscal | Já previsto na seção 3.4/3.5 |

Fontes: [Autenticação](https://developer.ifood.com.br/en-US/docs/guides/authentication/), [Aplicativo centralizado](https://developer.ifood.com.br/en-US/docs/guides/modules/authentication/centralized/), [Solicitar acesso](https://developer.ifood.com.br/pt-BR/docs/getting-started/first-steps/request-access), [Primeiros passos](https://developer.ifood.com.br/en-US/docs/guides/getting-started/), [Homologação (módulo financeiro, mesmo processo)](https://developer.ifood.com.br/en-US/docs/guides/modules/financial/homologation), [Pedido para retirada](https://developer.ifood.com.br/pt-BR/docs/guides/modules/order/takeout/), [Endpoints de pedido](https://developer.ifood.com.br/pt-BR/docs/guides/modules/order/endpoints), [Fluxo do pedido](https://developer.ifood.com.br/en-US/docs/guides/modules/order/workflow/), [Polling](https://developer.ifood.com.br/pt-BR/docs/guides/modules/events/polling-overview/). Parte das páginas recusou leitura direta (403) e foi lida pelos trechos indexados; conferir no portal logado antes de implementar.

As 8 decisões da seção 8 foram **aprovadas** como propostas (16/10). Implementação na fila depois do FUP1, dependendo dos acessos do By Koji.
