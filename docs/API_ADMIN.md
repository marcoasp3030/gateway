# API administrativa do gateway — contrato com o painel web

> Contrato entre o **painel web do Marco** (cliente) e o **gateway** (servidor). O painel cuida de login, usuários, papéis e telas. O gateway cuida de tenants, canais, conversas, cardápio, pedidos e métricas. O painel **nunca** acessa o banco do gateway.

## 1. Visão geral

```
Navegador ──► Painel web (login, papéis, telas)
                 │  HTTP pela rede interna da VPS (nunca pelo domínio público)
                 │  Authorization: Bearer <PANEL_SERVICE_KEY>
                 │  X-Actor: <usuário do painel>
                 ▼
            Gateway /admin/v1/*  ──► Postgres (bykoji_gateway) + Redis
```

- Base: `http://127.0.0.1:8100/admin/v1` (painel no host) ou `http://<ip-do-host-na-rede-docker>:8100/admin/v1` (painel em container).
- **A API administrativa não é pública (SEG02).** Pelo domínio, `/gateway/admin/...` responde 404, mesmo com a chave correta. O gateway também recusa qualquer chamada a `/admin` que tenha passado pelo Nginx (`X-Forwarded-For`/`X-Real-IP`). Se o painel um dia rodar em outro servidor, use túnel (WireGuard/SSH) ou rede privada, nunca a internet aberta.
- A chamada sai do **servidor** do painel, nunca do navegador. A `PANEL_SERVICE_KEY` não pode chegar ao front-end.
- `X-Actor` é obrigatório em toda escrita e vai para o `audit_log`. Formato livre, ex.: `marco@luxtia` ou `user:42`.
- **Controle de papel fica no painel.** Ex.: usuário de uma empresa cliente só pode chamar rotas do seu `tenant_id`. O gateway confia no painel para isso; por isso a chave de serviço é tratada como credencial de administrador.
- JSON em UTF-8; datas em ISO 8601 UTC; paginação por `?limite=50&cursor=<id>` com resposta `{ "itens": [...], "proximo_cursor": "..." }`.

### Erros

```json
{ "erro": { "codigo": "TENANT_NOT_FOUND", "mensagem": "Tenant 'xyz' não existe", "detalhes": {} } }
```

| HTTP | Códigos |
|---|---|
| 400 | `VALIDATION_ERROR` |
| 401 | `UNAUTHORIZED` |
| 404 | `TENANT_NOT_FOUND`, `NOT_FOUND` |
| 409 | `CONFLICT` (slug já existe, edição concorrente) |
| 422 | `BUSINESS_RULE` (ex.: pedido sem endereço) |
| 502 | `UPSTREAM_ERROR` (Skyone ou uazapi falhou num teste) |

### Segredos

Campos de credencial (`skyone_token`, tokens de canal) são **somente escrita**. Na leitura vêm como `"skyone_token_configurado": true`. Para trocar, envie o novo valor; para manter, omita o campo.

---

## 2. Fase 0.5 — Gestão de tenants, agentes e canais

### Tenants

| Método | Rota | Descrição |
|---|---|---|
| GET | `/tenants` | Lista com status, plano e consumo do mês |
| POST | `/tenants` | Cria tenant (gera config padrão) |
| GET | `/tenants/{id}` | Detalhe |
| PATCH | `/tenants/{id}` | Nome, plano, limite, timezone |
| POST | `/tenants/{id}/suspender` | Para de processar mensagens (webhooks respondem 200 e são descartados) |
| POST | `/tenants/{id}/reativar` | |

Criar:
```json
POST /tenants
{ "id": "bykoji", "nome": "By Koji", "plano": "pro", "limite_turnos_mes": 20000, "timezone": "America/Sao_Paulo" }
```
`id`: `^[a-z0-9_]{3,40}$`, imutável; use o mesmo `tenant_id` do DQE.

### Módulos do tenant

| Método | Rota | Descrição |
|---|---|---|
| GET | `/modulos` | Módulos de capacidade disponíveis na plataforma (`catalogo`, `pedidos`, `agenda`, `consulta_dados`...) com versão |
| GET | `/tenants/{id}/modulos` | Módulos habilitados na empresa |
| PUT | `/tenants/{id}/modulos/{modulo}` | `{ "ativo": true, "config": {...} }` |

Rotas de ferramentas de um módulo desabilitado respondem 404 para aquele tenant.

### Modelos de agente

| Método | Rota | Descrição |
|---|---|---|
| GET | `/modelos-agente` | Globais da plataforma + do tenant (`?tenant=`) |
| POST | `/modelos-agente` | Cria modelo (admin da plataforma). `tenant_id` opcional para modelo exclusivo |
| GET / PATCH | `/modelos-agente/{modelo_id}` | Alteração gera nova `versao`; agentes existentes continuam na versão em uso até serem atualizados |

```json
{
  "id": "atendente_delivery",
  "nome": "Atendente de delivery",
  "tipo": "atendimento",
  "modulos": ["catalogo", "pedidos"],
  "comportamento_padrao": { "debounce_s": 4, "max_wait_s": 12, "pause_minutes": 60, "modo_resposta": "sync" },
  "desfechos": ["pedido_feito", "duvida_resolvida", "reclamacao", "sem_interesse"],
  "followups_padrao": [{ "regra": "carrinho_abandonado", "passos_min": [30, 180], "encerrar": true }],
  "skyone_fluxo_modelo": "nome/URL do fluxo-modelo no Skyone para clonar"
}
```

`tipo`: `atendimento` (clientes externos) ou `interno` (funcionários, exige usuários internos — Fase 6.5).

### Agentes

| Método | Rota | Descrição |
|---|---|---|
| GET | `/tenants/{id}/agentes` | Lista com status, modelo, canais ligados, estado do circuito (`fechado|aberto|meio_aberto`) |
| POST | `/tenants/{id}/agentes` | Cria a partir de um modelo; módulos do modelo precisam estar habilitados no tenant |
| GET | `/tenants/{id}/agentes/{agente}` | Detalhe + **configuração efetiva** (modelo ← tenant ← agente) indicando a origem de cada valor |
| PATCH | `/tenants/{id}/agentes/{agente}` | Ajustes do agente (campos abaixo) |
| POST | `/tenants/{id}/agentes/{agente}/ativar` · `/pausar` | Pausado: canais respondem 200 e descartam (ou mandam para humano, conforme config) |
| POST | `/tenants/{id}/agentes/{agente}/testar` | Envia `{"mensagem": "teste do painel"}` ao fluxo e devolve `{ "ok": true, "latencia_ms": 3210, "resposta": [...] }` ou 502 com o motivo |
| POST | `/tenants/{id}/agentes/{agente}/simular` | `{ "usuario": "marco", "texto": "..." }` — conversa de teste `<tenant>:test:<usuario>` passando pelo pipeline completo |
| POST | `/tenants/{id}/agentes/{agente}/circuito/fechar` | Força o fechamento do circuit breaker depois de corrigir o problema |

Criar:
```json
POST /tenants/bykoji/agentes
{ "modelo_id": "atendente_delivery", "nome": "Atendimento By Koji" }
```

Campos de `PATCH` (todos opcionais; o que não for enviado herda do tenant/modelo):

```json
{
  "agente_mode": "live",
  "skyone_estrategia": "por_agente",
  "skyone_webhook_url": "https://.../webhook/...",
  "skyone_token": "<somente escrita>",
  "instrucoes_agente": "só na estratégia por_modelo",
  "modo_resposta": "sync",
  "callback_timeout_s": 300,
  "send_mode": "live",
  "debounce_s": 4,
  "max_wait_s": 12,
  "media_wait_s": 30,
  "history_turns": 20,
  "pause_minutes": 60,
  "slow_notice_s": 15,
  "aviso_demora": "Só um instante, estou verificando aqui 😊",
  "fallback": "Tive uma instabilidade agora. Já vou te responder, tudo bem? 🙏",
  "alerta_telefone": "5511999999999",
  "modo_piloto": true,
  "contatos_permitidos": ["5511999990001", "5511999990002"],
  "encerrar_vazia_min": 30,
  "max_concorrencia": 5,
  "skyone_dominios_permitidos": ["skyone.cloud"]
}
```

Validações: `debounce_s` 1–15, `max_wait_s` ≥ `debounce_s` e ≤ 30, `media_wait_s` ≤ 60, `callback_timeout_s` ≤ 1800, URL https (SEG03), telefones só dígitos com DDI.

- `modo_piloto`: com `true`, o bot só responde aos números de `contatos_permitidos`; os demais ficam registrados sem resposta automática.
- `modo_resposta`: `sync` (o fluxo responde na mesma chamada) ou `async` (o fluxo chama o callback do gateway ao terminar — para tarefas longas).

### Padrões do tenant

| Método | Rota |
|---|---|
| GET / PUT | `/tenants/{id}/config` |

Mesmos campos de comportamento do agente, usados como padrão para todos os agentes da empresa, mais `max_lotes_simultaneos` (limite do tenant inteiro) e `tenant_teste` (com `true`, fica fora dos canais de alerta).

### Canais

| Método | Rota | Descrição |
|---|---|---|
| GET | `/tenants/{id}/canais` | Lista com status de conexão e último evento recebido |
| POST | `/tenants/{id}/canais` | Cria canal; devolve a **URL de webhook** pronta para colar no provedor |
| PATCH | `/tenants/{id}/canais/{canal_id}` | Nome, credenciais, ativo, **agente ligado** |
| POST | `/tenants/{id}/canais/{canal_id}/rotacionar-segredo` | Gera novo `webhook_secret` e nova URL |
| POST | `/tenants/{id}/canais/{canal_id}/testar-envio` | `{ "telefone": "...", "texto": "..." }` |
| GET | `/tenants/{id}/canais/{canal_id}/status` | Estado da instância no provedor |
| GET | `/tenants/{id}/canais/{canal_id}/qrcode` | QR code para conectar (uazapi) |

Criar canal uazapi:
```json
POST /tenants/bykoji/canais
{ "tipo": "uazapi", "nome": "WhatsApp principal", "agente_id": "ag_01", "publico": "clientes",
  "credenciais": { "base_url": "https://x.uazapi.com", "token": "<instância>" } }
```

`tipo`: `uazapi`, `meta`, `site`, `email`, `teams`, `luxbrain`, `api`. `publico`: `clientes` ou `interno` (só usuários internos cadastrados). Em vez de `agente_id`, um canal pode ter `triagem` (regras para escolher o agente — Fase 6.5).
Resposta:
```json
{ "id": "can_7f3a", "tipo": "uazapi", "ativo": true,
  "webhook_url": "https://<dominio>/gateway/webhooks/uazapi/can_7f3a?s=<segredo>" }
```

> `status` e `qrcode` dependem dos endpoints da instância uazapi. Implementar a partir da documentação/resposta real da instância; se não houver endpoint equivalente, devolver 501.

### Unidades

| Método | Rota |
|---|---|
| GET / POST | `/tenants/{id}/unidades` |
| PATCH / DELETE | `/tenants/{id}/unidades/{unidade_id}` |

```json
{ "nome": "Morumbi", "telefone_whatsapp": "5511...", "ativo": true,
  "horarios": { "seg": [["11:30","15:00"],["18:00","23:00"]], "dom": [] } }
```

### Chaves de API do tenant (Skyone e sistemas do cliente)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/tenants/{id}/api-keys` | Lista nome, escopos, agente, validade, último uso (nunca o valor) |
| POST | `/tenants/{id}/api-keys` | Cria e devolve a chave **uma única vez** |
| PATCH | `/tenants/{id}/api-keys/{key_id}` | Nome, escopos, validade, IPs |
| DELETE | `/tenants/{id}/api-keys/{key_id}` | Revoga |

```json
POST /tenants/bykoji/api-keys
{
  "nome": "skyone-atendimento",
  "escopos": ["ferramentas:catalogo", "ferramentas:pedidos", "callback"],
  "agente_id": "ag_01",
  "expira_em": "2027-10-01T00:00:00Z",
  "ips_permitidos": ["200.10.20.0/24"]
}
```

Escopos disponíveis:

| Escopo | Libera |
|---|---|
| `ferramentas:<modulo>` | Rotas de ferramentas do módulo (`/api/v1/<modulo>/...`) |
| `notificar` | `POST /api/v1/conversas/{conv}/notificar` |
| `conversar` | `POST /api/v1/conversas/mensagens` (canal API direta) |
| `callback` | `POST /api/v1/agentes/callback/{request_id}` (modo assíncrono) |
| `eventos:ler` | Consulta de eventos/entregas |

- O gateway guarda só o hash; o painel mostra o valor com aviso "copie agora".
- `agente_id` opcional restringe a chave às conversas daquele agente.
- Fora do escopo → 403; vencida/revogada → 401; IP fora da lista → 403.
- Use uma chave por consumidor (`skyone-atendimento`, `skyone-analista`, `erp-cliente`) para revogar separadamente.

### Chave do painel (`PANEL_SERVICE_KEY`)

Fica no `.env` do gateway e do servidor do painel. Para trocar sem parada (SEG12): coloque a nova em `PANEL_SERVICE_KEY_NEXT` no gateway, reinicie, troque no painel, confirme no log do gateway que só a nova está em uso, mova a nova para `PANEL_SERVICE_KEY` e apague a `_NEXT`.

### Webhooks de saída (gateway → sistemas do cliente)

| Método | Rota | Descrição |
|---|---|---|
| GET / POST | `/tenants/{id}/webhooks-saida` | `{ "url": "https://...", "eventos": ["pedido.criado", "conversa.transferida", "conversa.encerrada"] }`; o segredo é gerado e mostrado uma vez |
| PATCH / DELETE | `/tenants/{id}/webhooks-saida/{wh_id}` | |
| POST | `/tenants/{id}/webhooks-saida/{wh_id}/testar` | Envia um evento `teste` |
| GET | `/tenants/{id}/webhooks-saida/{wh_id}/entregas` | Últimas entregas com status e tentativas |

Assinatura enviada em cada entrega:

```
X-Gateway-Event: pedido.criado
X-Gateway-Timestamp: 1760000000
X-Gateway-Signature: sha256=<hex HMAC-SHA256(segredo, timestamp + "." + corpo_bruto)>
```

O receptor deve recalcular a assinatura e recusar timestamps com mais de 5 minutos.

### Conversas e transbordo

| Método | Rota | Descrição |
|---|---|---|
| GET | `/tenants/{id}/conversas` | Filtros: `?agente=&canal=&status=ativa|pausada&busca=&desde=` |
| GET | `/tenants/{id}/conversas/{conv}/mensagens` | Histórico paginado |
| POST | `/tenants/{id}/conversas/{conv}/assumir` | Pausa o bot; body `{ "minutos": 60 }` opcional |
| POST | `/tenants/{id}/conversas/{conv}/devolver` | Retoma o bot |
| POST | `/tenants/{id}/conversas/{conv}/mensagens` | Atendente envia mensagem pelo painel (`origem = humano`); pausa o bot automaticamente |
| GET | `/tenants/{id}/eventos?desde=<id>` | Novidades (mensagens, transbordos, pedidos) desde o último id — o painel faz polling a cada 2–3 s |

Item da lista:
```json
{ "conversation_id": "bykoji:can_7f3a:5511999999999", "canal": "can_7f3a", "agente_id": "ag_01", "nome": "Ana",
  "ultima_mensagem": "quero pedir", "ultima_em": "2026-10-07T22:10:00Z",
  "status": "aberta | encerrada", "desfecho": null,
  "bot_pausado_ate": null, "aguardando_humano": false, "ultimo_silencio": null }
```

Filtros adicionais em `/conversas`: `?desfecho=reclamacao`, `?silenciadas=true`, `?transferidas=true`.

> Polling é suficiente para começar. SSE (`GET /eventos/stream`) pode vir depois sem mudar o formato dos eventos.

### Métricas, consumo e auditoria

| Método | Rota | Descrição |
|---|---|---|
| GET | `/tenants/{id}/metricas?de=&ate=&agente=&canal=&unidade=` | Ver formato abaixo |
| GET | `/tenants/{id}/metricas/conversas?indicador=transferidas&de=&ate=` | Lista das conversas que compõem um número |
| GET | `/tenants/{id}/metricas/export.csv?bloco=desfechos&de=&ate=` | CSV de um bloco |
| GET | `/tenants/{id}/consumo?mes=2026-10` | turnos no mês × limite do plano |
| GET | `/metricas/plataforma` | visão de todos os tenants (só para o papel admin no painel) |
| GET | `/tenants/{id}/auditoria` | quem fez o quê |
| POST | `/alertas/testar` | Envia alerta de teste para os canais configurados |

Formato de `/metricas` (cada bloco traz `atual` e `anterior` — período anterior de mesmo tamanho):

```json
{
  "periodo": {"de": "2026-10-01", "ate": "2026-10-07"},
  "conversas": {"atual": 812, "anterior": 760},
  "resolvidas_pelo_bot": {"atual": 655, "anterior": 590},
  "transferidas": {"atual": 98, "anterior": 120, "causas": {"reclamacao": 40, "fora_do_escopo": 31, "pedido_cliente": 27}},
  "silencios": {"atual": 140, "por_motivo": {"agradecimento": 120, "reacao": 20}},
  "desfechos": {"pedido_feito": 410, "duvida_resolvida": 230, "reclamacao": 22, "sem_interesse": 60, "vazia": 9},
  "primeira_resposta_ms": {"p50": 6200, "p95": 11800},
  "agente_ms": {"p50": 3100, "p95": 7400},
  "erros_por_etapa": {"agente": 3, "envio": 1, "transcricao": 2},
  "consumo": {"turnos": 4120, "limite": 20000}
}
```

---

### Dados de contato (LGPD)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/tenants/{id}/contatos?busca=` | Localiza contato por telefone, e-mail ou nome |
| GET | `/tenants/{id}/contatos/{contato}/exportar` | Exporta em JSON tudo que o gateway tem do contato (perfil, conversas, mensagens, pedidos) |
| DELETE | `/tenants/{id}/contatos/{contato}` | Apaga/anonimiza os dados pessoais do contato; mantém métricas agregadas. Exige `{ "confirmacao": "<telefone ou e-mail do contato>" }` no corpo |

Ambas registram no `audit_log` com o `X-Actor`.

### Validação de URLs (SEG03)

`skyone_webhook_url`, URLs de webhooks de saída e `base_url` de canal são recusadas com `400 VALIDATION_ERROR` quando: não são `https`, usam porta diferente de 443, têm usuário/senha, apontam para `localhost`/`.internal`/`.local` ou para IP privado/reservado. A resolução de DNS é verificada de novo a cada chamada.

---

### Usuários internos (Fase 6.5)

| Método | Rota | Descrição |
|---|---|---|
| GET / POST | `/tenants/{id}/usuarios-internos` | Funcionários que conversam com agentes internos |
| PATCH / DELETE | `/tenants/{id}/usuarios-internos/{usuario}` | |

```json
{
  "nome": "Carla Souza", "email": "carla@empresa.com.br", "papel": "gerente",
  "identificadores": { "teams": "29:1a2b...", "telefone": "5511999990003", "luxbrain": "u_88" },
  "escopo": { "filiais": ["Morumbi"], "areas": ["comercial"] },
  "ativo": true
}
```

O `escopo` é aplicado pelo gateway nas rotas de ferramentas (INV16) e enviado ao agente; o agente não consegue ampliá-lo.

## 3. Fase 4 — Cardápio e pedidos

| Método | Rota | Descrição |
|---|---|---|
| GET | `/tenants/{id}/cardapio` | Filtros `unidade`, `categoria`, `ativo`, `busca` |
| POST | `/tenants/{id}/cardapio` | Cria item |
| PATCH | `/tenants/{id}/cardapio/{item_id}` | Preço, descrição, ativo |
| POST | `/tenants/{id}/cardapio/{item_id}/em-falta` | Desativa até o fim do dia (`?ate=` opcional) |
| POST | `/tenants/{id}/cardapio/importar` | CSV `unidade,categoria,nome,descricao,preco,ativo`; `?modo=substituir|mesclar`; devolve prévia de erros sem gravar se `?simular=true` |
| GET | `/tenants/{id}/cardapio/exportar` | CSV |
| GET | `/tenants/{id}/pedidos` | Filtros `status`, `unidade`, `de`, `ate` |
| GET | `/tenants/{id}/pedidos/{numero}` | Detalhe com itens e conversa |
| POST | `/tenants/{id}/pedidos/{numero}/confirmar` | Unidade aceita; cliente é avisado |
| POST | `/tenants/{id}/pedidos/{numero}/recusar` | `{ "motivo": "..." }`; cliente é avisado |

---

## 4. Telas sugeridas no painel

| Tela | Papel | Rotas |
|---|---|---|
| Empresas | admin plataforma | `/tenants`, `/metricas/plataforma` |
| Onboarding da empresa (assistente em passos) | admin plataforma | criar tenant → habilitar módulos → criar agente a partir de modelo → URL/token do Skyone → testar agente → criar canal → QR code → testar envio → chave de API do Skyone |
| Modelos de agente | admin plataforma | `/modelos-agente` |
| Agentes | admin / gestor | `/agentes`, `/testar`, `/simular`, `/circuito/fechar` |
| Canais | admin / gestor da empresa | `/canais`, `/status`, `/qrcode` |
| Chaves de API e webhooks de saída | admin / gestor | `/api-keys`, `/webhooks-saida` |
| Usuários internos | gestor | `/usuarios-internos` |
| Conversas ao vivo | atendente | `/conversas`, `/mensagens`, `/assumir`, `/devolver`, `/eventos` |
| Cardápio | gestor | `/cardapio*` |
| Pedidos | atendente da unidade | `/pedidos*` |
| Métricas | gestor | `/metricas`, `/consumo` |
| Configurações | gestor | `/config`, `/unidades` |

Papéis sugeridos (controlados no painel): `admin_plataforma`, `gestor_empresa`, `atendente` (opcionalmente restrito a uma unidade).

---

## 5. Notas de implementação no gateway

- Toda rota `/tenants/{id}/...` começa validando que o tenant existe; toda query usa `WHERE tenant_id = $1`.
- Comparação de chaves e segredos em tempo constante (`hmac.compare_digest`).
- Escrita registra `audit_log` com `X-Actor`, sem gravar valores de segredo.
- Limite de requisições por chave para as rotas `/admin/v1` e por tenant para webhooks.
- OpenAPI gerado pelo FastAPI em `/admin/v1/docs`, protegido pela mesma chave (ou desligado em produção).

---

## 6. API de integração (`/api/v1`) — referência rápida

Não é usada pelo painel. É chamada pelas **Skills do Skyone** e pelos **sistemas do cliente**, com `X-API-Key: <chave do tenant>` (seção "Chaves de API"). A chave define o tenant.

### Notificar pela voz do agente

```
POST /api/v1/conversas/{conversation_id}/notificar
{
  "evento": "pedido_saiu",
  "dados": { "pedido": "K-1042", "previsao": "20 min" },
  "instrucao": "Avise o cliente de forma breve e simpática."
}
```

- O gateway monta o pacote com `tarefa: "notificar"` e o `evento`, chama o agente e envia a resposta no canal da conversa.
- Conversa pausada por humano: não envia; registra e alerta o atendente.
- Fora da janela de 24h da Meta: exige template; sem template, responde `422 BUSINESS_RULE`.
- Resposta: `{ "ok": true, "turno_id": 123, "enviado": true }`.

As rotas de cardápio, carrinho e pedidos (Fase 4) estão em `docs/SKILLS.md`.
