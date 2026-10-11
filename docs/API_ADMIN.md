# API administrativa — contrato com o painel

Banco `gateway`, Postgres próprio do gateway. O backend do painel autentica seus usuários e aplica papéis/tenant; a chave de serviço tem acesso administrativo global. O navegador nunca recebe essa chave nem acessa o banco.

**Legenda:** ✅ implantado e em produção · ⏳ planejado, **não disponível** (a fase está indicada). Não construir telas sobre rotas ⏳.

**Publicação atual (10/10/2026):** imagem `gateway:bb33dd5`, migrações 001–024, serviço em mock/dry. Processos: recepção (`gateway`), 2 `worker` e 1 `agendador`, com a **fila durável ligada** (`FILA_DURAVEL=on`, desde 10/10/2026): a recepção só valida, grava e agenda; quem processa são os workers. Voltar: `sh scripts/fila_duravel.sh desligar` (ver `docs/CAPACIDADE.md`); a chave não muda nenhuma rota deste contrato. Tudo marcado ✅ abaixo foi conferido no serviço em produção. Histórico em `PROGRESS.md`.

Rotas relativas a `/admin/v1`, salvo indicação de rota absoluta. Escritas confirmadas gravam auditoria na mesma transação, sem corpos nem segredos. Suspender/pausar usa PATCH.

Sumário: 1. Acesso · 2. Convenções · 3. Erros · 4. Empresas · 5. Modelos de agente · 6. Agentes · 7. Skyone · 8. Canais · 9. Chaves e API de integração · 10. Unidades · 11. Conversas e atendimento · 12. Contatos (LGPD03) · 13. Webhooks de saída · 14. Consumo, métricas e auditoria · 15. Benchmark · 16. Planejado

---

## 1. Acesso e autenticação

**Endereço interno:** no host, `http://127.0.0.1:8100/admin/v1`. O backend do painel em container está ligado à rede externa Docker `luxbrain_gateway_proxy` e usa **`http://luxbrain-gateway:8000/admin/v1`** (`luxbrain-gateway` é o alias do gateway nessa rede). Não usar IP do host: a porta 8100 está ligada só ao loopback. Bancos e Redis não entram na rede proxy.

**Cabeçalhos:** `Authorization: Bearer <PANEL_SERVICE_KEY>` em toda chamada e `X-Actor: <usuário autenticado>` em toda escrita (e na exportação LGPD). Não encaminhar headers do proxy/navegador na chamada interna.

**SEG02:** aceita IP privado da rede Docker e recusa `/admin` com `X-Forwarded-For`, `X-Real-IP` ou `Forwarded`, mesmo vazios/forjados e com chave válida (404). O Caddy não publica `/gateway/admin/*`. A rede privada **não dispensa autenticação**. Documentação interativa e OpenAPI desligadas (SEG08); não existe `/admin/v1/docs`.

**Rotação (SEG12):** o gateway aceita `PANEL_SERVICE_KEY` e `PANEL_SERVICE_KEY_NEXT`; o log indica `atual`/`proxima`, nunca o valor. Recriar só o gateway ao mudar variáveis; depois de migrar o backend, remover a chave antiga.

## 2. Convenções

- JSON UTF-8; timestamps ISO 8601 com fuso.
- IDs de tenant: `^[a-z0-9_]{3,40}$`, imutáveis. IDs de conversa: `<tenant>:<canal_id>:<identificador>`, codificados como segmento de URL.
- O painel valida a permissão do usuário sobre o tenant antes de chamar.
- Listas de tenants: `limite` 1–100 (padrão 50); agentes/canais/chaves: 1–99; cursor por ID. Resposta `{ "itens": [], "proximo_cursor": null }`.
- Histórico de mensagens: paginação regressiva por `antes`, `limite` 1–100 (padrão 100), com `tem_mais` (seção 11).
- Auditoria: `limite` 1–100 e cursor, resposta `{ "itens": [] }`.
- Não presumir filtros ou paginação onde não estiverem documentados.

## 3. Erros e limites ✅

Todo erro gerado pelo gateway, inclusive nos middlewares de rede, limite, corpo grande, Redis indisponível e no 404 de rota inexistente, usa o mesmo envelope:

```json
{"erro":{"codigo":"VALIDATION_ERROR","mensagem":"Confira os campos informados.","detalhes":{"campo":"nome"}}}
```

Os códigos são fixos e são os únicos emitidos; o painel decide pelo código e pelo status HTTP, nunca comparando a mensagem. Mensagens em português, sem eco de entradas, valores ou respostas de terceiros.

| Código | HTTP | Mensagem / uso |
|---|---|---|
| `VALIDATION_ERROR` | 400, 413, 422 | Confira os campos informados. Em 413: O corpo da requisição excede o tamanho permitido. |
| `NOT_FOUND` | 404 | Recurso não encontrado. Inclui tenant inexistente, isolamento entre tenants, rota inexistente e bloqueio SEG02. |
| `CONFLICT` | 409 | A operação conflita com o estado atual do recurso. |
| `UNAUTHORIZED` | 401 | Credencial de acesso ausente ou inválida. |
| `FORBIDDEN` | 403 | Você não tem permissão para esta operação. |
| `RATE_LIMITED` | 429 | Limite de requisições atingido. Aguarde e tente novamente. Sempre com `Retry-After`. |
| `UPSTREAM_ERROR` | 502 | Não foi possível concluir a chamada ao provedor. |
| `CREDENTIAL_REJECTED` | 502 | Credencial do Skyone recusada. Sem retry. |
| `NOT_IMPLEMENTED` | 501, 405 | Esta operação ainda não está disponível. |
| `UNAVAILABLE` | 503 | Serviço temporariamente indisponível. Banco, Redis ou credencial cifrada indisponíveis. |

**`detalhes`:**
- `VALIDATION_ERROR` sempre traz `detalhes.campo`: o campo conhecido com problema, ou `corpo` para JSON inválido, corpo grande ou regra que envolve vários campos.
- **Campo obrigatório enviado como `null`** → 400 `VALIDATION_ERROR` com o campo, recusado na validação, antes de qualquer gravação. Obrigatórios por recurso: tenant `nome, timezone, status, retencao_dias, tenant_teste`; agente `nome, status, provedor, skyone_estrategia, skyone_auth_tipo, timeout_s, instrucoes_agente, modo_resposta, modelo_id, modelo_versao`; canal `nome, tipo, publico, ativo`; chave `nome, escopos`; unidade `nome, horarios, ativo`; webhook de saída `url, eventos, ativo`; modelo `nome, tipo, modulos, comportamento_padrao, desfechos, followups_padrao`. Campos de comportamento do agente continuam aceitando `null` (= herdar).
- **Erros de integridade do banco** também indicam o campo: NOT NULL, CHECK e chave estrangeira → 400 `VALIDATION_ERROR` com `detalhes.campo`; valor único repetido (ex.: tenant já existente) → 409 `CONFLICT` com `detalhes.campo`. Só nomes de campo conhecidos são devolvidos.
- Os demais erros têm `detalhes: {}`.

**Limites:** corpo máximo 2 MiB, inclusive chunked. Token bucket por IP em `/api` e `/webhooks`. Limites por chave administrativa/tenant não estão implementados. Respostas emitidas antes de chegar ao gateway (ex.: bloqueio de rota pelo Caddy) pertencem ao proxy; a API administrativa do painel usa a rede interna diretamente.

## 4. Empresas (tenants)

| Métodos | Rota | Status |
|---|---|---|
| GET, POST | `/tenants` | ✅ Lista; cria tenant e config |
| GET, PATCH | `/tenants/{tenant}` | ✅ Detalhe/edição; `status=ativo|suspenso` |
| GET, PUT | `/tenants/{tenant}/config` | ✅ Padrões de comportamento da empresa |
| GET | `/modulos` | ✅ Registro de módulos |
| GET | `/tenants/{tenant}/modulos` | ✅ Módulos da empresa |
| PUT | `/tenants/{tenant}/modulos/{modulo}` | ✅ Habilitar/configurar módulo |

**Criação:** `{ "id":"bykoji", "nome":"By Koji", "plano":"pro", "limite_turnos_mes":20000, "timezone":"America/Sao_Paulo" }`. PATCH também aceita `retencao_dias`, `tenant_teste`, `status`. **`tenant_teste` pertence ao tenant**, não à config. A lista não traz consumo embutido.

**Configuração de comportamento** (`PUT /config` atualiza só os campos enviados): `debounce_s` (1–15), `debounce_curto_s` (0,5–15; padrão 1,5) e `debounce_palavras` (1–50; padrão 6) — **debounce adaptativo**: texto que termina com `?`, `.`, `!` ou `…`, ou com mais de `debounce_palavras` palavras, espera `debounce_curto_s` (nunca mais que `debounce_s`); mensagem curta, mídia e localização esperam `debounce_s`; o teto `max_wait_s` continua valendo —, `max_wait_s` (≥ debounce, ≤30), `media_wait_s` (0–60), `history_turns` (0–100), `pause_minutes`, `slow_notice_s`, `aviso_demora`, `fallback`, `alerta_telefone`, `send_mode=dry|live`, `agente_mode=mock|live`, `modo_piloto`, `contatos_permitidos`, `encerrar_vazia_min`, `max_lotes_simultaneos`, `max_concorrencia`, `skyone_dominios_permitidos`, `debounce_incompleta_s` (1–15; nulo = usa `debounce_s`; espera da mensagem que parece incompleta: termina em vírgula, reticências, "e", "mas", "que"…), `aviso_demora_janela_s` (0–3600; padrão 120; o aviso de demora não sai se o bot enviou mensagem nessa conversa dentro dessa janela, nem em turno refeito; 0 = sempre sai), `carrinho_expira_h` (1–720; padrão 6; carrinho sem alteração há mais tempo é arquivado quando o cliente volta a escrever, e o pacote informa `carrinho_expirado` e `itens_expirados`), `refazer_turno=on|off` (padrão `off`; `on`: se chega mensagem do cliente antes de a resposta sair, a resposta é descartada e o turno é refeito com o lote completo; até duas vezes, a segunda só se o turno refeito não chamou Skill nem enviou balão e o turno original começou há até 30 s — ver `docs/ESPEC_UX2.md`), `acoes_recentes_min` (0–1440; padrão 15; janela, em minutos, das chamadas de Skill que o pacote leva em `acoes_recentes`; 0 = lista vazia), `botoes_interativos=on|off` (padrão `off`: as opções que o agente devolve em `botoes` saem numeradas em texto; `on` envia botões de resposta no WhatsApp/uazapi — ver `docs/ESPEC_BOTOES.md`). Telefones: 8–16 dígitos. Valores nulos herdam; omitir em PATCH preserva. Cache de 30 s, invalidado por escritas da API.

**Fallback padrão da plataforma** (quem não define o próprio): “Tive uma instabilidade agora. Pode repetir sua mensagem em instantes? 🙏” — sem prometer retorno. A migração 015 trocou para ele só os textos antigos gravados; textos personalizados não mudam.

**Regra do `fallback` ✅ (agente e config da empresa):** pelo menos **15 caracteres e uma letra**; vazio ou `null` = usa o padrão (herda). Fora da regra → 400 `VALIDATION_ERROR` com `campo: fallback`. Registros já gravados fora da regra não são alterados nem impedem outras edições do agente.

**Módulos:** `GET /modulos` devolve nome, migrações, rotas e escopos. Registro atual: `catalogo` (busca/contexto) e `pedidos` (interface reservada, sem ferramentas até a Fase 4). PUT do módulo exige `{ "ativo":true, "config":{} }` e altera só o tenant indicado. Módulo desabilitado → 404 nas rotas do módulo.

## 5. Modelos de agente

| Métodos | Rota | Status |
|---|---|---|
| GET, POST | `/modelos-agente` | ✅ Lista/cria versões |
| GET, PATCH | `/modelos-agente/{modelo}` | ✅ Detalhe; PATCH cria nova versão |

POST exige `id`, `nome`; aceita `tenant_id` opcional (ausente/nulo = global), `tipo`, `modulos`, `comportamento_padrao`, `desfechos`, `followups_padrao` e `skyone_fluxo_modelo` (referência de fluxo criado do zero). Lista e detalhe usam `?tenant=` para incluir modelos privados; detalhe aceita `?versao=` (padrão: última). PATCH cria nova versão sem editar as anteriores; ID e tenant são imutáveis. Agentes ficam na versão fixada até PATCH explícito de `modelo_versao`. Modelos globais usam auditoria de plataforma (tenant nulo); privados, a do proprietário.

**Modelos iniciais confirmados (fechamento da Fase 0.5):** `atendente_delivery` (módulos `catalogo`, `pedidos`) e `assistente_generico` (sem módulos).

## 6. Agentes

| Métodos | Rota | Status |
|---|---|---|
| GET, POST | `/tenants/{tenant}/agentes` | ✅ Lista (com circuito e canais); cria a partir de modelo habilitado |
| GET, PATCH | `/tenants/{tenant}/agentes/{agente}` | ✅ Detalhe com configuração efetiva; `status=rascunho|ativo|pausado` |
| POST | `/tenants/{tenant}/agentes/{agente}/testar` | ✅ Teste sintético; mock ou provedor real (`usar_provedor_real`) |
| POST | `/tenants/{tenant}/agentes/{agente}/simular` | ✅ Conversa de teste; IA real permitida com envio dry |
| GET | `/tenants/{tenant}/agentes/{agente}/circuito` | ✅ Estado do circuito do Skyone |
| POST | `/tenants/{tenant}/agentes/{agente}/circuito/fechar` | ✅ Fechamento manual, auditado |

**Criação:** `{ "modelo_id":"assistente_generico", "nome":"Assistente" }`. Nasce em rascunho, provedor mock. O detalhe traz `configuracao_efetiva` com `origem_config` (plataforma/modelo/tenant/agente).

**Lista:** cada item inclui `circuito_estado` (`fechado|aberto|meio_aberto`) e `canais: [{id, tipo, ativo}]` (só canais do mesmo tenant; agente sem canal → `[]`; sem registro de circuito → `fechado`). Uma consulta agregada por página, além da validação do tenant, sem consultas por agente.

**Configuração Skyone** (exemplo; não ativa live):

```json
{
  "provedor": "skyone",
  "agente_mode": "mock",
  "send_mode": "dry",
  "skyone_estrategia": "por_modelo",
  "skyone_webhook_url": "https://luxtia.api.integrasky.cloud/FLUXO",
  "skyone_auth_tipo": "basic",
  "skyone_auth_usuario": "<usuário gerado pelo Skyone; somente escrita>",
  "skyone_auth": "<senha cadastrada pelo Marco; somente escrita>",
  "timeout_s": 60,
  "instrucoes_agente": "Instruções complementares",
  "modo_resposta": "sync"
}
```

`provedor=mock|skyone`; `llm_direto` reservado à Fase 2.5. `timeout_s` 1–600, **padrão 60** (migração 013). **Atendimento usa 60 s**: o Skyone síncrono foi comprovado até ~66 s e, entre ~66 s e ~127 s, segura a conexão sem responder (teste de 09/10/2026, `docs/SKYONE_RESULTADOS.md`). Valores acima de 60 s só fazem sentido **no modo assíncrono** (callback, adiado para antes da Fase 6.5). Só `modo_resposta=sync` por enquanto. `skyone_auth_tipo=nenhuma|cabecalho|bearer|basic`; a leitura devolve `skyone_auth_configurado` e `skyone_auth_usuario_configurado`, nunca valores ou ciphertexts. Basic exige usuário e senha; ambos somente escrita, cifrados separadamente (Fernet) e vinculados ao tenant. Omitir um campo preserva o valor; apagar um deles com Basic selecionado → 400, inclusive em tenant de teste. Usuário não aceita `:` nem caracteres de controle. `skyone_token` e `callback_timeout_s` **não são aceitos**. Live sem credencial é recusado fora de tenant de teste (SEG13). O fluxo do Skyone é **criado do zero pelo Marco**, nunca clonado; o gateway não cria fluxos.

### Testar

`POST .../agentes/{agente}/testar` aceita corpo ausente, `{}`, `{"message":"teste do painel"}` e **`{"usar_provedor_real": true}`**. Gera UUID novo, `session_id = request_id`, conversa sintética, `historico: []`, `instrucoes_agente` e IDs do agente/tenant/modelo. Não cria conversa nem envia mensagem ao canal. O campo antigo `mensagem` não é aceito.

- **Sem `usar_provedor_real`:** usa o modo efetivo do agente. Em mock devolve o mock, sem chamar o Skyone nem gastar cota.
- **Com `usar_provedor_real: true`:** chama o Skyone **só nesta requisição**, mesmo com o agente em mock, **sem alterar o agente**. Passa pela cota por URL e pelo circuito, como uma chamada live. Exige `provedor=skyone`, `skyone_webhook_url` e credencial completa; o que faltar → 400 `VALIDATION_ERROR` com `detalhes.campo` = `provedor`, `skyone_webhook_url`, `skyone_auth`, `skyone_auth_usuario` ou `skyone_auth_cabecalho`. Sem autenticação só em tenant de teste (SEG13). Disponível apenas pela chave do painel, como todo `/admin`.
- **Resposta:** `{ "ok":true, "latencia_ms":1240, "resposta":["…"], "provedor_real":true }`.
- **Falhas:** 502 com motivo fixo, sem URL/corpo/credencial externa; credencial recusada → `CREDENTIAL_REJECTED`; circuito aberto → `UPSTREAM_ERROR`.
- **Auditoria:** sucesso e falha sempre auditados: `agente.testado` / `agente.teste_falhou`; com provedor real, `agente.testado_provedor_real` / `agente.teste_provedor_real_falhou`.

### Simular

```json
{ "canal_id":"can_...", "identificador":"marco", "texto":"Olá", "message_id":"opcional-id-unico", "enviada_em":"2026-10-08T15:00:00-03:00" }
```

Exige canal API do mesmo tenant ligado ao agente. **Aceita `agente_mode=mock` ou `live` (IA real do Skyone) desde que `send_mode=dry`**: a resposta vem do fluxo, com cota e circuito, e nada é enviado para fora. Com envio real (`send_mode=live`) → 400 `VALIDATION_ERROR`, `detalhes.campo=send_mode`, sem gravar entrada. Retorno `{ "ok":true, "duplicada":false, "conversation_id":"tenant:can_...:marco", "mensagem_id":123 }`; a resposta é consultada no histórico. Piloto e status são respeitados (canal inativo, agente pausado/rascunho ou tenant suspenso → mensagem ignorada).

### Circuito

Estado persistente por agente: três falhas consecutivas em cinco minutos abrem por 60 s; depois, uma chamada de prova. Sucesso fecha; falha da prova reabre. Chamadas bloqueadas não chegam ao Skyone e usam fallback (`SKYONE_CIRCUIT_OPEN`). `GET .../circuito` → `estado`, `falhas`, `aberto_ate`, `ultimo_alerta`, isolado por tenant. Após corrigir a credencial, aguardar o período aberto antes de testar de novo. **Fechamento manual ✅:** `POST .../circuito/fechar` (sem corpo; exige `X-Actor`) zera as falhas, libera as chamadas e devolve o estado (`fechado`); auditado como `agente.circuito_fechado`. Agente de outro tenant → 404.

**Nova tentativa ✅:** uma única, 2 s depois, só para **falha de conexão ou HTTP 502/503/504 antes de qualquer resposta** e apenas se couber no `timeout_s`. Nunca em tempo esgotado nem em outros erros. Cada tentativa passa pela cota por URL; para o circuito conta uma chamada só.

**Canal uazapi (Fase 1, formato real):** `ultimo_evento_em` do canal é atualizado a cada evento autenticado do webhook (sinal de vida para o painel); `status_conexao` (`conectado`, `conectando`, `desconectado`) vem do `GET /instance/status` da instância a cada varredura e do evento `connection` do webhook; mudanças geram `canal.desconectado` / `canal.reconectado` na auditoria da empresa. **Varredura de reconciliação:** o agendador confere a cada 90 s (`VARREDURA_UAZAPI_S`; 0 desliga) e na subida as mensagens dos últimos 10 min da instância e recebe as que o webhook não entregou (só mensagens do cliente, com mais de 20 s); cada mensagem tem `origem_recebimento` (`webhook`, `varredura` ou nulo); recuperação gera `canal.varredura_recuperou` na auditoria; `GET /admin/v1/fila` traz `varredura` (última rodada, canais, recuperadas, erros, total). Cada mensagem tem `tipo` (`texto`, `audio`, `imagem`, `video`, `documento`, `figurinha`, `contato`, `localizacao`, `reacao`, `desconhecido`) e `meta` (coordenadas, dados do anexo, `resposta_a`, `reacao_a`); nos canais API o tipo é sempre `texto`. Reação é gravada e não abre turno. Anexo que o gateway não lê vira `[não foi possível ler <tipo>: <motivo>]` no texto entregue ao agente.

**Turno e agente (Fase 2):** cada turno grava `acao` (`nenhuma | silencio | transferir_humano | encerrar | pedido_fechado`), `motivo_silencio` (só quando `acao = silencio`) e `desfecho`. `aguardando_motivo` ganhou `transferido_pelo_agente` (o agente pediu a transferência) e `silencio_sem_motivo` (o agente ficou em silêncio sem motivo nas duas consultas; nada é enviado ao cliente). Novo código de erro do turno: `SILENCE_WITHOUT_REASON`. Resposta em texto único com linha em branco passa a sair em um balão por parágrafo. Contrato com o Skyone: `docs/SKYONE.md`.

**Aviso de demora e “digitando” (Fase 3):** o padrão de `slow_notice_s` passou de 15 para 20 s. Com envio real em canal uazapi, o gateway pede “digitando” ao canal enquanto o agente processa (25 s por pedido; o envio da resposta encerra; em silêncio é retirado). Em envio de teste nada é chamado.

**Conversa: encerramento, `#bot`, limite por contato (Fase 3):**
- `GET .../conversas` devolve também `encerrada_em`, `desfecho` e `limitado_ate`. Quando o agente responde `encerrar`, a conversa fica com `encerrada_em` e `desfecho` (`pedido_feito`, `duvida_resolvida`, `reclamacao`, `sem_interesse`...); a próxima mensagem do cliente reabre (os dois voltam a nulo; auditoria `conversa.reaberta`). O `status` (`ativa | pausada`) não muda de significado.
- **Conversa vazia:** a cada 5 min o agendador encerra com `desfecho = "vazia"` a conversa em que o cliente só mandou o que o gateway não lê (anexo, figurinha) e ninguém falou por `encerrar_vazia_min` (padrão 30). Não vale para conversa com atendente, pausada, aguardando atendente ou com mensagem pendente.
- **`#bot`:** mensagem do atendente pelo celular da instância com exatamente `#bot` devolve a conversa ao bot na hora (tira pausa e “aguardando atendente”), não vai para o agente e fica gravada com `tipo = "comando"`; auditoria `conversa.devolvida` com ator `atendente`.
- **Limite por contato (SEG07):** `max_msgs_por_minuto_contato` (1–600, padrão 20; por empresa ou agente). Acima disso em 60 s, o contato fica 5 min sem chamar o agente (`limitado_ate`); as mensagens continuam gravadas; auditoria `contato.limite_excedido` e aviso no log.
- **Resposta descartada (INV03):** se a conversa é pausada depois de o agente responder e antes do envio, a resposta não sai; turno `ignorado` e auditoria `resposta.descartada`.
- **Logs (LGPD04):** telefones aparecem como `5511****7777` em qualquer linha de log (inclusive URLs das rotas); nenhum log traz conteúdo de mensagem.

**Catálogo e unidades (Fase 4, bloco 1):**
- `POST /admin/v1/tenants/{t}/catalogo/importar?simulacao=true|false` — corpo: o arquivo CSV (UTF-8, com ou sem BOM, até 2 MB). Colunas, nesta ordem: `codigo,unidade,categoria,nome,descricao,preco,tipo,alcoolico,delivery,ativo,observacao`. **`simulacao=true` é o padrão**: só relata. O arquivo é o catálogo inteiro da empresa: cria ou atualiza pelo `codigo`; item que não está no arquivo fica **inativo** (não é apagado). Erro em qualquer linha → nada é gravado (`aplicado: false`). Resposta: `simulacao`, `aplicado`, `linhas`, `validas`, `inseridos`, `atualizados`, `inalterados`, `desativados`, `erros` (`linha`, `codigo`, `erro`) e `por_unidade` (`total`, `ativos`, `com_preco`, `vendaveis`, `por_tipo`). Arquivo inválido (cabeçalho, codificação, tamanho) → 400.
- Regras do arquivo: `preco` vazio = sem preço (nulo; **zero é erro**); `tipo` = `item | adicional | opcao_executivo | regra`; `alcoolico` e `ativo` = `sim | nao`; `delivery` = `sim | nao | a_confirmar`. **Vendável** (pode entrar em carrinho) = ativo, com preço e `delivery` diferente de `nao`.
- `GET /admin/v1/tenants/{t}/catalogo/itens` — filtros `unidade`, `tipo`, `ativo`, `q` (sem acento, tolerante); `limite` 1–200, `cursor` (id). `GET /admin/v1/tenants/{t}/catalogo/resumo` — contagens por unidade e tipo.
- **Unidades** (`/admin/v1/tenants/{t}/unidades`) ganharam: `codigo` (liga a unidade aos itens do catálogo; único na empresa), `endereco`, `telefone`, `whatsapp_pedidos` (10–15 dígitos, com DDI; o mesmo número pode atender mais de uma unidade), `formas_pagamento`, `taxa_entrega`, `pedido_minimo`, `area_entrega`, `aceita_retirada`, `regras`. **Nulo = não informado**: o agente não afirma nada sobre o campo e diz que a unidade confirma no retorno do pré-pedido. Todos, menos `whatsapp_pedidos`, vão no bloco `unidades` do pacote ao agente, junto de `horarios`, `aberta_agora` e `proxima_abertura`.
- **Busca para o agente:** `GET /api/v1/catalogo/busca?conversation_id=...&q=...&unidade=...` (cabeçalho `X-API-Key`, escopo `ferramentas:catalogo`). Só itens ativos; sem acento, sem diferença de maiúsculas e tolerante a erro de digitação; até 20 resultados. Devolve `item_id`, `codigo`, `unidade`, `categoria`, `nome`, `descricao`, `preco` (número ou `null`), `alcoolico`, `delivery`, `tipo`, `observacao`.

**Carrinho e pré-pedido (Fase 4, bloco 2)** — rotas das Skills do agente: cabeçalho `X-API-Key` com escopo `ferramentas:pedidos`. A empresa vem da chave (nunca de cabeçalho ou corpo). Exigem conversa existente, do agente da chave, com o módulo `pedidos` ligado na empresa e no modelo; senão 404.

| Método e rota | Função |
|---|---|
| `GET /api/v1/carrinho/{conversation_id}` | Carrinho: itens, subtotal, taxa, total e o que falta. |
| `POST /api/v1/carrinho/{conversation_id}/itens` | `{item_id, quantidade (1–99, padrão 1), observacao}`. Mesmo item com a mesma observação soma. |
| `PATCH /api/v1/carrinho/{conversation_id}/itens/{id}` | `{quantidade, observacao}`; quantidade 0 remove. `id` é o da linha do carrinho. |
| `DELETE /api/v1/carrinho/{conversation_id}/itens/{id}` | Remove a linha. |
| `DELETE /api/v1/carrinho/{conversation_id}` | Esvazia (solta a unidade e a confirmação de maioridade). |
| `PUT /api/v1/carrinho/{conversation_id}/entrega` | `{unidade, modalidade: delivery|retirada, endereco, horario, observacao, maioridade_confirmada}`; só muda o que vier. |
| `POST /api/v1/pedidos` | `{conversation_id}` → fecha o carrinho em pré-pedido `aguardando_loja` (201). |
| `GET /api/v1/pedidos/{numero}?conversation_id=...` | Situação do pedido (só da conversa dona). |
| `POST /api/v1/pedidos/{numero}/cancelar` | `{conversation_id, motivo}` → cancela enquanto estiver `aguardando_loja`. |

- **Resposta de escrita:** `{"ok": true, "carrinho": {...}}` com o carrinho completo: `unidade`, `modalidade`, `endereco`, `horario`, `observacao`, `maioridade_confirmada`, `itens` (`id`, `item_id`, `codigo`, `nome`, `quantidade`, `preco_unitario`, `subtotal`, `observacao`, `alcoolico`), `numero_itens`, `subtotal`, `taxa_entrega` (nula quando a unidade não tem taxa cadastrada ou não é entrega), `total`, `pedido_minimo`, `tem_alcoolico`, `faltando` (`itens`, `modalidade`, `endereco`, `maioridade_confirmada`) e `pode_fechar`.
- **Recusa:** envelope de erro com `detalhes.motivo` e, quando há carrinho, o campo `carrinho` atualizado. Motivos: `item_inexistente` (404), `item_inativo`, `sem_preco`, `sem_delivery`, `unidade_diferente`, `unidade_inexistente` (404), `carrinho_cheio`, `linha_inexistente` (404), `carrinho_vazio`, `falta_modalidade`, `falta_endereco`, `falta_maioridade`, `retirada_indisponivel`, `abaixo_do_minimo` (todos 422 salvo indicação), `preco_alterado` (409: o preço mudou no catálogo, o carrinho foi atualizado e precisa ser reconfirmado), `pedido_inexistente` (404), `pedido_nao_cancelavel` (409).
- **Regras (validadas no gateway, independentemente do agente):** item só entra se ativo, com preço e `delivery` diferente de `nao`; carrinho de uma unidade só; preço sempre do catálogo (copiado ao entrar e revalidado ao fechar; o agente não informa preço); bebida alcoólica só fecha com `maioridade_confirmada`; delivery exige endereço; `pedido_minimo` e `aceita_retirada` da unidade são respeitados; pedido fechado não muda (itens novos abrem outro carrinho); cancelamento só pelo dono e só antes de a unidade decidir (SEG06).
- **Pedido:** `numero` (5 dígitos ou mais, único na empresa), `status` (`aguardando_loja | confirmado | recusado | cancelado`), `itens` (cópia: `codigo`, `nome`, `quantidade`, `preco_unitario`, `subtotal`, `observacao`, `alcoolico`), `subtotal`, `taxa_entrega`, `total`, `unidade`, `modalidade`, `endereco`, `horario`, `observacao`, `motivo`, `cliente_nome`.
- **Painel:** `GET /admin/v1/tenants/{t}/pedidos` (filtros `status`, `unidade`; `limite` 1–100; `cursor` = id; mais novos primeiro) e `GET /admin/v1/tenants/{t}/pedidos/{numero}`; incluem `cliente_telefone` e `decidido_por`. Confirmar e recusar: ver “Unidade, avisos e transbordo”.
- **Webhooks de saída:** `pedido.criado`, `pedido.cancelado`, `pedido.confirmado` e `pedido.recusado` (os dois últimos com `motivo`), com `numero`, `status`, `conversation_id`, `unidade`, `modalidade`, `itens`, `subtotal`, `taxa_entrega`, `total`, `tem_alcoolico` (sem telefone nem endereço). Auditoria: `pedido.criado`, `pedido.cancelado`.
- **Pacote ao agente:** `contexto_modulos.pedidos` = `{"carrinho": {...}, "ultimo_pedido": {numero, status, total, criado_em} | null}`.

**Unidade, avisos e transbordo (Fase 4, bloco 3):**
- `POST /admin/v1/tenants/{t}/pedidos/{numero}/confirmar` e `.../recusar` (`{"motivo": "..."}` opcional) — decisão da unidade pelo painel. Só vale para pedido `aguardando_loja`; se já foi decidido (pelo WhatsApp da unidade, por outro usuário ou cancelado pelo cliente) → **409** com `detalhes.status`. Inexistente → 404. `decidido_por` guarda o usuário (`X-Actor`) ou `unidade:whatsapp`.
- **WhatsApp da unidade:** ao fechar o pedido, o resumo vai para o `whatsapp_pedidos` da unidade (vazio = o pedido fica só no painel). A unidade responde `OK <numero>` para confirmar ou `NÃO <numero> <motivo>` para recusar; só vale vindo do número de pedidos de uma unidade dona do pedido. Quem decide primeiro (painel ou WhatsApp) vale; o outro recebe “já decidido”.
- **Número de unidade e número de alerta nunca são clientes:** mensagens deles não criam conversa, turno nem resposta do agente.
- **Aviso ao cliente:** confirmado ou recusado (com o motivo), o gateway envia um texto fixo na própria conversa, pelo mesmo caminho das respostas do bot (respeita piloto, pausa do atendente e envio de teste). Na recusa, os itens voltam para o carrinho da conversa.
- **Alerta de transbordo:** `whatsapp_alerta_transbordo` na empresa (`PATCH /admin/v1/tenants/{t}`; 10–15 dígitos, com DDI; nulo = desligado). Quando uma conversa entra em “aguardando atendente”, sai um aviso curto com o telefone mascarado, o motivo, o último pedido e o endereço do painel (`PAINEL_URL`).
- **Avisos internos:** `GET /admin/v1/tenants/{t}/avisos` lista os avisos (`tipo`: `pedido_unidade | resposta_unidade | transbordo | pedido_cliente`; `status`: `pendente | enviando | enviado | simulado | descartado | falhou`; sem o texto). Saem pelo agendador em até ~2 s. Aviso para unidade/alerta só é enviado de verdade quando o agente do canal uazapi está com envio real; senão fica `simulado`. Falha: 3 tentativas para unidade/alerta; aviso ao cliente nunca é reenviado.
- Auditoria: `pedido.confirmado`, `pedido.recusado`. Especificação das Skills do agente: `docs/SKILLS.md`.

**Gestão G1 — horários da unidade** (`docs/ESPEC_GESTAO.md`). Todas sob `/admin/v1/tenants/{t}/unidades`, rede interna, chave do painel e `X-Actor`.

| Rota | Corpo | Resposta |
|---|---|---|
| `GET /unidades`, `GET /unidades/{id}` | — | Unidade com os campos de sempre mais `fuso`, `fuso_efetivo`, `pausa_ate`, `pausa_indefinida`, `pausa_motivo` e `situacao` |
| `POST /unidades`, `PATCH /unidades/{id}` | campos atuais + `fuso` (fuso IANA, ex. `America/Sao_Paulo`; nulo herda o da empresa) | Unidade com `situacao`. `horarios` com turnos sobrepostos → 400 |
| `POST /unidades/{id}/pausar` | `{"ate": "<data e hora com fuso>"}` **ou** `{"indefinida": true}`; `motivo` opcional (até 200) | Unidade. `ate` precisa estar no futuro e a no máximo 60 dias; os dois juntos ou nenhum → 400 |
| `POST /unidades/{id}/reabrir` | — | Unidade sem pausa |
| `PUT /unidades/{id}/horarios` | grade inteira: `{"seg": [["12:00","15:00"],["19:00","22:00"]], "sex": [["19:00","02:00"]]}` | Unidade. Dia ausente = fechado; até 12 turnos por dia; `HH:MM`; fim menor que o início = cruza a meia-noite; sobreposição → 400 |
| `GET /unidades/{id}/excecoes?desde=&ate=` | — | `{"itens": [{"id","data","fechado","faixas","motivo"}]}`; sem filtro, de ontem em diante |
| `POST /unidades/{id}/excecoes` | `{"data": "2026-12-25", "fechado": true, "motivo": "Natal"}` ou `{"data": "...", "faixas": [["12:00","16:00"]]}` | A exceção (cria ou substitui a da data). `fechado` e `faixas` juntos, ou nenhum → 400 |
| `DELETE /unidades/{id}/excecoes/{eid}` | — | `{"ok": true}`; inexistente → 404 |
| `GET /unidades/{id}/agenda?dias=7` | — | `{"fuso", "situacao", "dias": [{"data","dia","origem": "grade|excecao","fechado","faixas","motivo"}]}`; `dias` 1–62 |
| `GET /gestao/alteracoes?entidade=&entidade_id=&unidade_id=&desde=&ate=&limite=&cursor=` | — | `{"itens": [{"id","actor","entidade","entidade_id","acao","antes","depois","criado_em"}], "proximo_cursor"}` |

- **`situacao`** = `{"aberta_agora": true|false|null, "motivo": null|"inativa"|"pausa"|"excecao"|"horario"|"sem_horario", "fecha_as", "proxima_abertura", "pausada_ate"}`, com datas ISO no fuso da unidade. `aberta_agora: null` com `sem_horario` = nenhum horário cadastrado (não informado). Pausa indefinida não tem `proxima_abertura`. Pausa vencida é ignorada sozinha.
- A exceção substitui a grade **daquele dia**; um turno da véspera que cruza a meia-noite vai até o fim.
- **Histórico (`gestao/alteracoes`):** `entidade` = `unidade` (`entidade_id` = id da unidade; `acao`: `criado`, `alterado`, `pausado`, `reaberto`, `horarios`) ou `excecao` (`entidade_id` = `<id da unidade>:<data>`; `acao`: `criado`, `alterado`, `removido`). `antes` e `depois` trazem só os campos que mudaram; `whatsapp_pedidos` vai mascarado. Alteração que não muda nada não gera linha. Mais novos primeiro; `cursor` = `id`.
- **Filtros do histórico (G1.1):**
  - `unidade_id`: traz as alterações da unidade **e de todas as exceções dela** (inclusive as já removidas), na mesma lista. Não combina com `entidade`/`entidade_id` (juntos → 400). Unidade inexistente ou de outra empresa → 404.
  - `desde` (inclusivo) e `ate` (exclusivo): data e hora ISO 8601 **com fuso**, ex. `2026-10-10T00:00:00-03:00` ou `...Z`. Sem fuso, só a data ou valor inválido → 400 (`campo` = `desde` ou `ate`); `desde` depois de `ate` → 400. Na URL, codifique o `+` do fuso como `%2B` (um `+` cru também é aceito).
  - `limite`: de **1 a 200**, padrão 50; fora disso → 400. `cursor`: o `proximo_cursor` da página anterior (nulo = acabou). Os filtros valem em todas as páginas.
  - `criado_em` vem sempre com offset (`2026-10-10T21:40:12.123456+00:00`).
- **Telefones no formato E.164 sem `+` (G2):** `whatsapp_pedidos` (unidade) e `whatsapp_alerta_transbordo` (empresa) aceitam só dígitos, de **10 a 15**, com DDI (ex.: `5511999990000`). Fora disso → 400; nulo = não informado. O banco tem a mesma regra (migração 025).
- No pacote ao agente, cada unidade ganhou `motivo_fechada`, `fecha_as`, `pausada_ate` e `pausa_motivo`; `aberta_agora` e `proxima_abertura` passam a considerar exceções, pausa e o fuso da unidade.

**Limites compartilhados (fila durável, ativos quando `FILA_DURAVEL=on`):** `max_lotes_simultaneos` por empresa e `max_concorrencia` por agente valem somados entre todos os workers (vagas no Redis). Padrão 5 e 5; os dois valem ao mesmo tempo (uma conversa precisa de vaga na empresa **e** no agente). Por empresa: `PUT /admin/v1/tenants/{t}/config`; por agente: `PATCH /admin/v1/tenants/{t}/agentes/{a}` (1–100; nulo herda). Como dimensionar: `docs/CAPACIDADE.md`. Empresa no limite não atrasa as outras (INV07).

## 7. Skyone: autenticação Basic, limite por URL e credencial recusada ✅

**Basic:** no fluxo validado, o Skyone gera o usuário `flow-<id-do-fluxo>@luxtia` e o Marco define a senha. Cadastro pelo PATCH do agente (`skyone_auth_tipo:"basic"`, `skyone_auth_usuario`, `skyone_auth`). O adaptador envia `Authorization: Basic base64(usuario:senha)` (UTF-8); nada vai no corpo. Não colocar senha em chat, URL ou argumentos de shell; o operador pode enviar um arquivo local protegido pelo cliente HTTP.

**Limite por URL:** `SKYONE_REQUESTS_PER_MINUTE` é um teto global de infraestrutura, padrão **240**, aplicado a cada URL numa janela móvel de **60 s no Redis**. Agentes e tenants com a mesma URL compartilham o teto; não há multiplicação por agente. Configurar o mesmo valor em todas as instâncias e recriar só o gateway após mudar. O Webhook do Skyone está em 300/min; o teto local usa 80%. As chaves Redis guardam só o hash da URL.

Sem vaga, a chamada espera sem registrar falha. `timeout_s` limita **espera + chamada**; se esgotar, o pipeline registra erro e entrega o fallback (simulado em dry). Espera que expira antes da chamada não conta no circuito. Redis indisponível falha fechado. A robustez completa do Redis é da Fase 0.6.

**Credencial recusada:** 401/403 nunca são repetidos. `/testar` → 502 `CREDENTIAL_REJECTED`; o pipeline usa fallback com `turnos.erro=SKYONE_CREDENTIAL_REJECTED`. Alerta `agente.credencial_recusada` em auditoria/polling e log (sem credencial/URL), agrupado por agente por 3 h; pode ir pela outbox HMAC a webhooks inscritos nesse evento. Nenhum destino externo cadastrado.

## 8. Canais

| Métodos | Rota | Status |
|---|---|---|
| GET, POST | `/tenants/{tenant}/canais` | ✅ Lista/cria; URL com segredo mostrada uma vez |
| GET, PATCH | `/tenants/{tenant}/canais/{canal}` | ✅ Detalhe/edição |
| POST | `/tenants/{tenant}/canais/{canal}/rotacionar-segredo` | ✅ Nova URL uma vez; segredo anterior invalidado |
| POST | `/tenants/{tenant}/canais/{canal}/testar-envio` | ⏳ Fase 1; depende do provedor |
| GET | `/tenants/{tenant}/canais/{canal}/status` | ⏳ Fase 1 |
| GET | `/tenants/{tenant}/canais/{canal}/qrcode` | ⏳ Fase 1 |

**Criação:** `{ "tipo":"api", "nome":"Diagnóstico", "agente_id":"ag_...", "ativo":false, "publico":"clientes" }`. Tipos cadastráveis: uazapi, meta, site, email, teams, luxbrain, api; **só api e uazapi recebem mensagens nesta etapa** (uazapi ainda exige fixtures reais). Canal interno cadastrado não significa autenticação de funcionários (Fase 6.5). Triagem não é aceita pela API mínima. Canais nascem inativos se `ativo` for omitido.

**Credenciais de canal:** objeto `credenciais`, ex.: `{ "base_url":"https://instancia.uazapi.com", "token":"..." }`, cifrado por tenant. Campo omitido preserva; leitura devolve `credenciais_configurado`.

**URL do webhook:** na criação, `webhook_url` inclui o segredo e aparece uma vez: `https://painel.luxbrain.com.br/gateway/webhooks/api/can_...?s=...`. Proteger como credencial. `rotacionar-segredo` não exige corpo e devolve `{ "id":"can_...","webhook_url":"…?s=<novo>" }` só nessa resposta. Após o commit da rotação, o segredo anterior recebe 401; requisições autenticadas antes podem já estar em processamento. O provedor precisa ser atualizado pelo operador.

**Recepção** (rotas absolutas, autenticadas pelo segredo do canal):

| Método | Rota | Status |
|---|---|---|
| POST | `/webhooks/api/{canal}?s=...` | ✅ `{identificador,texto,message_id?,enviada_em?}` |
| POST | `/webhooks/uazapi/{canal}?s=...` | ✅ Normalizador herdado; fixtures reais pendentes (Fase 1) |
| POST | `/webhooks/{outro_tipo}/{canal}?s=...` | ⏳ Fases 6/6.5/7; tipo cadastrado não implementado → 501 após autenticação |

Canal inativo, agente pausado/rascunho ou tenant suspenso → 200 ignorado após autenticação. Mensagens de clientes são gravadas antes de agendar.

**URLs configuráveis (SEG03):** HTTPS, porta 443, sem credenciais na URL; destinos privados/locais recusados ao salvar; DNS revalidado antes de cada chamada, sem redirects.

## 9. Chaves de API e API de integração

| Métodos | Rota | Status |
|---|---|---|
| GET, POST | `/tenants/{tenant}/api-keys` | ✅ Lista/cria; valor mostrado uma vez |
| GET, PATCH, DELETE | `/tenants/{tenant}/api-keys/{chave}` | ✅ Detalhe/edição/revogação |

**Chave:** `{ "nome":"integracao", "escopos":["conversar","eventos:ler"], "agente_id":"ag_...", "expira_em":"2027-10-01T00:00:00Z", "ips_permitidos":["200.10.20.0/24"] }`. A criação devolve `chave` uma vez; o banco guarda hash com pepper. PATCH aceita nome/escopos/agente/validade/IPs; DELETE revoga. Restrição de agente opcional, sempre dentro do tenant. Escopos: `conversar`, `eventos:ler`, `notificar`, `callback`, `ferramentas:<modulo>` (depende do módulo habilitado). `callback` pode ser cadastrado, mas a rota é planejada.

**API de integração** (rotas absolutas; `X-API-Key`, nunca a chave do painel):

| Método | Rota | Status |
|---|---|---|
| POST | `/api/v1/conversas/mensagens` | ✅ Corpo da simulação; escopo `conversar` |
| GET | `/api/v1/conversas/{conv}/mensagens` | ✅ Escopo `eventos:ler`; `antes`, `limite`, `tem_mais` |
| POST | `/api/v1/conversas/{conv}/notificar` | ✅ Escopo `notificar`; resposta 202 |
| GET | `/api/v1/catalogo/busca?conversation_id=&q=` | ✅ Escopo `ferramentas:catalogo`; módulo habilitado no tenant e no modelo; até 50 itens ativos; preço do Postgres |
| POST | `/api/v1/agentes/callback/{request_id}` | ⏳ Adiado para antes da Fase 6.5; escopo `callback` |

Histórico e dados são filtrados por tenant e pela restrição de agente da chave; acesso cruzado → 404. O pacote ao agente contém `contexto_modulos` só de capacidades habilitadas no tenant e no modelo. A migração 006 renomeou a tabela legada para `catalogo_itens` sem apagar dados.

**Notificações na voz do agente:** `POST /api/v1/conversas/{conv}/notificar` exige `evento` (1–100 caracteres), `instrucao` (1–20000), `dados` opcional (objeto); ex.: `{ "evento":"pedido_saiu", "dados":{"pedido":"K-1042"}, "instrucao":"Avise o cliente" }`. Retorna **202** `{ "ok":true, "mensagem_id":123, "status":"enfileirada" }`, não confirmação de entrega. Cria entrada de origem `sistema`; o agendador chama o agente com `tarefa=notificar` e `evento`. A chave precisa do escopo e pertencer ao tenant/agente da conversa. Pausa humana, piloto e status são reavaliados antes de chamar e enviar; bloqueio → turno ignorado e auditoria `notificacao.nao_enviada`. Campos de segredo conhecidos nos dados são mascarados; não colocar credenciais em texto livre. A escrita registra o ID da chave como actor. Meta/janela de 24 h na Fase 7.

## 10. Unidades e horários

| Métodos | Rota | Status |
|---|---|---|
| GET, POST | `/tenants/{tenant}/unidades` | ✅ |
| GET, PATCH, DELETE | `/tenants/{tenant}/unidades/{unidade}` | ✅ DELETE desativa, preservando histórico |

POST exige `nome`; aceita `telefone_whatsapp` (8–16 dígitos), `ativo` (padrão true) e `horarios`, ex.: `{ "seg":[["11:30","15:00"],["18:00","23:00"]], "dom":[] }`. Dias seg/ter/qua/qui/sex/sab/dom; HH:MM de 00:00 a 23:59, até 12 faixas por dia, início e fim distintos; fim anterior ao início = madrugada seguinte. Horários ausentes = fechado, não 24 h. PATCH altera os campos enviados.

O pacote ao agente inclui só unidades ativas do tenant, com `id`, `nome`, `aberta_agora` e `proxima_abertura` (ISO no fuso do tenant; nulo se aberta ou sem próxima faixa). O instante de fechamento já conta como fechado. Nenhum horário comercial real foi cadastrado.

## 11. Conversas, histórico e atendimento humano

| Métodos | Rota | Status |
|---|---|---|
| GET | `/tenants/{tenant}/conversas` | ✅ Lista com filtros |
| GET | `/tenants/{tenant}/conversas/{conv}/mensagens` | ✅ Histórico paginado |
| POST | `/tenants/{tenant}/conversas/{conv}/assumir` | ✅ `{minutos:60}` |
| POST | `/tenants/{tenant}/conversas/{conv}/devolver` | ✅ |
| POST | `/tenants/{tenant}/conversas/{conv}/mensagens` | ✅ Mensagem humana |
| GET | `/tenants/{tenant}/eventos?desde=` | ✅ Polling |
| GET | `/tenants/{tenant}/eventos/cursor` | ✅ Último ID de evento confirmado do tenant, sem listar histórico |

**Lista:** filtros opcionais `agente`, `canal`, `status=ativa|pausada`, `busca` (substring literal de nome/telefone), `desde` (ISO com fuso; filtra `atualizado_em`). `limite` 1–100, `cursor=<conversation_id>`, ordem crescente por ID; resposta `itens`/`proximo_cursor`. Agente/canal de outro tenant → 404. `ativa` = bot sem pausa vigente; encerramento/desfecho na Fase 3. Itens trazem agente/canal, nome/telefone, última mensagem/horário, `bot_pausado_ate`, **`aguardando_atendente_desde`** e **`aguardando_motivo`**. Filtro opcional **`aguardando=true|false`** (outro valor → 400 `VALIDATION_ERROR` com `campo: aguardando`).

**Aguardando atendente ✅:** a conversa que recebe fallback por erro do agente, tempo esgotado ou circuito aberto fica marcada com `aguardando_atendente_desde` (instante da primeira marcação) e `aguardando_motivo` = `erro_agente | tempo_esgotado | circuito_aberto | entrega_incerta`, e gera o evento **`conversa.aguardando_atendente`** no `/eventos` (só na primeira marcação; `alvo` = conversa). A marcação sai quando um atendente **assume, devolve ou responde** a conversa (os dois campos voltam a `null`). `entrega_incerta` só ocorre no caminho da fila durável (parte da resposta presa em envio quando o worker caiu; nunca é reenviada; evento `mensagem.entrega_incerta`).

**Histórico paginado:** `GET .../conversas/{conv}/mensagens?antes=<id>&limite=40` → `{ "itens":[...], "tem_mais":true }`. `antes` é exclusivo (só IDs menores); `limite` 1–100; sem parâmetros, as últimas 100. Itens de cada página em ordem cronológica. Para a página anterior, use o **menor ID** da página atual como `antes`; pare quando `tem_mais=false`. Página vazia → `itens:[]`, `tem_mais:false`. Mensagens novas não deslocam páginas anteriores. Tenant e conversa sempre filtrados. A rota de integração `/api/v1/conversas/{conv}/mensagens` usa a mesma paginação.

**Assumir/devolver:** `assumir` aceita corpo opcional `{ "minutos":60 }` (inteiro 1–10080) e pausa ao confirmar a transação. `devolver` (sem corpo) remove a pausa e reagenda só entradas ainda pendentes, sem repetir turnos ignorados. Ambos retornam `{ "ok":true,"bot_pausado_ate":"… ou null" }`. Pausa e envio final do bot usam a mesma trava por conversa: resposta ainda em geração é descartada se a pausa confirmar antes do envio.

**Envio humano:** `{ "texto":"Olá, sou o atendente" }` (1–20000 caracteres não vazios). Pausa pelo `pause_minutes` efetivo e grava saída `origem=humano`; em dry registra a simulação sem chamar o provedor. Retorna 201 `{ "ok":true,"mensagem_id":123,"origem":"humano","send_mode":"dry" }`. Erro do provedor → 502 sanitizado, mantém a pausa e não registra entrega. Canal inativo/tenant suspenso → 409; envio live de canal não implementado → 501. Sem retry automático.

**Cursor inicial:** `GET /admin/v1/tenants/{tenant}/eventos/cursor` → `{"ultimo_id":123}`. Retorna `0` quando o tenant existe mas ainda não tem eventos; tenant inexistente → 404 `NOT_FOUND`. Mesma autenticação de painel e restrição de rede interna das demais rotas administrativas. O ID é o maior evento confirmado **desse tenant**, não um cursor global; a leitura não cria evento nem altera dados. Não recebe corpo ou parâmetros.

Ao abrir Conversas, obtenha esse cursor **antes** de carregar a lista/histórico inicial e comece o polling com `desde=ultimo_id`. Assim, mudanças confirmadas durante a carga inicial também aparecem no polling. Não reinicialize o cursor a cada consulta. Para retomar eventos antigos, continue usando o cursor salvo e o polling paginado existente; este endpoint não retorna nem reconstrói o histórico. Dispensa percorrer o histórico paginado para descobrir o ponto de partida. O marcador de leitura por usuário no navegador continua independente.

**Polling:** `GET /tenants/{tenant}/eventos?desde=0&limite=50` → `{ "itens":[{"id":123,"tipo":"mensagem.recebida","alvo":"tenant:canal:contato","criado_em":"..."}],"proximo_cursor":123,"tem_mais":false }`. Reutilizar `proximo_cursor` como `desde`; consumir páginas enquanto `tem_mais=true`, depois consultar a cada 2–3 s. IDs vêm da auditoria persistente, alocados em série por tenant até o commit. Inclui mudanças administrativas, mensagens recebidas/enviadas/humanas, resultado de turnos e consumo; nunca conteúdo ou credenciais. Use `alvo` para atualizar a conversa/lista e refaça a leitura inicial ao conectar. Eventos de mensagens anteriores a esta implementação não são reconstruídos.

## 12. Dados do contato (LGPD03)

| Métodos | Rota | Status |
|---|---|---|
| GET | `/tenants/{tenant}/contatos?busca=` | ✅ |
| GET | `/tenants/{tenant}/contatos/{contato}/exportar` | ✅ Exige `X-Actor`; auditado |
| DELETE | `/tenants/{tenant}/contatos/{contato}` | ✅ Anonimização auditada |

Contatos são vinculados às conversas na recepção; a migração 011 vinculou conversas existentes sem descartar dados. A busca devolve até 100 contatos do tenant por nome/telefone/e-mail. A exportação devolve contato, conversas, mensagens, turnos, brutos e entregas associadas, auditoria e `pedidos:[]` (módulo completo na Fase 4).

**DELETE** exige `{ "confirmacao":"<telefone ou e-mail atual>" }` e faz **anonimização**, não remoção de métricas: apaga telefone/e-mail/nome/perfil/resumo, substitui o conteúdo das mensagens, retira resposta/erro dos turnos, troca IDs de conversa com identificadores pessoais por IDs aleatórios, limpa payloads brutos e entregas associados, cancela entregas pendentes e retira agendamentos Redis antigos. A auditoria mantém ação/horário com identificadores substituídos. Retorno `{ "ok":true, "anonimizado":true }`; contato já anonimizado → 409; conversa em processamento → 409 sem alterações parciais (repetir depois). Outro tenant permanece intacto, mesmo com o mesmo telefone. Mensagens futuras viram novo contato. Backups e dados já entregues a terceiros não são alterados; retenção/restauração na Fase 9.

## 13. Webhooks de saída

| Métodos | Rota | Status |
|---|---|---|
| GET, POST | `/tenants/{tenant}/webhooks-saida` | ✅ |
| GET, PATCH, DELETE | `/tenants/{tenant}/webhooks-saida/{webhook}` | ✅ DELETE desativa, preservando entregas |
| POST | `/tenants/{tenant}/webhooks-saida/{webhook}/testar` | ✅ 202 |
| GET | `/tenants/{tenant}/webhooks-saida/{webhook}/entregas` | ✅ Últimas 100 tentativas |

POST exige `url`; aceita `eventos` (lista) e `ativo` (padrão false). Devolve `segredo` uma vez; leituras nunca devolvem credencial/ciphertext. PATCH altera URL/eventos/ativo. `/testar` exige webhook ativo e devolve 202 `{ "entrega_id":123, "status":"pendente" }`; `/entregas` sem corpo nem segredo.

**Assinatura:** corpo `{ "evento":"conversa.transferida", "tenant_id":"bykoji", "dados":{...} }`; headers `X-Gateway-Event`, `X-Gateway-Delivery` (ID estável para deduplicar), `X-Gateway-Timestamp` (Unix em segundos) e `X-Gateway-Signature: sha256=<HMAC-SHA256(segredo, timestamp + "." + corpo_bruto)>`. O receptor compara em tempo constante, recusa timestamp distante (>5 min) e deduplica pelo delivery ID. Até três tentativas, intervalos de 2 e 4 s (dependem do agendador); URL/DNS revalidados a cada tentativa, sem redirects, timeout total de 12 s. Entrega pelo menos uma vez. Eventos de transferência/encerramento do agente são enfileirados na transação do turno; capacidades usam `emitir`. Nenhum webhook externo cadastrado.

## 14. Consumo, métricas e auditoria

| Métodos | Rota | Status |
|---|---|---|
| GET | `/tenants/{tenant}/consumo?mes=AAAA-MM` | ✅ Contagem e avisos mensais |
| GET | `/tenants/{tenant}/metricas?agente=` | ✅ Contagem/erros/p50/p95 acumulados; bloco `botoes`: `{"enviados": {"botao", "lista"}, "em_texto": {"total", "por_motivo": {...}}, "clicados": {"atual", "antigo"}}`; bloco `picotadas`: `{"turnos_refeitos", "refazer_recusado": {...}, "atropelados", "mensagens_por_turno": {"media", "um", "dois", "tres_ou_mais"}, "espera_media_ms", "espera_p95_ms"}` |
| GET | `/tenants/{tenant}/auditoria` | ✅ Actor, ação, alvo, horário |
| GET | `/auditoria/plataforma` | ✅ Últimas 100 operações globais |

**Consumo:** devolve `mes`, `turnos`, `limite`, `agentes` (agente_id/turnos) e `avisos` (percentual/turnos/limite/criado_em). Mês no fuso do tenant; padrão mês atual. Contam todos os turnos registrados, inclusive sistema/erro/ignorados; não equivale a cobrança por tokens. `uso` do provedor é opcional e preservado quando contém contagens inteiras não negativas. Em 80% e 100%, um aviso persistente por tenant/mês/patamar vai para a auditoria e para webhooks ativos inscritos em `consumo.limite`. **Não corta atendimento.** `limite_turnos_mes=null` desativa os avisos; zero aciona ambos no primeiro turno; alterar o limite durante o mês não reenvia patamar já registrado. Postgres é a fonte da verdade; o Redis guarda contador reconstruível.

**Métricas:** `{ "turnos":6, "erros":0, "total_p50_ms":4200, "total_p95_ms":4300, "fila":{...} }`; sem turnos, percentis nulos. Contam só turnos concluídos. Único filtro: `agente`. Não enviar filtros de período/canal/unidade. `fila` traz os indicadores da empresa (abaixo); se o Redis não responder, `{"ligada":…, "indisponivel":true}` sem derrubar as métricas.

**Fila durável ✅ (indicadores; zerados enquanto `FILA_DURAVEL=off`):**

| Métodos | Rota | Status |
|---|---|---|
| GET | `/fila` | ✅ Plataforma: por empresa `aguardando`, `em_processamento`, `mais_antiga_s`; `agentes_em_andamento`; `cota_skyone` por URL (só 12 caracteres do hash, `uso`, `limite`, `percentual`); `workers_vivos`; `agendador_ativo` |
| GET | `/tenants/{tenant}/fila` | ✅ Só a empresa: `ligada`, `aguardando`, `em_processamento`, `mais_antiga_s`, `agentes_em_andamento`; tenant inexistente → 404 |

`/health` interno (sem cabeçalhos de proxy) devolve também `fila: {ligada, aguardando, mais_antiga_s, workers_vivos, agendador_ativo}`; pelo proxy público continua só `{"ok":true}`. Alertas na auditoria: `fila.atrasada` (empresa; mensagem esperando na fila > 30 s; agrupado a cada 10 min) e `skyone.cota_alta` (plataforma; URL acima de 80% por 5 min; agrupado a cada 30 min). Capacidade: `docs/CAPACIDADE.md`.

## 15. Benchmark administrativo (operação)

`scripts/bench.py` usa `--tenant`, `--agente`, `--panel-key` (ou a variável `PANEL_SERVICE_KEY`, preferível), `--clientes`, `--timeout`, `--canal` opcional e `--url`. Repita pares tenant/agente para rodar em paralelo; com mais de um canal API ativo para o agente, informe o canal de todos os pares. Exige agentes ativos **mock/dry**, piloto desligado e canal API ativo; não cria nem ativa nada. Envia duas mensagens por conversa e consulta o histórico; o relatório traz latências, erros e métricas acumuladas/delta. Saída diferente de zero em falha/timeout/erro de turno.

Dentro de `docker compose run ... gateway`, o nome `gateway` pode resolver para o próprio container temporário; use `--url http://gateway-gateway-1:8000`:

```bash
docker compose run --rm --no-deps -T -v /home/twadmin/gateway:/workspace:ro gateway \
  python /workspace/scripts/bench.py --url http://gateway-gateway-1:8000 \
  --tenant bykoji --agente bykoji_atendimento --clientes 4
```

## 16. Planejado para o painel ⏳

Não implementar telas dependentes destas rotas como se já estivessem disponíveis.

| Métodos | Rota | Status |
|---|---|---|
| GET | `/tenants/{tenant}/metricas/conversas` | ⏳ Fase 9 |
| GET | `/tenants/{tenant}/metricas/export.csv` | ⏳ Fase 9 |
| GET | `/metricas/plataforma` | ⏳ Fase 9 |
| POST | `/alertas/testar` | ⏳ Fase 9 |
| GET, POST | `/tenants/{tenant}/usuarios-internos` | ⏳ Fase 6.5 |
| PATCH, DELETE | `/tenants/{tenant}/usuarios-internos/{usuario}` | ⏳ Fase 6.5 |
| GET, POST | `/tenants/{tenant}/cardapio` | ⏳ Fase 4 |
| PATCH | `/tenants/{tenant}/cardapio/{item}` | ⏳ Fase 4 |
| POST | `/tenants/{tenant}/cardapio/{item}/em-falta` | ⏳ Fase 4 |
| POST | `/tenants/{tenant}/cardapio/importar` | ⏳ Fase 4; CSV |
| GET | `/tenants/{tenant}/cardapio/exportar` | ⏳ Fase 4; CSV |
| GET | `/tenants/{tenant}/pedidos` | ⏳ Fase 4 |
| GET | `/tenants/{tenant}/pedidos/{numero}` | ⏳ Fase 4 |
| POST | `/tenants/{tenant}/pedidos/{numero}/confirmar` | ⏳ Fase 4 |
| POST | `/tenants/{tenant}/pedidos/{numero}/recusar` | ⏳ Fase 4 |

Já listados nas seções acima como ⏳: callback do agente (adiado para antes da Fase 6.5), testar envio/status/QR code de canal (Fase 1) e webhooks de outros tipos de canal (6/6.5/7). O contrato das ferramentas de pedidos/carrinho entra na Fase 4 em `docs/SKILLS.md`. Métricas comparativas, LGPD de retenção, circuit breaker completo e usuários internos não devem ser inferidos das tabelas futuras.
