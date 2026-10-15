# Auditoria: o gateway está pronto para outros tipos de agente e outras empresas?

**Data:** 15/10/2026. **Só leitura:** nada foi alterado no gateway, no painel ou nos dados.
**Produção:** `gateway:256169c`, schema 42. **Painel:** lido em `painel_koji`, commit `9afa7b3` (com alterações locais que não conferi uma a uma).
**Exemplo usado:** um "assistente financeiro" para outra empresa (cobrança, segunda via, dúvidas de conta), sem cardápio nem pedido.

## Resumo

- **A base é multiempresa e multiagente de verdade.** Cada empresa (`tenant`) tem os seus agentes, canais, chaves, conversas, configuração versionada e consumo. Cada agente tem o seu fluxo no Skyone. Cada canal tem o seu número da uazapi. Tudo isso já se cria pelo painel.
- **O restaurante está concentrado em dois módulos** (`catalogo` e `pedidos`), que só entram quando a empresa os liga e o modelo do agente os inclui. Um agente sem eles recebe o pacote sem carrinho nem catálogo. As guardas de pedido só atuam com o módulo de pedidos.
- **O que ainda tem cara de restaurante:** o menu do painel (Cardápio, Pedidos, Unidades, Avisos de pedido aparecem para qualquer empresa), alguns textos e listas fixas (motivos de transferência e tipos de solicitação) e o único segmento pronto, `delivery_restaurante`.
- **Para um assistente financeiro conversar** (tirar dúvidas, transferir, avisar a equipe): dá para colocar hoje, com ajustes pequenos.
- **Para ele agir** (consultar boleto, saldo, segunda via): é preciso um módulo novo, estimado em 2 a 3 blocos, reaproveitando quase tudo do padrão dos módulos atuais.

---

## 1. Do zero ao primeiro "oi" respondido

| # | Passo | Painel | API admin | Script ou banco |
|---|---|---|---|---|
| 1 | Criar a empresa | ✅ Assistente "Nova empresa" (passo 1) | `POST /admin/v1/tenants` | — |
| 2 | Dar acesso ao cliente no painel | ✅ Passo 2 do assistente (usuário do painel) | — | — |
| 3 | Escolher o segmento do agente | ✅ Passo 3: lista de modelos | `GET /admin/v1/modelos-agente` | — |
| 3a | Criar um segmento novo (ex.: `assistente_financeiro`) | ❌ Não há tela | `POST /admin/v1/modelos-agente` (plataforma) | — |
| 4 | Criar o agente | ✅ Passo 3 | `POST /admin/v1/tenants/{t}/agentes` | — |
| 5 | Ligar o Skyone (fluxo e credencial) | ✅ Passo 4 "IA (Skyone)" | `PATCH …/agentes/{id}` | — |
| 6 | Criar o canal WhatsApp (uazapi) com endereço e token | ✅ Passo 5 "Canal" | `POST …/canais` com `credenciais` | Ou `scripts/canal_credencial.py`, que pede o token sem mostrar |
| 7 | Apontar o webhook da instância uazapi para o gateway | ❌ | — | ❌ **Manual, no painel ou na API da uazapi**: o gateway gera a URL (`webhook_url`, com segredo), mas não a cadastra na instância |
| 8 | Chave de API para as Skills do Skyone | ✅ "Chaves de API" | `POST …/api-keys` | — |
| 9 | Perfil de atendimento (nome, tom, regras) e comportamentos | ✅ "Comportamento do agente" (rascunho → publicar) | `…/atendimento` | — |
| 10 | Ligar módulos (catálogo, pedidos) | ❌ Não há tela | `PUT /admin/v1/tenants/{t}/modulos/{modulo}` | — |
| 11 | Unidades e horários (se usar) | ✅ "Unidades e horários" | `…/unidades` | — |
| 12 | Testar | ✅ Passo 6 "Simular" | `POST …/agentes/{id}/simular` e `…/testar` | — |
| 13 | Envio real e piloto (`send_mode`, `contatos_permitidos`) | ✅ Na tela do agente, para plataforma | `PATCH …/agentes/{id}` | — |
| 14 | Primeiro "oi" no WhatsApp | — | — | — |

**Fora do gateway e do painel:** montar o fluxo no Skyone (prompt, Skills e a chave do passo 8) e configurar o webhook na instância uazapi (passo 7).

**Só por banco:** nada. Os poucos ajustes feitos por script até hoje (cidade/UF do By Koji, almoços executivos) foram dados dentro de migração, não passos obrigatórios.

---

## 2. Módulos e o que é fixo de delivery

### 2.1 Como os módulos são ligados

São **duas chaves, e as duas precisam estar ligadas:**

1. **Na empresa:** tabela `modulos_tenant` (`PUT /admin/v1/tenants/{t}/modulos/{modulo}`). Hoje só o By Koji tem `catalogo` e `pedidos` ligados.
2. **No segmento do agente:** o modelo (`modelos_agente.modulos`) diz quais módulos aquele tipo de agente usa. `delivery_restaurante` traz `catalogo` e `pedidos`; `assistente_generico` não traz nenhum.

**Na prática é por agente:** dois agentes da mesma empresa com segmentos diferentes enxergam módulos diferentes, mesmo com o módulo ligado na empresa. As Skills conferem as duas chaves e a conversa: agente sem o módulo recebe 404.

`solicitacoes` (avisar a equipe, SOL1) não é módulo: é o comportamento `notificar_equipe`, ligado por agente com herança.

### 2.2 O pacote de um agente sem o módulo de pedidos

| Campo do pacote | Agente sem pedidos nem catálogo |
|---|---|
| `contexto_modulos` | `{}` — sem carrinho, sem catálogo, sem pedidos. Com `notificar_equipe` ligado, só `solicitacoes` |
| `unidades` | Sempre presente. Lista vazia se a empresa não cadastrar unidade; se cadastrar (ex.: agências), vem com horário, endereço e o resto. `tem_cardapio` e `itens_no_cardapio` vêm `false`/`0` |
| `fora_do_horario`, `proxima_abertura` | `false` e `null` sem unidades (não afirma que está fechado). Com unidades, seguem o horário delas |
| `calendario` | Sempre: datas e feriados (nacionais sem unidade; estaduais e municipais pela cidade das unidades). Útil para qualquer negócio |
| `perfil_atendimento`, `recursos`, `historico`, `cliente`, `aviso_do_gateway`, `acoes_recentes` | Genéricos |

**Conclusão:** o pacote fica limpo. Sobram campos genéricos com nome de loja (`unidades`, `fora_do_horario`), que servem para "agências" ou "horário de atendimento".

### 2.3 O que é fixo de delivery no código

| Item | Onde | Efeito num agente financeiro |
|---|---|---|
| Guarda de anúncio falso de pedido (PED2) | Pipeline | **Só atua com o módulo de pedidos.** Não afeta |
| Guarda de transferência sem recusa (HOR2) | Pipeline | **Só com o módulo de pedidos** e só para `pedido_para_confirmar`. Não afeta |
| Ação `pedido_fechado` no contrato do Retorno | `agentes/skyone.py` | Existe, mas não é obrigatória. Não afeta |
| Motivos de transferência configuráveis no perfil | `nucleo/atendimento.py`: `pediu_humano`, `reclamacao`, `pedido_para_confirmar`, `pagamento`, `restricao_alimentar`, `evento_ou_encomenda`, `catering`, `fora_do_escopo`, `assunto_proibido` | **Ajuste pequeno.** O agente pode transferir com qualquer motivo (o gateway aceita texto livre), mas a lista que o painel mostra e o rótulo do alerta são de restaurante. Precisa de lista por segmento |
| Tipos de solicitação à equipe | `nucleo/solicitacoes.py`: `evento`, `catering`, `encomenda`, `orcamento`, `parceria`, `outro` | **Ajuste pequeno:** lista por segmento (ex.: `negociacao`, `contestacao`, `segunda_via`) |
| Motivos que convertem transferência em aviso à equipe (`evento_ou_encomenda`, `catering`) | `nucleo/solicitacoes.py` | Não afeta (não dispara para outros motivos) |
| Texto do alerta de transbordo | `modulos/pedidos/avisos.py` | Genérico ("conversa aguardando atendente"); a linha "Pedido:" só aparece se houver pedido |
| Avisos de status de pedido ao cliente | `modulos/pedidos/avisos.py` | Só existem com pedidos |
| Comportamento `carrinho_expira_h` | Grupo `pedidos` dos comportamentos | Aparece no esquema para todos; sem efeito sem carrinho. **Ajuste pequeno:** esconder por módulo |
| Segmento pronto | Só `delivery_restaurante` (v1 a v3) e `assistente_generico` (interno, de teste) | **Ajuste pequeno:** criar `assistente_financeiro` pela API, com `perfil_padrao`, desfechos e módulos próprios |
| Desfechos | Por segmento (`pedido_feito` etc.) | Já configurável por segmento |
| Perfil de atendimento (tom, emojis, regras, nome da empresa) | Genérico | Serve |

---

## 3. Canal: várias instâncias da uazapi

- **Sim.** Cada canal é uma linha de `tenant_canais` com as suas credenciais (endereço base e token da instância), cifradas por empresa, o seu webhook (`/webhooks/uazapi/{canal}?s=<segredo>`) e um agente.
- **Um número por agente, ou vários números por empresa:** suportado. Hoje o By Koji tem um canal uazapi e alguns canais de API de teste.
- **Cadastro:** pelo painel ("Canais" ou passo 5 do assistente: tipo uazapi, endereço base e token) ou pela API (`POST …/canais` com `credenciais`). O token nunca é devolvido; o endereço passa pela proteção de saída (https, porta 443, sem IP interno).
- **O que falta:** o passo 7 da seção 1. Cadastrar o webhook na instância é manual. Rotacionar o segredo existe (`…/canais/{id}/rotacionar-segredo`), mas também exige atualizar a instância à mão.
- **Atenção a um ponto do piloto atual:** o número do By Koji também é usado para conversa pessoal. Para outra empresa, o recomendado é um número dedicado ao agente.

---

## 4. Isolamento entre empresas

| Camada | O que impede uma empresa de ler a outra |
|---|---|
| **Chave das Skills** (`X-API-Key`) | Guardada só como hash. Cada chave pertence a **uma empresa**, tem escopos (`ferramentas:catalogo`, `ferramentas:pedidos`), pode ser presa a **um agente** e ter lista de IPs. A empresa da chamada vem **sempre da chave**; não existe campo nem cabeçalho para escolher empresa |
| **`conversation_id`** | Toda Skill procura a conversa **dentro da empresa da chave** e confere que é do agente da chave. Conversa de outra empresa ou de outro agente: 404, sem revelar que existe |
| **Dados** | Toda consulta filtra por `tenant_id` (repositório com `escopo()`; chaves estrangeiras compostas com `tenant_id`). Pedido, carrinho e item só são vistos pela conversa dona. `item_nao_buscado` impede usar id de item que não veio de busca desta conversa |
| **Segredos** | Credenciais de canal e do Skyone cifradas por empresa; nunca devolvidas pela API |
| **Testes** | Há testes de isolamento entre empresas nos blocos de segurança (SEG06, INV08) e em cada bloco novo (catálogo, pedidos, calendário, disponibilidade, edição do catálogo) |
| **API admin** (`/admin/v1`) | Só na rede interna, com a chave do painel. **Ponto de atenção:** essa chave é única e enxerga todas as empresas. Quem limita o usuário do painel à empresa dele é o **painel**, que envia `X-Actor` e `X-Role`. O gateway confere o papel (membro, admin, plataforma), mas **não sabe a qual empresa o usuário pertence** |

**Conclusão:** entre agentes e Skills, o isolamento é feito pelo gateway e está coberto por teste. Na API admin, depende do painel não pedir dados de outra empresa. Para várias empresas com usuários próprios no painel, isso precisa estar garantido no servidor do painel. Não auditei o painel nesse ponto; o gateway poderia passar a exigir a empresa da sessão num cabeçalho e conferir.

---

## 5. Caminho para um módulo "financeiro"

Seguindo o padrão de `catalogo` e `pedidos`:

| Parte | O que fazer | Reaproveita |
|---|---|---|
| Registro | Entrada `financeiro` em `modulos/__init__.py` (migrações, rotas, escopo `ferramentas:financeiro`, função de contexto) | Todo o mecanismo de módulo, das duas chaves (empresa e segmento) |
| Tabelas | Ex.: `fin_clientes` (vínculo do contato com o cliente da empresa, com verificação de identidade), `fin_titulos` (boletos e faturas), `fin_solicitacoes` | Padrão `tenant_id` + chave composta; migração aditiva |
| **De onde vêm os dados** | **Decisão principal:** cópia importada (CSV ou API, como o catálogo) ou consulta ao sistema da empresa em tempo real | Importação por CSV do catálogo; o cliente HTTP com proteção de saída já existe |
| Skills | Ex.: `consultar_titulos`, `segunda_via`, `registrar_promessa_pagamento`, com recusas por motivo | Autenticação por chave, conferência da conversa, registro de todas as chamadas, `ref` sem dados pessoais, padrão de recusa |
| Contexto no pacote | `contexto_modulos.financeiro` (ex.: cliente identificado, títulos em aberto resumidos) | Mesmo mecanismo |
| **Verificação de identidade** | Novo: antes de mostrar dado financeiro, confirmar que quem fala é o titular (CPF e mais um dado, código por SMS ou e-mail) | Nada pronto; o "nome do cliente" de hoje não serve como verificação |
| Guardas | Ex.: não citar valor sem Skill de sucesso no turno (como o anúncio falso de pedido) | O mecanismo de refazer com `aviso_do_gateway` |
| Painel | Tela do módulo, só para empresas com ele ligado | Padrão das telas de Cardápio e Pedidos |
| LGPD | Dado financeiro é sensível: retenção, mascaramento no painel e nos alertas | Retenção por empresa e `ref` sem dado pessoal |

**Tamanho estimado:**
- **Com dados importados:** 2 blocos (FIN-a: identidade + títulos + Skills de consulta; FIN-b: ações e painel).
- **Com consulta em tempo real ao sistema da empresa:** 3 blocos (conector, cache e falhas a mais).

O que pesa não é o gateway, e sim a verificação de identidade e a origem dos dados.

---

## 6. Painel: o que é de restaurante e aparece para qualquer empresa

| Item | Situação |
|---|---|
| Menu "Cardápio" | Aparece para todas as empresas, sem conferir se o módulo está ligado |
| Menu "Pedidos" (com contador de pedidos novos) | Idem |
| Menu "Unidades e horários" | Idem. Útil para outros negócios, mas com campos de entrega (taxa, pedido mínimo, retirada, formas de pagamento) |
| "Avisos de pedido" (Configurações) | Idem |
| Telas de debug de iFood e 99Food | Rotas de restaurante no painel |
| Comportamentos de pedidos (`carrinho_expira_h`) na tela de comportamento | Aparece para todos |
| Motivos de transferência e tipos de solicitação | Listas de restaurante (seção 2.3) |
| Assistente "Nova empresa", Agentes, Canais, Chaves, Conversas, Simular, Comportamento, Histórico | Genéricos |

O filtro do menu é só por papel (`papeis`), não por módulo. **Ajuste no painel:** esconder os itens de módulo quando a empresa não tem o módulo ligado. O gateway já informa isso (`GET …/modulos`).

---

## 7. Lista final para colocar um segundo agente, de outro tipo, em produção

### Pronto

- Empresa, agente, canal uazapi próprio, chaves, perfil e comportamentos versionados, simulação: tudo pelo painel.
- Isolamento entre empresas nas Skills e nos dados.
- Pacote limpo para agente sem módulo de pedidos; guardas de pedido desligadas para ele.
- Fila, limites por empresa e por agente, consumo, alertas de operação, backup.
- Transferência para humano, aviso à equipe (SOL1), reações, botões e listas, calendário com feriados, aviso de espera.
- Piloto por lista de contatos e modo de teste antes do envio real.

### Ajuste pequeno (meio bloco a 1 bloco, junto)

1. **Segmento novo** (`assistente_financeiro`) criado pela API, com perfil padrão, desfechos e comportamentos próprios. É configuração, sem código.
2. **Motivos de transferência e tipos de solicitação por segmento**, em vez das listas fixas de restaurante.
3. **Comportamentos de pedido escondidos** no esquema quando o agente não tem o módulo.
4. **Painel:** menu filtrado por módulo; tela para ligar e desligar módulos (hoje só pela API).
5. **Webhook da uazapi cadastrado pelo gateway** (ou um passo guiado no painel), para não depender do passo manual.
6. **API admin:** o gateway conferir a empresa da sessão do painel, além do papel (seção 4).

### Bloco novo

- **Módulo financeiro** (seção 5): 2 a 3 blocos, conforme a origem dos dados. Necessário só se o agente precisar consultar ou agir sobre dados da empresa. Um assistente que só conversa, explica e transfere não precisa dele.
- **Verificação de identidade do cliente:** dentro do módulo, mas é o ponto mais sensível. Nada existe hoje.
- **Encerramento do atendimento (ENC1) e follow-up (FUP1)**, já planejados: não são pré-requisito, mas valem para qualquer tipo de agente.
