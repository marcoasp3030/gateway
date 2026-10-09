# API administrativa — contrato com o painel

Banco: `gateway`, Postgres próprio do gateway. O backend do painel autentica seus usuários e aplica papéis/tenant; a chave de serviço tem acesso administrativo global. O navegador nunca recebe essa chave nem acessa o banco. ✅ indica código implementado; ⏳ indica contrato planejado, **não disponível**. **Publicação atual:** complementos e migrações 001–011 implantados em 09/10/2026, mantendo mock/dry. Consulte `PROGRESS.md`.

## Acesso interno e autenticação

Base no host: `http://127.0.0.1:8100/admin/v1`. **Proposta para o backend em container**, dependente da confirmação do Marco para alterar o Compose do painel: conectar somente esse backend também à rede externa Docker `luxbrain_gateway_proxy` e usar **`http://luxbrain-gateway:8000/admin/v1`**. `luxbrain-gateway` já é o alias do gateway nessa rede; não depende de publicar portas adicionais. Não utilizar IP do host: a porta 8100 está ligada apenas ao loopback. Manter as redes atuais do backend e não conectar bancos/Redis à rede proxy.

Exemplo a incorporar ao Compose do painel somente após aprovação (substituir `backend` pelo serviço correto e preservar suas redes):

```yaml
services:
  backend:
    networks:
      - default
      - gateway_proxy
networks:
  gateway_proxy:
    external: true
    name: luxbrain_gateway_proxy
```

O backend envia `Authorization: Bearer <PANEL_SERVICE_KEY>` e, em toda escrita, `X-Actor: <usuário autenticado>`. Não encaminhar headers do proxy/navegador nessa chamada interna. SEG02 aceita IP privado da rede Docker e recusa `/admin` com `X-Forwarded-For`, `X-Real-IP` ou `Forwarded`, mesmo vazios/forjados e com chave válida. O Caddy não publica `/gateway/admin/*`. A rede privada **não dispensa autenticação**.

Teste em 08/10/2026: container temporário na rede existente, sem servidor, GET autenticado no alias → **200**; cada um dos três headers acima → **404**. Compose do painel não foi alterado. Documentação interativa e OpenAPI estão desligados (SEG08); não existe `/admin/v1/docs`.

Rotação SEG12: gateway aceita `PANEL_SERVICE_KEY` e `PANEL_SERVICE_KEY_NEXT`; log indica `atual`/`proxima`, nunca o valor. Recriar apenas gateway ao mudar variáveis; depois de migrar o backend, remover a chave antiga.

## Convenções

JSON UTF-8; timestamps ISO 8601 com fuso. IDs de tenant: `^[a-z0-9_]{3,40}$`, imutáveis. IDs de conversa: `<tenant>:<canal_id>:<identificador>`, codificados como segmento de URL. O painel deve validar permissão sobre o tenant antes de fazer a chamada.

Listas de tenants: `limite` 1–100 (padrão 50); agentes/canais/chaves: 1–99; cursor por ID. Resposta `{ "itens": [], "proximo_cursor": null }`. Histórico atual: últimos 100 itens em ordem cronológica, sem paginação; auditoria: limite 1–100 e cursor, resposta `{ "itens": [] }`. Não presumir filtros/paginação onde não documentados.

Erro: `{ "erro": { "codigo": "NOT_FOUND", "mensagem": "NOT_FOUND", "detalhes": {} } }`. Middlewares de limite/rede podem devolver `{ "detail": "..." }`.

| HTTP | Significado |
|---|---|
| 400 | Validação inválida; códigos/textos ainda não formam enum estável |
| 401 | Credencial ausente, inválida, vencida ou revogada |
| 403 | Escopo ou origem IP não permitido |
| 404 | Recurso indisponível nesse tenant/agente, rota ausente ou proteção SEG02 |
| 409 | Conflito de integridade |
| 422 | Regra de negócio (planejada nas operações que a exigirem) |
| 429 | Limite de requisições; observar `Retry-After` |
| 501 | Tipo de canal/triagem ainda não implementado |
| 502 | Falha upstream nas operações que a documentarem |
| 503 | Banco/Redis/credencial criptografada indisponível |

## Rotas administrativas

Todas as rotas desta seção são relativas a `/admin/v1`. Escritas confirmadas gravam auditoria na mesma transação, sem corpos/segredos. Suspender/pausar hoje usa PATCH, não uma rota de ação.

| Métodos | Rota | Status / comportamento |
|---|---|---|
| GET, POST | `/tenants` | ✅ Lista; cria tenant e config |
| GET, PATCH | `/tenants/{tenant}` | ✅ Detalhe/edição; `status=ativo|suspenso` |
| GET, PUT | `/tenants/{tenant}/config` | ✅ Padrões de comportamento |
| GET, POST | `/tenants/{tenant}/agentes` | ✅ Lista/cria a partir de modelo habilitado |
| GET, PATCH | `/tenants/{tenant}/agentes/{agente}` | ✅ Detalhe/config efetiva; `status=rascunho|ativo|pausado` |
| POST | `/tenants/{tenant}/agentes/{agente}/simular` | ✅ Pipeline mock/dry; corpo abaixo |
| POST | `/tenants/{tenant}/agentes/{agente}/testar` | ⏳ Fase 0.5; ainda não implementada, sem corpo real aceito |
| POST | `/tenants/{tenant}/agentes/{agente}/circuito/fechar` | ⏳ Fase 0.6 |
| GET, POST | `/tenants/{tenant}/canais` | ✅ Lista/cria; segredo mostrado uma vez |
| GET, PATCH | `/tenants/{tenant}/canais/{canal}` | ✅ Detalhe/edição |
| POST | `/tenants/{tenant}/canais/{canal}/rotacionar-segredo` | ⏳ Fase 0.5 |
| POST | `/tenants/{tenant}/canais/{canal}/testar-envio` | ⏳ Fase 1; contrato depende do provedor |
| GET | `/tenants/{tenant}/canais/{canal}/status` | ⏳ Fase 1; depende do provedor |
| GET | `/tenants/{tenant}/canais/{canal}/qrcode` | ⏳ Fase 1; depende do provedor |
| GET, POST | `/tenants/{tenant}/api-keys` | ✅ Lista/cria |
| GET, PATCH, DELETE | `/tenants/{tenant}/api-keys/{chave}` | ✅ Detalhe/edição/revogação |
| GET | `/tenants/{tenant}/auditoria` | ✅ Actor, ação, alvo, horário |
| GET | `/tenants/{tenant}/conversas/{conv}/mensagens` | ✅ Últimas 100 mensagens |
| GET | `/tenants/{tenant}/metricas?agente=` | ✅ Contagem/erros/p50/p95, acumulados |
| GET | `/modulos` | ✅ Registro/configuração de módulos |
| GET | `/tenants/{tenant}/modulos` | ✅ Registro/configuração de módulos |
| PUT | `/tenants/{tenant}/modulos/{modulo}` | ✅ Registro/configuração de módulos |
| GET, POST | `/modelos-agente` | ✅ Lista/cria versões |
| GET, PATCH | `/modelos-agente/{modelo}` | ✅ Detalhe/PATCH cria nova versão |
| GET, POST | `/tenants/{tenant}/unidades` | ✅ Gestão e horários locais |
| GET, PATCH, DELETE | `/tenants/{tenant}/unidades/{unidade}` | ✅ Gestão e horários locais |
| GET, POST | `/tenants/{tenant}/webhooks-saida` | ✅ Outbox assinada, escopo do tenant |
| GET, PATCH, DELETE | `/tenants/{tenant}/webhooks-saida/{webhook}` | ✅ Outbox assinada, escopo do tenant |
| POST | `/tenants/{tenant}/webhooks-saida/{webhook}/testar` | ✅ Outbox assinada, escopo do tenant |
| GET | `/tenants/{tenant}/webhooks-saida/{webhook}/entregas` | ✅ Outbox assinada, escopo do tenant |
| GET | `/tenants/{tenant}/consumo?mes=AAAA-MM` | ✅ Contagem e avisos mensais |
| GET | `/tenants/{tenant}/contatos?busca=` | ✅ LGPD03 |
| GET | `/tenants/{tenant}/contatos/{contato}/exportar` | ✅ LGPD03; exige actor, registra auditoria |
| DELETE | `/tenants/{tenant}/contatos/{contato}` | ✅ LGPD03; anonimização auditada |

### Tenants e configuração

Criação: `{ "id":"bykoji", "nome":"By Koji", "plano":"pro", "limite_turnos_mes":20000, "timezone":"America/Sao_Paulo" }`. PATCH também aceita `retencao_dias`, `tenant_teste`, `status`. **`tenant_teste` pertence ao tenant**, não à config. Não há consumo embutido na lista atual.

Comportamento: `debounce_s` (1–15), `max_wait_s` (≥ debounce, ≤30), `media_wait_s` (0–60), `history_turns` (0–100), `pause_minutes`, `slow_notice_s`, `aviso_demora`, `fallback`, `alerta_telefone`, `send_mode=dry|live`, `agente_mode=mock|live`, `modo_piloto`, `contatos_permitidos`, `encerrar_vazia_min`, `max_lotes_simultaneos`, `max_concorrencia`, `skyone_dominios_permitidos`. Telefones: 8–16 dígitos. Valores nulos de comportamento herdam; omitir em PATCH preserva. PUT config atualiza somente os campos enviados. Cache de 30 s, invalidado por escritas da API.

### Agentes e credenciais

Criação: `{ "modelo_id":"assistente_generico", "nome":"Assistente" }`. Padrão rascunho, provedor mock. Detalhe retorna `configuracao_efetiva` com `origem_config` (plataforma/modelo/tenant/agente). A lista não inclui estado de circuito nem canais agregados.

Exemplo de configuração Skyone (não ativa live):

```json
{
  "provedor": "skyone",
  "agente_mode": "mock",
  "send_mode": "dry",
  "skyone_estrategia": "por_modelo",
  "skyone_webhook_url": "https://luxtia.api.integrasky.cloud/FLUXO",
  "skyone_auth_tipo": "cabecalho",
  "skyone_auth_cabecalho": "X-Credencial-EXEMPLO",
  "skyone_auth": "<somente escrita; formato real pendente do Marco>",
  "timeout_s": 90,
  "instrucoes_agente": "Instruções complementares",
  "modo_resposta": "sync"
}
```

`provedor=mock|skyone`; `llm_direto` reservado à Fase 2.5. `timeout_s` 1–600, padrão 90. Só sync aceito; async/callback na 0.6. `skyone_auth_tipo=nenhuma|cabecalho|bearer`; leitura retorna `skyone_auth_configurado`, nunca valor/ciphertext. `skyone_token` e `callback_timeout_s` **não são aceitos**. Live sem credencial é recusado fora de tenant de teste (SEG13). O fluxo Skyone deve ser **criado do zero pelo Marco**, nunca clonado/duplicado; gateway não cria fluxos.

Simulação real:

```json
{ "canal_id":"can_...", "identificador":"marco", "texto":"Olá", "message_id":"opcional-id-unico", "enviada_em":"2026-10-08T15:00:00-03:00" }
```

Canal API ativo ligado ao agente, mock/dry. Retorno `{ "ok":true, "duplicada":false, "conversation_id":"tenant:can_...:marco", "mensagem_id":123 }`; resposta consultada no histórico. Piloto e status são respeitados. `/testar` ainda não existe: o painel não deve enviar o antigo exemplo `{mensagem:...}` esperando resposta.

### Canais e chaves

Canal: `{ "tipo":"api", "nome":"Diagnóstico", "agente_id":"ag_...", "ativo":false, "publico":"clientes" }`. Tipos cadastráveis: uazapi, meta, site, email, teams, luxbrain, api. Somente api/uazapi têm recepção nesta etapa; uazapi ainda exige fixtures reais. Canal interno cadastrado não significa autenticação de funcionários implementada (Fase 6.5). Triagem não é aceita pela API mínima. Canais iniciam inativos se `ativo` omitido.

Credenciais de canal: objeto `credenciais`, por exemplo `{ "base_url":"https://instancia.uazapi.com", "token":"..." }`, cifrado por tenant. Campo omitido preserva; leitura devolve `credenciais_configurado`. Na criação, `webhook_url` inclui segredo e aparece uma vez: `https://painel.luxbrain.com.br/gateway/webhooks/api/can_...?s=...`. Proteger esse retorno como credencial.

Chave: `{ "nome":"integracao", "escopos":["conversar","eventos:ler"], "agente_id":"ag_...", "expira_em":"2027-10-01T00:00:00Z", "ips_permitidos":["200.10.20.0/24"] }`. Criação retorna `chave` uma vez; banco guarda hash com pepper. PATCH aceita nome/escopos/agente/validade/IPs; DELETE revoga. Restrição de agente opcional, sempre dentro do tenant. `ferramentas:<modulo>` depende da capacidade habilitada; `callback` pode ser cadastrado, mas sua rota permanece planejada.

URLs configuráveis: HTTPS, 443, sem credenciais na URL, destinos privados/locais recusados ao salvar; DNS revalidado antes da chamada, sem redirects. Limite de corpo 2 MiB e token bucket por IP em `/api` e `/webhooks`. Limites por chave administrativa/tenant não estão implementados.

### Métricas atuais

`{ "turnos":6, "erros":0, "total_p50_ms":4200, "total_p95_ms":4300 }`. Sem turnos, percentis nulos. Único filtro atual: `agente`. Não enviar filtros de período/canal/unidade supondo que serão aplicados.

## Integração e recepção

Rotas absolutas; API usa `X-API-Key`, nunca chave do painel. Webhooks usam segredo do canal.

| Método | Rota | Status / autenticação |
|---|---|---|
| POST | `/webhooks/api/{canal}?s=...` | ✅ `{identificador,texto,message_id?,enviada_em?}` |
| POST | `/webhooks/uazapi/{canal}?s=...` | ✅ Normalizador herdado; fixtures reais pendentes na Fase 1 |
| POST | `/webhooks/{outro_tipo}/{canal}?s=...` | ⏳ Fases 6/6.5/7; tipo cadastrado não implementado → 501 após autenticação |
| POST | `/api/v1/conversas/mensagens` | ✅ Corpo da simulação; escopo `conversar` |
| GET | `/api/v1/conversas/{conv}/mensagens` | ✅ Escopo `eventos:ler`; últimos 100 itens |
| POST | `/api/v1/conversas/{conv}/notificar` | ✅ Escopo `notificar`; resposta 202 |
| POST | `/api/v1/agentes/callback/{request_id}` | ⏳ Fase 0.6; escopo `callback` |

Canal inativo, agente pausado/rascunho ou tenant suspenso: recepção retorna 200 ignorado após autenticação. Mensagens de clientes são gravadas antes de agendar. Histórico e dados são filtrados por tenant e restrição de agente da chave; acesso cruzado → 404. `/notificar`: `{ "evento":"pedido_saiu", "dados":{"pedido":"K-1042"}, "instrucao":"Avise o cliente" }`, respeitando pausa humana.

## Rotas planejadas para o painel

Base `/admin/v1`. Não implementar telas dependentes destas rotas como se já estivessem disponíveis.

| Métodos | Rota | Status |
|---|---|---|
| GET | `/tenants/{tenant}/conversas` | ⏳ Fase 0.5; lista/filtros |
| POST | `/tenants/{tenant}/conversas/{conv}/assumir` | ⏳ Fase 0.5; `{minutos:60}` |
| POST | `/tenants/{tenant}/conversas/{conv}/devolver` | ⏳ Fase 0.5 |
| POST | `/tenants/{tenant}/conversas/{conv}/mensagens` | ⏳ Fase 0.5; mensagem humana |
| GET | `/tenants/{tenant}/eventos?desde=` | ⏳ Fase 0.5; polling |
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

O contrato das ferramentas de pedidos/carrinho entra na Fase 4 em `docs/SKILLS.md`. Métricas comparativas, LGPD de retenção, circuit breaker e usuários internos não devem ser inferidos das tabelas futuras. A revisão deste contrato com o Marco precede as telas do painel.

## Módulos implementados

`GET /modulos` retorna nome, migrações, rotas e escopos. Registro atual: `catalogo` (busca/contexto) e `pedidos` (interface reservada, sem ferramentas até a Fase 4). PUT do módulo exige `{ "ativo":true, "config":{} }`; altera apenas o tenant indicado.

| Método | Rota absoluta | Status |
|---|---|---|
| GET | `/api/v1/catalogo/busca?conversation_id=&q=` | ✅ Escopo `ferramentas:catalogo`, conversa do tenant/agente, módulo habilitado no tenant e no modelo. Até 50 itens ativos. |

Módulo desabilitado retorna 404; preço vem do Postgres. O pacote ao agente contém `contexto_modulos` somente de capacidades habilitadas no tenant e no modelo. Migração 006 renomeia a tabela legada para `catalogo_itens` sem apagar dados.

## Modelos versionados

POST exige `id`, `nome`; aceita `tenant_id` opcional (ausente/nulo = global), `tipo`, `modulos`, `comportamento_padrao`, `desfechos`, `followups_padrao` e `skyone_fluxo_modelo` (referência de fluxo criado do zero). GET de lista/detalhe usa `?tenant=` para incluir modelos privados; detalhe aceita `?versao=`, padrão última. PATCH com o mesmo escopo cria nova versão, sem editar anteriores; ID/tenant são imutáveis. Agentes continuam na versão fixada até PATCH explícito de `modelo_versao`. Modelos globais usam auditoria de plataforma (tenant nulo), privados usam auditoria do proprietário.

| Método | Rota administrativa | Status |
|---|---|---|
| GET | `/auditoria/plataforma` | ✅ Últimas 100 operações globais; chave de serviço |

## Webhooks de saída

POST exige `url`; aceita `eventos` (lista de nomes) e `ativo` (padrão false). Retorna `segredo` uma vez; leituras nunca retornam credencial/ciphertext. PATCH altera URL/eventos/ativo; DELETE desativa preservando entregas e auditoria. `/testar` exige webhook ativo e retorna 202 `{ "entrega_id":123, "status":"pendente" }`; `/entregas` retorna as últimas 100 tentativas consolidadas, sem corpo nem segredo.

Corpo assinado: `{ "evento":"conversa.transferida", "tenant_id":"bykoji", "dados":{...} }`. Headers: `X-Gateway-Event`, `X-Gateway-Delivery` (ID estável para deduplicar), `X-Gateway-Timestamp` (Unix em segundos) e `X-Gateway-Signature: sha256=<HMAC-SHA256(segredo, timestamp + "." + corpo_bruto)>`. Receptor deve comparar assinatura em tempo constante, recusar timestamp distante (>5 min) e deduplicar delivery ID. Três tentativas no máximo, intervalos de 2 e 4 segundos após as falhas (execução depende do agendador); URL/DNS revalidados em cada tentativa, sem redirects, timeout total de 12 s. Entrega é pelo menos uma vez: uma falha após o receptor aceitar pode provocar repetição.

Eventos de transferência/encerramento retornados pelo agente são enfileirados na transação do turno; capacidades usam a função `emitir`. Isso não antecipa toda a semântica de encerramento da Fase 3. Entregas usam tarefa separada do processamento de conversas. Nenhum webhook externo foi cadastrado/ativado nesta implementação; os testes usam receptor local em memória.

## Notificações na voz do agente

`POST /api/v1/conversas/{conv}/notificar` exige `evento` (1–100 caracteres), `instrucao` (1–20000), `dados` opcional (objeto). Retorna **202** `{ "ok":true, "mensagem_id":123, "status":"enfileirada" }`, não uma confirmação de entrega. Cria entrada persistente de origem `sistema`; o agendador chama o agente com `tarefa=notificar` e `evento`, sem misturar esse evento a mensagens do cliente. Turno registra origem, resultado e uso opcional do provedor. Consultar histórico/métricas depois.

Chave deve ter escopo `notificar` e pertencer ao tenant/agente da conversa. Pausa humana, piloto e status são reavaliados antes da chamada e envio: notificação bloqueada fica em turno ignorado e gera auditoria `notificacao.nao_enviada`, sem resposta automática. Alertas externos para atendentes dependem dos canais da Fase 9. Campos de segredo conhecidos nos dados estruturados são mascarados; não colocar credenciais em texto livre. Escrita registra o ID da chave como actor. Meta/janela de 24 h permanecem na Fase 7.

## Consumo mensal

`GET /tenants/{tenant}/consumo?mes=AAAA-MM` retorna `mes`, `turnos`, `limite`, `agentes` (agente_id/turnos) e `avisos` (percentual/turnos/limite/criado_em). Mês é calculado no fuso do tenant; padrão mês atual. Contam todos os turnos registrados, inclusive sistema/erro/ignorados; não equivale a cobrança por tokens. `uso` do provedor é opcional e preservado no turno quando contém contagens inteiras não negativas.

Ao alcançar 80% e 100%, um aviso persistente por tenant/mês/patamar é registrado na auditoria e enfileirado para webhooks ativos inscritos em `consumo.limite`. **Não corta atendimento**. `limite_turnos_mes=null` desativa os avisos; zero aciona ambos no primeiro turno. Alterar limite durante o mês não reenvia patamar já registrado. Postgres é fonte da verdade, Redis guarda contador reconstruível; falha do cache não corta respostas. A API consulta Postgres. Entrega a WhatsApp/e-mail interno depende dos canais de alertas da Fase 9.

## Unidades e horários

POST de unidade exige `nome`; aceita `telefone_whatsapp` (8–16 dígitos), `ativo` (padrão true) e `horarios`, por exemplo `{ "seg":[["11:30","15:00"],["18:00","23:00"]], "dom":[] }`. Dias: seg/ter/qua/qui/sex/sab/dom; HH:MM de 00:00 a 23:59, até 12 faixas por dia, início e fim distintos. Fim anterior ao início indica funcionamento pela madrugada seguinte. Horários ausentes significam fechado, não 24 horas. PATCH altera campos enviados; DELETE desativa preservando histórico/auditoria.

Pacote ao agente inclui somente unidades ativas do tenant, com `id`, `nome`, `aberta_agora` e `proxima_abertura` ISO no fuso do tenant (nulo se aberta ou sem próxima faixa). O instante de fechamento já conta como fechado. Nenhum horário comercial real foi presumido/cadastrado na implantação.

## Dados do contato (LGPD03)

Contatos são vinculados às conversas na recepção; migração 011 vincula conversas existentes sem descartar dados. Busca retorna até 100 contatos do tenant, por nome/telefone/e-mail. Exportação exige `X-Actor` mesmo sendo GET e devolve contato, conversas, mensagens, turnos, brutos e entregas associadas, auditoria e `pedidos:[]` (módulo de pedidos completo ainda na Fase 4); exportação é auditada.

DELETE exige corpo `{ "confirmacao":"<telefone ou e-mail atual>" }`. Faz **anonimização**, não remoção de métricas: apaga telefone/e-mail/nome/perfil/resumo, substitui conteúdo das mensagens, retira resposta/erro dos turnos, troca IDs de conversa que continham identificadores pessoais por IDs aleatórios, limpa payloads brutos associados e entregas que carregam esses identificadores, cancela entregas pendentes e retira agendamentos Redis antigos. Auditoria mantém ação/horário, com identificadores pessoais substituídos. Retorno `{ "ok":true, "anonimizado":true }`. Repetição em contato já anonimizado → 409.

Conversa em processamento → 409, sem alterações parciais; repetir após terminar. Verificações de referências entre tenants continuam imediatas normalmente; só a transação de anonimização as adia e verifica antes de concluir. Outro tenant permanece intacto, mesmo contendo o mesmo telefone. Mensagens futuras constituem novo contato/atendimento, não recuperam histórico apagado. Payloads antigos sem vínculo são localizados pelos identificadores conhecidos, inclusive telefone formatado; fixtures reais dos provedores ainda são necessárias na Fase 1 para validar formatos novos. Backups e dados já entregues a terceiros não são alterados por esta API; retenção/restauração é tratada na Fase 9.

## Benchmark administrativo

`scripts/bench.py` usa `--tenant`, `--agente`, `--panel-key` (ou variável `PANEL_SERVICE_KEY`), `--clientes`, `--timeout` e `--canal` opcional. Repita pares tenant/agente para execução simultânea; se houver mais de um canal API ativo para o agente, informe canal para todos os pares. Exemplo sem chave em argumentos:

```bash
python scripts/bench.py --tenant bykoji --agente bykoji_atendimento \
  --tenant bench_passo5 --agente ag_DO_TENANT_TESTE --clientes 2
```

Exige agentes ativos mock/dry, piloto desligado e canais API existentes. Não cria nem ativa tenants/agentes. Envia duas mensagens por conversa e consulta o histórico; relatório traz latências observadas, erros e métricas acumuladas/delta de cada agente. Exit diferente de zero em falha/timeout/erro de turno. Não usa mais `/test/*`, `/metricas` global ou `--admin-key`. Configure URL interna; a chave nunca deve chegar ao navegador.
