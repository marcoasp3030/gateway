# PLANO_CURSOR.md — Gateway de Agentes (VPS + Skyone Studio)

> **Instrução para o agente do Cursor:** este arquivo é o roteiro oficial do projeto. Leia inteiro antes de começar. Trabalhe **uma fase por vez**, na ordem. Ao fim de cada tarefa, rode a validação indicada. **Não avance de fase** se alguma validação falhar ou se houver um checkpoint "🛑 PARE" pendente. Registre tudo no `PROGRESS.md`.

---

## 1. Contexto

**Plataforma SaaS de agentes de IA**, multiempresa, multiagente e multicanal. **Toda conversa passa pelo gateway**: ele recebe os canais, organiza a conversa, chama o agente no Skyone Studio, entrega a resposta e mede tudo. Os agentes podem ser de atendimento ao cliente (delivery, clínica, advocacia, varejo) ou internos de empresas (assistente, analista, especialista, ex.: especialista em projetos).

**Primeiro cliente e primeiro modelo de agente:** By Koji (delivery e catering), unidades Morumbi, Einstein, Vila Nova Conceição e Pátio Guedala, sem integração com EPOC e Everest nesta etapa.

Divisão de responsabilidades:

| Camada | Responsável por |
|---|---|
| **Gateway (este repositório, VPS)** | Canais (entrada e saída), deduplicação, debounce, fila durável, workers, histórico e memória, identidade de clientes e usuários internos, transbordo humano, módulos de capacidade (catálogo, pedidos, agenda...), chaves e permissões, métricas, alertas, LGPD |
| **Skyone Studio** | Inteligência do agente: prompt, base de conhecimento, LLM e Skills. Fluxo: Webhook → JavaScript → IF → AI Agent Call → Log → Retorno (ou chamada de callback) |
| **Painel web do Marco** (outro projeto) | Login, usuários do painel, telas de gestão. Fala com o gateway só pela API `/admin/v1`, pela rede interna |

Canais previstos: WhatsApp (uazapi para testes, Meta oficial depois), site, e-mail, Microsoft Teams, chat do Luxbrain e API direta. iFood e 99Food são condicionais.

```
Canais ──► RECEPÇÃO (/webhooks/*) ──► grava mensagem ──► fila durável
 (WhatsApp, site, e-mail,                                     │
  Teams, Luxbrain, API)                                       ▼
                                   WORKERS: debounce, roteamento para o agente,
                                   montagem do contexto (módulos), chamada ao Skyone
                                                              │
                              ADAPTADOR DE AGENTE (Skyone hoje) ──► fluxo do agente
                                                              │  resposta (síncrona ou callback)
                                                              ▼
                               envio pelo canal + Postgres + turnos/métricas
```

### 1.1 Modelo de domínio

| Conceito | O que é | Exemplo |
|---|---|---|
| **Tenant** | A empresa cliente | `bykoji`, `shimada` |
| **Módulo de capacidade** | Código reutilizável que dá uma habilidade ao agente: tabelas próprias, rotas de ferramentas para as Skills e montagem de contexto | `catalogo`, `pedidos`, `agenda`, `documentos`, `consulta_dados` (antigo DQE), `calculos`, `tarefas` |
| **Modelo de agente** | Configuração reutilizável, sem código: tipo (atendimento / interno), módulos que usa, comportamento padrão, desfechos possíveis, regras de follow-up, referência ao fluxo-modelo no Skyone | `atendente_delivery`, `recepcionista_clinica`, `analista_comercial`, `especialista_projetos` |
| **Agente** | Um modelo aplicado a um tenant, com fluxo e token do Skyone próprios e ajustes de comportamento | "Atendimento By Koji" |
| **Canal** | Uma entrada/saída concreta, ligada a um agente (ou a uma triagem) | WhatsApp 11 9xxxx, widget do site, time no Teams |
| **Contato** | Cliente externo, unificado entre canais | Ana (WhatsApp + e-mail) |
| **Usuário interno** | Funcionário do tenant que conversa com agentes internos, com papel e escopo de dados | Gerente da filial Morumbi |

**Convenções:**
- Configuração de comportamento (debounce, pausa, mensagens padrão, modo piloto, etc.) fica no **agente**, herdando os padrões do tenant e do modelo. Quando uma fase disser "configuração do tenant", leia "configuração efetiva do agente".
- Segmento novo = **modelo novo** combinando módulos existentes. Módulo novo só quando surgir uma capacidade realmente nova.
- O código do núcleo nunca contém regra de um segmento específico (ex.: nada de "cardápio" fora do módulo `catalogo`).

---

## 2. Regras obrigatórias (valem para todas as fases)

1. **Nunca rode testes contra o banco de produção.** Testes automatizados usam um banco cujo nome termina em `_test`. O `conftest.py` deve abortar se `DATABASE_URL` não terminar em `_test`.
2. **Não altere outros serviços da VPS.** Em especial a API DQE (`/dqe`) e as `location` existentes do Nginx. No Nginx, apenas **adicione** o bloco `/gateway/`. Antes de recarregar, rode `nginx -t`.
3. **Nunca exponha portas publicamente.** Gateway em `127.0.0.1:8100` e Postgres em `127.0.0.1:5433`. Acesso externo só pelo Nginx.
4. **Nunca coloque segredos no código, em logs ou no git.** Tudo em `.env`, que deve estar no `.gitignore`.
5. **Recepção e processamento separados (a partir da Fase 0.6).** Até a Fase 0.6, uvicorn com 1 worker (o agendador roda no processo). A partir dela, a recepção só valida, grava e enfileira; workers separados processam. Nenhuma chamada ao Skyone ou ao canal acontece dentro da requisição do webhook.
6. **Preço nunca vem da LLM.** Todo preço e total vêm do Postgres.
7. **Mudança de schema:** a partir da Fase 0.5, use migrações versionadas em `app/migrations/NNN_descricao.sql`, aplicadas em ordem e registradas numa tabela `schema_migrations`. Não edite migração já aplicada.
8. **Comandos destrutivos** (`DROP`, `DELETE` sem `WHERE`, `docker compose down -v`, apagar volumes) exigem confirmação explícita do Marco.
9. **Não invente formatos de API externa.** Quando um payload de terceiro for incerto (uazapi, Meta, iFood), capture um exemplo real em `webhook_raw` e ajuste o código a partir dele.
10. **Multiempresa (a partir da Fase 0.5):** toda tabela de negócio tem `tenant_id`; toda consulta filtra por ele; toda rota nova recebe um teste de isolamento entre dois tenants. Configuração de cliente vai para o banco, nunca para o `.env`.
11. **O painel web é cliente da API.** Não crie acesso do painel ao banco do gateway. Toda funcionalidade de gestão entra primeiro em `docs/API_ADMIN.md` e depois no código.
12. **Núcleo genérico (seção 1.1).** Regras de segmento vivem em módulos (`app/modulos/<nome>/`) e modelos de agente; o núcleo só conhece interfaces. O Skyone é acessado apenas pelo adaptador de agente (`app/agentes/`).
13. **Invariantes do atendimento (seção 2.1) são lei.** Todo invariante tem pelo menos um teste automatizado com o mesmo código (ex.: `test_INV03_...`). Uma mudança que quebra um invariante não entra.
14. Ao terminar cada fase, atualize o `PROGRESS.md` e faça commit com a mensagem `fase N: <resumo>`.

### 2.1 Invariantes do atendimento

Regras que valem sempre, em qualquer canal e tenant. A coluna "Fase" indica quando o teste passa a ser obrigatório.

| Código | Invariante | Fase |
|---|---|---|
| INV01 | **Nenhuma mensagem do cliente se perde**: nem em reinício do processo, nem quando duas chegam ao mesmo tempo, nem quando chega durante o processamento de outro lote. Toda mensagem gravada termina em um turno (respondido, silenciado com motivo ou em erro registrado). | 3 |
| INV02 | **Webhook repetido não gera efeito duplicado** (mensagem, resposta, transcrição, pedido). Idempotência por `message_id`. | 1 (msg), 5 (áudio) |
| INV03 | **Bot não responde por cima de humano.** Se um atendente respondeu (inclusive antes do bot, no início da conversa, ou com o eco chegando fora de ordem), o bot fica pausado. Mensagens enviadas pelo próprio bot nunca pausam. | 3 |
| INV04 | **Silêncio sempre tem motivo.** O bot pode não responder (ex.: "obrigado", "👍"), mas o turno grava `motivo_silencio`. Silêncio sem motivo declarado é tratado como falha: o agente é consultado mais uma vez e, persistindo, a conversa vai para humano. | 3 |
| INV05 | **Cliente nunca fica sem resposta por erro.** Falha ou timeout do agente gera mensagem de fallback e registro de erro. | 3 |
| INV06 | **Um lote só fecha com as mídias resolvidas.** Áudio em transcrição e imagem que chega logo depois do texto entram no mesmo lote (com teto de espera). | 5 |
| INV07 | **Justiça entre tenants.** O pico de uma empresa não atrasa as outras: o despacho de lotes é limitado por tenant. | 3 |
| INV08 | **Isolamento entre tenants.** Nenhuma rota lê ou altera dado de outro tenant. | 0.5 |
| INV09 | **Modo piloto respeitado.** Com o modo piloto ativo, o bot só responde aos contatos da lista; os demais ficam registrados e sem resposta automática. | 3 |
| INV10 | **Tempo é explícito.** Toda mensagem enviada ao agente leva a data/hora em que foi enviada e o tempo desde a mensagem anterior. | 2 |
| INV11 | **Anexo ilegível é declarado.** Se o gateway não conseguir ler um anexo, o agente recebe "não foi possível ler <arquivo>: <motivo>" em vez de nada. | 1 |
| INV12 | **Preço nunca vem da LLM.** | 4 |
| INV13 | **Segredo nunca aparece** em resposta da API, log ou payload ao Skyone (exceto o token próprio do Skyone). | 0.5 |
| INV14 | **Fila durável.** Mensagem aceita pela recepção sobrevive a reinício do gateway, dos workers **e do Redis**; só sai da fila depois de terminar em turno. | 0.6 |
| INV15 | **Falha isolada por agente.** Um fluxo do Skyone lento ou fora do ar não atrasa outros agentes nem outros tenants (circuit breaker por agente + limite de concorrência por agente). | 0.6 |
| INV16 | **Escopo do usuário interno.** Um agente interno só acessa dados dentro do escopo do usuário que está falando (ex.: só a filial dele). O escopo é aplicado pelo gateway nas rotas de ferramentas, nunca só pelo prompt. | 6.5 |
| INV17 | **Agente certo, conversa certa.** Toda conversa tem exatamente um agente responsável por vez; mensagens de um canal nunca chegam a um agente de outro tenant; troca de agente (triagem) fica registrada. | 0.5 |

### 2.2 Segurança e LGPD

Regras testáveis, no mesmo espírito dos invariantes. Código do teste: `test_SEGxx_...` / `test_LGPDxx_...`. O código-base já implementa SEG01–SEG05, SEG07–SEG09 e LGPD01 em `app/seguranca.py`, `app/main.py` e `docker-compose.yml`; os testes unitários estão em `tests/test_seguranca.py`. **Nenhuma fase pode remover ou enfraquecer essas proteções.**

| Código | Regra | Como é verificada | Fase |
|---|---|---|---|
| SEG01 | **Falhar fechado.** O gateway não sobe com segredo vazio, com menos de 16 caracteres ou com valor de exemplo (`troque...`). Webhook sem segredo configurado recusa tudo. Chave de API, segredo de canal ou tenant inexistente → recusa, nunca "deixa passar". | Subir com `ADMIN_KEY=troque-x` deve encerrar com erro; webhook com segredo vazio → 401 | 0 ✅ |
| SEG02 | **Rotas administrativas só pela rede interna.** `/admin`, `/test`, `/metricas`, `/docs`, `/openapi.json` respondem 404 para qualquer chamada que passou pelo Nginx (presença de `X-Forwarded-For`/`X-Real-IP`) ou veio de IP público, **mesmo com a chave certa**. O Nginx público expõe só `/gateway/webhooks/`, `/gateway/api/` e `/gateway/health` (ver `docs/nginx-gateway.conf`). O painel chama `http://127.0.0.1:8100` direto. | Teste com cabeçalho `X-Forwarded-For` → 404; `curl https://<dominio>/gateway/admin/v1/tenants` → 404 | 0 ✅ / 1 (Nginx) / 0.5 (`/admin/v1`) |
| SEG03 | **Proteção contra SSRF.** Toda URL que o gateway chama e que vem de configuração (webhook do Skyone, webhooks de saída, base do provedor de canal) é validada: só `https`, porta 443, sem usuário/senha, sem `localhost`/`.internal`/`.local`, e o DNS precisa resolver **só para IPs públicos** — validado ao salvar (`validar_formato_url`) **e imediatamente antes de cada chamada** (`validar_url_saida`, contra DNS rebinding). Chamadas de saída não seguem redirecionamentos. Opcional por tenant: lista de domínios permitidos para o Skyone. | Testes com `127.0.0.1`, `10.x`, `172.17.x`, `169.254.169.254`, `[::1]`, `localhost`, DNS falso apontando para IP privado → recusado | 0.5 |
| SEG04 | **Segredos comparados em tempo constante** (`hmac.compare_digest` via `segredo_confere`), com falha fechada para valor esperado vazio. Chaves de API armazenadas só como hash (SHA-256 com pepper do `.env`). | Teste unitário | 0 ✅ / 0.5 (hash) |
| SEG05 | **Nenhum segredo em log.** Query string sensível (`s`, `token`, `key`, `secret`, `api_key`) é mascarada em todos os logs da aplicação e do uvicorn; o Nginx registra webhooks sem query string. Tokens nunca vão para `webhook_raw` nem para o payload do Skyone (exceto o token do próprio Skyone). | Grep do segredo no log após uma chamada real = 0 | 0 ✅ |
| SEG06 | **Defesa contra prompt injection no servidor, não no prompt.** Toda Skill opera só sobre a conversa do `conversation_id` recebido, que precisa pertencer ao tenant da chave. Ações sensíveis (cancelar, alterar endereço depois de fechar, reembolso) são validadas pelo gateway (status, janela de tempo, dono). O pacote enviado ao agente nunca contém dados de outro contato. | Testes: Skill com conversa de outro contato/tenant → 404; cancelar pedido fora da janela → 422 mesmo que o agente peça | 0.5 / 4 |
| SEG07 | **Limites de abuso.** Corpo máximo (`MAX_BODY_BYTES` no app + `client_max_body_size` no Nginx); limite de requisições por IP no Nginx; por tenant na API; e por contato no pipeline (ex.: mais de 20 mensagens/min do mesmo contato → para de chamar o agente, registra e alerta). | Corpo de 3 MB → 413; rajada do mesmo contato não gera chamadas ao agente além do limite | 0 ✅ / 3 (por contato) |
| SEG08 | **Documentação da API desligada em produção** (`DOCS_ENABLED=false`); `/health` público responde só `{"ok": true}`. | `/docs` → 404; health via Nginx sem detalhes | 0 ✅ |
| SEG09 | **Infraestrutura mínima.** Container sem root, sistema de arquivos somente leitura, `no-new-privileges`; Redis com senha; `.env` com permissão 600; firewall da VPS só 22/80/443; SSH só por chave. | `docker compose exec gateway id` ≠ root; `stat -c %a .env` = 600; `ufw status` | 0 ✅ (container) / 9 (VPS) |
| SEG11 | **Chaves de API com escopo, validade e origem.** Cada chave tem escopos (ex.: `ferramentas:catalogo`, `ferramentas:pedidos`, `notificar`, `eventos:ler`, `callback`), validade opcional e lista opcional de IPs. Chave usada fora do escopo → 403; vencida ou revogada → 401. Último uso registrado. | Testes por escopo; chave `skyone` chamando `/notificar` sem o escopo → 403 | 0.5 |
| SEG12 | **Rotação sem parada da chave do painel.** O gateway aceita `PANEL_SERVICE_KEY` e `PANEL_SERVICE_KEY_NEXT` ao mesmo tempo durante a troca; o log indica qual foi usada para saber quando remover a antiga. | Teste com as duas chaves válidas | 0.5 |
| SEG10 | **Webhooks de entrada autenticados pelo meio mais forte que o provedor oferecer**: assinatura HMAC da Meta (`X-Hub-Signature-256`), segredo por canal no uazapi; todos com deduplicação. | Teste com assinatura inválida → 401 | 0.5 / 7 |
| LGPD01 | **Retenção curta do payload bruto:** `webhook_raw` apagado após `WEBHOOK_RAW_DIAS` (padrão 30). | Linha antiga é removida pela limpeza | 0 ✅ |
| LGPD02 | **Retenção por tenant** de mensagens e conversas (prazo definido pelo cliente): após o prazo, anonimizar (telefone/nome/conteúdo) mantendo métricas agregadas. | Teste com relógio avançado | 9 |
| LGPD03 | **Direitos do titular:** exportar (JSON) e apagar/anonimizar todos os dados de um contato via API administrativa, com registro em `audit_log`. | Teste: após apagar, nenhuma tabela retorna o telefone do contato | 0.5 |
| LGPD04 | **Logs sem conteúdo de mensagem** em nível INFO; telefones mascarados nos logs (`5511****7777`). | Grep de conteúdo/telefone completo no log = 0 | 3 |
| LGPD05 | **Backup criptografado e fora da VPS**, com teste de restauração documentado. | Restauração em banco `_test` | 9 |

### Variáveis úteis para validação

```bash
cd ~/luxbrain-gateway   # ajuste para o caminho real
set -a; source .env; set +a
GW=http://127.0.0.1:8100
```

---

## 3. Formato do PROGRESS.md

Crie o arquivo na Fase 0 e acrescente uma entrada por fase:

```markdown
## Fase N — <nome> — <data>
- Status: concluída | bloqueada | em andamento
- Feito:
- Validações (comando → resultado):
- Métricas (se houver): p50 / p95 / erros
- Pendências / decisões para o Marco:
```

---

## FASE 0 — Implantação em modo simulado

**Objetivo:** gateway rodando na VPS com agente `mock` e envio `dry`.

### Tarefas
- [ ] Conferir pré-requisitos: `docker --version`, `docker compose version`, portas livres (`ss -ltnp | grep -E '8100|5433'` deve vir vazio).
- [ ] Criar `.gitignore` com `.env`, `__pycache__/` e `*.pyc`. Inicializar git, se ainda não houver.
- [ ] `cp .env.example .env`, `chmod 600 .env` e gerar valores fortes para **todas** as senhas e chaves (`openssl rand -hex 24`), inclusive `REDIS_PASSWORD`. Manter `SKYONE_MODE=mock` e `SEND_MODE=dry`. O gateway se recusa a subir com valor de exemplo (SEG01).
- [ ] `docker compose up -d --build`
- [ ] Rodar os testes de segurança num venv na VPS: `python3 -m venv .venv && .venv/bin/pip install -r app/requirements.txt pytest && .venv/bin/python -m pytest -q tests/test_seguranca.py`.
- [ ] Criar `PROGRESS.md`.

### Validação
```bash
curl -s $GW/health
# esperado: {"ok":true,"agente":"mock","envio":"dry"}

docker compose ps            # 3 serviços "running/healthy"
pip install httpx && python scripts/bench.py --url $GW --admin-key $ADMIN_KEY --clientes 5
# esperado: 5/5 respondidos; msgs_por_lote > 1; erros = 0

# segurança
docker compose exec gateway id                                    # uid=10001, não root (SEG09)
curl -s -o /dev/null -w '%{http_code}\n' $GW/docs                  # 404 (SEG08)
curl -s -o /dev/null -w '%{http_code}\n' $GW/metricas -H "X-Admin-Key: $ADMIN_KEY" -H "X-Forwarded-For: 1.2.3.4"   # 404 (SEG02)
curl -s -o /dev/null -w '%{http_code}\n' -X POST $GW/webhooks/uazapi -d '{}'   # 401 (SEG01)
docker compose logs gateway | grep -c "$UAZAPI_WEBHOOK_SECRET"     # 0 (SEG05)
```

### Pronto quando
- Health OK, bench com 5/5 respondidos e 0 erros.
- Resultado do bench registrado no `PROGRESS.md` (é o **overhead da VPS**).
- Testes `tests/test_seguranca.py` verdes e as verificações de segurança acima conferidas.

---

## FASE 0.3 — Descobertas no Skyone (validação rápida)

**Objetivo:** responder três perguntas que definem a modelagem dos agentes antes da Fase 0.5. Trabalho principal do Marco no Skyone; o agente do Cursor prepara o payload de teste e registra as respostas.

### Tarefas
- [ ] Preparar em `scripts/skyone_probe.py` um envio de teste para uma URL de fluxo, com dois payloads iguais exceto pelo campo `instrucoes_agente` (ex.: "responda só em inglês" × "responda só em português") e medição de tempo.
- [ ] 🛑 **PARE:** o Marco testa no Skyone e informa:
  1. **Agente parametrizável?** Um mesmo fluxo/AI Agent Call respeita instruções e configuração enviadas no payload (`instrucoes_agente`, base de conhecimento, lista de Skills)? → define **"1 fluxo por agente"** ou **"1 fluxo por modelo de agente"**.
  2. **Existe API de gestão** no Skyone para criar/clonar fluxos e agentes, ou é só pela interface? → define se o provisionamento pode ser automatizado.
  3. **Limite de tempo do webhook síncrono** (o que acontece com um fluxo que demora 60 s, 120 s, 300 s?) e se um bloco REST no fim do fluxo consegue chamar uma URL externa (base do callback assíncrono).
- [ ] Registrar as respostas no `PROGRESS.md` e em `docs/SKYONE.md` (decisões e limites conhecidos).

### Pronto quando
- As três respostas estão registradas e a estratégia escolhida (`por_agente` ou `por_modelo`, `sync`/`async`) está anotada em `docs/SKYONE.md`.

---

## FASE 0.5 — Multiempresa, agentes e chaves

**Objetivo:** o gateway passa a atender várias empresas, cada uma com vários agentes e canais. Tudo que hoje está no `.env` e é específico de cliente vai para o banco. O painel web do Marco gerencia tudo **via API administrativa** (`docs/API_ADMIN.md`).

> Fazer agora, com o banco praticamente vazio. Depois exigiria migração de dados.

### Decisões fixas
- **O painel nunca acessa o banco do gateway diretamente.** Só pela API `/admin/v1/*`, pela rede interna.
- **Bancos separados no mesmo Postgres da VPS:** `bykoji_gateway` (gateway) e o banco do painel, cada um com seu usuário. Se o painel já tem um Postgres rodando, reutilizar o servidor e criar só o banco e o usuário novos.
- **O painel autentica os usuários dele.** O gateway confia no painel por chave de serviço (`PANEL_SERVICE_KEY`, com rotação via `PANEL_SERVICE_KEY_NEXT`, SEG12) e recebe, a cada chamada, quem agiu (`X-Actor`) para auditoria.
- **`tenant_id` = slug** (ex.: `bykoji`), igual ao `tenant_id` usado no DQE.
- **Credenciais de terceiros criptografadas** (Fernet, `ENCRYPTION_KEY`). Nunca devolvidas pela API (só `"configurado": true`).
- **Estratégia Skyone** conforme a Fase 0.3: `por_agente` (cada agente com sua URL/token) ou `por_modelo` (agentes do mesmo modelo compartilham o fluxo e recebem `instrucoes_agente` no payload). O schema suporta as duas.

### Tarefas — banco
- [ ] Criar o mecanismo de migrações (`app/migrations/NNN_*.sql` + `schema_migrations`). A migração `001_base.sql` é o `schema.sql` atual.
- [ ] Migração `002_plataforma.sql`:
  - `tenants` (id slug PK, nome, status `ativo|suspenso`, plano, limite_turnos_mes, timezone, `retencao_dias`, `tenant_teste` bool, criado_em)
  - `tenant_config` (padrões do tenant: os mesmos campos de comportamento de `agentes`, todos opcionais)
  - `modulos_tenant` (tenant_id, modulo, ativo, config jsonb) — quais módulos de capacidade a empresa contratou
  - `modelos_agente` (id slug, nome, tipo `atendimento|interno`, modulos text[], comportamento_padrao jsonb, desfechos text[], followups_padrao jsonb, skyone_fluxo_modelo text, versao) — globais da plataforma; tenant pode ter modelos próprios (`tenant_id` nulo = global)
  - `agentes` (id, tenant_id, modelo_id, nome, status `rascunho|ativo|pausado`, `skyone_estrategia` `por_agente|por_modelo`, skyone_webhook_url, skyone_token_enc, `instrucoes_agente` text, `modo_resposta` `sync|async`, e o **comportamento**: debounce_s, max_wait_s, media_wait_s, pause_minutes, history_turns, slow_notice_s, aviso_demora, fallback, alerta_telefone, send_mode `dry|live`, agente_mode `mock|live`, modo_piloto bool, contatos_permitidos text[], encerrar_vazia_min, max_lotes_simultaneos, max_concorrencia, skyone_dominios_permitidos text[])
  - `tenant_canais` (id, tenant_id, tipo `uazapi|meta|site|email|teams|luxbrain|api`, nome, `agente_id` (ou `triagem` com regras jsonb), webhook_secret, credenciais_enc jsonb, `publico` `clientes|interno`, ativo, status_conexao, ultimo_evento_em)
  - `tenant_unidades` (id, tenant_id, nome, telefone_whatsapp, horarios jsonb, ativo)
  - `audit_log` (id, tenant_id, actor, acao, alvo, dados jsonb, criado_em)
  - `tenant_api_keys` (id, tenant_id, nome, hash, **escopos** text[], **agente_id** opcional, **expira_em** opcional, **ips_permitidos** cidr[] opcional, ultimo_uso_em, criado_em, revogada_em)
  - `tenant_webhooks_saida` (id, tenant_id, url, segredo_enc, eventos text[], ativo)
  - `contatos` (id, tenant_id, telefone/e-mail, nome, perfil jsonb, resumo text, atualizado_em)
  - Adicionar `tenant_id NOT NULL` + FK em `conversas`, `mensagens`, `turnos`, `webhook_raw`; `agente_id` e `canal_id` em `conversas` e `turnos`; índices compostos começando por `tenant_id`.
  - `cardapio_itens` sai do núcleo e passa a pertencer ao módulo `catalogo` (Fase 4 completa o módulo).
  - `conversation_id` passa a ser `<tenant>:<canal_id>:<identificador>` (ex.: `bykoji:can_7f3a:5511999999999`) — o mesmo tenant pode ter vários números de WhatsApp.
- [ ] Seed: modelos globais iniciais — `atendente_delivery` (módulos `catalogo`, `pedidos`), `assistente_generico` (sem módulos) — e o tenant `bykoji` com o agente "Atendimento By Koji". Migrar ou limpar os dados de teste (com confirmação do Marco).

### Tarefas — código
- [ ] Estrutura de pastas: `app/nucleo/` (pipeline, fila, conversas), `app/canais/<tipo>.py` (adaptadores), `app/agentes/` (adaptador de agente; `skyone.py` e `mock.py`), `app/modulos/<nome>/` (rotas, contexto, migrações do módulo), `app/admin/` (API `/admin/v1`).
- [ ] **Interface de módulo:** cada módulo declara nome, migrações, rotas de ferramentas (`/api/v1/<modulo>/...`), escopos que essas rotas exigem e uma função `contexto(conversa) -> dict` que entra no pacote ao agente. Rotas de módulo não habilitado no tenant → 404.
- [ ] **Configuração efetiva** = padrão do modelo ← padrão do tenant ← agente (o mais específico vence), com cache em memória (TTL 30 s) e invalidação ao salvar pela API.
- [ ] **Roteamento:** canal → agente. Canal com `triagem` fica previsto no schema; implementação simples por regra (palavra-chave/menu) entra na Fase 6.5.
- [ ] **Webhooks por canal:** `POST /webhooks/<tipo>/{canal_id}?s=<webhook_secret>`. Canal inativo, agente pausado ou tenant suspenso → 200 sem processar (não forçar reenvio do provedor).
- [ ] O pipeline lê toda configuração do **agente efetivo**, não do `.env`. O `.env` guarda só infraestrutura e chaves globais.
- [ ] Payload ao Skyone ganha `tenant_id`, `agente_id`, `modelo_id` e, na estratégia `por_modelo`, `instrucoes_agente` (Apêndice A).
- [ ] **Chaves de API (SEG04 + SEG11):** só o hash (`sha256(pepper + chave)`, `API_KEY_PEPPER`); valor exibido uma vez; escopos, agente opcional, validade e IPs; `ultimo_uso_em` atualizado. Dependência única `contexto_da_chave(escopo)` usada por **todas** as rotas `/api/*`, que devolve `(tenant_id, agente_id?, conversa?)` (SEG06).
- [ ] **SEG12:** aceitar `PANEL_SERVICE_KEY` e `PANEL_SERVICE_KEY_NEXT`; registrar no log qual foi usada (sem o valor).
- [ ] **Teto mensal por tenant (em turnos):** contador mensal no Redis, persistido em `turnos`, com detalhamento por agente. Avisos em 80% e 100%; ao estourar, registrar e alertar, **sem cortar o atendimento** sem decisão do Marco. Gravar `uso` do Skyone se vier no Retorno.
- [ ] **Horários no pacote:** horários de `tenant_unidades` entram no payload (aberta agora, próxima abertura), no fuso do tenant.
- [ ] **Notificar pela voz do agente:** `POST /api/v1/conversas/{conv}/notificar` (escopo `notificar`) com `{ "evento": "...", "dados": {...}, "instrucao": "..." }`. Cria turno com origem `sistema`, chama o agente da conversa e envia a resposta. Respeita pausa humana.
- [ ] **Webhooks de saída assinados:** eventos (`conversa.transferida`, `conversa.encerrada`, eventos de módulos) enviados com `X-Gateway-Timestamp` e `X-Gateway-Signature: sha256=<HMAC(segredo, timestamp + "." + corpo)>`; 3 tentativas com backoff; registro de entrega.
- [ ] **API administrativa** conforme `docs/API_ADMIN.md`: tenants, módulos do tenant, modelos de agente, agentes (incluindo `testar` e `simular` por agente), canais, unidades, chaves com escopo, webhooks de saída, conversas, pausa/retomada, contatos (LGPD), métricas (por tenant e por agente), auditoria.
- [ ] Remover as rotas antigas `/admin/*`, `/test/*` e `/metricas` globais. Equivalentes: `POST /admin/v1/tenants/{id}/agentes/{agente}/simular` (conversa `<tenant>:test:<usuario>`), `GET /admin/v1/tenants/{id}/conversas/{conv}/mensagens`, `GET /admin/v1/tenants/{id}/metricas?agente=`.
- [ ] Atualizar `scripts/bench.py`: `--tenant`, `--agente`, `--panel-key` no lugar de `--admin-key`; opção de rodar vários tenants/agentes ao mesmo tempo.
- [ ] Variáveis de validação a partir desta fase: `AUTH=(-H "Authorization: Bearer $PANEL_SERVICE_KEY" -H "X-Actor: cursor")`, `T=bykoji`, `A=<id do agente>`.
- [ ] **SEG03 (SSRF):** `validar_formato_url` ao salvar URLs (Skyone, webhooks de saída, `base_url` de canal); `validar_url_saida` antes de cada chamada.
- [ ] **SEG02:** toda rota nova de gestão sob `/admin`.
- [ ] **LGPD03:** exportar e apagar/anonimizar contato, com `audit_log`.

### Testes obrigatórios
- [ ] **INV08/INV17 — isolamento:** tenant A não lê nem altera nada do tenant B por nenhuma rota; mensagem de canal do tenant A nunca chega a agente do tenant B; dois agentes do mesmo tenant com canais diferentes recebem só as suas conversas.
- [ ] **SEG11 — escopo:** chave `skyone` com escopo `ferramentas:catalogo` chamando `/notificar` → 403; chave vencida → 401; IP fora da lista → 403; chave de agente X usada em conversa do agente Y → 404.
- [ ] Rota de módulo não habilitado no tenant → 404.
- [ ] Configuração efetiva: valor do agente sobrepõe tenant, que sobrepõe modelo.
- [ ] Webhook com segredo errado → 401; canal de tenant suspenso ou agente pausado → ignorado.
- [ ] Tokens nunca aparecem em resposta da API nem em log (INV13).
- [ ] Assinatura HMAC do webhook de saída validada por um receptor de teste.
- [ ] SEG03: URL com IP privado, `http://` ou porta não padrão → 400; DNS para IP privado → recusado na chamada.
- [ ] SEG02: rota `/admin/v1` com `X-Forwarded-For` → 404 mesmo com chave válida.
- [ ] SEG12: `PANEL_SERVICE_KEY` e `PANEL_SERVICE_KEY_NEXT` aceitas; uma terceira → 401.
- [ ] LGPD03: após apagar um contato, nenhuma tabela retorna o telefone dele.

### Validação
```bash
docker compose exec gateway pytest -q
# criar 2 tenants; no tenant A, 2 agentes com canais diferentes; rodar o bench nos 3 e conferir métricas separadas
curl -s $GW/admin/v1/tenants "${AUTH[@]}"
curl -s "$GW/admin/v1/tenants/$T/metricas?agente=$A" "${AUTH[@]}"
```

### Pronto quando
- Dois tenants e três agentes rodando ao mesmo tempo, com configuração, canais, chaves, métricas e dados isolados.
- Criar um agente novo a partir de um modelo é só configuração (nenhuma linha de código).
- 🛑 **PARE:** revisar com o Marco a `docs/API_ADMIN.md` antes de ele começar as telas do painel.

---

## FASE 0.6 — Robustez: recepção, fila durável e workers

**Objetivo:** como toda conversa passa pelo gateway, ele não pode perder mensagem nem deixar um cliente ou agente lento afetar os outros.

### Tarefas
- [ ] **Redis durável:** conferir o AOF já ligado no `docker-compose.yml` (`--appendonly yes --appendfsync everysec`, volume `redisdata`). Mesmo assim, o Postgres é a fonte da verdade: a mensagem é gravada antes de entrar na fila.
- [ ] **Separar processos** (mesma imagem, comandos diferentes no compose):
  - `gateway-api`: recepção de webhooks + `/api` + `/admin`. Valida, grava a mensagem, enfileira e responde. **Nunca chama o Skyone nem o canal dentro da requisição.**
  - `gateway-worker` (N réplicas): debounce, montagem do contexto, chamada ao agente, envio, turnos.
  - `gateway-agendador` (1 réplica, com trava de liderança no Redis): lotes vencidos, follow-ups, limpezas.
- [ ] **Fila durável:** Redis Streams com grupo de consumidores (`XREADGROUP` + `XACK`); item só é confirmado após o turno gravado; itens pendentes de worker morto são retomados (`XAUTOCLAIM`). Reconciliação na subida: mensagens no Postgres sem turno e fora da fila voltam para a fila (INV01/INV14).
- [ ] **Trava por conversa** mantida (um lote por conversa por vez) e **justiça** por tenant e por agente (INV07): limites `max_lotes_simultaneos` (tenant) e `max_concorrencia` (agente).
- [ ] **Adaptador de agente** (`app/agentes/`): interface `enviar(pacote) -> Resposta | Pendente`. Implementações `mock` e `skyone`. O núcleo não importa nada do Skyone diretamente.
- [ ] **Circuit breaker por agente (INV15):** após N falhas/timeouts em uma janela, o agente entra em "aberto" por alguns minutos: novas mensagens recebem o fallback (ou vão para humano, conforme configuração), sem chamar o Skyone; depois um teste "meio aberto" decide se volta. Estado visível na API administrativa e alertado.
- [ ] **Retry com backoff** só para falhas transitórias (timeout de conexão, 502/503/504), com limite; nunca reenviar se o Skyone já pode ter respondido ao cliente.
- [ ] **Modo assíncrono (callback):** para agentes com `modo_resposta=async`, o worker envia o pacote com `callback_url` e `callback_token` de uso único e libera; o Skyone chama `POST /api/v1/agentes/callback/{request_id}` (escopo `callback`) com o mesmo formato do Retorno. Callback repetido é ignorado (idempotência por `request_id`); callback que não chega até `callback_timeout_s` gera fallback/transbordo. Mensagens intermediárias (`"parcial": true`) são permitidas.
- [ ] **Saúde dos componentes:** `/health` interno mostra fila (tamanho, pendentes, idade do mais antigo), workers vivos e agentes com circuito aberto.
- [ ] Atualizar a Regra 5 no `PROGRESS.md` como concluída e ajustar `bench.py` para medir com 1 e 3 workers.

### Testes obrigatórios
- [ ] **INV14:** derrubar o Redis com mensagens enfileiradas e subir de novo → todas respondidas; matar um worker no meio de um lote → outro worker retoma.
- [ ] **INV15:** agente A com Skyone mockado em timeout; agente B normal → B mantém a latência; A entra em circuito aberto e envia fallback.
- [ ] **INV07:** 50 conversas no tenant A e 1 no tenant B → B despachada sem esperar A.
- [ ] Callback: resposta via callback chega ao cliente; callback duplicado ignorado; callback com token errado → 401; sem callback no prazo → fallback.
- [ ] O webhook de entrada responde em < 200 ms mesmo com o agente mock configurado para 30 s.

### Pronto quando
- Testes verdes; bench com 3 workers e dois tenants sem perda e sem interferência.
- Reiniciar qualquer container (inclusive Redis) não perde mensagem.

---

## FASE 1 — Exposição via Nginx e uazapi real

**Objetivo:** receber mensagens reais do WhatsApp de teste, ainda sem responder.

### Tarefas
- [ ] Adicionar ao Nginx o conteúdo de `docs/nginx-gateway.conf` (sem alterar outros blocos): `limit_req_zone` e `log_format` no contexto `http {}`, e os `location` no `server {}` do domínio HTTPS. Só webhooks, API de integração e health ficam públicos (SEG02); todo o resto de `/gateway/` responde 404. Rodar `nginx -t` e só então `systemctl reload nginx`.
- [ ] Validar de fora da VPS: `curl -s -o /dev/null -w '%{http_code}' https://<dominio>/gateway/metricas` → 404; `https://<dominio>/gateway/health` → `{"ok":true}`; o log `/var/log/nginx/gateway.log` não contém `?s=`.
- [ ] Criar (ou conferir do seed) o tenant `bykoji`, o agente "Atendimento By Koji" e o canal uazapi de teste ligado a ele, pela API administrativa; anotar a `webhook_url` devolvida.
- [ ] 🛑 **PARE:** pedir ao Marco para configurar essa URL como webhook na instância uazapi de teste (evento de mensagens) e enviar: texto, várias mensagens picadas, áudio, imagem, imagem logo depois de um texto, **pino de localização**, documento PDF, reação com emoji, e **uma resposta manual pelo celular antes de o bot responder** (para capturar o caso de borda do INV03).
- [ ] Ler os payloads reais:
  ```bash
  docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
    -c "SELECT id, jsonb_pretty(payload) FROM webhook_raw ORDER BY id DESC LIMIT 5;"
  ```
- [ ] Ajustar `app/uazapi.py → normalizar()` ao formato real: telefone, texto, message_id, fromMe, enviada-pela-API, tipo de mídia **e data/hora de envio informada pelo provedor** (guardar em `mensagens.enviada_em`; usar o horário de recebimento só se o provedor não informar).
- [ ] **Localização:** converter o pino em texto para o agente: `[localização: lat, long — <nome/endereço se vier no payload>]`, guardando as coordenadas em `mensagens.meta`.
- [ ] **Anexo ilegível (INV11):** tipo desconhecido, arquivo grande demais ou download falho viram `[não foi possível ler <nome/tipo>: <motivo>]`, para o agente pedir outro formato.
- [ ] **Reação com emoji:** registrar como mensagem `tipo=reacao`; não abre turno sozinha (o agente não responde a 👍), mas entra no histórico.
- [ ] Confirmar na documentação da instância os endpoints de envio de texto e de presença ("digitando") e ajustar `enviar_texto()` e `digitando()`.
- [ ] Criar testes unitários de `normalizar()` usando os payloads reais **anonimizados** em `tests/fixtures/`.

### Validação
```bash
curl -s https://<dominio>/gateway/health
# na tabela mensagens: as mensagens de teste aparecem com origem 'cliente'
# a resposta manual pelo celular aparece com origem 'humano' e a conversa fica com bot_pausado_ate preenchido
# reenviar o mesmo payload → resposta {"duplicada": true}
```

### Pronto quando
- Texto, mídia, localização, reação e mensagem do atendente são classificados corretamente, com fixtures reais anonimizadas.
- Mensagens enviadas pelo próprio bot **não** pausam a conversa.

---

## FASE 2 — Agente Skyone ligado e linha de base de latência

**Objetivo:** medir o tempo real do agente, ainda sem enviar ao WhatsApp.

### Tarefas
- [ ] 🛑 **PARE:** o Marco cria no Skyone o **fluxo-modelo** `atendente_delivery`: `Webhook → JavaScript (valida token, registra request_id no Log) → IF → AI Agent Call → Log → Retorno`, e informa a URL do webhook. O Retorno segue o **contrato do Apêndice A** (inclui `acao: "silencio"` com `motivo` e `desfecho`). Se a Fase 0.3 indicou estratégia `por_modelo`, o JavaScript repassa `instrucoes_agente` ao AI Agent Call.
- [ ] Configurar o agente pela API (`PATCH /admin/v1/tenants/$T/agentes/$A`): `agente_mode=live`, `skyone_webhook_url`, `skyone_token`; manter `send_mode=dry`. Usar `POST .../agentes/$A/testar` para conferir.
- [ ] Documentar em `docs/SKYONE.md` o passo a passo para **criar um agente novo no Skyone a partir de um fluxo-modelo** (o que clonar, o que trocar, como testar), para o provisionamento ser repetível.
- [ ] **Tempo explícito (INV10):** cada item de `historico` e a mensagem atual levam `enviada_em` (ISO, fuso do tenant); o pacote leva `agora` e `tempo_desde_ultima` em texto ("há 3 dias", "há 2 minutos"). Se a última conversa foi há mais de 12 h, marcar `"retomada": true`.
- [ ] Interpretar o Retorno conforme o Apêndice A: `nenhuma`, `silencio`, `transferir_humano`, `encerrar` (com `desfecho`). Formato antigo (`resposta` como texto) continua aceito.
- [ ] Conversa manual de teste:
  ```bash
  curl -s -X POST $GW/admin/v1/tenants/$T/agentes/$A/simular "${AUTH[@]}" \
    -H "Content-Type: application/json" -d '{"usuario":"marco","texto":"oi, vocês entregam no morumbi?"}'
  sleep 15; curl -s $GW/admin/v1/tenants/$T/conversas/$T:test:marco/mensagens "${AUTH[@]}"
  ```
- [ ] Se o formato do Retorno não for reconhecido, ajustar `app/agente.py → _extrair()` com base no corpo real (logar o corpo bruto apenas em nível DEBUG).
- [ ] Rodar o bench com 1, 5 e 10 clientes.

### Validação
```bash
python scripts/bench.py --url $GW --panel-key $PANEL_SERVICE_KEY --tenant $T --clientes 5
curl -s "$GW/admin/v1/tenants/$T/metricas" "${AUTH[@]}"
```

### Pronto quando
- 0 erros com 5 clientes.
- `agente_p50`, `agente_p95` e `total_p95` registrados no `PROGRESS.md`.
- 🛑 **PARE se `total_p95` > 15 s:** reportar ao Marco antes de seguir (pode exigir prompt menor, modelo mais rápido ou resposta assíncrona).

---

## FASE 3 — Conversa humanizada no WhatsApp

**Objetivo:** atendimento real pelo celular, robusto a falhas.

### Tarefas
- [ ] **Infra de testes:** `docker-compose.test.yml` ou banco `bykoji_test` separado; `tests/conftest.py` que aborta se o banco não terminar em `_test`; `pytest` + `pytest-asyncio`.
- [ ] Testes do pipeline com agente mock:
  - [ ] 3 mensagens em menos de `DEBOUNCE_S` geram 1 chamada ao agente.
  - [ ] Teto `MAX_WAIT_S` respeitado mesmo com mensagens contínuas.
  - [ ] Mensagem que chega durante o processamento entra no lote seguinte, nunca em paralelo.
  - [ ] Conversa pausada não chama o agente.
  - [ ] Falha ou timeout do agente envia `FALLBACK` e grava `status='erro'`.
  - [ ] Agente lento (> `SLOW_NOTICE_S`) envia `AVISO_DEMORA` uma única vez.
- [ ] Testes dos invariantes desta fase (nome do teste começa com o código):
  - [ ] **INV01:** reinício com lote no buffer; duas mensagens no mesmo milissegundo; mensagem durante processamento — todas terminam em turno.
  - [ ] **INV03:** atendente responde antes do primeiro turno do bot; eco do atendente chega depois da mensagem seguinte do cliente; mensagem do bot via API não pausa.
  - [ ] **INV04:** agente devolve `silencio` com motivo → nada é enviado e `turnos.motivo_silencio` é gravado; agente devolve resposta vazia sem motivo → uma nova consulta ao agente e, persistindo, transferência para humano.
  - [ ] **INV05:** timeout e erro HTTP do agente → fallback enviado.
  - [ ] **INV07:** tenant A com 50 conversas simultâneas e tenant B com 1 → a de B é despachada sem esperar a fila de A.
  - [ ] **INV09:** modo piloto ativo → contato fora da lista é registrado e não recebe resposta automática.
- [ ] **SEG07 por contato:** mais de `max_msgs_por_minuto_contato` (padrão 20) do mesmo contato → para de chamar o agente por alguns minutos, registra e alerta.
- [ ] **LGPD04:** logs em INFO sem conteúdo de mensagem; telefones mascarados (`5511****7777`).
- [ ] **Silêncio declarado (INV04):** migração adiciona `turnos.motivo_silencio` e `turnos.acao`. "Obrigado", "ok", "👍" após um encerramento não geram resposta; ficam no histórico.
- [ ] **Justiça entre tenants (INV07):** o agendador despacha no máximo `max_lotes_simultaneos` lotes por tenant; lotes acima do limite esperam a vez sem bloquear outros tenants.
- [ ] **Modo piloto (INV09):** `modo_piloto` + `contatos_permitidos` por agente (com padrão no tenant), editáveis pela API administrativa.
- [ ] **Encerramento:** quando o agente devolve `encerrar`, gravar `conversas.status='encerrada'`, `desfecho` (ex.: `pedido_feito`, `duvida_resolvida`, `reclamacao`, `sem_interesse`) e o horário. Uma nova mensagem do cliente reabre a conversa.
- [ ] **Conversa vazia:** job a cada 5 min encerra, com `desfecho='vazia'`, conversas cuja única entrada não tem texto nem anexo legível após `encerrar_vazia_min`.
- [ ] **Retomada do bot:** quando a pausa expirar, o bot volta sozinho. Adicionar também um comando do atendente (ex.: mensagem `#bot` enviada pelo celular) que retoma imediatamente e não é encaminhado ao agente.
- [ ] **Alerta de transbordo:** quando `acao = transferir_humano`, enviar aviso para um número interno (`alerta_telefone` da configuração do tenant) com nome, telefone e motivo.
- [ ] **Recuperação após reinício:** na subida do app, reagendar conversas com `buf:*` pendente no Redis.
- [ ] `SEND_MODE=live` somente para a instância de teste.
- [ ] 🛑 **PARE:** o Marco testa pelo celular com o roteiro de 30 a 50 conversas e ajusta prompt e base de conhecimento no Skyone.

### Validação
```bash
docker compose exec gateway pytest -q          # tudo verde
curl -s "$GW/admin/v1/tenants/$T/metricas" "${AUTH[@]}"
```

### Pronto quando
- Testes verdes.
- Mensagens picadas recebem 1 resposta coerente no celular.
- Atendente assume e o bot silencia; `#bot` retoma.
- Reiniciar o container com mensagens no buffer não perde a resposta.
- "Obrigado" depois de um pedido concluído não gera resposta, e o motivo aparece em `turnos`.
- Bench com dois tenants mostra o tenant pequeno sem atraso enquanto o grande está em pico.

---

## FASE 4 — Módulos `catalogo` e `pedidos` (modelo `atendente_delivery`)

**Objetivo:** cliente monta e fecha pedido pelo WhatsApp; a unidade confirma. Tudo construído como **módulos reutilizáveis** (`app/modulos/catalogo/`, `app/modulos/pedidos/`): o mesmo catálogo serve varejo, e o mesmo fluxo de pedido serve qualquer negócio que venda por conversa.

> Sem EPOC, o pedido é um **pré-pedido**: só vale após a confirmação da unidade.

### Tarefas
- [ ] Criar as migrações **dentro dos módulos** (o mecanismo já existe desde a Fase 0.5). Nomes genéricos: o módulo fala em "itens do catálogo", não em "cardápio"; o termo "cardápio" fica só no prompt/modelo de agente:
  - `carrinhos` (conversation_id, unidade, modalidade `delivery|retirada`, endereço, horário, observação, status)
  - `carrinho_itens` (carrinho_id, item_id, quantidade, preço unitário **copiado do cardápio**, observação)
  - `pedidos` (número curto legível, conversation_id, unidade, itens jsonb, total, status `aguardando_loja | confirmado | recusado | cancelado`, timestamps)
- [ ] 🛑 **PARE:** obter do Marco o cardápio real (CSV), números de WhatsApp de cada unidade, formas de pagamento e regras de entrega.
- [ ] Endpoints para Skills (header `X-API-Key`), todos exigindo `conversation_id` válido e ativo:

  | Método e rota | Função |
  |---|---|
  | `GET /api/cardapio/busca` | já existe |
  | `GET /api/carrinho/{conv}` | itens, subtotal, dados faltantes |
  | `POST /api/carrinho/{conv}/itens` | adiciona item por `item_id` e devolve o carrinho atualizado |
  | `PATCH /api/carrinho/{conv}/itens/{id}` | altera quantidade ou observação |
  | `DELETE /api/carrinho/{conv}/itens/{id}` | remove item |
  | `PUT /api/carrinho/{conv}/entrega` | unidade, modalidade, endereço, horário |
  | `POST /api/pedidos` | fecha o carrinho; recusa se faltar dado obrigatório |
  | `GET /api/pedidos/{numero}` | status do pedido |

- [ ] Toda resposta de escrita devolve o **carrinho completo atualizado**, para o agente não precisar de outra chamada.
- [ ] Ao fechar o pedido: enviar resumo para o WhatsApp da unidade. A unidade confirma pelo **painel web** (`POST /admin/v1/tenants/{id}/pedidos/{numero}/confirmar|recusar`) ou respondendo `OK <numero>` / `NÃO <numero> <motivo>` no WhatsApp; o status é atualizado e o cliente é avisado.
- [ ] Expor cardápio (CRUD + importação CSV) e pedidos na API administrativa (`docs/API_ADMIN.md`, seção Fase 4).
- [ ] O módulo `pedidos` preenche `contexto_modulos.pedidos.carrinho` no payload ao Skyone.
- [ ] Ao fechar e ao confirmar o pedido, disparar os webhooks de saída `pedido.criado` / `pedido.confirmado` e usar `/notificar` para avisar o cliente.
- [ ] Rotas sob `/api/v1/catalogo/...` e `/api/v1/pedidos/...`, exigindo os escopos `ferramentas:catalogo` e `ferramentas:pedidos` (SEG11). Atualizar o modelo `atendente_delivery` com a função de contexto do carrinho.
- [ ] Documentar em `docs/SKILLS.md` a especificação de cada Skill para o Marco criar no Skyone: nome, descrição para o agente, parâmetros e exemplo de retorno.
- [ ] **SEG06:** cancelamento e alterações após o fechamento validados no gateway (status do pedido, janela de tempo, dono da conversa), independentemente do que o agente pedir.
- [ ] Testes: preço sempre do banco; item inativo recusado; `conversation_id` inexistente → 404; pedido sem endereço em delivery → 422; total = soma dos itens.

### Validação
```bash
docker compose exec gateway pytest -q
# fluxo manual via curl: adicionar 2 itens → alterar → fechar → unidade confirma → cliente recebe aviso
```

### Pronto quando
- Pedido completo pelo celular chega à unidade com o total correto.
- Nenhum preço da resposta do agente diverge do banco no roteiro de testes.

---

## FASE 5 — Áudio e imagem

### Tarefas
- [ ] Baixar mídia pelo uazapi (confirmar o endpoint com um payload real).
- [ ] Áudio → transcrição (Whisper via API; chave em `.env`) → texto entra no mesmo debounce, com prefixo indicando que veio de áudio.
- [ ] **Transcrição idempotente (INV02):** resultado guardado por `message_id`; webhook repetido ou reprocessamento não transcreve de novo. Trava no Redis evita duas transcrições simultâneas do mesmo áudio.
- [ ] **Lote espera mídia (INV06):** enquanto houver mídia `pendente` no lote (áudio transcrevendo, imagem baixando), o lote não fecha; teto `media_wait_s` (padrão 30 s). Estourado o teto, a mídia entra como ilegível (INV11) e o lote segue.
- [ ] **Imagem logo depois do texto:** imagem que chega dentro da janela do debounce entra no mesmo lote, mesmo que chegue depois do texto.
- [ ] Imagem → enviar ao Skyone como URL temporária ou base64 em campo `midia` do payload. Combinar com o Marco o formato que o fluxo aceita (Processamento OCR).
- [ ] Limites: tamanho máximo, timeout de transcrição e fallback ("não consegui ouvir seu áudio, pode escrever?").
- [ ] Registrar a duração da transcrição em `turnos` (nova coluna via migração).

### Pronto quando
- Pedido feito por áudio funciona igual ao feito por texto.
- Falha na transcrição gera resposta amigável, não silêncio.
- Testes INV02 (áudio) e INV06 verdes: texto + áudio em sequência viram uma única resposta coerente.

---

## FASE 5.5 — Memória de longo prazo e follow-ups

**Objetivo:** o agente lembra do cliente entre atendimentos sem carregar todo o histórico, e o sistema retoma conversas paradas.

### Tarefas
- [ ] **Resumo de atendimento encerrado:** ao encerrar (ou após 12 h sem mensagens), pedir ao agente um resumo curto (rota/flag `"tarefa": "resumir"` no payload, combinada com o Marco no fluxo Skyone) e gravar em `contatos.resumo` + fatos estáveis em `contatos.perfil` (nome, unidade preferida, endereço usado, preferências). Pedidos e consultas vêm do banco, não do resumo.
- [ ] O payload passa a levar `cliente.resumo_anterior` e o histórico **só da conversa atual** (limitado a `history_turns`). Medir a redução do tamanho do pacote e da latência.
- [ ] **Follow-ups em sequência:** tabela `followups` (tenant, conversa, regra, passo, agendado_para, status `agendado|enviado|cancelado|concluido`). Regras por tenant: ex. carrinho abandonado (30 min → 3 h → encerrar), aguardando resposta do cliente (2 h → 24 h → encerrar).
- [ ] **Cancelar ao responder:** qualquer mensagem do cliente cancela os follow-ups pendentes daquela conversa. Pausa humana também suspende.
- [ ] **Último passo encerra** a conversa com o desfecho da regra, sem chamar o agente.
- [ ] **Janela de 24h (Meta):** fora da janela, follow-up só com template aprovado; sem template configurado, o passo é pulado e registrado.
- [ ] Testes: varredura não duplica passo já enviado; passo em execução não é sobrescrito; follow-up continua após o agente retomar conversa antiga (bugs conhecidos de produtos similares).

### Pronto quando
- Cliente que volta dias depois é reconhecido pelo resumo, e o agente não age como se fosse a mesma conversa.
- Carrinho abandonado recebe lembrete; resposta do cliente cancela os próximos passos.

---

## FASE 6 — Site e e-mail

### Tarefas
- [ ] `POST /webhooks/site` com sessão por visitante (`site:<visit_id>`), CORS restrito ao domínio do cliente e limite de requisições por IP.
- [ ] Resposta para o site: polling `GET /site/mensagens/{visit_id}?desde=<id>` (mais simples que WebSocket nesta fase).
- [ ] Widget JS mínimo de exemplo em `widget/`.
- [ ] E-mail: worker IMAP (processo separado no compose), a cada 2–5 min, conversa `email:<endereco>`, sem debounce, resposta via SMTP com o assunto `Re:` original; ignorar no-reply, auto-respostas e listas.
- [ ] Vincular contato entre canais quando o cliente informar telefone (tabela `contato_vinculos`).

### Pronto quando
- Mesmo agente responde nos três canais com o mesmo comportamento.

---

## FASE 6.5 — Agentes internos e canais corporativos

**Objetivo:** suportar agentes usados por funcionários (assistente, analista, especialista em projetos), com identidade, permissões e tarefas longas.

### Tarefas
- [ ] **Usuários internos:** tabela `usuarios_internos` (tenant_id, nome, e-mail, identificadores por canal — id do Teams, telefone, id do Luxbrain —, papel, **escopo** jsonb, ativo). Canal com `publico=interno` só aceita usuários cadastrados; desconhecido recebe mensagem padrão e é registrado.
- [ ] **Escopo aplicado no servidor (INV16):** o escopo do usuário (ex.: `{"filiais": ["Morumbi"], "areas": ["comercial"]}`) entra no pacote ao agente **e** é aplicado pelo gateway em toda rota de ferramenta (`contexto_da_chave` recebe também o usuário da conversa). O agente nunca consegue ampliar o escopo pedindo.
- [ ] **Módulo `consulta_dados`** (evolução do DQE): consultas analíticas com SQL validado (só leitura, lista de tabelas permitidas, limite de linhas e de tempo), filtradas pelo escopo do usuário. Skills específicas antes do SQL livre (mesma filosofia do DQE).
- [ ] **Tarefas longas:** agentes internos usam `modo_resposta=async` (Fase 0.6) com aviso imediato ("estou preparando o relatório") e entrega posterior.
- [ ] **Arquivos na resposta:** o contrato aceita `arquivos: [{"nome", "url" | "base64", "tipo"}]` (PDF, planilha, gráfico); o adaptador de cada canal envia no formato suportado ou manda link temporário assinado.
- [ ] **Canal Microsoft Teams:** adaptador via Bot Framework (app Azure já existe para o Skyone; confirmar com o Marco se o mesmo registro será usado ou um novo). Validar o token JWT de entrada do Bot Framework (SEG10).
- [ ] **Canal chat do Luxbrain:** adaptador para o chat existente (hoje ele chama o webhook do agente de forma síncrona com timeout de 120 s e aceita `file_url`/`file_base64`); passa a chamar o gateway, que repassa ao agente.
- [ ] **Canal API direta:** `POST /api/v1/conversas/mensagens` (escopo `conversar`) para sistemas do cliente embutirem o agente; resposta por webhook de saída ou consulta.
- [ ] **Triagem simples:** canal com `triagem` encaminha para um agente por regra (menu ou palavra-chave) e registra a troca (INV17).
- [ ] 🛑 **PARE:** o Marco escolhe o primeiro agente interno piloto (ex.: analista comercial) e define os escopos dos usuários de teste.

### Testes obrigatórios
- [ ] INV16: usuário com escopo `Morumbi` pedindo dados de outra filial → ferramenta devolve só Morumbi (ou recusa), mesmo que o agente peça outra filial.
- [ ] Usuário não cadastrado em canal interno → não chega ao agente.
- [ ] Token inválido do Bot Framework → 401.
- [ ] Tarefa longa entregue por callback com arquivo anexado.

### Pronto quando
- Um agente interno responde no Teams e no chat do Luxbrain, respeitando o escopo de cada usuário, e entrega um relatório em PDF por callback.

---

## FASE 7 — WhatsApp oficial (Meta)

### Tarefas
- [ ] 🛑 **PARE:** o Marco providencia app na Meta, número de produção, tokens e templates aprovados.
- [ ] `GET /webhooks/meta` (verificação `hub.challenge`) e `POST /webhooks/meta` com validação da assinatura `X-Hub-Signature-256` sobre o corpo bruto, em tempo constante (SEG10); assinatura inválida → 401.
- [ ] Responder 200 imediatamente e processar depois; deduplicar por id da mensagem.
- [ ] Adaptador de envio Meta; controle da janela de 24h (fora dela, só template).
- [ ] Download de mídia com token.
- [ ] O canal (uazapi ou Meta) passa a ser escolhido por configuração, sem mudar o pipeline nem o Skyone.

### Pronto quando
- O mesmo roteiro de testes da Fase 3 passa pelo número oficial.

---

## FASE 8 — iFood e 99Food (condicional)

> Só iniciar após o Marco confirmar o credenciamento e o tipo de acesso (eventos por webhook ou polling; existência ou não de API de mensagens com o cliente).

### Tarefas
- [ ] Capturar eventos reais em `webhook_raw` antes de modelar.
- [ ] Registrar pedidos externos em `pedidos` com `origem = ifood | 99food`.
- [ ] Avisar a unidade e, se o cliente tiver autorizado, enviar status pelo WhatsApp.

---

## FASE 9 — Piloto e produção

### Tarefas
- [ ] **LGPD05:** backup diário do Postgres (`pg_dump` + rotação 7/30 dias), **criptografado** (ex.: `age` ou `gpg`) e copiado para fora da VPS; **teste de restauração** em banco `_test` documentado.
- [ ] **LGPD02:** retenção por tenant (`tenants.retencao_dias`): job diário que anonimiza mensagens e conversas antigas, mantendo métricas agregadas.
- [ ] **SEG09 na VPS:** `ufw` só com 22/80/443; SSH só por chave (`PasswordAuthentication no`); fail2ban; atualizações de segurança automáticas; `pip-audit` no CI ou mensal.
- [ ] Revisão final de segurança: rodar a lista SEG01–SEG10 e LGPD01–LGPD05 e registrar o resultado no `PROGRESS.md`.
- [ ] Logs estruturados com `request_id`, sem conteúdo de mensagens em nível INFO.
- [ ] **Alertas pelo desfecho, não por tentativa** (WhatsApp interno, e-mail ou webhook):
  - falha que se recupera numa nova tentativa **não** alerta;
  - a mesma causa (credencial recusada, Skyone fora, canal desconectado, teto estourado) alerta **uma vez a cada 3 h**, com a contagem de repetições;
  - falhas intermitentes alertam por **taxa** (ex.: > 5% em 15 min), uma vez por janela;
  - todo alerta diz o que aconteceu, o tenant e o link para a conversa/log no painel;
  - `POST /admin/v1/alertas/testar` envia um alerta de teste;
  - tenants marcados como teste podem ficar fora dos canais de alerta.
- [ ] **API de métricas para o painel** (`docs/API_ADMIN.md`), por período, tenant, canal e unidade, com comparação ao período anterior:
  - conversas, turnos, resolvidas pelo bot, transferidas (e **causas** da transferência), **silêncios** (por motivo), **desfechos**, tempo da primeira resposta (p50/p95), latência do agente, erros por etapa, consumo do teto;
  - cada número abre a lista de conversas que o compõem;
  - **exportação CSV** de cada bloco.
- [ ] Conferir que `audit_log` cobre toda escrita feita pelo painel e por chaves de API.
- [ ] 🛑 **PARE:** piloto em uma unidade, horário limitado, com atendente acompanhando. Revisão diária das conversas com o Marco.

### Critérios de go-live
- Transbordo abaixo de ~15% das conversas.
- Zero pedidos com preço divergente durante o piloto.
- Restauração de backup testada.
- Lista SEG01–SEG10 e LGPD01–LGPD05 conferida, sem pendência.

---

## Apêndice A — Contrato VPS → Skyone

Ida (gateway → Skyone):

```json
{
  "token": "<token do Skyone do tenant>",
  "request_id": "uuid",
  "tenant_id": "bykoji",
  "agente_id": "ag_01",
  "modelo_id": "atendente_delivery",
  "instrucoes_agente": "só na estratégia por_modelo: prompt complementar do agente",
  "conversation_id": "bykoji:can_7f3a:5511999999999",
  "canal": {"id": "can_7f3a", "tipo": "uazapi", "publico": "clientes"},
  "modo_resposta": "sync",
  "callback_url": null,
  "callback_token": null,
  "tarefa": "responder",
  "agora": "2026-10-08T19:42:00-03:00",
  "tempo_desde_ultima": "há 3 dias",
  "retomada": true,
  "mensagem": "texto consolidado do lote",
  "mensagens_do_lote": [
    {"enviada_em": "2026-10-08T19:41:50-03:00", "tipo": "texto", "texto": "oi"},
    {"enviada_em": "2026-10-08T19:41:55-03:00", "tipo": "localizacao", "texto": "[localização: -23.60, -46.72]"}
  ],
  "historico": [{"role": "user|assistant", "content": "...", "enviada_em": "..."}],
  "cliente": {"nome": "Ana", "unidade_preferida": "Morumbi", "resumo_anterior": "..."},
  "unidades": [{"nome": "Morumbi", "aberta_agora": true, "proxima_abertura": null}],
  "usuario": null,
  "contexto_modulos": {
    "pedidos": {"carrinho": {"itens": [], "total": 0}}
  },
  "evento": null
}
```

- `usuario` (agentes internos): `{"id": "...", "nome": "...", "papel": "gerente", "escopo": {"filiais": ["Morumbi"]}}`.
- `contexto_modulos`: cada módulo habilitado no agente acrescenta o seu bloco (substitui o antigo campo `carrinho`).
- Modo `async`: o Skyone responde `202` (ou qualquer 2xx) imediatamente e, ao terminar, chama `POST {callback_url}` com `Authorization: Bearer {callback_token}` e o mesmo corpo do Retorno. Pode enviar retornos intermediários com `"parcial": true`.

`tarefa`: `responder` (padrão), `notificar` (com `evento` preenchido, ver `/notificar`) ou `resumir` (Fase 5.5).

Volta (Retorno do Skyone → gateway):

```json
{
  "resposta": ["balão 1", "balão 2"],
  "acao": "nenhuma | silencio | transferir_humano | encerrar | pedido_fechado",
  "motivo": "texto curto; obrigatório para silencio e transferir_humano",
  "desfecho": "pedido_feito | duvida_resolvida | reclamacao | sem_interesse | null",
  "uso": {"tokens_entrada": 0, "tokens_saida": 0},
  "arquivos": [{"nome": "relatorio.pdf", "tipo": "application/pdf", "url": "https://..."}],
  "parcial": false
}
```

- `silencio`: nada é enviado; `motivo` vai para `turnos.motivo_silencio`. `resposta` vazia **sem** `acao: "silencio"` é falha (INV04).
- `encerrar`: envia `resposta` (se houver) e encerra com `desfecho`.
- `uso` é opcional — só se o Skyone expuser o consumo.
- `arquivos` é opcional; URLs de arquivo também passam pela validação SEG03 antes de serem baixadas pelo gateway.
- Compatibilidade: `resposta` como texto único continua aceita (parágrafos separados por linha em branco viram balões).

## Apêndice B — Metas de latência

| Métrica | Meta |
|---|---|
| Overhead da VPS (modo mock, sem contar o mock) | < 300 ms |
| `total_p50` (live) | ≤ 10 s |
| `total_p95` (live) | ≤ 15 s |
| Taxa de erro | < 1% |

## Apêndice C — Checkpoints que dependem do Marco

| Fase | O que é necessário |
|---|---|
| 0.3 | Testes no Skyone: agente parametrizável pelo payload? API de gestão? limite do webhook síncrono e chamada de callback? |
| 0.5 | Revisar `docs/API_ADMIN.md`; informar se o Postgres do painel será reaproveitado; confirmar modelos iniciais de agente |
| 1 | Configurar webhook no uazapi e enviar mensagens de teste |
| 2 | Fluxo do agente no Skyone e URL do webhook; Retorno no contrato do Apêndice A (silêncio, desfecho) |
| 3 | Rodada de testes no celular e ajustes de prompt |
| 4 | Cardápio real, contatos das unidades, pagamento, regras de entrega; criação das Skills no Skyone |
| 5 | Formato de mídia aceito pelo fluxo Skyone |
| 5.5 | Tarefa `resumir` no fluxo Skyone; regras de follow-up por tenant; templates Meta para fora da janela |
| 6.5 | Primeiro agente interno piloto, usuários de teste e escopos; registro do app no Teams/Azure; acesso ao chat do Luxbrain |
| 7 | Conta Meta, número e templates |
| 8 | Credenciamento iFood / 99Food |
| 9 | Prazo de retenção (LGPD) e escolha da unidade piloto |
