# Progresso do gateway

## B1 — clique Apple (`ButtonsResponseMessage`) — 2026-10-20

- Motivo: em 16/10 uma cliente real do By Koji tocou Entrega/Retirada 4 vezes; o uazapi mandou `ButtonsResponseMessage` (típico de iPhone) e o gateway só lia `TemplateButtonReplyMessage` → mensagem `[não foi possível ler ButtonsResponseMessage…]` e o agente pediu texto de novo.
- Correção: `CLIQUES_DE_BOTAO` inclui `ButtonsResponseMessage`; título também em `content.Response.SelectedDisplayText`; id também em `selectedButtonID`. Fixture `clique_botao_apple.json`; teste em `tests/test_botoes.py`.
- Publicação: tag de volta `gateway:volta-buttons-apple-1e7c6b6` (`9f31b408fd7d`); imagem nova `gateway:buttons-apple-20261020`. `docker compose up -d --build gateway worker agendador`. Health OK; `CLIQUES_DE_BOTAO` no container inclui `ButtonsResponseMessage`. Dados preservados (mesmo volume do Postgres).

## Fase 0 — Implantação em modo simulado — 2026-10-08

- Status: concluída.
- Feito:
  - Analisados README, GUIA_CURSOR, PLANO_CURSOR, regras do projeto, contrato administrativo e código existente.
  - Criado `.gitignore`, incluindo segredos, ambiente virtual e caches.
  - Criado `.env` local com permissão 600, senhas/chaves aleatórias e chave no formato Fernet. Valores não versionados.
  - Mantidos `SKYONE_MODE=mock`, `SEND_MODE=dry` e documentação interativa desligada.
  - Criado banco **bykoji_test** no Postgres próprio deste Compose. O benchmark grava apenas nesse ambiente de teste; o banco do painel não foi usado.
  - Subidos gateway, Postgres e Redis com volumes próprios. Gateway em `127.0.0.1:8100`, Postgres em `127.0.0.1:5433`; Redis sem porta publicada.
- Validações (comando → resultado):
  - `docker --version` → 29.1.3; `docker compose version` → 2.40.3.
  - `docker compose up -d --build` → concluído; `docker compose ps` → gateway ativo, Postgres e Redis saudáveis.
  - `curl --fail --silent http://127.0.0.1:8100/health` → `{"ok":true,"agente":"mock","envio":"dry"}`.
  - `tests/test_seguranca.py` em Python 3.12 → **26 passed**.
  - `scripts/bench.py`, 5 clientes, executado dentro do gateway via stdin, com chave lida do ambiente do container → **5/5 respondidos**, **0 erros**.
  - `docker compose exec -T gateway id` → uid 10001, sem root.
  - `/docs` → 404; `/metricas` sem chave → 401; `/metricas` com chave correta e `X-Forwarded-For` → 404.
  - Webhook sem segredo → 401; webhook com segredo e corpo vazio `{}` → 200, ignorado; corpo de 3 MB via curl → 413.
  - `/health` com `X-Forwarded-For` → somente `{"ok":true}`.
  - Busca das chaves locais nos logs do gateway após webhook autenticado → zero ocorrências.
  - `.env` com permissão 600 e ignorado pelo Git.
  - `docker compose exec -T -e ADMIN_KEY=troque-x gateway python -c 'import main'` → recusado com exit 1, como esperado; processo em execução preservado.
- Métricas da primeira execução, 5 turnos:
  - Respostas observadas pelo benchmark: p50 8,0 s, máximo 8,9 s.
  - Servidor: total p50 7.915 ms, p95 8.641,2 ms, máximo 8.659 ms.
  - Agente simulado: p50 3.812 ms, p95 4.505,4 ms; espera p50 4.101 ms.
  - Média de 2,2 mensagens por lote; zero erros e zero avisos de demora.
  - Estes números incluem debounce de 4 s e mock de 2–5 s; não medem a latência real do Skyone.
- Ajustes ao ambiente:
  - O Python 3.10 do host não tem `ensurepip/python3-venv`. Testes executados em venv temporário dentro de container Python 3.12, sem instalar pacotes no host.
  - O proxy existente é **Caddy**, no projeto `painel_koji`, ocupando 80/443. Nginx não está disponível no PATH e `sudo -n` exige senha. A configuração Nginx do roteiro não deve ser aplicada diretamente nesse ambiente.
  - Nenhum serviço do painel foi alterado. Não houve publicação externa, conexão ao Skyone ou envio ao WhatsApp.

### Reproduzir os testes unitários

```bash
cd /home/twadmin/gateway
docker run --rm --user "$(id -u):$(id -g)" \
  -v /home/twadmin/gateway:/workspace:ro -w /workspace python:3.12-slim \
  sh -c 'python -m venv /tmp/test-venv && /tmp/test-venv/bin/pip -q install pytest && /tmp/test-venv/bin/python -m pytest -q -p no:cacheprovider tests/test_seguranca.py'
```

### Reproduzir o benchmark sem expor a chave no comando

```bash
cd /home/twadmin/gateway
python3 - <<'PY'
from pathlib import Path
import subprocess
source = '''import os, sys
from urllib.parse import urlsplit
assert urlsplit(os.environ["DATABASE_URL"]).path.endswith("_test")
sys.argv = ["bench.py", "--url", "http://127.0.0.1:8000",
            "--admin-key", os.environ["ADMIN_KEY"], "--clientes", "5"]
''' + Path('scripts/bench.py').read_text()
subprocess.run(['docker', 'compose', 'exec', '-T', 'gateway', 'python', '-'],
               input=source, text=True, check=True)
PY
```

## Análise para as próximas fases

- O código atual implementa somente a base da Fase 0. `docs/API_ADMIN.md` descreve o contrato futuro: `/admin/v1`, tenants, modelos, agentes, permissões e auditoria ainda não estão implementados.
- **Próxima fase: 0.3, descobertas no Skyone.** Preparar o probe e validar instruções por payload, API de criação/clonagem, timeout síncrono e callback. O checkpoint do PLANO_CURSOR exige resultados reais do Marco antes de escolher a estratégia da Fase 0.5. Nenhuma conclusão sobre essas capacidades foi presumida.
- **Fase 0.5:** já existe Postgres no painel (`painel_koji-banco-1`). Definir a ligação de rede e banco/usuário separados para o gateway conforme o plano, preservando o banco de teste. Revisar o contrato antes de construir as telas.
- **Fase 1:** adaptar a publicação ao Caddy existente, mantendo rotas administrativas internas; definir domínio e capturar payloads reais da instância uazapi de teste.
- Pontos encontrados no código, a tratar antes do uso real nas fases correspondentes:
  - A validação DNS `validar_url_saida` existe, mas ainda não é chamada pelos adaptadores Skyone/uazapi antes das requisições (Fase 0.5).
  - O limite de corpo atual depende de `Content-Length`; falta limitar a leitura de corpos sem esse cabeçalho.
  - O buffer é retirado antes de concluir o processamento; ainda não há recuperação durável dos lotes em execução (Fase 0.6).
  - A pausa humana é consultada antes da chamada ao agente, sem nova checagem antes do envio (INV03, Fase 3).
  - O adaptador uazapi registra HTTP de erro, mas não o propaga ao pipeline; precisa evitar registrar entrega bem-sucedida em envio recusado.
  - Tokens presentes em payloads de terceiros ainda não são sanitizados antes de gravar `webhook_raw`; validar com fixtures reais antes de conectar o provedor.
- A aprovação da Fase 0 confirma a simulação e as verificações descritas acima; as garantias de produção e os demais invariantes continuam pendentes das fases seguintes.

## Publicação no domínio existente — 2026-10-08

- Status: concluída a publicação; Fase 1 completa ainda depende das fases anteriores e de payloads reais do uazapi.
- Escopo: publicação antecipada por solicitação explícita do Marco, preservando as rotas do painel. A plataforma multiempresa continua pendente.
- Feito:
  - Gateway acessível em `https://painel.luxbrain.com.br/gateway/health`; `/gateway/` redireciona para esse endereço.
  - Corrigida a emissão do certificado do domínio: o DNS atual aponta para `177.67.71.211`; a tentativa antiga usava `177.67.71.227`. Caddy recarregado com Let's Encrypt explícito; certificado validado, vencimento 2027-01-06.
  - Rede dedicada `luxbrain_gateway_proxy`, com proxy e gateway. Postgres/Redis não foram conectados a essa rede.
  - Complemento `docker-compose.proxy.yml`, selecionado pelo `COMPOSE_FILE` no `.env` local. O Compose do painel também preserva sua participação na rede.
  - Adicionados apenas os caminhos `/gateway` ao Caddy, além da correção TLS e filtros de segredos nos logs. Rotas do painel, autenticação e dados mantidas.
  - Limite real de corpo, inclusive chunked, e token bucket por IP no Redis. API 10/s com capacidade 20; webhooks 30/s com capacidade 60.
  - Operação, referências, acesso interno e desativação da publicação documentados em `docs/PUBLICACAO.md`; configuração de referência em `docs/Caddyfile.painel.example`.
- Validações:
  - `caddy validate` antes de `caddy reload` → configuração válida; reload concluído sem recriar os serviços do painel.
  - HTTPS com verificação de certificado, consultado pelo hostname a partir da VPS → `/`, `/auth/v1/health` e `/gateway/health` retornaram 200.
  - `/gateway/health` → somente `{"ok":true}`, inclusive tentando forjar headers de origem local.
  - Rotas administrativas, testes, documentação e tentativas de travessia `/gateway/api/../metricas` e versão codificada → 404, mesmo com chave administrativa.
  - API de categorias sem/com chave → 401/200; webhook vazio sem/com segredo → 401/200.
  - Corpo de 3 MiB por HTTPS com Content-Length e com chunked → 413 nos dois casos.
  - Token bucket no Redis: 50 chamadas concorrentes com identificador sintético → 20 aceitas e 30 recusadas.
  - Rajada pela conexão HTTPS reutilizada: 60 consultas sem chave → 22 respostas 401 e 38 respostas 429. A tentativa inicial com várias conexões TLS novas sofreu EOF no transporte; a validação foi repetida usando keep-alive.
  - Chaves administrativa, de integração e de webhook → nenhuma ocorrência nos logs do gateway/Caddy após chamadas autenticadas.
  - Testes de segurança e limites antes da publicação → 33 aprovados; suíte ampliada com probe → 38 aprovados.
  - Benchmark após publicação → 5/5 respondidos, p50 observado 7,4 s, máximo 9,2 s. Métricas acumuladas de 10 turnos: zero erros, total p95 8.817,95 ms. Continua medindo mock/dry.
- Arquivos do painel alterados: `docker/Caddyfile`, `docker-compose.yml` e registro em `instrucoes.md`. Não foram incluídas em commit deste repositório as alterações preexistentes do painel.
- Pendências: conectar provedores reais somente após as etapas do plano; não existe interface de gestão própria no gateway nem `/admin/v1` implementado.

## Fase 0.3 — Descobertas no Skyone — 2026-10-08

- Status: em andamento; parte local concluída, validação externa pendente.
- Feito:
  - Criado `scripts/skyone_probe.py`: dois payloads que diferem apenas em `instrucoes_agente`, medição de duração/status e casos de tempo de 60/120/300 s.
  - Credenciais por ambiente (`SKYONE_PROBE_URL`/`SKYONE_PROBE_TOKEN`), prévia sem rede, proteção de URL de saída, redirects recusados, tamanho de resposta limitado e relatório sanitizado.
  - Criado `docs/SKYONE.md` com instruções de configuração, comandos e tabela de decisões ainda sem respostas presumidas.
  - Mantidos `SKYONE_MODE=mock`, `SEND_MODE=dry` e banco `bykoji_test`.
- Validações:
  - `docker compose run --rm --no-deps -v /home/twadmin/gateway:/workspace:ro gateway python /workspace/scripts/skyone_probe.py --dry-run` → dois payloads corretos com token mascarado, sem chamada externa.
  - Suíte `pytest -q -p no:cacheprovider tests` em venv temporário baseado na imagem do gateway → **38 passed**. Inclui diferenças dos payloads, sanitização, redirect recusado, URL privada recusada e timeout registrado sem URL/token.
- Pendências / decisões para o Marco:
  - Criar ou indicar fluxo de diagnóstico e configurar URL/token no `.env` da VPS. Nenhuma URL do Skyone estava configurada nesta execução.
  - Confirmar parametrização de instruções/base/Skills; disponibilidade de API de criação/clonagem; resultado de timeout e callback.
  - Conforme o checkpoint da Fase 0.3 em `PLANO_CURSOR.md`, essas evidências definem a estratégia antes de iniciar a Fase 0.5. Não foi declarada conclusão dessa fase.


## Fase 0.3 — Probe no fluxo validado — 2026-10-08

- Status: parte autorizada concluída; fase continua pendente dos testes do Marco. Este registro atualiza as pendências históricas acima, sem reescrevê-las.
- Feito:
  - Adaptado `scripts/skyone_probe.py` aos cinco campos validados, sem token no corpo; leitura de `resposta`, `request_id`, `acao` e validação dos identificadores/status/tipo.
  - Adicionado `--caso latencia` com três chamadas sequenciais e sessões sintéticas independentes; token vazio permitido só no probe e ausência de autenticação registrada por chamada.
  - Removido fallback para credenciais/URL do atendimento. Token não vazio é recusado até confirmação do formato pelo Marco; nenhuma estratégia de autenticação foi presumida.
  - Configurada somente `SKYONE_PROBE_URL` no `.env` (600, ignorado pelo Git). Nenhum serviço foi reiniciado ou modo de atendimento alterado.
  - Executados os dois casos de instrução e as três medições de latência. Resultados, respostas, request_ids e comandos em `docs/SKYONE_RESULTADOS.md`, seção "Probe executado na VPS".
  - Adicionada nota em `docs/SKYONE.md` identificando o roteiro antigo como histórico; consolidação adiada conforme solicitado.
- Validações:
  - `--dry-run` via container → cinco campos esperados e `autenticacao=ausente`, sem rede.
  - `pytest -q -p no:cacheprovider tests` com dependências da imagem do gateway e pytest temporário → **42 passed**. Cobertura de contrato, HTTP 200 inválido, token não enviado, ausência de fallback de credencial, sanitização, SSRF, redirects e timeout.
  - A primeira tentativa de executar `/tmp/test-venv/bin/pip` foi recusada por permissão de execução no tmpfs. Repetido usando `python /tmp/test-venv/bin/pip --target /tmp/test-packages` e `PYTHONPATH=/tmp/test-packages python -m pytest`, sem instalar no host ou mudar o container em execução.
  - `--caso idiomas` → exit 0; inglês **14,106 s**, português **9,703 s**, ambos no idioma pedido e com até 10 palavras.
  - `--caso latencia` → exit 0; **6,844 s / 15,110 s / 7,991 s**.
  - Três medições de latência: **média 9,982 s**, **mediana 7,991 s**, mínimo 6,844 s e máximo 15,110 s.
  - Cinco chamadas sem autenticação; **5/5 HTTP 200, contrato válido, zero erros**. Tempos incluem DNS/rede/Skyone, sem debounce ou envio; amostra insuficiente para validar p95 de produção.
  - `curl --fail --silent http://127.0.0.1:8100/health` → `{"ok":true,"agente":"mock","envio":"dry"}`.
  - `.env` e relatórios `.local/` ignorados pelo Git; `.env` com permissão 600.
- Pendências / ponto de parada:
  - Marco fornecerá resultados de memória, autenticação e limite de requisições. Estratégias de sessão e autenticação **não consolidadas**.
  - Testes de simultâneas, Delay/callback e agente "Teste Rápido" continuam pendentes.
  - Fase 0.5 não iniciada; nenhuma declaração de conclusão integral da Fase 0.3.

## Fase 0.3 — Decisão de histórico e sessão — 2026-10-08

- Resultado informado pelo Marco: o Skyone não manteve memória no teste com `session_id`. Decisão registrada na tarefa de estratégia de sessão da Fase 0.5 do `PLANO_CURSOR.md`.
- Decisão: **o gateway é dono do histórico** e envia `historico`; **`session_id = request_id`**, com sessão nova por turno, para evitar duplicação de contexto caso o Skyone passe a manter memória. Este registro substitui a pendência de decisão de sessão dos registros anteriores.
- Adaptado `scripts/skyone_probe.py`: todos os casos (`idiomas`, `latencia`, `tempo`) enviam `historico: []` e usam o mesmo UUID em `session_id` e `request_id`. Histórico vazio corresponde às conversas novas dos diagnósticos atuais; não é teste de recuperação de contexto.
- Validações:
  - Suíte `pytest -q -p no:cacheprovider tests` em container, com pytest temporário → **42 passed**; testes atualizados verificam o campo `historico` e a igualdade dos IDs.
  - `docker compose run --rm --no-deps -v /home/twadmin/gateway:/workspace:ro gateway python /workspace/scripts/skyone_probe.py --dry-run` → seis campos no payload, `historico: []`, `session_id = request_id` e autenticação ausente; nenhuma chamada externa.
  - `git diff --check` → sem erros.
- Escopo: probe e registro de progresso. O envio do histórico real limitado a `history_turns`, com `enviada_em`, e a montagem do prompt no fluxo-modelo continuam tarefas de implementação do plano; o resumo entra na Fase 5.5.
- Autenticação e limite de requisições continuam pendentes. Fase 0.5 não iniciada. Nenhuma nova medição real de latência, alteração de serviço ou envio ao WhatsApp nesta tarefa.

## Atualização documental e Fase 0.5 — Migrações iniciais — 2026-10-08

- Status: Fase 0.5 iniciada; mecanismo, schema e seed validados em banco isolado. Fase 0.5 completa ainda pendente.
- Atualização aplicada:
  - Lido `ATUALIZACAO_CURSOR.md`; os cinco documentos do pacote anterior já estavam integrados. Preservadas as novas edições locais do Marco em `PLANO_CURSOR.md` e `docs/SKYONE_RESULTADOS.md` no commit `faaee65`, antes de qualquer ajuste.
  - Seguido o plano atualizado: início da 0.5 liberado, autenticação Skyone bloqueia sua conclusão; Postgres do gateway permanece independente do painel.
  - Recuperada do Git a seção "Probe executado na VPS", que havia sido removida na edição local, mantendo memória e comparação de modelos acrescentadas pelo Marco. Commit `8f388ed`.
  - `docs/SKYONE_RESULTADOS_acrescimo.md` não existe no checkout; a comparação de modelos já estava incorporada ao documento principal, sem necessidade de presumir conteúdo adicional.
  - Ajustado `SKYONE_TIMEOUT_S=90` no `.env` e `.env.example`; sem reiniciar o serviço atual. `.env` segue fora do Git. Esse valor passa a valer no próximo carregamento do ambiente.
  - Pendências técnicas históricas continuam cobertas pelo plano: SSRF na 0.5, fila na 0.6, sanitização/erro de envio na 1 e pausa humana na 3; chunked já resolvido.
- Implementação:
  - `app/migrar.py`: aplicação explícita, versões contíguas, SHA-256, registro em `schema_migrations`, trava consultiva entre executores e transação por arquivo. Não executa no startup.
  - `001_base.sql`: cópia integral do `app/schema.sql` atual.
  - `002_plataforma.sql`: tabelas multiempresa, modelos versionados, campos de provedor/autenticação genérica/timeout, configurações herdáveis, chaves, contatos e auditoria; `tenant_id` obrigatório nas tabelas legadas, índices e FKs compostas para impedir referências entre tenants. Modelos privados só podem ser usados pelo tenant proprietário; escopo do modelo é imutável. IDs de mensagens passam a ser únicos por tenant.
  - `003_seed.sql`: dois modelos globais, tenant By Koji, módulos catálogo/pedidos e agente "Atendimento By Koji" em rascunho, mock/dry, sem canais externos nem credenciais.
  - Migração 002 recusa tabelas legadas com dados antes de alterar o schema; sem apagar ou atribuir registros automaticamente. Conversão de dados segue pendente de roteiro explícito.
  - Criado `gateway_migrations_test` no Postgres do próprio gateway exclusivamente para a validação. O serviço existente continua em `bykoji_test`, que não recebeu migrações. O banco de produção `gateway` e seu usuário não foram criados.
  - `tests/conftest.py` recusa bancos que não terminem em `_test`; testes de integração usam schemas aleatórios e rollback. Operação e limitações em `docs/MIGRACOES.md`.
- Validações:
  - `cmp app/schema.sql app/migrations/001_base.sql` → idênticos.
  - `pytest -q -p no:cacheprovider tests` em container com pytest temporário e `MIGRATION_TEST_DATABASE_URL` apontando para `gateway_migrations_test` → **48 passed** (última execução: 2,64 s).
  - Cobertura: seed/reaplicação, checksum alterado, rollback de DDL e registro em falha, preservação de legado populado, FKs entre tenants, modelos privados, deduplicação de mensagem por tenant, campos de autenticação e timeout.
  - Duas chamadas concorrentes de `migrar.aplicar` em conexões separadas no banco isolado → uma aplicou `001_base.sql`, `002_plataforma.sql`, `003_seed.sql`; outra não reaplicou nenhuma. Terceira execução → nenhuma pendência.
  - Registro persistente no banco isolado → versões 1/2/3; seed com 1 tenant, 2 modelos e 1 agente. Suíte repetida após aplicação para verificar isolamento dos testes → 48 aprovados.
  - Banco do serviço: `current_database()` → `bykoji_test`; `to_regclass('public.schema_migrations')` → nulo, confirmando ausência da nova estrutura no banco atual.
  - Health interno → `{"ok":true,"agente":"mock","envio":"dry"}`; `git diff --check` → sem erros.
- Próximas tarefas da 0.5:
  - API administrativa, criptografia de credenciais, autenticação genérica no adaptador e teste real informado pelo Marco, filtros de tenant nas consultas, pipeline e configuração efetiva.
  - Extração física de `cardapio_itens` para o módulo `catalogo` (por enquanto recebeu tenant e indicação de responsabilidade), conversão dos dados legados e troca controlada do banco do serviço após código compatível.
  - Criação do banco `gateway`/usuário continua sujeita ao checkpoint específico do plano; não é necessária para revisar ou validar as migrações agora.
  - FKs não comprovam isolamento de leitura das futuras APIs. Nenhuma garantia de produção ou conclusão integral da 0.5 foi declarada.

## Fase 0.5 — Bloco 1: base de acesso — 2026-10-08

- Concluído: `nucleo/credenciais.py` cifra com Fernet e vincula o conteúdo ao tenant; token inválido ou transplantado é recusado sem expor conteúdo. `nucleo/banco.py` exige tenant explícito nas operações de entidades e filtra modelos globais/privados. `nucleo/configuracao.py` resolve modelo ← tenant ← agente, preserva false/zero, guarda a origem de cada valor e mantém cache de 30 s com invalidação e cópias independentes, sem credenciais.
- Validação: `sh scripts/test_suite.sh` → **50 passed**. Inclui roundtrip/tamper/transplante Fernet, isolamento de leitura/escrita, precedência, expiração e invalidação do cache.
- Imagem reconstruída para incluir `cryptography==46.0.3`, sem recriar o serviço em execução. A tentativa inicial de carregar a extensão nativa em `/tmp` foi recusada pelo tmpfs noexec; dependência instalada na imagem, pytest temporário continua em `/tmp`.
- Decisão recebida: não converter dados legados. `bykoji_test` será preservado; criação do banco vazio `gateway` e troca do serviço exigem confirmação no passo 5. Nenhuma migração aplicada no banco do serviço neste bloco.

## Fase 0.5 — Bloco 2: API administrativa mínima — 2026-10-08

- Concluído em `app/plataforma.py` e `app/admin/`: tenants, agentes, canais, chaves com escopos, configuração do tenant e auditoria. Escritas e auditoria na mesma transação; falhas revertem ambas. Leituras omitem hashes/ciphertexts/segredos; criação de chave mostra o valor uma vez e canal mostra a URL autenticada uma vez. API mínima documentada em `docs/API_ADMIN.md`.
- SEG02: acesso administrativo interno, rejeitando cabeçalhos de proxy inclusive vazios. SEG12: chave atual/próxima aceitas, log somente do identificador da chave. `X-Actor` obrigatório nas escritas. SEG11: escopo, validade, revogação, IP e vínculo de agente; identidade de tenant obtida exclusivamente pelo hash da chave.
- Validação: `sh scripts/test_suite.sh` → **53 passed**. Inclui dois tenants, leitura/edição de IDs alheios, referências de canal/agente, auditoria sem credenciais, rollback, cache invalidado ao salvar, IP público/proxy recusados, rotação de chave e escopos de integração.
- Aplicação nova ainda não ativada no serviço. Nenhuma migração ou mudança de banco do atendimento. As rotas além do contrato mínimo continuam pendentes dos blocos seguintes ou das fases correspondentes.

## Fase 0.5 — Bloco 3: contrato Skyone — 2026-10-08

- Concluído: adaptador `app/agentes/skyone.py`, histórico real limitado por `history_turns`, anterior ao lote e filtrado por tenant/conversa, `enviada_em` no fuso do tenant; `session_id=request_id`, instruções e IDs de contexto. Migração nova `004_horario_mensagens.sql`; migrações já aplicadas preservadas.
- Credencial Fernet enviada por Bearer/cabeçalho customizado; timeout do agente (total e HTTP), URL/DNS verificados imediatamente antes de cada chamada, redirects e respostas grandes recusados. Contrato exige correlação de IDs; erros têm texto fixo, sem corpo/URL/segredo. Eco de credencial é recusado.
- SEG13: API impede agente live sem credencial fora de tenant de teste, inclusive por herança de configuração. A chamada também valida a credencial antes de sair.
- Validação: `sh scripts/test_suite.sh` → **59 passed**. Testes usam Postgres isolado e `httpx.MockTransport`: três itens de histórico cronológicos, outra conversa excluída, fuso, seis campos do contrato, headers de autenticação, timeout, DNS recusado antes do envio, redirects/HTTP de erro e ausência de segredos nos erros.
- Nenhuma chamada real ao Skyone. Autenticação real ainda depende do Marco para fechar a fase, conforme o plano; implementação genérica autorizada não depende desse resultado. Serviço e banco atuais preservados.

## Fase 0.5 — Bloco 4: pipeline e webhooks por canal — 2026-10-08

- Concluído: `main:app` aponta para a aplicação multiempresa; rotas globais antigas não são montadas. O serviço existente continua com sua imagem anterior e `bykoji_test`; nova aplicação só será ativada no passo 5.
- `/webhooks/<tipo>/{canal_id}` autentica com segredo Fernet do canal, resolve tenant e roteia para o agente vinculado. API direta usa chave com escopo `conversar` e restrição de agente. Inativos/pausados/suspensos → 200 ignorado. Webhooks de tipos ainda não implementados → 501; uazapi mantém normalizador herdado, a confirmar com fixtures na Fase 1.
- Conversas no formato `<tenant>:<canal>:<identificador>`, deduplicação por tenant/canal, histórico real, debounce/trava Redis, limites de concorrência consultados da configuração efetiva, piloto, fallback e aviso de demora. Nenhuma configuração de atendimento vem do `.env` nesse pipeline.
- Pausa humana/roteamento consultados novamente antes do envio; HTTP recusado pelo uazapi não é registrado como entrega. URLs de saída do canal validadas antes da chamada e redirects recusados. Tokens por chave de campo removidos antes de registrar `webhook_raw`; fixtures reais ainda são necessárias na Fase 1.
- Migração `005_processamento.sql`: marca mensagens processadas e indexa pendências. Na subida, pendências são reagendadas; fila/worker durável completo permanece escopo da 0.6.
- Simulação administrativa somente mock/dry e consultas de mensagens/métricas por tenant/agente implementadas e documentadas. O contrato mínimo de simulação usa canal e identificador explícitos.
- Validações:
  - `sh scripts/test_suite.sh` → **65 passed**, última execução 13,59 s. Cenário de integração com dois tenants/três agentes, agrupamento de duas mensagens, deduplicação, configuração do agente, mensagem durante processamento indo ao próximo lote, pausa durante chamada e falha de envio sem entrega falsa.
  - Teste Lua em Redis real com namespace aleatório → first preservado e vencimento conforme debounce; chaves de teste removidas ao fim.
  - Migrações explícitas no banco isolado → 004 e 005 aplicadas; reaplicação → nenhuma pendência. Banco atual do serviço preservado.
  - Servidor temporário em `127.0.0.1:18100`, banco `gateway_migrations_test`, chaves sintéticas: inicialização completa, health 200, tenants 200 com chave atual/próxima, proxy forjado → 404, sem chave → 401; `/metricas` e `/docs` → 404. Container encerrado após validação.
  - Primeira tentativa do smoke usou script em `.local/` e foi recusada pela permissão do diretório; repetido com `tests/smoke_runtime.py`, sem afrouxar a permissão da pasta local.
  - Serviço publicado preservado: health interno `{"ok":true,"agente":"mock","envio":"dry"}`.
- Ponto de parada solicitado:
  - Blocos 1–4 implementados e testados; **passo 5 não executado**. Nenhum banco `gateway`/usuário criado, nenhuma troca de `.env` de conexão ou recriação do serviço. `bykoji_test` não será convertido nem apagado.
  - Aguardar confirmação do Marco para criar banco vazio/usuário próprio, aplicar migrações e apontar o serviço. O benchmark pós-troca com dois tenants/três agentes ainda não foi executado (o cenário acima é teste de integração isolado).
  - Autenticação real do Skyone e limite por minuto continuam dependências do Marco para concluir a 0.5. As demais tarefas do plano fora destes blocos mínimos — módulos, webhooks de saída, LGPD administrativa e gestão completa — não foram declaradas concluídas.

## Fase 0.5 — Passo 5 autorizado, interrompido antes da troca — 2026-10-08

- Status: bloqueado no pré-requisito de imagem para rollback. Marco autorizou a troca com backup, tag da imagem antiga, fila vazia e retorno imediato em caso de falha. A implantação não foi iniciada após a falha descrita abaixo.
- Feito:
  - Cópia `.env.antes-gateway` criada com permissão 600 e ignorada pelo Git; comparação em memória confirmou conteúdo idêntico ao `.env` atual. Nenhuma senha foi exibida.
  - Tentativa de marcar a imagem real do container em execução como `gateway:fase0` → **falhou: `No such image`**. ID obtido do container: `sha256:61a0f7129afd63f46cf024dea5bb4d2ad8956438bad20796f89b1024d01799f8`. `docker image inspect` também não encontrou essa imagem. Não foi usada a imagem `latest` como substituta. Causa ainda não investigada.
  - Backup `pg_dump -Fc` concluído em `/home/twadmin/backups/gateway/bykoji_test-20261008T192052Z.dump`, fora do Git, permissão 600 (diretório 700), 14.498 bytes. `pg_restore --list` → arquivo listável; não houve teste de restauração.
  - SHA-256 do backup: `078326f86fdb576126979bea4fed86854eabfa1cea5cd113572c532e9b427110`.
  - Redis do serviço antigo: **zero chaves `buf:*`**; nenhuma chave apagada.
  - Procedimento de volta registrado em `docs/MIGRACOES.md`, com aviso explícito de que depende da recuperação da imagem antiga.
- Estado preservado / validações:
  - Health interno → `{"ok":true,"agente":"mock","envio":"dry"}`.
  - Consulta ao catálogo do Postgres → `bykoji_test` existente, `gateway` ausente.
  - Não houve criação de usuário/banco, geração de senha, aplicação de migrações, alteração da conexão ou recriação de containers. Postgres, Redis e painel não foram alterados.
  - Não foi necessário executar rollback: o `.env` e o container antigo permaneceram intactos. Recriar o container seria inseguro enquanto a imagem antiga estiver indisponível.
- Ponto de parada: conforme a condição do Marco, interrompido diante da falha do pré-requisito. Benchmark de dois tenants/três agentes, validações públicas pós-troca e busca de segredos nos logs pós-troca não foram executados. A retomada exige primeiro recuperar e verificar a imagem de volta; Fase 0.5 não concluída.

## Fase 0.5 — Reconstrução da imagem antiga — 2026-10-08

- Status: imagem reconstruída; troca interrompida na validação de importação, conforme condição de parar em qualquer falha.
- Identificação:
  - Inventariados e calculados os SHA-256 dos sete arquivos `.py` sob `/app` no container em execução; comparados com os arquivos obtidos por `git show` de todos os commits anteriores ao Bloco 1.
  - Correspondência exata (conteúdo e conjunto de arquivos) em seis commits: `8f388ed`, `faaee65`, `bf7651d`, `52a2f6f`, `1e3f4a0` e `d6c607a`. Os hashes não distinguem o commit original entre eles. Escolhido o mais recente, `8f388ed6a2b47ce64acc43f39c93b148a8cac6f3`, para reconstruir o mesmo código.
- Feito:
  - `git worktree add --detach .local/fase0-worktree 8f388ed6a2b47ce64acc43f39c93b148a8cac6f3` e `docker build -t gateway:fase0 .local/fase0-worktree/app` → concluídos. Worktree removido ao final; checkout principal não foi trocado. Nenhum `docker commit` ou prune executado.
  - Imagem `sha256:7230ce922e07f577e443c734c910451eb306cb88a206fa01d4be8e2e454096ee`, tags `gateway:fase0` e `gateway:8f388ed`.
  - Acrescentada regra 15 ao `PLANO_CURSOR.md`: preservar imagem com tag do commit antes de build que a substitua; exigir confirmação do Marco para `docker image prune` e `docker builder prune`.
- Validações:
  - Hashes Python da imagem candidata versus container → **7/7 iguais**, sem arquivos adicionais/ausentes.
  - `pip freeze` ordenado da imagem versus container → **idêntico**, zero diferenças de pacotes/versões.
  - `docker run --rm --network none --env-file .env.antes-gateway gateway:fase0 python -c 'import main'` → **exit 1**, `KeyError: 'DATABASE_URL'`. O arquivo salvo não contém `DATABASE_URL` nem `REDIS_URL`; essas variáveis são sintetizadas pelo Compose. Nenhum servidor/agendador adicional iniciado.
  - `.env` continua idêntico a `.env.antes-gateway`; health do serviço antigo → `{"ok":true,"agente":"mock","envio":"dry"}`.
- Ponto de parada:
  - Imagem candidata preservada, mas validação completa ainda pendente. O próximo teste precisa receber as variáveis de conexão equivalentes às do Compose, sem expor segredos e sem iniciar servidor. Não alterado o arquivo original de rollback.
  - Não retomado o passo 5: backup anterior preservado, sem nova geração nesta tentativa; nenhuma criação de banco/usuário, migração, troca de conexão ou recriação do serviço. Não foi necessário rollback, pois o serviço permaneceu intacto.
  - Motivo e estado atual documentados em `docs/MIGRACOES.md`. Benchmark e demais validações pós-troca continuam pendentes.

## Fase 0.5 — Passo 5: banco próprio e troca concluída — 2026-10-08

- Status: **passo 5 concluído**, conforme autorização do Marco. A Fase 0.5 completa continua com as pendências funcionais e externas já registradas.
- Imagem de volta:
  - Teste pelo Compose com `.env.antes-gateway`, arquivos base/proxy/`.local/compose.volta.yml`, `run --rm --no-deps --pull never gateway python -c "import main; print('ok')"` → **ok, exit 0**, sem servidor/agendador adicional.
  - A opção `run --no-build` não é aceita pelo Compose instalado; foi substituída por `--pull never`, com `image: gateway:fase0` explícita e existente. Nenhum build nesse teste. As falhas anteriores de invocação não eram defeitos da imagem.
  - Preservadas `gateway:fase0` e `gateway:8f388ed`, ID `sha256:7230ce922e07f577e443c734c910451eb306cb88a206fa01d4be8e2e454096ee`. Hashes 7/7 e pip freeze idênticos, conforme registro anterior.
- Antes da troca:
  - Novo `pg_dump -Fc` em `/home/twadmin/backups/gateway/bykoji_test-20261008T193845Z.dump`, permissão 600, diretório 700, fora do Git; `pg_restore --list` → válido. Backup anterior preservado; nenhum restore executado.
  - SHA-256 do novo dump: `7e6e12a4473700b22720ca37d12cf76aff63b0cd1da578e15c2e803981838c1b`.
  - `.env.antes-gateway` preservado (600), idêntico ao ambiente antigo antes da edição; Redis antigo com **zero chaves `buf:*`**, nenhuma chave removida nesta implantação.
  - Imagem nova construída; `sh scripts/test_suite.sh` antes da troca → **65 passed em 11,23 s**, usando banco isolado.
- Troca:
  - Criados usuário `gateway` e banco vazio `gateway` no Postgres do próprio Compose. Senha gerada por `openssl rand -hex 24`, gravada somente no `.env`, sem exibição; SQL entregue por stdin com logging de statements desligado nessa sessão.
  - Papel sem superuser/createdb/createrole/replication/bypassrls; consulta de propriedade confirma apenas o banco `gateway`.
  - Acrescentado `docker-compose.database.yml`, selecionado pelo `COMPOSE_FILE` local, com `GATEWAY_DATABASE_URL` exclusiva do gateway. `POSTGRES_*`/Redis mantidos: nenhum ajuste de credenciais ou recriação dos serviços existentes.
  - `docker compose run --rm --no-deps --pull never gateway python /app/migrar.py --apply` → aplicadas **001, 002, 003, 004, 005**; `schema_migrations` conferida.
  - `docker compose up -d --no-deps --no-build gateway` → recriado somente gateway. Conexão efetiva `current_database()=gateway`, `current_user=gateway`.
- Validações pós-troca:
  - Health interno e HTTPS público → **200, somente `{"ok":true}`**; certificado verificado pelo cliente HTTP.
  - Pelo domínio, `/gateway/admin/v1/tenants`, `/gateway/metricas`, `/gateway/docs` → **404**, inclusive com credencial de painel válida.
  - Benchmark via API administrativa e webhooks autenticados: **2 tenants, 3 agentes, 3 canais API diferentes, 6 conversas simultâneas, 12 mensagens de entrada, 6/6 respondidas, zero erros**. Duas mensagens agrupadas por conversa.
  - `bykoji`: agente seed `bykoji_atendimento` e `ag_7e8683a49b68e615aa4e2101`; tenant de teste `bench_passo5` (`tenant_teste=true`): agente `ag_0e4cabdea1793a645e60944c`.
  - Todos os agentes ativos em **provedor mock / agente_mode mock / send_mode dry**, piloto desligado para as conversas sintéticas. Nenhum provedor externo conectado; nenhuma mensagem enviada ao WhatsApp. Os canais API e dados sintéticos foram preservados.
  - Mediana observada **4,3075 s**, máximo **4,335 s** (inclui debounce de 4 s; não mede Skyone real).
  - Métricas por agente (turnos / erros / p50 servidor / p95 servidor):
    - `bykoji_atendimento`: **2 / 0 / 4.229 ms / 4.256 ms**.
    - Segundo agente By Koji: **2 / 0 / 4.192,5 ms / 4.203,75 ms**.
    - Agente do tenant teste: **2 / 0 / 4.190,5 ms / 4.208,05 ms**.
  - Métricas agregadas: By Koji **4 turnos, zero erros, p95 4.250,9 ms**; tenant teste **2 turnos, zero erros, p95 4.208,05 ms**. Amostra pequena, sem inferência de p95 de produção.
  - Conteúdo de cada conversa corresponde somente às suas duas entradas. Chaves restritas por agente lendo conversas de outro agente (inclusive mesmo tenant) → **404**; rotas administrativas sob tenant incorreto → **404**. Três chaves temporárias revogadas ao final.
  - SQL: **zero cruzamentos** de tenant/agente/canal entre turnos e conversas, zero turnos em erro/ignorados e zero entradas pendentes.
  - Busca em memória pelos valores das chaves, senhas, tokens e segredos dos canais criados nos logs desde o início da tentativa → **0 ocorrências** no gateway, Postgres, Redis e Caddy. Conteúdo bruto dos logs não foi exibido.
  - IDs de containers do Postgres, Redis e todos os serviços do painel inalterados. `bykoji_test` preservado e sem `schema_migrations`.
  - Resultados sanitizados em `docs/VALIDACAO_PASSO5.json`; roteiro local executado em `.local/validar_troca.py`. Procedimento de volta atualizado em `docs/MIGRACOES.md`.
- Nenhuma validação real pós-troca falhou; rollback não necessário. Autenticação real/limite Skyone e demais tarefas da 0.5 fora dos blocos mínimos continuam pendentes; não iniciada a Fase 0.6.

## Fase 0.5 — Contrato único e acesso do painel — 2026-10-08

- Reescrito `docs/API_ADMIN.md` com status por rota, contrato real de credenciais/simulação, campos válidos, erros, limites e separação do planejamento. `/testar` identificado como não implementado (não há corpo real aceito); documentação interativa ausente, banco `gateway` e fluxos criados do zero.
- Proposta de conectar somente backend do painel à rede existente `luxbrain_gateway_proxy`, utilizando alias já configurado `luxbrain-gateway:8000`. Alteração do Compose do painel depende de confirmação e não foi feita.
- Container temporário na rede, somente cliente HTTP: administração autenticada → 200; `X-Forwarded-For`, `X-Real-IP` e `Forwarded` → 404 cada, com chave válida. Nenhum servidor/agendador novo e nenhuma exposição de credenciais.
- Suíte em banco `_test`: 65 testes aprovados. Serviço permanece mock/dry e não foi recriado.

## Fase 0.5 — Módulos — 2026-10-08

- Interface com nome, migrações, rotas, escopos e contexto; catálogo separado fisicamente por migração 006, sem apagar dados. Registro de pedidos reservado à Fase 4.
- API de registro/habilitação por tenant e busca de catálogo com conversa, chave/escopo e modelo corretos; desabilitado → 404; contexto só de módulos contratados/habilitados.
- Suíte isolada: **66 passed**. Teste INV08/SEG06 cobre dados de dois tenants, módulo desabilitado, conversa alheia e tenant inexistente. Corrigido valor padrão de config omitida encontrado no primeiro teste.
- Código/migração preparados, ainda não aplicados no serviço. Nenhum modo live ativado.

## Fase 0.5 — Modelos versionados — 2026-10-08

- API de modelos globais/privados, leitura de versão e PATCH que cria versão imutável; agentes mantêm a versão fixada. Escopo/ID imutáveis, validação de capacidades e configuração.
- Auditoria global explícita (tenant nulo, migração 007) e consulta administrativa de plataforma; modelos privados auditados no proprietário.
- Suíte isolada: **67 passed**, incluindo acesso privado cruzado recusado, atualização de versão sem mudar agentes existentes e auditoria global. Ainda sem aplicar ao serviço.

## Fase 0.5 — Webhooks de saída — 2026-10-09

- CRUD administrativo isolado, segredo Fernet mostrado uma vez, outbox persistente por tenant (migração 008), HMAC sobre timestamp + corpo bruto, ID estável de entrega e três tentativas com backoff. DELETE desativa sem apagar histórico.
- URL/DNS verificados a cada tentativa, sem redirects; timeout limitado; resposta/erros de terceiros não são logados. Tarefa separada do processamento de conversas, cancelada na parada.
- Eventos do agente enfileirados junto ao turno; API de teste 202 e consulta de entregas. Nenhuma saída real configurada/ativada.
- Suíte isolada: **68 passed**. Receptor HTTP em memória verifica HMAC, rejeita corpo adulterado, recebe o mesmo delivery ID nas retentativas; DNS privado não toca no receptor; três falhas encerram entrega; rotas recusam IDs de outro tenant.
- Ainda sem implantar código/migração no serviço.

## Fase 0.5 — Notificações — 2026-10-09

- `/api/v1/conversas/{conv}/notificar` aceita evento/dados/instrução com escopo `notificar`; entrada persistente de sistema e resposta 202. Processamento separado de lotes do cliente, `tarefa=notificar`, origem/uso no turno (migração 009).
- Tenant/agente da chave conferidos; auditoria com ID da chave. Pausa/piloto/status impedem chamada/envio; turno ignorado e auditoria explicam notificação não enviada.
- Suíte isolada: **69 passed**, incluindo chave catálogo → 403, outro tenant → 404, payload sanitizado, origem sistema, pausa sem chamada/envio adicional. Nenhum live ou implantação nesta etapa.

## Fase 0.5 — Consumo mensal — 2026-10-09

- Contagem por tenant/mês no fuso local, detalhamento por agente, avisos persistentes únicos em 80%/100% (migração 010), auditoria e evento `consumo.limite` para webhooks inscritos. Não corta atendimento; cache Redis reconstruível e Postgres como fonte.
- API de consumo com validação de mês e isolamento. Turnos guardam uso opcional do provedor desde o bloco de notificações.
- Suíte isolada: **70 passed**. Sete turnos com limite cinco: sete respostas, avisos somente nos turnos quatro/cinco, nenhum vazamento ao segundo tenant; mês local conferido na borda UTC. Sem alteração do serviço.

## Fase 0.5 — Unidades e horários — 2026-10-09

- CRUD isolado/auditado de unidades, DELETE como desativação, validação de faixas semanais e telefone. Pacote ao agente inclui apenas unidades ativas do tenant, aberta agora/próxima abertura no fuso local.
- Suíte isolada: **72 passed**. Cobertura de madrugada, limite exato de fechamento, semana sem horários, URLs de unidade alheia recusadas e exclusão de unidades inativas do pacote. Não cadastrados horários reais nem alterado serviço.

## Fase 0.5 — LGPD03 — 2026-10-09

- Contatos vinculados na recepção; migração 011 associa conversas existentes. Busca isolada, exportação auditada com actor obrigatório e anonimização confirmada por telefone/e-mail.
- Anonimização transacional de contato, conversa/ID pessoal, mensagens, respostas dos turnos, brutos, entregas pendentes e identificadores da auditoria. Métricas preservadas; outro tenant preservado. Agendamentos antigos retirados após confirmação da transação; conversa em processamento retorna 409 sem alteração parcial.
- Suíte isolada: **73 passed**. LGPD03 verifica ausência do telefone em todas as tabelas afetadas do tenant, inclusive IDs e brutos, manutenção das métricas e do mesmo telefone em outro tenant. Corrigida a primeira versão da migração para manter FKs imediatas no uso normal e adiar somente na transação LGPD.
- Exportação inclui brutos/entregas/auditoria associados e campos JSON estruturados. Backups/dados já enviados a terceiros e formatos reais ainda não capturados continuam com as limitações documentadas. Nenhum dado do banco de serviço foi anonimizado.

## Fase 0.5 — Benchmark atualizado — 2026-10-09

- `scripts/bench.py` usa `/admin/v1`, `--tenant`, `--agente`, `--panel-key` ou variável de ambiente; repetição de alvos para múltiplos tenants/agentes, canal explícito ou descoberta inequívoca, verificação mock/dry/piloto antes de escrever.
- Duas entradas por conversa, conferência do histórico, timeout e exit de falha, métricas/deltas separados e latências. Nenhuma criação/ativação automática de entidades, nenhuma chave em relatório/erro.
- Suíte isolada: **74 passed**, incluindo benchmark com dois tenants em transporte simulado e HTTP recusado contabilizado como erro. Serviço ainda não recriado.

## Fase 0.5 — Validação integrada dos complementos e parada — 2026-10-09

- Status: **oito blocos solicitados implementados**, cada um com commit/testes verdes; Fase 0.5 integral ainda não concluída.
- Suíte final: **75 passed em 19,33 s**, banco `_test`. Acrescentada regressão LGPD para telefone formatado sem concatenar números de campos alheios; ajuste evita anonimizar um bruto sem relação com o contato.
- `tests/smoke_runtime.py` agora cria schema Postgres e namespace Redis exclusivos, com chaves sintéticas. Nenhum agendador temporário consome a fila do serviço.
- Servidor temporário em `127.0.0.1:18100`, migrations 001–011, schema `smoke_482e3057accb41c3a06b81d11dfd0afe` em `gateway_migrations_test`: startup/health OK, SEG02 → 404 com proxy forjado, docs → 404, novas rotas de módulos/modelos/unidades/consumo/contatos/saídas respondem no ambiente isolado.
- Novo `scripts/bench.py` executado contra esse servidor com **2 tenants, 3 agentes, 3 canais, 6 conversas e 12 entradas**: **6/6 respondidas, 0 erros, p50 observado 4,407 s, máximo 4,422 s**. Mock/dry com debounce 4 s. Exportação/anonimização LGPD validadas também com transação real, depois do benchmark; consumo de By Koji continuou em 4 turnos.
- Container temporário encerrado; schema de diagnóstico preservado no banco de testes. Relatório sanitizado em `docs/VALIDACAO_COMPLEMENTOS05.json`.
- Serviço publicado preservado: health interno/público `{"ok":true}`, schema **005**, **zero agentes em live**. Complementos/migrações 006–011 ainda não implantados no serviço; contrato identifica essa diferença explicitamente. Nenhum Compose do painel alterado, nenhum envio externo ou prune.
- Seção 0 do plano e procedimento de migrações atualizados. A imagem do passo 5 exige schema 005 e não pode simplesmente ser recolocada sobre 011; implantação futura deve preparar backup/volta compatível antes de migrar.
- Para fechar a fase:
  - Implantação controlada dos complementos e revalidação no serviço.
  - Marco: autenticação real Skyone (401/403 sem credencial, 200 com credencial), limite/minuto, revisão do contrato/modelos iniciais; confirmação para conectar backend do painel à rede interna, se for iniciar a integração.
  - Gestão ainda planejada no contrato da 0.5, fora dos oito blocos solicitados: `/agentes/{id}/testar`, rotação do segredo de canal, lista/filtros de conversas, assumir/devolver/envio humano e polling de eventos. Não foram implementados por inferência nem declarados concluídos.
  - Simultâneas e timeout/callback antes da 0.6 continuam com o Marco. Nenhuma fase posterior iniciada.


## Fase 0.5 — Implantação dos complementos 006–011 — 2026-10-09

- Status: concluída; serviço permanece mock/dry. Imagem anterior identificada por 24 hashes Python, preservada como `gateway:ccb261b` (`sha256:a9bfabd15be399adb0df5784f36f3b6236007a66952f978f810333b0c44c2156`); nova imagem também marcada `gateway:9bcc828`.
- Suíte antes da troca: **75 passed em 20,37 s**, somente banco `_test`.
- Antes da parada: zero `buf:*`, lotes `gw05:due`, travas e mensagens pendentes no Postgres; zero agentes live. Nenhuma chave removida.
- Backup: `/home/twadmin/backups/gateway/gateway-pre011-20261009T003648Z.dump`, permissão 600, `pg_restore --list` aprovado; SHA-256 `b56a246156f4f49a8609b495b6ec714d7b2043c85c694688dbdb315171d35204`. Cópia do ambiente `/home/twadmin/gateway/.env.antes011-20261009T003648Z` (600). Backups anteriores preservados.
- Dump restaurado em `gateway_pre011_20261009t003648z` e schema 005 conferido, preparando a volta com imagem antiga sem rebaixar migrações. Banco principal preservado; procedimento em `docs/MIGRACOES.md`.
- Aplicadas explicitamente 006–011; recriado somente gateway. IDs de Postgres/Redis/painel inalterados.
- Health interno/público 200 com somente `{"ok":true}`; pelo domínio admin, métricas antigas e docs 404 com chave válida.
- `bench.py`: **2 tenants / 3 agentes / 3 canais / 6 conversas / 12 entradas, 6/6 respondidas, zero erros**, p50 **4,3685 s**, máximo **4,390 s**. Cada agente acrescentou dois turnos e zero erros. Conteúdo e leituras isolados; SQL com zero cruzamentos e zero entradas pendentes. Zero agentes em live.
- Busca de segredos nos logs de gateway/Postgres/Redis/Caddy: zero ocorrências. Relatório sanitizado: `docs/VALIDACAO_IMPLANTACAO011.json`. Nenhuma validação falhou; volta não necessária.
- Próximo bloco: rotas de gestão solicitadas. Autenticação real Skyone e demais checkpoints externos continuam pendentes.


## Fase 0.5 — Gestão de agentes, canais e atendimento — 2026-10-09

- Status: rotas solicitadas implementadas, testadas e implantadas em mock/dry; schema 011, sem migração adicional.
- `/agentes/{id}/testar`: pacote sintético com `message: "teste do painel"`, histórico vazio, sessão igual ao request_id; usa configuração/timeout/adaptador do agente, sem canal ou conversa persistente. Mock responde mock; falhas 502 sanitizadas; sucesso/falha auditados.
- `/canais/{id}/rotacionar-segredo`: segredo Fernet novo, URL exibida uma vez, segredo anterior recusado após rotação; transação e auditoria sem credenciais.
- Conversas: listagem paginada com filtros agente/canal/status/busca/desde; assumir/devolver com auditoria e retomada somente de entradas pendentes. Envio humano pausa, registra origem humana e respeita dry; falha externa mantém pausa e não grava entrega fictícia. Trava por conversa serializa pausa e verificação/envio final do bot, evitando resposta gerada antes da pausa chegar depois dela.
- Polling por tenant sobre auditoria persistente: mensagens, ações administrativas e resultado de turnos, sem conteúdo/segredos. Alocação de IDs serializada por tenant até o commit; ordem de travas consumo/auditoria mantida. Eventos antigos de mensagens não foram reconstruídos. Não antecipa fila/worker durável da 0.6.
- Validação: **82 passed em 25,84 s**, banco `_test`. Cobre todas as novas rotas com isolamento, SEG02/SEG12, segredo antigo 401, contrato sintético, erro sanitizado, filtros/paginação, pausa durante chamada ao agente, retomada, envio humano recusado sem falsa entrega e cursor de eventos. Primeira rodada identificou expectativa incorreta do teste sobre alvo de auditoria de simulação (agente, não conversa); corrigida e suíte repetida.
- Antes da atualização: `gateway:9bcc828` conferida como imagem de volta; zero buffers, lotes agendados, travas ou entradas pendentes. Dump adicional `/home/twadmin/backups/gateway/gateway-pre-gestao-20261009T125335Z.dump` (600, fora do Git), listagem `pg_restore` válida; SHA-256 `818d5a9d1f593544b809c3e4d0db88b849113ef0ba37c7145d8c874b2f54b454`. Procedimento de volta de código no mesmo schema 011 documentado em `docs/MIGRACOES.md`.
- Recriado somente gateway; IDs de Postgres, Redis e painel preservados. Imagem desta entrega `sha256:8365cc6253988d6a8ac9ef1225b5ec65b402d15602a170ea504399e41cdd46e4`, marcada com o commit do bloco. Nenhum live, alteração de Compose do painel ou prune.
- Validação operacional: health interno/público OK; admin/métricas/docs públicas 404 com chave válida. **Benchmark 2 tenants/3 agentes: 6/6 respostas, zero erros, p50 4,334 s, máximo 4,356 s**; dois novos turnos por agente, nenhum dado cruzado. SQL: schema 011, zero live, cruzamentos e pendências.
- `/testar` nos três agentes → mock; filtro de conversas, assumir, mensagem humana dry, devolver e polling aprovados. Canal API adicional de diagnóstico criado **inativo** no tenant teste: rotação recusou segredo antigo (401), aceitou novo (200 ignorado), leitura omitiu segredo. Canal/dados sintéticos preservados, sem ligação externa.
- Logs gateway/Postgres/Redis/Caddy: zero ocorrências dos segredos pesquisados, inclusive segredo antigo/novo da rotação. Relatório `docs/VALIDACAO_GESTAO05.json`. Nenhuma falha operacional; nenhum rollback necessário.
- Contrato único e seção 0 do plano atualizados. Ponto de parada: trabalho solicitado concluído. Para fechar formalmente a 0.5, Marco precisa validar autenticação real Skyone (401/403 sem credencial, 200 com credencial), confirmar limite máximo/valor adotado de requisições por minuto e revisar contrato/modelos iniciais. Conectar backend do painel à rede depende de confirmação separada se a integração for iniciada. Simultâneas/timeout/callback continuam antes da 0.6; OpenAI direto pertence à decisão da 2.5.


## Fase 0.5 — Skyone Basic, quota por URL e circuito — 2026-10-09

- Resultado informado pelo Marco: Basic Auth no fluxo gateway, usuário gerado `flow-<id>@luxtia`, senha definida por ele. Sem credencial: 401 em 1,88 s; senha errada: 401; correta: 200 com contrato completo e histórico respeitado. Webhook configurado em **300/min**. Não foi presumido um máximo comercial do provedor. Registro em `docs/SKYONE_RESULTADOS.md`.
- Migração **012**: tipo Basic, usuário em `skyone_auth_usuario_enc`, senha em `skyone_auth_enc`, ambos Fernet vinculados ao tenant e somente escrita; GET devolve apenas indicadores de configuração. Bearer/cabeçalho preservados. Basic incompleto → 400, inclusive tenant de teste. Credencial vai somente no header Basic, nunca no corpo.
- Teto de infraestrutura `SKYONE_REQUESTS_PER_MINUTE`, padrão **240 por URL**, compartilhado entre agentes/tenants; janela móvel Redis de 60 s com admissão Lua atômica, URL somente como hash nas chaves. Quando cheio, aguarda sem erro; espera + HTTP limitados por `timeout_s`, exceder gera fallback. Expiração da espera antes da chamada não conta no circuito. Mock não chama provedor nem consome quota.
- 401/403: **“credencial do Skyone recusada”**, sem retry; `/testar` retorna 502; pipeline registra `SKYONE_CREDENTIAL_REJECTED` e fallback. Alerta persistente em auditoria/polling/log, agrupado por agente por 3 h, com evento HMAC para destinos já inscritos. Nenhum destino externo cadastrado/ativado.
- Circuito antecipado conforme pedido: três falhas consecutivas na janela de cinco minutos abrem por 60 s; uma chamada de prova depois, sucesso fecha e falha reabre. Estado em `GET /admin/v1/tenants/{tenant}/agentes/{agente}/circuito`. Workers, justiça, fechamento manual e garantias completas da 0.6 continuam pendentes.
- Testes: **87 passed em 30,93 s**, exclusivamente `_test`. Cobrem Basic/header/Fernet/omissão em API/logs, ausência de usuário/senha, compatibilidade dos tipos anteriores, Redis real com URL compartilhada e URL independente, espera sem erro e timeout com fallback sem HTTP, 401/403 com uma tentativa, alerta agrupado, circuito/prova/sucesso e erro claro em `/testar`. Somente credenciais sintéticas; nenhuma chamada real ao Skyone.
- Implantação: imagem anterior `gateway:a528a8c` verificada; zero buffers, agendamentos, travas, entradas pendentes e agentes live. Backup `/home/twadmin/backups/gateway/gateway-pre012-20261009T133216Z.dump`, permissão 600, `pg_restore --list` válido; SHA-256 `428dab9dd6fb383d2e17572382f2e3a36796b2a9a7ede6dac9d85f7fc6f72531`. Ambiente preservado em `/home/twadmin/gateway/.env.antes012-20261009T133216Z` (600).
- Dump restaurado na nova base `gateway_pre012_20261009t133216z`, schema 011 confirmado; aplicada somente 012 no principal, recriado somente gateway. Postgres/Redis/painel mantiveram IDs. A comparação final exclui containers temporários de testes, que já encerraram normalmente. Backups/bancos anteriores preservados; nenhum prune.
- Validação publicada: schema 012, limite efetivo 240, três diagnósticos mock, health interno/público OK e admin/métricas/docs públicas 404. Benchmark **6/6, zero erros**, 2 tenants/3 agentes, p50 **4.4 s**, máximo **4.436 s**. Zero cruzamentos, pendências e agentes live. Logs gateway/Postgres/Redis/Caddy: zero ocorrências dos segredos pesquisados. Relatório `docs/VALIDACAO_SKYONE_BASIC.json`. Nenhuma falha operacional/rollback.
- Ponto de parada: implantado em mock/dry. **Senha real não recebida, cadastrada ou lida.** Marco fará PATCH pelo túnel e `/testar`; para chamar Skyone ele selecionará provedor Skyone/agente live, mantendo envio dry. Contrato e seção 0 atualizados. A confirmação desse teste pelo gateway e revisão de contrato/modelos continuam pendentes para fechamento formal.


## Fase 0.5 — Teste real Basic pelo Marco e verificação de logs — 2026-10-09

- Status: **autenticação e limite concluídos**; fechamento formal da fase aguarda somente revisão do contrato administrativo e confirmação dos modelos iniciais pelo Marco.
- Resultado informado pelo Marco: `/testar` do tenant `postman_teste`, agente `ag_dba38a6fae985abf584d512f`, com Skyone/live/dry e Basic cadastrado por ele → **HTTP 200, ok=true, 8.194 ms (8,194 s)**, resposta em português do fluxo `k3iynwDoOU`. Senha ausente da resposta do PATCH. É amostra individual; não representa percentis de produção.
- Testes diretos informados: sem credencial → 401; senha errada → 401; correta → 200. Limite adotado no Webhook **300/min**; ambiente do gateway conferido com teto **240/min por URL**, compartilhado entre agentes. Sem nova chamada ao provedor nesta conferência.
- Evidência independente: auditoria registra **1** sucesso `agente.testado` desse tenant/agente, em `2026-10-09T13:48:08.992228+00:00`.
- Verificação autorizada pelo Marco: logs stdout/stderr disponíveis do container atual (início `2026-10-09T13:32:46.777594627Z`), lidos somente em memória. Processo dentro do container decifrou o registro e gerou Basic para comparação; não retornou credenciais nem conteúdo bruto. Senha literal/JSON/URL, Base64 Basic, valor completo e nome `Authorization`: **zero ocorrências em todas as buscas**. Relatório sanitizado `.local/verificacao-auth-real.json`; credenciais e logs brutos não gravados em arquivo ou Git. A conclusão é restrita aos logs disponíveis desse container.
- Configuração efetiva conferida: agente informado preservado em **provedor skyone / agente_mode live / send_mode dry**, **zero outros agentes live**, **zero agentes com envio live**. Nenhum PATCH, alteração de banco/configuração, rotação, build, restart ou envio nesta tarefa.
- Atualizados `docs/SKYONE_RESULTADOS.md` e seção 0 do plano. Alterações somente documentais; `git diff --check` aprovado, sem necessidade de repetir a suíte de código (última entrega: 87 testes verdes).
- Pendências formais exatas: **(1)** Marco revisar/aprovar `docs/API_ADMIN.md`; **(2)** confirmar `atendente_delivery` e `assistente_generico`. Integração do painel depende de confirmação separada se iniciada; simultâneas/timeout/callback são anteriores à 0.6, não bloqueios adicionais da 0.5.


## Fase 0.5 — Ajustes finais do contrato e fechamento formal — 2026-10-09

- Status: **concluída formalmente.** Marco aprovou o contrato e confirmou os modelos iniciais `atendente_delivery` e `assistente_generico` (ambos globais, presentes em produção).
- Feito (um commit por item, todos com testes):
  - Edições pendentes da sessão anterior revisadas e separadas: `88eaccf` erros padronizados (códigos fixos, mensagem em português, `detalhes.campo`, envelope nos middlewares/404, `TENANT_NOT_FOUND` → `NOT_FOUND`); `fa468ad` histórico paginado (`antes`, `limite` 1–100, `tem_mais`); `03297a6` lista de agentes com `circuito_estado` e `canais` (consulta única por página). Cada commit verificado isoladamente em worktree: 88 → 89 → 90 testes.
  - `9d631ee` null em campo obrigatório → 400 `VALIDATION_ERROR` com `detalhes.campo`, recusado antes de qualquer UPDATE (teste com mutação comprova); integridade NOT NULL/CHECK/FK → 400 com campo, UNIQUE → 409 com campo, só nomes conhecidos.
  - `2aedcc9` `/testar` com **`{"usar_provedor_real": true}`**: chama o Skyone só nessa requisição, mesmo com o agente em mock, pela cota por URL e pelo circuito; agente não muda; falta de provedor/URL/credencial → 400 com campo; sucesso/falha auditados (`agente.testado_provedor_real` / `agente.teste_provedor_real_falhou`).
  - `8f387be` `/simular` aceita IA real quando `send_mode=dry`; envio real → 400 `detalhes.campo=send_mode`, sem gravar entrada.
  - `82f8e2e` `docs/API_ADMIN.md` reorganizado por assunto; ✅ só para o conferido em produção.
  - `429dff4` `scripts/test_suite.sh` fixado no compose base (`bykoji_test`): o `COMPOSE_FILE` do `.env` aponta o gateway para o banco de produção.
- Validações (comando → resultado):
  - Suíte `scripts/test_suite.sh` → **96 passed**, somente bancos `_test`: ambiente do executor em `bykoji_test`, testes de integração em schemas isolados de `gateway_migrations_test`.
  - Pré-implantação: `gateway:39e1a53` = imagem em uso (volta); schema 012, nenhuma migração alterada; zero entradas pendentes, `gw05:due` vazio, sem buffers/travas.
  - Backup `/home/twadmin/backups/gateway/gateway-pre-contrato-20261009T173104Z.dump` (600, `pg_restore --list` OK), SHA-256 `ac83c3bdff517f3c4c5b932fe3c1c2a33b3356aec194876c7240242840ff71d6`; ambiente em `.env.antes-contrato-20261009T173104Z` (600, fora do Git).
  - Build → nova imagem `sha256:978a03be…` marcada `gateway:8f387be`; recriado só o gateway; postgres/redis/painel com os mesmos IDs.
  - Health interno/público `{"ok":true}`; público `/gateway/admin/v1/tenants`, `/docs`, `/openapi.json`, `/metricas` → 404; interno com `X-Forwarded-For` → 404 no envelope; sem chave → 401 no envelope.
  - Contrato em produção: rota e tenant inexistentes → `NOT_FOUND` "Recurso não encontrado."; lista com `circuito_estado`/`canais`; histórico `limite=1` → `tem_mais:true`; PATCH `timeout_s:null` → 400 campo `timeout_s`; `/testar` com `usar_provedor_real` em agente mock → 400 campo `provedor` (sem chamada externa); `/testar` comum → mock. 21 rotas GET ✅ do contrato → 200; ⏳ `circuito/fechar` e `/metricas/plataforma` → 404.
  - Benchmark `bykoji/bykoji_atendimento` (mock/dry), 4 clientes: **4/4, zero erros, p50 4,323 s, máximo 4,336 s**; delta 4 turnos, 0 erros. O par `bench_passo5` ficou de fora: o Marco desativou e rotacionou o canal API pelo painel às 17:23–17:24 UTC; não foi reativado.
  - SQL pós-implantação: schema 12, zero pendentes, zero envio live.
  - Logs gateway/Postgres/Redis/Caddy: **zero ocorrências** de 10 segredos pesquisados (inclui a senha do `GATEWAY_DATABASE_URL`).
- Estado preservado: agentes live/dry `postman_teste/ag_dba38a6fae985abf584d512f` e `bykoji/ag_7e8683a49b68e615aa4e2101` (“[teste] Bench segundo agente”), por escolha do Marco; nenhum agente com envio live. Nenhum Compose do painel alterado, nenhuma chamada real ao Skyone, nenhum prune.
- Nome publicado do parâmetro do `/testar`: **`usar_provedor_real`**.
- Pendências / decisões para o Marco: simultâneas e timeout/callback antes da 0.6; comparação OpenAI direta (2.5). Próxima fase: 0.6.

## Fase 0.5 — Conferência após fechamento — 2026-10-09

- Revisão da entrega já implantada: os 38 arquivos Python do checkout correspondem exatamente, por SHA-256, aos do container atual (`gateway:8f387be`). Nenhuma nova implantação necessária.
- `sh scripts/test_suite.sh` → **96 passed em 25,80 s**; executor no ambiente `bykoji_test`, integração em schemas isolados de `gateway_migrations_test`. Corrigida a descrição imprecisa do banco no registro anterior. Nenhum teste contra `gateway`.
- Health interno e público → `{"ok":true}`; pelo domínio, `/gateway/admin/v1/tenants`, `/gateway/metricas`, `/gateway/docs` e `/gateway/openapi.json` → **404**.
- Imagem de volta `gateway:39e1a53` existe. Backup pré-contrato e cópia do ambiente seguem com permissão 600; SHA-256 do dump confere com o registro e `pg_restore --list` → exit 0.
- Completada em `docs/MIGRACOES.md` a documentação de volta da última implantação: somente código, mantendo banco/schema 012 e dados atuais. Os roteiros anteriores de restauração para schemas antigos não se aplicam a essa volta.
- `git diff --check` aprovado. Alterações desta revisão somente documentais; nenhum agente, canal, credencial, banco ou serviço alterado. Não repetido benchmark no serviço nem chamadas ao Skyone.
- **Fase 0.5 permanece concluída formalmente.** Fase 0.6 não iniciada: aguarda testes do Marco de 10 chamadas simultâneas e tempo limite/callback no Skyone.


## Fase 0.5 — Cursor inicial de eventos e teste de carga do Skyone — 2026-10-09

- Status: concluído; serviço em mock/dry. Pedido do painel, antes da 0.6.
- Feito: `GET /admin/v1/tenants/{tenant}/eventos/cursor` → `{"ultimo_id": N}` (maior ID de evento confirmado do tenant; `0` sem eventos; tenant inexistente → 404 `NOT_FOUND`; sem corpo, sem listar histórico, sem alterar dados). Edições iniciadas pela sessão anterior (Codex), revisadas e assumidas: rota antes do roteador genérico (sem captura por `/{entidade}/{identidade}`), consulta só agregada, testes de isolamento/continuação/SEG02. Commit `50c22ca`. Contrato marcou ⏳ até a conferência em produção e depois ✅.
- Implantação: **feita pela sessão anterior às 18:18 UTC**, antes do commit, com backup prévio em `/home/twadmin/backups/gateway/pre-cursor-20261009T181713Z/` (dump `pg_restore --list` OK, SHA-256 `58e24456aeeee79501163af29d0029a910ac766df67546d0eb945d3ee804b836`; `.env` e Composes, 600), mas **sem a tag de commit exigida pela regra 15**. Conferido que os 38 arquivos `.py` do container são idênticos, por SHA-256, aos do commit `50c22ca`; imagem em uso `sha256:480fd191…` marcada `gateway:50c22ca`. Não houve novo build/restart (seria a mesma imagem). Imagem de volta `gateway:8f387be` preservada.
- Validações (comando → resultado):
  - `sh scripts/test_suite.sh` → **97 passed**, somente bancos `_test`.
  - Produção: schema 12, zero pendentes, zero envio live; agente live/dry apenas `postman_teste/ag_dba38a6fae985abf584d512f` (“[teste] Bench segundo agente” voltou à simulação pelo Marco).
  - Cursor: bykoji 216, bench_passo5 162, postman_teste 145, iguais ao `MAX(id)` de cada tenant; tenant inexistente → 404 no envelope; interno com `X-Forwarded-For` → 404; sem chave → 401; público `/gateway/admin/.../eventos/cursor` → 404. Health interno/público OK.
  - Isolamento ao vivo: benchmark bykoji (mock/dry, 4 clientes) **4/4, zero erros, p50 4,341 s, máx. 4,355 s**; cursor bykoji 216 → 240, bench_passo5 e postman_teste inalterados; `eventos?desde=216` → 24 eventos só de bykoji, `proximo_cursor` 240 = novo cursor, `tem_mais=false`.
  - Logs gateway/Postgres/Redis/Caddy: **zero ocorrências** de 10 segredos pesquisados.
- Teste de carga do Marco no fluxo `k3iynwDoOU` (Basic Auth, direto no Webhook): 3 em sequência 5,10 / 7,27 / 8,96 s; **10 simultâneas 10/10 corretas, HTTP 200, 6,95–7,87 s, lote em 7,89 s** — Skyone processa em paralelo, sem fila. OpenAI direta não executada. Resultado `.local/resultado_carga_20261009_181055.json`; registro em `docs/SKYONE_RESULTADOS.md`.
- Pendências / decisões para o Marco: timeout/callback no Skyone antes da 0.6; comparação OpenAI direta (2.5). Fase 0.6 não iniciada.


## Fase 0.6 — Bloco 1: tempo limite padrão de 60 s — 2026-10-09

- Status: concluído e implantado; serviço em mock/dry. **Fase 0.6 iniciada.**
- Base: teste de tempo do Marco no fluxo `k3iynwDoOU` (espera artificial em “Normalizar Entrada”, campo `segundos_teste` já removido): 0 s → 200 em 7,0 s; 30 s → 200 em 36,9 s; 60 s → 200 em 66,2 s; 120 s → nenhuma resposta nem código até o cliente desistir aos 210,9 s. Síncrono comprovado até ~66 s; teto entre ~66 e ~127 s em que o Skyone segura a conexão (possível limite do módulo JavaScript, não confirmado no Logger). Resultado `.local/resultado_tempo_20261009_191510.json`; registro em `docs/SKYONE_RESULTADOS.md`.
- Feito (`f5a1c22`): migração **013** — padrão de `agentes.timeout_s` 90 → **60 s**; agentes no valor de fábrica antigo (90) → 60, por decisão do Marco, cada um auditado (`migracao013`, `agentes.alterado`) sob a trava por tenant. Máximo aceito continua 600 s. `.env.example`: `SKYONE_TIMEOUT_S=60`, marcado como legado (só `app/pipeline.py` da Fase 0, fora do serviço; o `.env` em produção não foi alterado). Contrato: atendimento usa 60 s; acima disso, só modo assíncrono.
- Plano: callback assíncrono será implementado/testado na 0.6 com provedor simulado; **validação com callback real do Skyone marcada para a Fase 6.5**. Comparação OpenAI direta (2.5) continua pendente e não bloqueia a 0.6.
- Validações (comando → resultado):
  - `sh scripts/test_suite.sh` → **98 passed** (novo teste da 013: só 90 → 60, valor próprio preservado, novos agentes com 60, auditoria por agente), somente `_test`.
  - Pré-implantação: `gateway:50c22ca` = imagem em uso (volta); zero pendentes, `gw05:due` vazio, sem buffers.
  - Backup `/home/twadmin/backups/gateway/gateway-pre013-20261009T192140Z.dump` (600, `pg_restore --list` OK), SHA-256 `2cffcedb7fa7edd6c2864a72ffa77ffe711b03d4fea2179f1b4438d43463db51`; `.env.antes013-20261009T192140Z` (600, fora do Git).
  - Build → `gateway:f5a1c22`; `migrar.py --apply` → aplicada só a 013; recriado só o gateway; postgres/redis/painel com os mesmos IDs.
  - Produção: schema **13**, padrão da coluna 60; os 4 agentes com `timeout_s` 60 (modos inalterados: só `postman_teste/ag_dba38…` em live/dry); 4 registros `migracao013` na auditoria, visíveis como último evento de cada tenant no `eventos/cursor`. API: efetivo 60; `timeout_s: 601` → 400.
  - Health interno/público OK; admin/docs/openapi/métricas públicos → 404; interno com `X-Forwarded-For` → 404.
  - Benchmark bykoji (mock/dry, 4 clientes): **4/4, zero erros, p50 4,344 s, máx. 4,369 s**.
  - Logs gateway/Postgres/Redis/Caddy: **zero ocorrências** de 10 segredos.
- Volta: a imagem `gateway:50c22ca` funciona sobre o schema 13 (só mudou o padrão da coluna); voltar o valor dos agentes exigiria PATCH explícito ou restaurar o dump acima.
- Próximo bloco (aguarda validação do Marco): fila durável e workers da 0.6.


## Fase 0.6 — Bloco 1: schema e estados (migração 014) — 2026-10-09

- Status: concluído e implantado; serviço em mock/dry, nenhum modo de agente alterado. Desenho da fila durável aprovado pelo Marco (2 workers, até 3 no teste de carga; parte incerta nunca reenviada; uma nova tentativa só para conexão/502/503/504; circuito aberto → fallback).
- Feito (`219d71a`): migração **014**, só acréscimos com padrão compatível:
  - `turnos.etapa` (`processando|respondido|aguardando_callback|concluido|abandonado`, padrão `concluido`), `turnos.mensagem_ids`, índice único em `request_id` (44/44 distintos em produção antes da migração) e índice de turnos em andamento.
  - `mensagens.turno_request_id`, `parte`, `entrega` (`enviando|enviada|incerta`), coerência turno/parte e **chave única por (tenant, turno, parte)**; índice de entregas incertas.
  - `conversas.aguardando_atendente_desde` e `aguardando_motivo` (`erro_agente|tempo_esgotado|circuito_aberto|entrega_incerta`), coerentes entre si.
  - Tabela `agente_callbacks` (token só em hash, uso único por `recebido_em`, prazo > criação, FK para agente do mesmo tenant; sem FK para conversa porque a LGPD troca o ID). A anonimização LGPD03 passou a trocar também o `conversation_id` dos callbacks; teste estendido e verificado por mutação (sem a linha, o teste falha).
  - Ainda **não usados** pelo código: entram nos blocos 2–4.
- Validações (comando → resultado):
  - `sh scripts/test_suite.sh` → **102 passed**, somente `_test` (4 testes novos da 014; LGPD com callbacks).
  - Pré-implantação: `gateway:f5a1c22` = imagem em uso (volta); schema 13; zero pendentes, `due` vazio, sem buffers. Backup `/home/twadmin/backups/gateway/gateway-pre014-20261009T193800Z.dump` (600, `pg_restore --list` OK), SHA-256 `996092eca623555f4057f012cb19b6a9cb21011a55f6d0b1d0ebce3a8997b315`; `.env.antes014-20261009T193800Z` (600).
  - Build `gateway:219d71a`; `migrar.py --apply` → só a 014. **Compatibilidade em produção:** com a imagem antiga ainda no ar sobre o schema 14, health OK e benchmark **4/4, zero erros, p50 4,379 s**; turnos gravados como `concluido`. Depois, recriado só o gateway com `gateway:219d71a`; postgres/redis/painel com os mesmos IDs.
  - Imagem nova: benchmark bykoji **4/4, zero erros, p50 4,352 s, máx. 4,44 s**. Schema 14; 52 turnos `concluido`; zero partes, aguardando e callbacks (ainda sem uso); modos: só `postman_teste/ag_dba38…` em live/dry, todos com `timeout_s` 60.
  - Health interno/público OK; admin/docs/openapi/métricas públicos → 404; interno com `X-Forwarded-For` → 404; sem chave → 401.
  - Logs gateway/Postgres/Redis/Caddy: **zero ocorrências** de 10 segredos.
- Volta: recriar o gateway com `gateway:f5a1c22` sobre o mesmo schema 14 (registrado em `docs/MIGRACOES.md`).
- Próximo bloco (aguarda validação do Marco): 2 — fila por empresa, workers, agendador com trava de liderança, reconciliação, fallback sem promessa falsa (A), recepção resiliente (B) e capacidade/indicadores/alertas (E1–E3).


## Fase 0.6 — Bloco 2: fila durável, workers e agendador (publicados desligados) — 2026-10-09

- Status: concluído e implantado com **`FILA_DURAVEL=off`** (decisão do Marco: ligar só no bloco 5). Nenhum modo de agente alterado.
- Feito (`a0128b4`):
  - **Chave e exclusividade:** com `off`, workers e agendador sobem, dão sinal de vida e indicadores, mas não agendam nem consomem; com `on`, o agendador em processo da recepção não processa nada. Teste nas três posições (desligada, ligada, divergente com os dois caminhos rodando juntos): cada lote vira exatamente um turno e uma resposta. **Verificado por mutação:** sem a trava do caminho atual, falha a posição ligada; com a fila nova ignorando a chave, falha a desligada. (A primeira versão do teste passava por tempo de execução, não pela regra; foi refeita.)
  - **Fila:** Redis Streams por empresa (`gw05:fila:<tenant>`, grupo `workers`) com leitura em rodízio; movimento atômico `due → fila` (Lua); agendador com trava de liderança de 10 s renovada a cada iteração; reconciliação com o Postgres a cada 60 s; retomada de itens de worker morto (`XAUTOCLAIM`, 90 s).
  - **Processamento por etapas** (caminho novo): turno `processando` antes do agente, `respondido` com a resposta antes do envio, partes `enviando → enviada` sob a trava da conversa; parte presa vira `incerta`, não é reenviada, marca “aguardando atendente” e gera `mensagem.entrega_incerta`. Métricas e consumo passam a contar só turnos concluídos.
  - **Item A (já ativo no caminho atual):** fallback padrão “Tive uma instabilidade agora. Pode repetir sua mensagem em instantes? 🙏”; migração **015** troca só textos antigos gravados (agentes e config de empresa), auditada — em produção alterou **0** registros (nenhum texto antigo gravado). Erro, tempo esgotado (`TempoEsgotado`, erro `AGENT_TIMEOUT`) ou circuito aberto marcam `aguardando_atendente_desde`/`aguardando_motivo` e geram `conversa.aguardando_atendente`; assumir/devolver/responder limpam. Lista de conversas com os campos e filtro `aguardando`.
  - **Item B:** `restart: unless-stopped` e healthcheck nos 3 serviços (recepção: `/health`; worker/agendador: arquivo de sinal de vida em `/tmp` renovado a cada 5 s). Plano, Fase 1: confirmar com o uazapi o reenvio do webhook quando a recepção está fora.
  - **E1–E3:** `WORKERS=2`, `CONCORRENCIA_POR_WORKER=20` no `.env`; limites 192/256/128 MB e 0,5/0,5/0,25 CPU; indicadores em `/admin/v1/fila`, `/admin/v1/tenants/{t}/fila`, métricas e `/health` interno; alertas `fila.atrasada` (> 30 s) e `skyone.cota_alta` (> 80% por 5 min), agrupados. `docs/CAPACIDADE.md` explica como aumentar.
- Validações (comando → resultado):
  - `sh scripts/test_suite.sh` → **116 passed** (14 novos em `tests/test_fila.py`, com Redis real e pool real em schema temporário), somente bancos `_test`.
  - Pré-implantação: `gateway:219d71a` = imagem em uso (volta); zero pendentes, `due` e filas vazios. Backup em `/home/twadmin/backups/gateway/pre-bloco2-20261009T200721Z/` (dump `pg_restore --list` OK, SHA-256 `9e87d89fab2e41507618678b21ba04ea5b49f9d4c3d9b0c7ed4256b03a982b5c`; `.env`; os 3 Composes anteriores; tudo 600).
  - `.env` recebeu `FILA_DURAVEL=off`, `WORKERS=2`, `CONCORRENCIA_POR_WORKER=20` (600 mantido). Build `gateway:a0128b4`; migração 015 aplicada com a imagem anterior no ar; `up -d --no-deps gateway worker agendador`; postgres/redis/painel com os mesmos IDs. Os 4 containers `healthy`, `unless-stopped`, imagem `a0128b4`.
  - **Exclusividade em produção:** benchmark bykoji (mock/dry, 4 clientes) **4/4, zero erros, p50 4,409 s, máx. 4,42 s**; os 4 turnos novos vieram **todos do caminho atual** (`mensagem_ids` nulo) e **nenhum da fila nova**; Redis sem filas por empresa, `due` vazio, líder ativo, 2 workers vivos.
  - Indicadores: `/health` interno com `fila` zerada (`ligada:false`, 2 workers, agendador ativo); público só `{"ok":true}`; `/admin/v1/fila`, `/tenants/bykoji/fila` e `metricas.fila` zerados.
  - Item A: fallback efetivo do agente que herda = texto novo; `bykoji_atendimento` mantém o fallback personalizado `"/"` (definido pelo Marco no painel às 18:00); lista com os dois campos novos; filtro `aguardando=true` → 0.
  - Segurança: `/gateway/admin/v1/fila` e demais rotas administrativas públicas → 404; interno com `X-Forwarded-For` → 404; sem chave → 401.
  - Logs gateway/worker/agendador/Postgres/Redis/Caddy: **zero ocorrências** de 10 segredos.
  - **Recursos reais, parados** (3 amostras de `docker stats`): recepção 58 MB / ~0,5% CPU; worker 1 50 MB e worker 2 46 MB, 0,02% CPU (pico de 6–8% numa amostra); agendador 47 MB / ~0,3% CPU. Os três novos somam ~142 MB; memória disponível na VPS depois da implantação: 1.052 MB.
- Pendência pequena: `aguardando` com valor inválido devolve `campo: corpo`; incluir o nome nos campos conhecidos no bloco 3.
- Volta: `docker compose down` só de `worker` e `agendador` não é necessário com a chave desligada; para voltar o código, restaurar os Composes de `pre-bloco2-20261009T200721Z/` e recriar o gateway com `gateway:219d71a` (schema 15 é compatível: a 015 só altera dados).
- Próximo bloco (aguarda validação do Marco): 3 — justiça entre empresas com limites compartilhados no Redis (INV07), fechamento manual do circuito e nova tentativa (INV15).


## Fase 0.6 — Bloco 3: justiça entre empresas, circuito e nova tentativa — 2026-10-10

- Status: concluído e implantado; `FILA_DURAVEL=off` mantido; nenhum modo de agente alterado; sem migração.
- Feito (`2efffd1`):
  - **INV07:** vagas compartilhadas no Redis entre todos os workers, obtidas num único script Lua: `max_lotes_simultaneos` por empresa (`gw05:fila:lotes:<tenant>`) e `max_concorrencia` por agente (`gw05:fila:ativos:<tenant>:<agente>`), com validade `timeout_s + 90 s`. Sem vaga, o item volta ao agendamento (0,3 s) e o worker deixa de ler a fila da empresa cheia por um instante, seguindo para as outras. Vale para o caminho da fila durável; o caminho atual mantém os limites em processo.
  - **INV15:** `POST /admin/v1/tenants/{t}/agentes/{a}/circuito/fechar` (manual, auditado `agente.circuito_fechado`, avança a geração); **uma única nova tentativa** 2 s depois, só para falha de conexão (`ConnectError`) ou HTTP 502/503/504 antes de qualquer resposta e se couber no `timeout_s`; nunca em tempo esgotado. Cada tentativa passa pela cota por URL; o circuito conta uma chamada.
  - Filtro `aguardando` inválido → 400 com `detalhes.campo=aguardando`.
  - **Fallback validado** (agente e config da empresa): mínimo de 15 caracteres e ao menos uma letra; vazio/nulo = usa o padrão; inválido → 400 `campo: fallback`. Registros existentes não foram alterados.
- **Fallbacks gravados que não passariam na regra** (só listados): 1 agente — `bykoji/bykoji_atendimento` (“Atendimento By Koji”), fallback `/` (1 caractere, sem letra), definido pelo Marco no painel em 09/10 às 18:00 UTC. Nenhuma config de empresa nem modelo fora da regra. Enquanto não for corrigido, uma falha desse agente envia só “/” ao cliente.
- Validações (comando → resultado):
  - `sh scripts/test_suite.sh` → **126 passed** (10 novos em `tests/test_fila_justica.py`), somente bancos `_test`. INV07: 50 conversas de uma empresa + 1 de outra, 2 workers → a conversa da outra empresa sai junto com a primeira leva (≤ 10 da grande antes dela) e o pico da grande fica em 5. **Mutação:** sem a checagem de vaga, o pico vai a 39 e o teste falha. INV15: limite de 2 por agente respeitado entre 2 workers; nova tentativa nos 6 casos (503→200, 502→504, conexão recusada, 500, tempo esgotado, não cabe no prazo); circuito abre em 3 falhas, a 4ª conversa recebe fallback sem chamada HTTP, agente da outra empresa responde normalmente, fechamento manual auditado e isolado por tenant.
  - Pré-implantação: `gateway:a0128b4` = imagem em uso (volta); schema 15; zero pendentes; `due` vazio. Backup em `/home/twadmin/backups/gateway/pre-bloco3-20261010T001758Z/` (dump `pg_restore --list` OK, SHA-256 `8f76ac44df48c0c19d77ed2f8d24e8329ee886cf53af25c9cc9ed060cb8600e1`; `.env` e os 3 Composes; 600).
  - Build `gateway:2efffd1`; recriados gateway, 2 workers e agendador (todos `healthy`); postgres/redis/painel com os mesmos IDs.
  - Produção: benchmark bykoji pelo caminho atual **4/4, zero erros, p50 4,374 s, máx. 4,418 s**; 4 turnos novos, todos do caminho atual, nenhum da fila nova; nenhuma fila por empresa no Redis; 2 workers vivos; `/health` interno com a fila zerada, público só `{"ok":true}`.
  - Novidades em produção: `aguardando=talvez` → 400 `campo: aguardando`; PATCH de fallback `"/"`, `"curto demais"` e `"123456789012345678"` no agente de teste → 400 `campo: fallback`, valor inalterado; `circuito/fechar` no agente de teste → 200 `fechado`; mesmo agente por outro tenant → 404.
  - Segurança: `circuito/fechar`, admin e docs públicos → 404; interno com `X-Forwarded-For` → 404; sem chave → 401.
  - Logs gateway/worker/agendador/Postgres/Redis/Caddy: **zero ocorrências** de 10 segredos. Modos: só `postman_teste/ag_dba38…` em live/dry.
- Volta: recriar gateway, workers e agendador com `gateway:a0128b4` (mesmo schema 15; sem migração neste bloco).
- Próximo bloco (aguarda validação do Marco): 4 — callback assíncrono com provedor simulado (item D).


## Fase 0.6 — Bloco 4 (callback assíncrono) adiado — 2026-10-10

- Status: **adiado para antes da Fase 6.5**, por decisão do Marco. Nada implementado neste bloco.
- Motivo: o atendimento do By Koji responde em 5–9 s pelo caminho síncrono (teste de carga de 09/10: 10/10 em 6,95–7,87 s); callback só é necessário para agentes internos com tarefas longas.
- O que já existe e fica sem uso até lá: tabela `agente_callbacks`, etapa `aguardando_callback` do turno e troca do `conversation_id` dos callbacks na anonimização LGPD (migração 014, bloco 1). A rota `POST /api/v1/agentes/callback/{request_id}` continua ⏳ no contrato e `modo_resposta` continua aceitando só `sync`.
- Plano: a tarefa e o teste de callback da Fase 0.6 ficaram marcados como adiados (texto original preservado) e a Fase 6.5 ganhou o item de implementar o callback antes de validar com um callback real do Skyone.
- A Fase 0.6 segue para o bloco 5 (troca) sem depender do callback.


## Caso de teste “aguardando atendente” no postman_teste — 2026-10-10

- Status: criado a pedido do Marco para validar o selo e o filtro no painel. **Fica no ar até o Marco confirmar; a limpeza só com pedido dele.**
- Feito (pela API administrativa, caminho atual, ator `assistente-gateway (pedido do Marco)`; `bykoji` e os agentes existentes não foram tocados):
  - Agente `ag_393bad0c761589e969d99555` — “[teste] Aguardando atendente (validação do painel)”, provedor skyone com URL `https://aguardando-atendente-teste.invalid/fluxo` (domínio que nunca resolve: nenhuma chamada chega ao Skyone), credencial fictícia, IA real + envio de teste (`live`/`dry`), ativo, sem piloto.
  - Canal API `can_33e464559d7d941f750258ee` — “[teste] Canal do caso aguardando”.
  - Conversa `postman_teste:can_33e464559d7d941f750258ee:cliente-teste-aguardando`: mensagem do cliente “Olá, preciso de ajuda com meu pedido.” → falha do agente → fallback padrão gravado como resposta → `aguardando_motivo=erro_agente`, `aguardando_atendente_desde=2026-10-10T00:24:40Z`.
- Validações: eventos da conversa `mensagem.recebida`, `mensagem.enviada`, `conversa.aguardando_atendente`, `turno.erro`; `GET …/conversas?aguardando=true` devolve só essa conversa; circuito do agente de teste fechado (1 falha).
- Pendências: há agora dois agentes com IA real no `postman_teste` (o do Marco e este de teste, que não chama ninguém). Ao limpar: apagar/pausar o agente e o canal de teste e a conversa.


## Fase 0.6 — Bloco 5: troca preparada, teste de carga e INV14 — 2026-10-10

- Status: concluído e implantado com **`FILA_DURAVEL=off` mantido** (quem liga é o Marco); nenhum modo de agente alterado; sem migração; nenhuma chamada ao Skyone real.
- Feito (`7423fac` código, `209896c` ferramentas):
  - **Parada graciosa:** recepção (caminho atual) e workers terminam os lotes em andamento antes de sair (`PARADA_GRACIOSA_S`, padrão 75 s; `stop_grace_period` 90 s no compose). A troca e os reinícios deixam de cortar lote no meio.
  - **Liberações insistentes na queda do Redis:** trava da conversa, vaga da empresa/agente e confirmação do item insistem por até 60 s enquanto o Redis volta. Achado do teste de falha: com o limite padrão de 5 por empresa, uma queda de 10 s no meio do lote deixava a empresa parada 60–90 s (vagas presas até a retomada); agora retoma assim que o Redis volta.
  - `MOCK_ATRASO_S` (atraso do agente simulado, padrão 0; não definido em produção) para o teste de carga.
  - **Pilha isolada de carga** (`docker-compose.carga.yml`, `scripts/carga_fila.sh`, `scripts/carga_fila.py`): mesma imagem e limites da produção, banco e Redis próprios, sem portas, segredos gerados na hora, só agente mock/dry.
  - **`scripts/fila_duravel.sh`** (`estado`, `ligar`, `desligar`). Só o `estado` foi executado em produção.
- Teste de carga (200 conversas ao mesmo tempo, 2 empresas, agente simulado de 8 s; detalhes e leitura em `docs/CAPACIDADE.md`):
  - **2 workers × 20:** espera na fila mediana **16,9 s**, máxima **32,8 s**; tudo respondido em 48,9 s; 0 perdidas, 0 duplicadas.
  - **3 workers × 20:** mediana **10,2 s**, máxima **23,9 s**; 40,1 s; 0 perdidas, 0 duplicadas.
  - Extra: 2 × 50 → 4,9 s / 11,6 s; 2 × 100 → 2,2 s / 3,3 s. Workers em ~47 MB e CPU perto de zero: o limite é o número de vagas, não a VPS; com Skyone real o teto passa a ser a cota de 240/min por URL.
- **INV14 — feito na pilha isolada, não na produção:** com a chave desligada em produção não há fila durável lá para testar, e derrubar o Redis de produção só exercitaria o caminho atual. Resultados (200 conversas, 2 workers):
  - Redis morto 10 s no meio do processamento → 200/200, 0 perdidas, 0 duplicadas, 0 partes incertas (54,0 s).
  - Redis morto 10 s durante a recepção → recepção recusa enquanto está fora; com reenvio da mesma mensagem ficaram exatamente 200 entradas, 0 perdidas, 0 duplicadas (62,5 s).
  - Limite padrão 5 (40 conversas): 101–121 s antes da correção → 46–52 s depois (36,8 s sem falha).
  - Worker morto (SIGKILL) e mantido parado → o outro seguiu (180/200 em 74 s); as 20 que estavam com o morto foram refeitas e respondidas em ~170–180 s (validade da trava 150 s + reconciliação); 0 perdidas, 0 duplicadas, 0 partes incertas.
- Validações (comando → resultado):
  - `sh scripts/test_suite.sh` → **130 passed** (4 novos em `tests/test_fila_troca.py`; mutações conferidas: sem insistir e sem parada graciosa os testes falham), somente bancos `_test`.
  - Pré-implantação: `gateway:2efffd1` = imagem em uso (volta). Backup `/home/twadmin/backups/gateway/pre-bloco5-20261010T010600Z/gateway.dump` (`pg_restore --list` OK, 21 tabelas, SHA-256 `b9cf270c65368fd632dc6b8cc72ffc4ead2c571787c2534f704ab8cc7696cebb`).
  - Build `gateway:7423fac`; recriados gateway, 2 workers e agendador (`healthy`); postgres e redis intocados.
  - Produção: benchmark bykoji pelo caminho atual **5/5, zero erros, p50 4,408 s, máx. 4,415 s**; nenhuma fila por empresa no Redis (exclusividade); `/health` interno `ligada: false`, 2 workers vivos, agendador ativo; 0 entradas pendentes; schema 15.
  - Segurança: `/health` público só `{"ok":true}`; admin com `X-Forwarded-For` → 404; sem chave → 401. Logs: **zero ocorrências** de 10 segredos.
  - Modos: bykoji e bench em mock/dry; em live/dry só os dois agentes do `postman_teste` (o do Marco e o de teste do caso “aguardando”).
- Volta desta implantação: recriar gateway, workers e agendador com `gateway:2efffd1` (mesmo schema 15).
- Pendências:
  - **Ligar a chave (Marco):** `sh scripts/fila_duravel.sh ligar`; voltar: `sh scripts/fila_duravel.sh desligar`. Depois de ligada, repetir em produção um lote do agente simulado e, se o Marco quiser, a queda do Redis e do worker lá; só então fechar a Fase 0.6.
  - Worker morto à força: as conversas que estavam com ele levam ~3 min para serem refeitas. Não encurtado de propósito (risco de resposta dupla); decidir se vale revisar.
  - Fallback `"/"` do `bykoji/bykoji_atendimento` continua fora da regra (listado no bloco 3).

## Fase 0.6 — Fechamento: fila durável ligada e INV14 em produção — 2026-10-10

- Status: **Fase 0.6 concluída.** `FILA_DURAVEL=on` em produção (ligada pelo Marco, ~01:19 UTC). Imagem `gateway:7423fac`, schema 15, sem build nem migração neste fechamento. **Callback assíncrono continua adiado para antes da Fase 6.5.**
- Pré-checagem antes de derrubar o Redis: todos os canais são do tipo API de teste (nenhum uazapi); nas últimas 24 h só há mensagens de bench/simulação. Nenhum cliente real.
- Backup: `/home/twadmin/backups/gateway/pre-inv14-prod-20261010T012219Z/gateway.dump` (`pg_restore --list` OK, 21 tabelas).
- **INV14 em produção** (só `postman_teste`, agente simulado `ag_002430f4999ada600015605f` “[teste] INV14 fila durável (simulado)”, canal `can_0c276f791ffdd5b141eeaaf5`, criados para o teste; limites padrão):
  - 600 conversas em fluxo contínuo (8/s, 75 s). 01:22:55 worker-2 morto (SIGKILL) e mantido parado; 01:23:16 Redis morto (SIGKILL); 01:23:27 Redis de volta.
  - Resultado: **600/600 respondidas, 0 perdidas, 0 duplicadas**, 0 turnos refeitos/em aberto/com erro, 0 partes incertas; 370 recusas 503 da recepção durante a queda, todas resolvidas pelo reenvio da mesma mensagem; tudo concluído em 85 s.
  - Limite do teste: o agente simulado de produção responde na hora, então nenhum lote estava com o worker quando ele morreu. A retomada de lote cortado no meio está provada na pilha isolada (bloco 5).
  - bykoji: 158 mensagens e 54 turnos antes e depois (nada tocado durante o teste); benchmark simulado depois, pela fila durável: **5/5, zero erros**, ~4,4 s.
  - Depois: worker-2 religado; `estado` → `ligada: true`, 2 workers vivos, agendador ativo, 0 pendentes, 0 turnos em aberto, 0 partes incertas. Avisos nos logs só da janela da queda (“temporariamente indisponível”, “Cache de consumo indisponível; Postgres preservado”).
  - Recepção com a fila ligada: 20 mensagens simuladas → mediana 14 ms, máxima 93 ms.
- **Limite por empresa:** `PUT /admin/v1/tenants/bykoji/config` com `max_lotes_simultaneos=30` (antes: nulo = padrão 5). Demais empresas no padrão 5. Regra registrada em `docs/CAPACIDADE.md`. **Atenção:** `max_concorrencia` (por agente) continua 5; enquanto não subir, cada agente do bykoji segue limitado a 5 conversas ao mesmo tempo.
- Painel: os campos `max_lotes_simultaneos` e `max_concorrencia` não aparecem no código do painel (busca em `painel_koji/src`, só leitura); não são editáveis por lá hoje.
- Limpeza: removida só a imagem `gateway-carga:latest` (sem prune). Mantidos: agente e canal do caso “aguardando” (até o Marco validar o painel), agente/canal/conversas do teste INV14, `.local/teste_skyone_tempo.py`. Nenhuma alteração no Skyone.
- Observação: a conversa do caso “aguardando” foi assumida pelo Marco no painel às 01:15 UTC (auditoria `conversa.assumida`); por isso saiu do filtro `aguardando=true`.
- Pendências: decidir `max_concorrencia` do bykoji; limpeza dos dois agentes `[teste]` do `postman_teste` quando o Marco pedir; fallback `"/"` do `bykoji/bykoji_atendimento`; ~3 min do worker morto à força. Próxima: Fase 1 (uazapi real).

## Limite do By Koji e preparação da Fase 1 — 2026-10-10

- Status: preparação feita; **Fase 1 ainda não iniciada** (aguarda o Marco configurar a instância uazapi de teste). Sem build, sem migração; imagem `gateway:7423fac`, `FILA_DURAVEL=on`.
- `max_concorrencia = 30` no agente `bykoji/bykoji_atendimento` (`PATCH /admin/v1/tenants/bykoji/agentes/bykoji_atendimento`); efetivo: 30 por empresa e 30 no agente. Registrado em `docs/CAPACIDADE.md`.
- Canal uazapi de teste criado no bykoji: `can_49fce1f15a43f87f22739d60` (“[teste] WhatsApp uazapi (Fase 1)”), ligado ao “Atendimento By Koji”, que continua em simulação com envio de teste (mock/dry). Sem credenciais ainda. A URL do webhook (contém segredo) foi gravada só em `.local/fase1_webhook_uazapi.txt` (600, fora do git) e não foi exibida.
  - Conferido por fora: `POST` na rota do canal com segredo errado → 401; `/gateway/health` → `{"ok":true}`.
- `scripts/canal_credencial.py` (novo): pede endereço base e token com `getpass`, valida o endereço com as regras de SSRF (https, 443, sem usuário/senha, DNS só para IP público) e grava cifrado pelo `PATCH` do canal. Recusa terminal não interativo; erros não repetem os valores. Testes: `tests/test_canal_credencial.py` (3). Suíte: **133 passed**, só bancos `_test`.
- Limpeza do `postman_teste`: **não executada**; backup `/home/twadmin/backups/gateway/pre-limpeza-postman-20261010T013732Z/gateway.dump` (21 tabelas) e contagem entregue ao Marco. Achado: `conversas.agente_id` e `conversas.canal_id` são obrigatórios e têm chave estrangeira, então não dá para apagar o agente e o canal do caso “aguardando” mantendo a conversa como registro. Aguardando decisão.
- uazapi (documentação oficial, `POST /webhook`): campos `url`, `events`, `excludeMessages`, `enabled`; a documentação não descreve reenvio de webhook quando o destino falha (há `/webhook/errors` para diagnóstico). Pendência da Fase 1 mantida.

## Limpeza do postman_teste e piloto do By Koji — 2026-10-10

- Limpeza (opção 1, autorizada pelo Marco; backup `pre-limpeza-postman-20261010T013732Z`), numa transação que só confirmava com as contagens combinadas:
  - Removido do teste INV14: 620 conversas, 1.240 mensagens, 620 turnos, 620 contatos, o canal `can_0c276f791ffdd5b141eeaaf5` e o agente `ag_002430f4999ada600015605f`.
  - Caso “aguardando”: canal `can_33e464559d7d941f750258ee` desativado e agente `ag_393bad0c761589e969d99555` pausado; a conversa `postman_teste:can_33e464559d7d941f750258ee:cliente-teste-aguardando` fica como registro.
  - Auditoria intacta (2.944 linhas antes e depois). Marcadores no Redis expiram sozinhos. Agente Postman e bykoji não tocados.
- Fase 1 (preparação): `bykoji/bykoji_atendimento` com `modo_piloto: true` e `contatos_permitidos` com o celular de teste informado pelo Marco; continua em simulação com envio de teste. Canal uazapi `can_49fce1f15a43f87f22739d60` **ativado** (canal novo nasce inativo e, inativo, o webhook é ignorado sem gravar o payload). Número da instância uazapi de teste informado pelo Marco: 5511957769899. Correção do Marco em seguida: esse é só o número da instância; `contatos_permitidos` passou a ter o celular que envia as mensagens de teste.
- Efeitos do piloto ligado: o `scripts/bench.py` recusa o agente (exige piloto desligado) e conversas simuladas com identificador fora da lista são gravadas e ignoradas.
- Achado: `tenant_canais.ultimo_evento_em` e `status_conexao` não são atualizados por nenhum código; ficam nulos mesmo com eventos chegando. A chegada se confere por `webhook_raw`. Corrigir junto com os ajustes da Fase 1.

## Fase 1 — uazapi no formato real — 2026-10-10

- Status: **implantado e parado para validação.** Imagem `gateway:03657d0` (volta: `gateway:7423fac`, conferida como a imagem em uso antes do build), **migração 016** aplicada com a imagem antiga rodando (health OK), schema 16, `FILA_DURAVEL=on`. bykoji em simulação com envio de teste, piloto com um contato; nenhum modo alterado.
- Antes: webhook da instância corrigido pela API (a URL tinha o prompt do terminal colado no fim; 151 → 129 caracteres), com autorização do Marco. Duas mensagens enviadas com a URL errada foram recusadas (400) e não reenviadas pela uazapi (`attempts: 1`).
- Payloads reais lidos (14, de 23:14 a 23:21 de Brasília): texto, texto do atendente (WhatsApp Web e celular da instância), áudio, imagem sem e com legenda, **localização em tempo real** (não veio pino comum), PDF de 21 MB, reação, texto citando mensagem.
- Feito (`03657d0`):
  - `app/canais/uazapi.py`: telefone pelo `chatid` (ou `sender_pn`/chat quando vem `@lid`), `messageid`, data/hora do provedor (`messageTimestamp` em ms → `mensagens.enviada_em`; fora de uma janela plausível vale o recebimento), `fromMe` → atendente, `wasSentByApi` → bot (ignorado, não pausa), grupos/canais/listas ignorados.
  - Tipos e `meta` (migração 016: `mensagens.tipo`, `mensagens.meta`): localização vira `[localização: lat, long — nome/endereço]` com coordenadas em `meta`; reação vira `[reação: 👍]`, fica no histórico, **não abre turno e não pausa** (inclusive reação do atendente); anexo vira `[não foi possível ler <tipo>: <motivo>]` (legenda preservada; acima de 15 MB o motivo é o tamanho); mensagem citada em `meta.resposta_a`.
  - `webhook_raw`: além de token/segredos, saem `BaseUrl`, miniaturas e forma de onda; `mediaKey` mascarada. As 14 linhas já gravadas foram limpas.
  - `tenant_canais.ultimo_evento_em` atualizado a cada evento autenticado. Anonimização (LGPD) também limpa `meta`.
  - Fixtures anonimizadas em `tests/fixtures/uazapi/` (10 casos; números, nomes, textos, ids, URLs, chaves e coordenadas trocados; verificação automática na geração).
- Validações: `sh scripts/test_suite.sh` → **138 passed** (5 novos em `tests/test_uazapi_real.py`; mutação conferida), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-fase1-20261010T023518Z/gateway.dump` (21 tabelas). Produção: 4 containers `healthy` na imagem nova, fila zerada, `/health` público `{"ok":true}`, admin por fora 404, com `X-Forwarded-For` 404, sem chave 401, zero segredos nos logs. O normalizador novo, rodado sobre os 14 payloads reais guardados, classificou todos como esperado.
- Conferido no roteiro real (com a imagem anterior): resposta do atendente gravada como `humano` e conversa pausada por 60 min; mensagens seguintes do cliente gravadas e não respondidas (turnos `ignorado`).
- Pendências: mandar uma mensagem nova para conferir a imagem nova de ponta a ponta (as 14 anteriores ficaram com o texto antigo `[mídia ainda não interpretada]`); pino de localização comum real; reenvio do webhook com a recepção fora do ar; conferir endpoints de envio e de presença; `status_conexao` (exige assinar o evento `connection`).

## Fase 1 — varredura de reconciliação, estado da conexão e envio — 2026-10-10

- Status: **implantado; Fase 1 pronta para fechar, faltando um teste ao vivo** (recepção fora por 1 min com o Marco enviando mensagens — só ele pode enviar). Imagem `gateway:a7db43a` (volta: `gateway:03657d0`, conferida), migração 017 aplicada com a imagem antiga rodando, schema 17, `FILA_DURAVEL=on`, nenhum modo alterado, nada enviado ao WhatsApp.
- Validado pelo Marco: Fase 1 (formato real); reação do atendente não pausa; limite de 15 MB; aviso de anexo ilegível até a Fase 5.
- Imagem `03657d0` conferida de ponta a ponta com mensagens novas (23:39–23:42): pino comum → `[localização: lat, long]` (`ao_vivo: false`), áudio de 6 s e imagem com o aviso de anexo, hora do provedor em `enviada_em`, `ultimo_evento_em` atualizado, payload bruto sem token/`BaseUrl`/miniatura. O 👍 veio como texto (emoji digitado), não como reação.
- Testes de leitura na instância (nenhuma escrita):
  - `POST /message/find` sem `chatid` → traz todos os chats (inclusive grupos), da mais recente para trás; paginação por `offset` (`hasMore`, `nextOffset` no topo da resposta). Mensagens vêm sem `wasSentByApi` nem `sender_pn`.
  - Retenção: 60.000 mensagens listadas sem chegar ao fim; a mais antiga vista é de 14/07/2026 (≥ 88 dias).
  - Limite: 40 chamadas em paralelo → 40 × 200 em 1,1 s; nenhum cabeçalho de limite; página de 100 ≈ 0,2 s e 300 KB.
- Feito (`a7db43a`):
  - **Varredura** (`app/canais/varredura.py`): no agendador (líder), na subida e a cada 90 s (`VARREDURA_UAZAPI_S`), por canal uazapi ativo com credencial: janela de 10 min, pagina do mais recente até o corte, ignora grupos, **só mensagens do cliente** (a busca não distingue bot de atendente; gravar errado pausaria o bot), carência de 20 s para o webhook chegar antes, dedup pelo mesmo `messageid`, recepção pelo caminho normal com `origem_recebimento='varredura'` (migração 017). Métrica em `GET /admin/v1/fila` (`varredura`) e alerta `canal.varredura_recuperou` na auditoria + log.
  - **Estado da conexão:** `status_conexao` pelo `GET /instance/status` a cada varredura e pelo evento `connection` (formato não documentado: leitura tolerante; o payload bruto fica guardado para conferir). Alerta `canal.desconectado` / `canal.reconectado` na auditoria + log, uma vez por mudança. Webhook da instância passou a assinar `messages` e `connection` (`excludeMessages` só `isGroupYes`; URL conferida, 129 caracteres).
  - **Envio e “digitando”:** `enviar()` = `POST /send/text {number,text}`; `digitando()` = `POST /message/presence {number,presence,delay}` (máx. 5 min; a instância cancela ao enviar). `digitando()` ainda não é chamado pelo pipeline.
  - Defeito achado pelos testes e corrigido antes de implantar: duas mensagens pendentes com o mesmo instante quebravam a ordenação da varredura.
- Validações: `sh scripts/test_suite.sh` → **141 passed** (8 em `tests/test_uazapi_real.py`), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-varredura-20261010T030007Z/gateway.dump` (21 tabelas). Produção: 4 containers `healthy` na imagem nova; primeira varredura 1 canal, 0 erros; `status_conexao = conectado`; `/health` público `{"ok":true}`; admin por fora 404, com `X-Forwarded-For` 404, sem chave 401; zero segredos e zero endereço da instância nos logs.
- **Prova em produção:** uma rodada única da varredura de produção com a janela recuada até 02:11:00 UTC recuperou exatamente 1 mensagem: o texto real das 02:11:37 que o webhook não entregou (URL errada na hora). Gravada como `cliente`, `origem_recebimento='varredura'`, hora do provedor preservada, auditoria `canal.varredura_recuperou`; rodada repetida → 0 (sem duplicar). Turno `ignorado` porque a conversa estava pausada pelo atendente.
- Limites conhecidos: mensagem do atendente perdida numa queda não é recuperada (bot não pausa por ela); chat identificado só por `@lid` na busca não tem telefone e é contado em `sem_telefone`; acima de 2.000 mensagens em 10 min a varredura avisa que não alcançou a janela.
- Pendências: teste ao vivo com a recepção fora por 1 min; formato real do evento `connection` (só aparece quando a instância mudar de estado).

## Fase 1 — id dos envios e mensagem do atendente na varredura — 2026-10-10

- Status: implantado (`gateway:675b350`; volta `gateway:a7db43a`, conferida nos 4 containers; sem migração; backup `pre-id-envio-20261010T031645Z`). Fase 1 ainda aguardando o teste ao vivo.
- Primeiro teste ao vivo (recepção parada 03:06:38–03:07:31 UTC): **inconclusivo**. A instância só registrou uma mensagem no intervalo (03:06:58), enviada **pelo número da instância**, não pelo celular de teste; nada do celular de teste chegou à instância. Achado útil: a uazapi recebeu 502, registrou `attempts: 1` e **não reenviou** — confirma que só a varredura cobre a recepção fora do ar.
- Feito (`675b350`), a pedido do Marco:
  - Todo envio ao vivo (resposta do bot, partes do turno, resposta pelo painel) grava o `messageid` devolvido pela uazapi em `mensagens.message_id`; se a resposta não trouxer id, a mensagem fica marcada (`meta.envio_sem_id`).
  - Varredura: mensagem da instância (`fromMe`) que não corresponde a nenhum envio do gateway = atendente pelo celular → gravada como `humano`, `origem_recebimento='varredura'`, conversa pausada (INV03). Se houver, na mesma conversa e a até 3 min, envio do gateway sem id (ou em andamento/incerto), a mensagem é contada como `ambiguas` e **não** é gravada. Registros de ligação são ignorados. Indicadores novos: `do_atendente`, `ambiguas`.
- **Não confirmado na prática:** a documentação da uazapi diz que `/send/text` devolve `messageid`/`id`, mas nenhum envio real foi feito (tudo em envio de teste). Se a resposta real vier sem id, a proteção acima evita pausa indevida e o indicador `ambiguas` mostra; conferir no primeiro envio real.
- Validações: `sh scripts/test_suite.sh` → **143 passed**, só bancos `_test`. Produção: 4 containers `healthy`, fila zerada, varredura sem erros, `/health` público `{"ok":true}`, modos inalterados.

## Fase 1 — Fechamento: teste ao vivo da varredura — 2026-10-10

- Status: **Fase 1 concluída.** Imagem `gateway:675b350`, schema 17, `FILA_DURAVEL=on`; bykoji em simulação com envio de teste e piloto com um contato; nada enviado ao WhatsApp.
- Teste ao vivo (só a recepção parada, de 03:20:03 a 03:22:30 UTC; workers, agendador, Redis e Postgres no ar):
  - Na instância, durante a queda: 6 mensagens na conversa de teste — 3 do cliente (03:20:48, 03:21:24 “Teste queda 1”, 03:21:34 “Teste queda 2”) e 3 do atendente pelo celular da instância (03:21:05, 03:21:10, 03:21:55 “Resposta atendente”).
  - A uazapi tentou o webhook uma vez por mensagem, recebeu 502 e **não reenviou**; nenhum payload novo chegou pela recepção.
  - A varredura recuperou as 6 em duas rodadas (03:21:32, ainda com a recepção parada, e 03:23:03): 3 como `cliente`/entrada e 3 como `humano`/saída, todas com `origem_recebimento='varredura'`, `enviada_em` com a hora original do provedor, 0 duplicadas; `bot_pausado_ate` renovado a cada mensagem do atendente (final 04:23:03); 2 alertas `canal.varredura_recuperou` na auditoria; indicador `do_atendente` e `ambiguas: 0`.
  - Conferência cruzada: as 10 mensagens que a instância tem dos últimos 15 min nessa conversa estão todas no gateway (4 pelo webhook, 6 pela varredura).
- Depois: recepção `healthy`, fila zerada, `status_conexao = conectado`.
- Ficam para as próximas fases: confirmar no primeiro envio real que `/send/text` devolve o id (proteção `ambiguas` ativa até lá); formato real do evento `connection`; ligar `digitando()` ao envio; leitura de anexos (Fase 5).

## Fase 2 — código do gateway: pacote, Retorno, INV04 e INV10 — 2026-10-10

- Status: **implantado e parado; a fase segue aberta até o Marco validar o fluxo no Skyone.** Imagem `gateway:fba9283` (volta: `gateway:675b350`, conferida nos 4 containers), **migração 018** aplicada com a imagem antiga rodando (health OK), schema 18, `FILA_DURAVEL=on`. **Nenhum modo alterado:** `bykoji_atendimento` continua em simulação, envio de teste, piloto ligado, sem URL nem credencial do Skyone. Nenhuma chamada ao Skyone.
- Feito (`fba9283`):
  - **Pacote (Apêndice A):** `agora` (ISO, fuso da empresa), `tempo_desde_ultima` em texto (nulo no primeiro contato), `retomada` (> 12 h), `mensagens_do_lote` (`enviada_em`, `tipo`, `texto`), `cliente` (`nome`, `unidade_preferida`, `resumo_anterior`), `canal`, `modo_resposta`, `tarefa`; `unidades` e `contexto_modulos` já existiam. O intervalo conta da última mensagem da conversa antes do lote até a primeira do lote.
  - **Retorno:** `resposta` como lista (um balão por item) ou texto único (um balão por parágrafo separado por linha em branco; antes saía tudo em um balão); `acao` `nenhuma | silencio | transferir_humano | encerrar` (`pedido_fechado` reservado); `motivo` e `desfecho`. Continuam obrigatórios `request_id`, `conversation_id`, `status: "success"` e `tipo: "text"`.
  - **INV04:** silêncio sem motivo (ou resposta vazia sem `silencio`) → uma reconsulta com o mesmo pacote; persistindo, nada é enviado (nem o fallback), bot pausado e conversa aguardando atendente com motivo `silencio_sem_motivo`; turno com erro `SILENCE_WITHOUT_REASON`.
  - **Transferência pedida pelo agente** passa a marcar a conversa como aguardando atendente (`transferido_pelo_agente`), além de pausar o bot.
  - **Migração 018:** `turnos.acao`, `turnos.motivo_silencio`, `turnos.desfecho`; dois motivos novos em `conversas.aguardando_motivo`.
  - `docs/SKYONE.md`: fluxo-modelo `atendente_delivery`, contrato de ida e volta e passo a passo de agente novo, com os nós marcados 🔲 aguardando o conteúdo validado.
- Validações: `sh scripts/test_suite.sh` → **148 passed** (5 novos em `tests/test_fase2_contrato.py`, incluindo o teste de contrato do pacote e o INV04 nos dois caminhos de processamento), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-fase2-20261010T034508Z/gateway.dump` (21 tabelas). Produção: 4 containers `healthy`; benchmark simulado no agente `[teste]` do bykoji **5/5, zero erros, p50 4,28 s**, turnos gravados com `acao = nenhuma`; fila zerada; `/health` público `{"ok":true}`; admin por fora 404, com `X-Forwarded-For` 404, sem chave 401; zero segredos nos logs; varredura sem erros.
- Observação de teste: `tests/test_fila_troca.py` (queda do Redis) e `tests/test_fila_justica.py` falharam uma vez cada em rodadas completas sob carga e passaram sozinhos e nas rodadas seguintes; são sensíveis a tempo. Ficam para revisar.
- Para o [PAINEL]: `aguardando_motivo` pode vir `transferido_pelo_agente` ou `silencio_sem_motivo`; turnos têm `acao`, `motivo_silencio`, `desfecho`.
- Pendências (dependem do Marco): fluxo validado e conteúdo dos nós para o `docs/SKYONE.md`; URL e credencial pelo painel; depois, bench com 1, 5 e 10 clientes em envio de teste (parar se `total_p95` > 15 s). **O bench recusa agente com piloto ligado** e os clientes do bench não estão na lista de contatos: será preciso desligar o piloto do “Atendimento By Koji” durante o bench (envio continua em teste) ou medir em outro agente com a mesma URL.

## Fase 2 — bench com IA real: PARADO (total_p95 > 15 s) — 2026-10-10

- Status: **parado pela regra do plano.** Fluxo `atendente_delivery` cadastrado pelo Marco no “Atendimento By Koji” (IA real, envio de teste). Sem build nem migração; imagem `gateway:fba9283`.
- `scripts/bench.py`: opção `--ia-real` (aceita agente em IA real; envio sempre de teste; piloto desligado) e `--texto` (mensagens do cliente simulado). Teste novo em `tests/test_bench.py`.
- Execução (04:24–04:26 UTC): piloto desligado só durante o bench e religado em seguida com `contatos_permitidos = ["5511942717046"]` (conferido). Cliente simulado: “oi” + “vocês entregam no Morumbi?” (debounce 4 s).

| Clientes | Turnos | Erros | agente_p50 | agente_p95 | total_p50 | total_p95 | Aviso de demora (15 s) |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 0 | 25,2 s | 25,2 s | 29,4 s | 29,4 s | 1 de 1 |
| 5 | 5 | 0 | 16,2 s | 24,2 s | 21,1 s | 29,0 s | 3 de 5 |
| 10 | não executado (parada) | | | | | | |

- Tempos do agente nos 6 turnos: 25,2 · 16,2 · 22,9 · 24,6 · 10,1 · 10,1 s. Espera na fila (inclui o debounce de 4 s): 4,2–4,9 s. Zero erros, zero silêncio; `acao = nenhuma` em todos; respostas em 1 ou 2 balões.
- Comparação: o fluxo anterior (mesma URL) respondia em 5,7–8,6 s (Fase 0.3) e 4,4 s no bench de 09/10. O tempo novo é do lado do Skyone (fluxo/agente novo), não do gateway: a espera na fila ficou no debounce.
- Não feito por causa da parada: bench com 10 clientes; as três conversas do Simular; `docs/SKYONE.md` (o conteúdo dos nós não foi enviado).

## Fase 2 — conversas do Simular com IA real e correção do debounce — 2026-10-10

- Status: validação funcional feita; **1 de 3 comportamentos não saiu como esperado (transferência)**. Latência segue acima da meta (o Marco investiga no Skyone). Imagem `gateway:ccf5113` (volta: `gateway:fba9283`, conferida nos 4 containers; sem migração; backup `pre-debounce-20261010T043909Z`).
- Conversas no Simular (“Atendimento By Koji”, IA real, envio de teste, piloto ligado; para a segunda conversa um número fictício entrou em `contatos_permitidos` só durante o teste e a lista voltou a `["5511942717046"]`, conferido):

| Mensagem | Agente | Total | Enviado | Gravado no turno |
|---|---|---|---|---|
| “oi, vocês entregam no Morumbi?” | 28,9 s | 33,0 s | aviso de demora + “Boa noite, Jussara!” + “Sim, entregamos no Morumbi. Você pode me dizer o que gostaria de pedir, a quantidade e o endereço completo?” | `acao=nenhuma` |
| “obrigado” (mesma conversa) | 15,2 s | 15,4 s | só o aviso de demora (“Só um instante, estou verificando.”) | `acao=silencio`, `motivo_silencio=agradecimento_final` ✅ |
| “quero falar com uma pessoa” (conversa nova) | 11,6 s | 15,8 s | “A equipe vai continuar o atendimento em instantes.” | `acao=nenhuma` ❌ (o fluxo devolveu `motivo=pediu_humano` com `acao=nenhuma`); conversa não ficou aguardando atendente nem pausada |

- Achados:
  - **Transferência:** o Retorno do fluxo veio com `acao: "nenhuma"`; o gateway só transfere com `acao: "transferir_humano"`. Ajuste no nó “Interpretar Resposta”/Retorno do Skyone.
  - **Aviso de demora antes de silêncio:** como o agente levou 15,2 s, o cliente recebeu “Só um instante, estou verificando.” e depois nada. Some quando a latência voltar para baixo de 15 s; se não voltar, rever `slow_notice_s` ou a regra.
  - **Defeito do gateway corrigido (`ccf5113`):** com a fila durável ligada, a marca da primeira mensagem do lote não era apagada (só o caminho antigo apagava) e ficava 24 h no Redis; a partir da segunda mensagem da conversa o debounce não valia (no teste, espera de 138 ms no “obrigado”). Mensagens picadas depois da primeira resposta virariam turnos separados. Corrigido e conferido em produção com o agente simulado: três turnos seguidos na mesma conversa com espera de 4,2 · 4,4 · 4,2 s (debounce 4 s). Existia desde que a fila foi ligada (10/10, ~01:19 UTC).
- `docs/SKYONE.md` **não atualizado:** o arquivo `docs/fase2_skyone_atendente_delivery.md` não existe no repositório nem em `/home/twadmin` (busca por nome).
- Validações: `sh scripts/test_suite.sh` → **151 passed**, só bancos `_test`. Produção: 4 containers `healthy`, fila zerada, `/health` público `{"ok":true}`, modos inalterados.

## Fase 2 — transferência conferida com o fluxo ajustado — 2026-10-10

- O Marco trocou o “Interpretar Resposta” no Skyone (normaliza `acao` e converte `nenhuma` + motivo `pediu_humano | pedido_para_confirmar | catering | reclamacao` em `transferir_humano`).
- Reteste no Simular (conversa nova, IA real, envio de teste; número fictício em `contatos_permitidos` só durante o teste, lista de volta a `["5511942717046"]`, conferido): “quero falar com uma pessoa” → enviado “Só um instante, estou verificando.” (aviso de demora) e “Boa noite! A equipe vai continuar o atendimento em instantes.”; turno com `acao=transferir_humano`, sem `motivo_silencio` nem `desfecho`; conversa **pausada** e **aguardando atendente** com motivo `transferido_pelo_agente`. Tempos: agente 20,4 s, total 24,8 s.
- Anotado para depois do teste A/B de latência (sem mudar agora): rever o aviso de demora (`slow_notice_s` = 15 s), que hoje sai antes de respostas de 15–29 s e antes de silêncio.
- `docs/SKYONE.md` continua pendente: `docs/fase2_skyone_atendente_delivery.md` não está no servidor (busca em todo o disco por nome e por arquivos `.md` recentes).

## Fase 2 — comparação de modelos: rodada das 14:18 UTC — 2026-10-10

- Nada alterado no gateway (imagem `gateway:ccf5113`). Piloto desligado só durante o bench e religado; números fictícios em `contatos_permitidos` só durante o Simular; lista de volta a `["5511942717046"]` (conferido). Envio sempre de teste.
- **O modelo desta rodada não veio na mensagem** (o campo ficou como `<GPT-4.1 mini | Gemini 3 Flash>`); a tabela em `docs/SKYONE_RESULTADOS.md` está com “a preencher”.
- Bench 5 clientes: 5/5, 0 erros; agente_p50 8,8 s, agente_p95 10,6 s; total_p50 14,4 s, **total_p95 15,8 s**; nenhum aviso de demora.
- Simular: “oi, vocês entregam no Morumbi?” 9,0 s (`nenhuma`); “obrigado” 9,9 s (`silencio`, `agradecimento_final`, nada enviado); “quero falar com uma pessoa” 7,6 s (`transferir_humano`, motivo `pediu_humano`, conversa pausada e aguardando atendente). Todas em português.
- Fora do formato JSON: o gateway não consegue contar (o Retorno chega igual); 0 de 8 respostas com indício visível no texto.

## Fase 2 — comparação de modelos: Gemini 3 Flash × GPT-4.1 mini — 2026-10-10

- Nada alterado no gateway (`gateway:ccf5113`). Mesma rodada nas duas vezes: bench de 5 clientes e três conversas do Simular em conversas novas; piloto desligado só durante o bench; números fictícios só durante o Simular; lista de volta a `["5511942717046"]` (conferido); envio sempre de teste.
- Rodada das 14:18 UTC identificada pelo Marco como **Gemini 3 Flash**; rodada das 14:25 UTC com **GPT-4.1 mini** (chave própria da OpenAI).

| Modelo | Erros | agente_p50 | agente_p95 | total_p50 | total_p95 |
|---|---|---|---|---|---|
| Gemini 3 Flash | 0 | 8,8 s | 10,6 s | 14,4 s | 15,8 s |
| GPT-4.1 mini | 0 | 10,7 s | 11,3 s | 15,2 s | 15,7 s |

- Simular (GPT-4.1 mini): 10,0 s (`nenhuma`), 11,5 s (`silencio`, `agradecimento_final`), 9,5 s (`transferir_humano`, `pediu_humano`, pausada e aguardando atendente). Tudo em português; 0 de 8 respostas com indício de texto fora do formato.
- Dúvida registrada: os textos das duas rodadas são quase iguais em estilo e os tempos parecidos; conferir no Skyone se a troca de modelo estava ativa. Detalhes e textos exatos em `docs/SKYONE_RESULTADOS.md`.

## Fase 2 — Fechamento — 2026-10-10

- **Fase 2 concluída** (decisão do Marco). `total_p95` = 15,7 s com 5 clientes, **aceito com a ressalva** de que o debounce adaptativo da Fase 3 deve trazê-lo abaixo de 15 s.
- **Modelo padrão de atendimento do By Koji: GPT-4.1 mini** (confirmado pelo Marco como o modelo da rodada das 14:25 UTC). **Gemini 3 Flash** testado como alternativa equivalente. **GPT OSS 20b proibido para atendimento.** Registrado em `docs/SKYONE.md`.
- Pendência que fica: completar `docs/SKYONE.md` com o conteúdo dos nós quando o guia `docs/fase2_skyone_atendente_delivery.md` chegar ao servidor.

## Fase 3 — Bloco 1: debounce adaptativo, aviso de demora e “digitando” — 2026-10-10

- Status: **implantado e parado para validação.** Imagem `gateway:123ca72` (volta: `gateway:ccf5113`, conferida nos 4 containers), **migração 019** aplicada com a imagem antiga rodando (health OK), schema 19, `FILA_DURAVEL=on`. “Atendimento By Koji” inalterado: IA real, **envio de teste**, piloto ligado com `["5511942717046"]`.
- Feito (`123ca72`):
  - **Debounce adaptativo:** texto que termina com `?`, `.`, `!` ou `…` (ignorando emoji/aspas depois) ou com mais de `debounce_palavras` palavras espera `debounce_curto_s`; mensagem curta ou picada, mídia, localização e reação esperam `debounce_s`. A última mensagem do lote decide. O curto nunca aumenta a espera (`min(debounce_s, debounce_curto_s)`); teto `max_wait_s` inalterado. Padrões: 1,5 s e 6 palavras; configuráveis por empresa e por agente (migração 019).
  - **Aviso de demora:** padrão de `slow_notice_s` de 15 para 20 s (vale para quem herda o padrão; o By Koji herda).
  - **“Digitando”:** com envio real em canal uazapi, pedido ao canal quando a consulta ao agente começa (25 s) e de novo depois do aviso de demora; retirado (`paused`) quando nada será enviado (silêncio, transferência sem texto). Em envio de teste nada é chamado; falha no “digitando” nunca atrasa nem derruba o turno. Só no caminho da fila durável.
- Sobre “nunca enviar aviso se a resposta final for silêncio”: **não é possível garantir**, porque o aviso sai enquanto o agente ainda não respondeu. Proposta registrada para decisão do Marco: não enviar o aviso quando o lote parece só um fechamento (texto curto sem pergunta, como “obrigado”, “ok”, “valeu”, “👍”); nesses casos a demora do agente fica só com o “digitando”. Não implementado.
- Validações: `sh scripts/test_suite.sh` → 154 passed + 1 falha de montagem de um teste novo (id de envio repetido no provedor simulado), corrigida e conferida nos arquivos afetados (**155 testes**; 4 novos em `tests/test_fase3_bloco1.py`), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-fase3-b1-20261010T144520Z/gateway.dump` (21 tabelas).
- Produção (agente simulado `[teste]` do bykoji): “vocês entregam no Morumbi?” → espera 1,78 s; “oi” → 4,27 s; “quero” / “um temaki” / “de salmão?” → **um turno com as 3 mensagens**, espera 1,72 s depois da última. 4 containers `healthy`, fila zerada, `/health` público `{"ok":true}`, admin por fora 404, zero segredos nos logs.
- Efeito esperado na latência: para pergunta completa, o total cai cerca de 2,5 s (de ~15,7 s para ~13 s no p95 medido). A medir com IA real quando o Marco autorizar novo bench.
- Para o [PAINEL]: campos novos de configuração `debounce_curto_s` (0,5–15) e `debounce_palavras` (1–50).

## Fase 3 — Bloco 1: aviso de demora em fechamento e novo bench — PARADO (total_p95 > 15 s) — 2026-10-10

- Bloco 1 validado pelo Marco. Ajuste pedido, implantado em `gateway:962c0d7` (volta `gateway:123ca72`, conferida nos 4 containers; sem migração; backup `pre-fechamento-20261010T150231Z`): lote que parece só fechamento (“obrigado”, “ok”, “valeu”, “blz”, emoji e afins; até 6 palavras, sem pergunta, só texto) **não recebe aviso de demora**; se o agente responder silêncio, nada é enviado. Saudações (“oi”, “bom dia”, “tudo bem”) e respostas (“sim”, “pode”) não contam como fechamento. Suíte: **158 passed**, só bancos `_test`.
- Bench de 5 clientes com IA real, envio de teste (15:03 UTC; piloto desligado só durante o bench e religado com `["5511942717046"]`, conferido). O Marco avisou que acabara de ligar uma chave própria da OpenAI (projeto “bykoji”) no agente: **não houve erro** (5/5, zero fallback).

| Rodada | Espera na fila | agente_p50 | agente_p95 | total_p50 | total_p95 |
|---|---|---|---|---|---|
| 14:25 UTC, debounce fixo de 4 s | 4,3–4,6 s | 10,7 s | 11,3 s | 15,2 s | **15,7 s** |
| 15:03 UTC, debounce adaptativo | 2,4–2,5 s | 12,4 s | 13,0 s | 15,0 s | **15,6 s** |

- **Parado pela regra combinada (total_p95 acima de 15 s): bloco 2 não iniciado.** O debounce tirou ~2 s da espera, mas o agente respondeu ~1,7 s mais devagar nesta rodada (4 de 5 chamadas entre 12,4 e 13,2 s), e o ganho foi consumido.
- Da espera de 2,4 s, 1,5 s é o debounce curto e ~0,9 s é o caminho do gateway (agendador, fila, vaga, montagem do pacote).

## Fase 3 — Bloco 2: envio real ligado no canal de teste — 2026-10-10

- Decisão do Marco: `total_p95` de 15,6 s **aceito** (o gateway entregou a redução; o restante é do Skyone/OpenAI). Debounce curto mantido em 1,5 s. **A meta de 15 s fica sob observação com dados reais; a alavanca para reduzir o tempo do agente é a Fase 2.5 (LLM direto), a avaliar depois do piloto.**
- Status: **envio real ligado e parado antes do teste pelo celular.** Sem build nem migração (`gateway:962c0d7`). Nenhuma mensagem foi enviada por mim.
- Conferência antes de ligar: os 5 agentes estavam em envio de teste; nenhuma empresa nem modelo define envio real; o fallback do “Atendimento By Koji” é o padrão da plataforma (o antigo `"/"` não existe mais); conversa de teste sem pausa, sem “aguardando” e sem mensagens pendentes; canal uazapi `conectado` e com credencial; nenhuma chave de API ativa no bykoji.
- Feito:
  - `send_mode=live` **só** em `bykoji/bykoji_atendimento`, com `modo_piloto: true` e `contatos_permitidos = ["5511942717046"]`.
  - O envio real é por agente, não por canal. Para valer “só no canal de teste”, o outro canal do agente (`can_6128998aa8cbfd1b9dc59812`, API de bench) foi **desativado**; fica ativo só o `can_49fce1f15a43f87f22739d60` (uazapi). Reativar o canal de bench quando o agente voltar a envio de teste.
  - `scripts/envio_real.sh` (novo): `estado` (quem está com envio real, piloto, canais) e `desligar` (freio: todos os agentes com envio real, ou um só, voltam a envio de teste).
- Conferido depois: `sh scripts/envio_real.sh estado` → 1 de 5 agentes com envio real (o do By Koji, piloto ligado, um contato, um canal ativo); fila zerada; varredura sem erros.
- A conferir no teste do Marco: id devolvido pela uazapi gravado em cada envio (pendência da Fase 1); regra do atendente pela varredura com envios reais (`ambiguas` = 0); “digitando”; silêncio sem aviso; transferência; áudio.
- Efeitos colaterais conhecidos: com envio real, o Simular e o bench desse agente recusam (exigem envio de teste).

## Fase 3 — Bloco 2 conferido com envio real e Bloco 3 implantado — 2026-10-10

- Status: **implantado e parado para validação.** Imagem `gateway:7b7c6f3` (volta: `gateway:962c0d7`, conferida nos 4 containers), **migração 020** aplicada com a imagem antiga rodando (health OK), schema 20. Envio real continua ligado **só** em `bykoji/bykoji_atendimento`, piloto com `["5511942717046"]`, um canal ativo (conferido com `sh scripts/envio_real.sh estado`: 1 de 5).
- **Primeiro envio real (bloco 2), “Oi” às 15:21:01 UTC:**
  - Resposta em dois balões enviados às 15:21:11 e 15:21:13: “Boa tarde, Jussara! Tudo bem?” e “Como posso ajudar você hoje? Se quiser fazer um pedido, é só me falar os itens e a unidade de preferência. 🍣”.
  - **Id da uazapi gravado nos dois envios** (pendência da Fase 1 resolvida: `/send/text` devolve o id).
  - Varredura: nas rodadas seguintes (até 15:29:59 UTC, dentro da janela de 10 min) `do_atendente = 0`, `ambiguas = 0`; nenhuma mensagem `humano` criada; conversa não pausada.
  - Turno: `acao = nenhuma`, agente 9,3 s, total 12,4 s, sem aviso de demora. A espera foi de 0,5 s em vez do debounce: sobra da marca antiga do defeito corrigido em `ccf5113` (durava 24 h). Removidas as 639 marcas antigas do Redis; daqui em diante o debounce vale desde a primeira mensagem.
  - O Marco não informou se a resposta chegou ao celular (campo da mensagem sem preencher); pelo gateway, as duas partes estão como `enviada`.
- **Bloco 3 (`7b7c6f3`):**
  - **INV03 antes do envio:** a checagem já existia (pausa conferida sob a trava antes de cada parte); agora a resposta descartada fica na auditoria (`resposta.descartada`).
  - **`#bot`:** mensagem do atendente pelo celular da instância com exatamente `#bot` tira a pausa e o “aguardando atendente”, não vai para o agente, fica gravada como `tipo = comando`; se havia mensagem do cliente esperando, o bot responde na hora. Vale também quando recuperada pela varredura.
  - **Encerramento:** `encerrar` grava `conversas.encerrada_em` e `desfecho`; mensagem nova do cliente reabre (`conversa.reaberta`). A lista de conversas devolve os dois campos.
  - **Conversa vazia:** job no agendador a cada 5 min encerra com `desfecho = vazia` a conversa em que o cliente só mandou o que o gateway não lê e ninguém falou por `encerrar_vazia_min`.
  - **SEG07 por contato:** `max_msgs_por_minuto_contato` (padrão 20, configurável); acima disso, 5 min sem chamar o agente (`conversas.limitado_ate`), mensagens gravadas, auditoria `contato.limite_excedido` e aviso no log.
  - **LGPD04:** telefones mascarados (`5511****7046`) em todo log, inclusive URLs das rotas; teste confirma que nenhum log do gateway traz conteúdo de mensagem.
- Validações: `sh scripts/test_suite.sh` → **165 passed** antes da implantação; depois, 3 testes de invariantes que faltavam com o nome do código (INV05, INV09, mensagem durante o processamento) → `tests/test_fase3_bloco3.py` com 10 passando (**168 no total**; só testes, sem mudança no código implantado). Backup `/home/twadmin/backups/gateway/pre-fase3-b3-20261010T153115Z/gateway.dump` (21 tabelas).
- Produção: 4 containers `healthy`; fila zerada; `/health` público `{"ok":true}`; admin por fora 404; log de acesso com o telefone mascarado; zero ocorrência do telefone de teste em claro e do segredo do webhook nos logs.
- Efeito do job de conversa vazia na primeira rodada: 3 conversas do bykoji encerradas como `vazia` — são conversas de outros contatos da instância de teste (fora do piloto) que só tinham anexo. Nada foi enviado a elas.
- Para o [PAINEL]: lista de conversas com `encerrada_em`, `desfecho`, `limitado_ate`; configuração `max_msgs_por_minuto_contato`; eventos novos de auditoria `conversa.reaberta`, `conversa.encerrada` (vazia), `contato.limite_excedido`, `resposta.descartada`; `conversa.devolvida` pode ter ator `atendente`.
- Pendências da Fase 3: roteiro pelo celular (Marco); **alerta de transbordo** para `alerta_telefone` (não estava na lista do bloco 3; não implementado); reativar o canal API de bench quando o agente voltar a envio de teste.

## Fase 4 — Bloco 1: catálogo, importação CSV, busca e unidades — 2026-10-10

- Status: **implantado e parado para validação.** Imagem `gateway:8646078` (volta: `gateway:7b7c6f3`, conferida nos 4 containers), **migração 021** aplicada com a imagem antiga rodando (health OK), schema 21. Envio real inalterado: só `bykoji/bykoji_atendimento`, piloto `["5511942717046"]`. Nenhum cliente real.
- Feito (`8646078`):
  - **Migração 021:** em `catalogo_itens`, `codigo` (único por empresa), `tipo`, `alcoolico`, `delivery`, `observacao`; `preco` passa a aceitar nulo e proíbe zero. Em `tenant_unidades`, `codigo`, `endereco`, `telefone`, `whatsapp_pedidos`, `formas_pagamento`, `taxa_entrega`, `pedido_minimo`, `area_entrega`, `aceita_retirada`, `regras`. Tabelas novas `carrinhos` (um aberto por conversa; `maioridade_confirmada`), `carrinho_itens` (preço unitário copiado do catálogo) e `pedidos` (número curto; `aguardando_loja | confirmado | recusado | cancelado`). A anonimização (LGPD) acompanha carrinho e pedido e apaga endereço e observações.
  - **Importação:** `POST /admin/v1/tenants/{t}/catalogo/importar` (simulação por padrão) e `scripts/importar_catalogo.py` (`--aplicar` para gravar). Upsert pelo `codigo`; o que some do arquivo fica inativo; erro em qualquer linha não grava nada; preço vazio vira nulo e zero é recusado.
  - **Busca do agente:** `GET /api/v1/catalogo/busca` com filtro de unidade, sem acento e tolerante a erro de digitação (ordenação por relevância no gateway; a extensão `pg_trgm` não está instalada e não foi necessária), só ativos.
  - **Unidades:** os campos novos na API administrativa e no bloco `unidades` do pacote ao agente (nulo = não informado; `whatsapp_pedidos` não vai ao agente). Listagem e resumo do catálogo para o painel.
- **O arquivo `.local/bykoji_catalogo.csv` não estava no servidor.** Foi gravado a partir do anexo da mensagem do Marco (UTF-8 com BOM, permissão 600, fora do git) e conferido: 888 linhas de item + cabeçalho, 11 colunas em todas, códigos em sequência completa por prefixo (MOR 1–250, EIN 1–193, VNC 1–222, GUE 1–223), prefixo coerente com a unidade. SHA-256 `b2f97e98…ea15e`. Como é uma transcrição, vale reimportar o arquivo original quando ele estiver no servidor (a reimportação só atualiza o que diferir).
- **Unidades do bykoji criadas** (4): morumbi, einstein, vila_nova_conceicao, patio_guedala, com endereço, telefone (Pátio Guedala sem) e horários do salão informados pelo Marco; `regras` avisa que os horários são do salão e o delivery está a confirmar (Morumbi: segunda não informada; Einstein: domingo fechado e taxa de 13% nos quartos, só texto). `whatsapp_pedidos`, `formas_pagamento`, `taxa_entrega`, `pedido_minimo`, `area_entrega` e `aceita_retirada` vazios.
- **Importação no bykoji:** simulação → 888 a inserir, 0 erros, nada gravado (conferido: 0 itens no banco). Real → 888 inseridos. Repetida → 0 inseridos, 0 atualizados, 888 inalterados.

| Unidade | item | adicional | opcao_executivo | regra | Total | Ativos | Com preço | Vendáveis | Alcoólicos |
|---|---|---|---|---|---|---|---|---|---|
| morumbi | 222 | 2 | 26 | 0 | 250 | 224 | 0 | 0 | 63 |
| einstein | 176 | 1 | 15 | 1 | 193 | 177 | 0 | 0 | 18 |
| vila_nova_conceicao | 200 | 2 | 20 | 0 | 222 | 202 | 0 | 0 | 62 |
| patio_guedala | 201 | 4 | 18 | 0 | 223 | 205 | 193 | 193 | 65 |
| **Total** | 799 | 9 | 79 | 1 | 888 | 808 | 193 | 193 | 208 |

  - Nenhum preço zero; 695 sem preço (nulo). No Pátio Guedala, 12 itens ativos sem preço (“preço sob consulta”).
- Validações: `sh scripts/test_suite.sh` → **173 passed** (5 novos em `tests/test_fase4_catalogo.py`: reimportar não duplica, preço vazio nulo e nunca zero, item removido fica inativo, busca sem acento, unidades e pacote), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-fase4-b1-20261010T160134Z/gateway.dump` (21 tabelas). Produção: 4 containers `healthy`, fila zerada; busca “tempura” no Pátio Guedala devolve 7 itens com preço e no Morumbi 7 com preço nulo; “yakisoba” (com erro) acha Yakissoba; “creme brulee” acha “Crème Brûlée de Matchá”.
- Observações para o Marco: no CSV, **“Heineken Zero” está com `alcoolico = sim`** nas três unidades em que aparece; todos os itens estão com `delivery = a_confirmar`; nenhuma chave de API ativa no bykoji (as Skills do Skyone vão precisar de uma com os escopos `ferramentas:catalogo` e `ferramentas:pedidos`, a criar no bloco 3).
- Efeito no atendimento do piloto: o agente passa a receber as 4 unidades (endereço, telefone, horários, regras) no pacote.

## Fase 4 — Bloco 2: carrinho e pré-pedido — 2026-10-10

- Status: **implantado e parado para validação.** Imagem `gateway:4cb04d9` (volta: `gateway:8646078`, conferida nos 4 containers), **migração 022** aplicada com a imagem antiga rodando (health OK), schema 22. Envio real inalterado (só o piloto). Nenhum cliente real.
- Origem do pedido: o Marco enviou duas vezes um texto colado com um desenho diferente (migrações 022/023 recriando tabelas, empresa por cabeçalho `X-Tenant-ID`, item sem preço entrando no carrinho, status do pedido alterável por qualquer chamador). **Não foi aplicado como estava**: conflita com a migração 021 e com as regras decididas por ele no bloco 1, e a empresa por cabeçalho quebraria o isolamento. O bloco foi feito pelo plano e pelas regras 1–3, aproveitando do texto: esvaziar o carrinho, cópia dos itens no pedido, nome e telefone do cliente no pedido, confirmação de maioridade como passo explícito e os webhooks de pedido.
- Feito (`4cb04d9`): módulo `app/modulos/pedidos/` com as rotas `/api/v1/carrinho/{conversa}` (ver, adicionar, alterar, remover, esvaziar, entrega) e `/api/v1/pedidos` (fechar, consultar, cancelar), por `X-API-Key` com escopo `ferramentas:pedidos`; lista e detalhe de pedidos para o painel; `contexto_modulos.pedidos` no pacote ao agente; webhooks de saída `pedido.criado` e `pedido.cancelado`; migração 022 (subtotal, taxa, cliente, álcool/maioridade e quem decidiu, no pedido). Regras e contrato em `docs/API_ADMIN.md`.
- Decisões tomadas na implementação (a validar): número do pedido com 5 dígitos aleatórios (fácil de ditar e de usar em “OK <numero>”); recusas com HTTP 422/404/409 trazendo `motivo` e o carrinho atual; preço que mudou no catálogo → `preco_alterado` (409) e o carrinho volta atualizado para o cliente reconfirmar; taxa de entrega só entra no total quando a unidade tem taxa cadastrada (senão fica nula e o total é o subtotal); cancelamento pelo agente só enquanto `aguardando_loja`.
- Validações: `sh scripts/test_suite.sh` → **177 passed** (4 novos em `tests/test_fase4_pedidos.py`: preço sempre do banco, item inativo/sem preço/de outra unidade recusado, conversa inexistente 404, delivery sem endereço 422, total = soma dos itens, álcool sem maioridade, mínimo, retirada, SEG06, reimportar CSV não mexe em carrinho/pedido, carrinho no pacote), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-fase4-b2-20261010T162620Z/gateway.dump` (24 tabelas).
- Produção, com chave temporária (criada e revogada no próprio teste; depois de revogada → 401) numa conversa simulada: 2 × Tempurá Moriawase (136) + 1 Sapporo (55) → subtotal 327; Toro → `sem_preco`; item do Morumbi → `sem_preco` (as outras unidades não têm preço); fechar sem maioridade → `falta_maioridade`; com maioridade e retirada → pedido de 5 dígitos, `aguardando_loja`, total 327; outra conversa → 404; cancelado pelo dono. Ficou no banco esse pedido de teste (cancelado) e o carrinho dele (fechado).
- Exposição: `/gateway/api/v1/carrinho/...` e `/gateway/api/v1/catalogo/busca` respondem 401 sem chave (com ou sem `X-Tenant-ID`); `/gateway/admin/...` continua 404 por fora. Nenhuma chave de API ativa no bykoji.
- Pendências do bloco 3: notificação e confirmação da unidade (painel e WhatsApp), avisos ao cliente, `pedido.confirmado`/`pedido.recusado`, alerta de transbordo, chave definitiva e `docs/SKILLS.md`.

## Fase 4 — Bloco 3: unidade, confirmações, aviso ao cliente, transbordo e Skills — 2026-10-10

- Status: **implantado e parado para o teste do Marco.** Imagem `gateway:9a77e6d` (volta: `gateway:4cb04d9`, conferida nos 4 containers), **migração 023** aplicada com a imagem antiga rodando (health OK), schema 23. Envio real ao cliente inalterado (só o piloto `5511942717046`).
- Especificação recebida como texto colado; seguida no que cabe no desenho atual. Diferenças registradas: status `confirmado`/`recusado` e webhooks `pedido.confirmado`/`pedido.recusado` (nomes do plano e do banco, em vez de `*_unidade`); aviso ao cliente sem prazo (“fica pronto em ~30 minutos” não foi incluído: o gateway não sabe o prazo); o aviso ao cliente é texto fixo do gateway na conversa.
- Feito (`9a77e6d`):
  - **Caixa de saída `avisos_internos`** (migração 023), enviada pelo agendador a cada ~2 s: `pedido_unidade`, `resposta_unidade`, `transbordo`, `pedido_cliente`. Gravada na transação do fato; um aviso por fato; unidade/alerta só saem de verdade com o agente do canal em envio real (senão `simulado`); 3 tentativas para internos, nenhuma repetição para o cliente.
  - **Unidade decide** pelo painel (`POST .../pedidos/{numero}/confirmar|recusar`) ou pelo WhatsApp (`OK <numero>` / `NÃO <numero> motivo`), só do número de pedidos de uma unidade dona do pedido. O primeiro vale; o segundo recebe 409 / “já estava…”. Recusa devolve os itens ao carrinho.
  - **Número de unidade e de alerta nunca viram cliente** (nem conversa, nem turno), inclusive pela varredura.
  - **Aviso ao cliente** de confirmação/recusa, pelo caminho das respostas do bot (piloto, pausa, envio de teste respeitados).
  - **Alerta de transbordo:** `tenants.whatsapp_alerta_transbordo`; sai quando a conversa entra em “aguardando atendente”, com telefone mascarado, motivo (e o motivo do agente na transferência), último pedido e endereço do painel.
  - `docs/SKILLS.md` com as 10 Skills (nome, descrição para o agente, parâmetros, exemplos), o que já vem no pacote e orientações de prompt.
  - Ajuste: o pedido de “digitando” deixou de ser cancelado no meio (espera terminar).
- Configuração aplicada no bykoji (temporária, pedida pelo Marco): `whatsapp_pedidos = 5511957769899` nas 4 unidades e `whatsapp_alerta_transbordo = 5511957769899`. **Esse é o número da própria instância de teste.**
- **Chave definitiva das Skills:** `key_2ae0d4a4e82a1034f55013ff` (“Skills Skyone — catálogo e pedidos”), escopos `ferramentas:catalogo` e `ferramentas:pedidos`, presa ao agente `bykoji_atendimento`, sem validade. Valor só em `.local/fase4_chave_skills.txt` (600, fora do git); não foi exibido.
- Validações: `sh scripts/test_suite.sh` → **182 passed** (5 novos em `tests/test_fase4_avisos.py`), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-fase4-b3-20261010T171052Z/gateway.dump` (24 tabelas).
- Produção, pela URL pública com a chave definitiva, numa conversa simulada: busca 200; pedido de teste (1 Edamame, retirada, observação “TESTE do gateway”) → `aguardando_loja`; **aviso à unidade `enviado`** (a uazapi aceitou o envio para o próprio número da instância); confirmado pelo painel → 200; recusar depois → 409 (`status: confirmado`); aviso ao cliente `descartado` (conversa simulada, fora do piloto e com canal inativo); o agente consulta e vê `confirmado`. Nenhuma conversa criada com o número da unidade; zero segredos nos logs (inclui a chave das Skills).
- Ficaram no banco 2 pedidos de teste (1 cancelado, 1 confirmado).
- A conferir no teste do Marco: se a resposta `OK <numero>` digitada no WhatsApp da própria instância chega pelo webhook (conversa “consigo mesmo”); com um número de unidade diferente do da instância isso não depende de nada especial.

## Gestão — especificação aprovada e Bloco G1 (horários da unidade) — 2026-10-10

- `docs/ESPEC_GESTAO.md` aprovada pelo Marco com 10 decisões (seção 9 do documento): bot `sempre` por padrão e By Koji em `unidades`; unidade fechada segue o modo de pedido da empresa (By Koji recusa); `abaixo_do_minimo` → `abaixo_minimo` (G4); CSV padrão `mesclar` e `substituir` com confirmação explícita; `info_unidades` no escopo `ferramentas:catalogo`; 13% do Einstein só texto; 20 últimas cópias do catálogo com desfazer; texto padrão de fora do horário; bairro com apelidos e `bairro_nao_reconhecido` (sem recusar); categorias por empresa. Corpo do documento ajustado para as decisões 4, 9 e 10.
- Status do G1: **implantado e parado para validação.** Imagem `gateway:bb33dd5` (volta: `gateway:9a77e6d`, conferida nos 4 containers), **migração 024** aplicada com a imagem antiga rodando (health OK), schema 24. Envio real só no piloto.
- Feito (`bb33dd5`):
  - `nucleo/horarios.situacao()`: função única — aberta agora (ou nulo sem horário), motivo (`inativa`, `pausa`, `excecao`, `horario`, `sem_horario`), fecha às, próxima abertura (procura até 35 dias), pausada até. Vários turnos, turno que cruza a meia-noite, turnos emendados, exceção por data (fechado ou horário especial), pausa até um horário ou indefinida (vencida é ignorada), fuso da unidade (nulo herda o da empresa). A forma antiga `estado()` continua valendo.
  - Grade validada contra sobreposição (inclusive turno que invade o dia seguinte e a virada domingo→segunda).
  - Rotas do painel: `pausar`, `reabrir`, `PUT horarios`, exceções (listar, criar/substituir, remover), `agenda`; unidade sempre devolvida com `situacao` e `fuso_efetivo`.
  - `gestao_alteracoes` (antes/depois só do que mudou, telefone mascarado) + `GET gestao/alteracoes`; cada escrita também gera o evento resumido no `audit_log`.
  - Pacote ao agente: `motivo_fechada`, `fecha_as`, `pausada_ate`, `pausa_motivo`.
  - Migração 024: `tenant_unidades.fuso`, `pausa_ate`, `pausa_indefinida`, `pausa_motivo`; `unidade_excecoes`; `gestao_alteracoes`. (As colunas de entrega da especificação ficam para o G4.)
- Validações: `sh scripts/test_suite.sh` → 188 passed e 1 falha em `tests/test_fila_justica.py` (INV07: pico de 6 conversas simultâneas numa empresa com limite 5); rodado de novo junto com os testes novos e os de unidades/catálogo → **24 passed**. Os 7 testes novos estão em `tests/test_gestao_horarios.py`. Backup `/home/twadmin/backups/gateway/pre-gestao-g1-20261010T202915Z/gateway.dump` (25 tabelas).
- **Ponto de atenção (não é deste bloco):** o teste de justiça entre empresas já falhou três vezes em rodadas completas com a máquina carregada (picos 9, 6 e 6 com limite 5) e passa sozinho. Pode ser só a medição do teste, mas também pode ser o limite por empresa estourando por um instante sob carga. Precisa de investigação antes de carga real.
- Produção (sábado, 17:30 em São Paulo): as 4 unidades do By Koji aparecem fechadas por `horario`, com próxima abertura às 19:00, no fuso `America/Sao_Paulo`; agenda do Morumbi: sábado e domingo com dois turnos, segunda fechada. 4 containers `healthy`, fila zerada, `/health` público `{"ok":true}`, admin por fora 404. Só leituras em produção: nenhuma pausa, exceção ou alteração foi criada.
- Contrato das rotas para o [PAINEL] em `docs/API_ADMIN.md` ("Gestão G1 — horários da unidade").

## INV07 — corrida real na vaga por empresa: corrigida, AINDA NÃO IMPLANTADA — 2026-10-10

- Status: **corrigido e testado; aguardando autorização do Marco para implantar.** Produção continua em `gateway:bb33dd5` (com o defeito).
- Investigação da falha intermitente de `tests/test_fila_justica.py` (pico 6 e 9 com limite 5): **não era a medição do teste; o limite estourava de verdade.**
- Causa: a vaga da empresa/agente era reservada pelo worker **antes** de obter a trava da conversa, e a reserva aceitava a mesma conversa de novo ("já consta → vaga obtida"). Quando chegava um **segundo item da mesma conversa** enquanto a primeira execução ainda chamava o agente, o segundo worker "reservava" a mesma vaga, saía como `ocupado` (a trava era do primeiro) e, ao sair, **devolvia a vaga** — que ainda estava em uso. Outra conversa entrava no lugar: limite estourado.
  - Quando acontece em produção: o cliente manda mais uma mensagem enquanto o agente responde (caso comum), ou um item é retomado por outro worker depois de 90 s parado. Cada ocorrência libera uma vaga a mais. Nos testes, a retomada é de 0,2 s, por isso só aparecia com a máquina carregada.
  - Existe desde o bloco 3 da Fase 0.6 (vagas compartilhadas) e vale desde que a fila durável foi ligada. Impacto até aqui: nenhum cliente real; com carga real, o limite por empresa (e a cota do fluxo Skyone que ele protege) podia ser ultrapassado.
- Correção (reserva continua atômica em Lua): a vaga passa a ser reservada **depois** de obter a trava da conversa e devolvida **antes** de soltá-la, dentro de `Pipeline.processar_duravel` (classe `Vaga` em `nucleo/fila.py`). Só quem processa a conversa mexe na vaga dela; o segundo item sai como `ocupado` sem tocar em nada. Sem vaga → `sem_vaga`, reagendado em 0,3 s como antes (sem passar pelo debounce de novo). Conversa que consta na vaga com a trava livre é sobra de execução que caiu: quem pega a trava assume a mesma vaga.
- Testes novos (`tests/test_fila_vaga.py`): reprodução determinística (limite 1; item repetido da conversa em andamento; a outra conversa da empresa tem de receber `sem_vaga`) e estresse em 3 rodadas (3 workers, 46 conversas, limite 5 e 2, retomada de 30 ms e itens repetidos injetados no meio do processamento).
  - **Antes da correção:** reprodução falha (a segunda conversa entra em processamento) e o estresse falha 3 de 3 (pico 8 com limite 5; 3 com limite 2).
  - **Depois:** os 4 passam; nenhuma conversa respondida duas vezes, nenhuma perdida, nenhuma vaga presa.
- Suíte completa 3 vezes seguidas (`sh scripts/test_suite.sh`, só bancos `_test`): **193 passed · 193 passed · 193 passed** (6:50, 7:24, 7:20). O teste de justiça que falhava passou nas três.
- Para implantar: sem migração; build, tag e recriação de gateway, workers e agendador (volta: `gateway:bb33dd5`).

## INV07 implantado — 2026-10-10 21:15 UTC

- Autorizado pelo Marco (3 rodadas 193 passed). Tag de volta `gateway:bb33dd5` conferida nos 4 containers; backup `/home/twadmin/backups/gateway/pre-inv07-20261010T211425Z/gateway.dump` (27 tabelas, 600); sem migração; build → `gateway:d8895c7`; recriados gateway, 2 workers e agendador.
- Conferido depois: 4 containers `healthy` na imagem nova; fila zerada e nenhuma vaga ocupada; `/health` público 200; admin por fora 404 (também com `X-Forwarded-For`); `/api/v1` sem chave 401; envio real em 1 de 5 agentes (By Koji, piloto, um contato); zero erros nos logs.

## Gestão G2 — Skill info_unidades, histórico G1.1 e telefone E.164 — 2026-10-10 21:35 UTC

- Status: **implantado; aguardando validação do Marco.** Código `622ed66`, imagem `gateway:622ed66`, schema **25**. Volta: `gateway:d8895c7` (a imagem antiga roda sobre o schema 25, mas não sobe do zero nele: a checagem de schema no início exige 24).
- Telefones antes da migração (produção, todos os tenants): 4 unidades com `whatsapp_pedidos` e 1 empresa com `whatsapp_alerta_transbordo`, todos com 13 dígitos; **zero fora de 10–15**. Nada foi alterado nem apagado.
- Migração `025_telefone_e164.sql`: troca as duas restrições para 10–15 dígitos. Aplicada com a imagem antiga no ar (health 200 depois). Se houvesse valor fora da regra, ela falharia sem mudar nada (testado).
- `GET /api/v1/unidades` (`app/modulos/unidades.py`), escopo `ferramentas:catalogo`, empresa pela chave, `conversation_id` obrigatório, `unidade` opcional. Situação e agenda pelas mesmas funções do G1. Contrato em `docs/SKILLS.md` (Skill 11).
- `GET /admin/v1/tenants/{t}/gestao/alteracoes`: `desde`/`ate` com fuso, `unidade_id` (unidade + exceções, sem migração: lido do `entidade_id` já gravado), `criado_em` com offset, `limite` 1–200. Contrato em `docs/API_ADMIN.md`.
- Decisões tomadas na implementação (a validar): sem filtro, a Skill devolve só unidades ativas; com `unidade=<codigo>` devolve a unidade mesmo desativada (`ativa: false`). `delivery` vem `null` (não existe esse cadastro até o G4). `taxa` já vem como objeto `{"modo":"fixa","valor"}` para o G4 acrescentar modos sem quebrar. O bloco `atendimento` da ESPEC 6.1 fica para o G3. `telefone_whatsapp` da unidade (campo antigo) continua 8–16: não estava no pedido.
- Validações: `sh scripts/test_suite.sh` → **197 passed** (4 novos em `tests/test_gestao_g2.py`), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-gestao-g2-20261010T213222Z/gateway.dump` (27 tabelas, 600).
- Produção (só leituras): `info_unidades` com a chave das Skills → 4 unidades do By Koji, agenda de 7 dias, sem WhatsApp interno no retorno; sem chave/chave errada 401; conversa de outra empresa 404; unidade inexistente 404. Histórico: `unidade_id` 200, sem fuso 400, limite 201 400, `unidade_id` com `entidade` 400. 4 containers `healthy`, fila zerada, admin por fora 404, `/api/v1/unidades` sem chave 401, envio real só no piloto, zero erros e zero ocorrência da chave nos logs.

## Gestão G2 — ajuste final da info_unidades — 2026-10-10 22:34 UTC

- Status: **implantado; aguardando validação do Marco.** Código `11c9083`, imagem `gateway:11c9083`, schema 25 (sem migração). Volta: `gateway:622ed66`.
- A pedido do Marco: campos `aceita_retirada` e `taxa_entrega` (número) no lugar de `retirada` e `taxa` (objeto); sem filtro a Skill devolve **todas** as unidades (desativada com `ativa: false`, depois das ativas); saiu o campo `fuso`. Mantidos além da lista dele: `telefone` (público, já vai no pacote) e `situacao.pausa_motivo`. A Skill ainda não existia no Skyone, então nada quebrou.
- Testes: **198 passed** (5 em `tests/test_gestao_g2.py`: isolamento, conversa de outra empresa, unidade inexistente com o mesmo envelope, sem horário, pausada, exceção hoje, turno que cruza a meia-noite, escopo errado 403). Backup `/home/twadmin/backups/gateway/pre-gestao-g2b-20261010T223304Z/gateway.dump` (27 tabelas, 600).
- Produção (só leituras): 4 unidades do By Koji, sem id interno nem WhatsApp no retorno; 401/404/400 conferidos; 4 containers `healthy`, fila zerada, admin por fora 404, envio real só no piloto, zero erros e zero ocorrência da chave nos logs.

## UX1 — botões interativos: levantamento e especificação — 2026-10-10 22:41 UTC

- Status: **`docs/ESPEC_BOTOES.md` escrita; nada implementado; aguardando aprovação do Marco e o clique no teste.**
- Teste real autorizado: UMA mensagem `POST /send/menu` (`type: button`, 2 botões de resposta) ao piloto às 22:39:43 UTC → HTTP 200, `messageType: NativeFlowMessage`, id do provedor devolvido. Gravada em `mensagens` como envio do bot (para a varredura não tratar como atendente). O webhook da própria mensagem veio com `wasSentByApi: true` e foi ignorado. Conversa do piloto sem pausa.
- Pendente: o payload do clique em `webhook_raw` (seção 1.3 da ESPEC). Até o B1, um clique chega ao agente como anexo não suportado e o agente responde de verdade ao piloto.

## UX1 B1 — ler o clique em botão — 2026-10-10 23:00 UTC

- Status: **implantado; aguardando validação do Marco.** Código `14c2843`, imagem `gateway:14c2843`, schema 25 (sem migração). Volta: `gateway:11c9083`.
- `docs/ESPEC_BOTOES.md` aprovada pelo Marco (decisões na seção 8; ordem B1 → B2 → B4, depois G4; B3 e B5 junto do G3). Seção 1.3 preenchida com o payload real do clique (registro 104 de `webhook_raw`, mascarado): `messageType: TemplateButtonReplyMessage`, `text` vazio, título em `content.selectedDisplayText`/`vote`, id em `content.selectedID`/`buttonOrListid`, mensagem de origem em `quoted`/`contextInfo.stanzaID`.
- Entrega: `uazapi.normalizar()` transforma o clique em texto do cliente com o título do botão e guarda `meta.interacao`; `skyone.interacao()` e o pacote com `ultima_interacao` (nulo sem clique) e `interacao` no item do lote; `atual=false` para botão de mensagem antiga; clique em mensagem que o bot não enviou nesta conversa = texto puro. Nenhum envio de botão ainda (B2).
- Testes: **201 passed** (3 novos em `tests/test_botoes.py`, com a fixture real `tests/fixtures/uazapi/clique_botao.json`), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-ux1-b1-20261010T225934Z/gateway.dump` (27 tabelas, 600).
- Produção (só leituras): o clique real relido pelo código novo vira texto "Sim, funcionou" com `interacao.id = teste_sim`; `ultima_interacao` para ele hoje daria `atual: false` (o bot falou depois). 4 containers `healthy`, fila zerada, admin por fora 404, envio real só no piloto, zero erros nos logs.
- Diagnóstico das 22:53 UTC (só leitura): a mensagem do piloto chegou e o turno foi `ignorado` porque, às 22:52:07, o mesmo texto foi enviado pelo WhatsApp Web da instância (fromMe, sem `wasSentByApi`) no chat do piloto → tratado como atendente → pausa de 60 min. Não retomei; na conferência das 23:00 a pausa já não existia (retomada por fora desta sessão).

## Piloto: segundo contato — 2026-10-10 23:20 UTC

- Por ordem explícita do Marco, `contatos_permitidos` do agente `bykoji/bykoji_atendimento` passou de `["5511942717046"]` para `["5511942717046", "5511947281513"]` (API admin, `X-Actor: marco`, auditado como `agentes.alterado`). Envio real continua em 1 de 5 agentes, piloto ligado, mesmo canal.

## UX1 B2 — botões de resposta do agente — 2026-10-10 23:29 UTC

- Status: **implantado e LIGADO só no agente `bykoji/bykoji_atendimento`** (ordem do Marco, `X-Actor: marco`). Código `90bbfdc`, imagem `gateway:90bbfdc`, schema **26**. Volta: `gateway:14c2843` (roda sobre o schema 26; não sobe do zero nele).
- Entrega: `nucleo/botoes.py`, `uazapi.enviar_menu()` (`/send/menu`, `type: button`, só resposta), plano B em texto numerado, `mensagens.meta.botoes`, `botoes_interativos` (migração 026 em `tenant_config` e `agentes`, padrão `off`), bloco `botoes` em `GET /metricas`. Detalhes fixados na seção 9 de `docs/ESPEC_BOTOES.md` (erro incerto não reenvia; 4xx vira texto).
- Testes: primeira rodada 204 passed + 1 failed (teste do G2 que assumia a 025 como última migração; corrigido o teste); rodada final **205 passed**, só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-ux1-b2-20261010T232751Z/gateway.dump` (27 tabelas, 600). Migração 026 aplicada com a imagem antiga no ar (health 200).
- Conferência: 4 containers `healthy`, fila zerada, admin por fora 404, `/api/v1` sem chave 401, envio real em 1 de 5 agentes, zero erros nos logs.
- `botoes_interativos` antes: `off` (origem plataforma) nos 5 agentes. Depois: `on` (origem agente) só em `bykoji/bykoji_atendimento`; os outros 4 seguem `off`.
- Freio: `PATCH /admin/v1/tenants/bykoji/agentes/bykoji_atendimento {"botoes_interativos":"off"}` (ou `null` para voltar a herdar).

## UX2 — diagnóstico das picotadas e especificação — 2026-10-10 23:37 UTC

- Status: **`docs/ESPEC_UX2.md` escrita; nada implementado; aguardando aprovação do Marco.**
- Botões: o teste do Marco ("quero 1 edamame do Guedala", 23:24 UTC, contato final 1513) foi **antes** de o B2 entrar (containers novos às 23:28:03; `botoes_interativos=on` às 23:28:39). Nenhum turno depois disso até 23:37.
- Picotadas (25 turnos de hoje no WhatsApp do By Koji): 24 com 1 mensagem, 1 com 2; espera média 4,3 s; agente média 12,2 s (p95 14,9 s); 4 turnos atropelados por mensagem nova (4 com o agente pensando, 1 com a resposta saindo). Causa principal: o agente demora mais que o intervalo entre mensagens do cliente e a espera só vale antes de chamar o agente. Proposta: espera em três faixas (P1), refazer o turno uma vez quando chega mensagem antes de a resposta sair (P2), "digitando" do cliente se a uazapi entregar (P3, depende de teste).
- Reação: teste real autorizado, UMA reação 👍 na última mensagem do piloto às 23:35:48 UTC (`POST /message/react {id,text}`) → 200, `ReactionMessage`; webhook próprio com `wasSentByApi: true`, ignorado; gravada em `mensagens` como reação do bot; conversa sem pausa.
- Presença: evento `presence` existe na documentação, sem formato; a instância assina só `messages` e `connection`. Teste depende de autorização para assinar o evento.
- Carrossel: `POST /send/carousel` existe (cartões com texto, mídia e botões REPLY/URL/COPY/CALL); limites e comportamento no Web não documentados. Nenhum teste enviado.

## UX2 P1 + P2 — espera adaptativa e refazer o turno — 2026-10-11 00:02 UTC

- Status: **implantado; `refazer_turno` DESLIGADO em todos os agentes** (o Marco liga depois). Código `2297054`, imagem `gateway:2297054`, schema **27**. Volta: `gateway:90bbfdc` (roda sobre o schema 27; não sobe do zero nele).
- `docs/ESPEC_UX2.md` aprovada (decisões na seção 7; andamento e contrato de `acoes_ja_executadas` na seção 8).
- P1: `forma_da_mensagem()` / `debounce_da_mensagem()` em três faixas; `debounce_incompleta_s` (nulo na plataforma = como antes). Única mudança geral: reticências deixaram de contar como frase completa.
- P2: `_deve_refazer`, `_descartar`, `_refeito_de`; `skill_chamadas` (ligação por conversa + janela do turno); `acoes_ja_executadas` no pacote (lista vazia em turno comum); `turnos.refeito_de` e `refazer_recusado`; bloco `picotadas` em `GET /metricas`.
- Presença: evento `presence` é descartado na entrada do webhook, guardando só os 20 primeiros.
- Testes: **208 passed** (3 novos em `tests/test_ux2.py`, incluindo carrinho sem duplicar, limite de um refazer, efeitos que impedem, queda do worker; 1 teste antigo de debounce ajustado para as reticências), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-ux2-p1p2-20261011T000017Z/gateway.dump` (27 tabelas, 600). Migração 027 aplicada com a imagem antiga no ar (health 200).
- Conferência: 4 containers `healthy`, fila zerada, admin por fora 404, `/api/v1` sem chave 401, envio real em 1 de 5 agentes, zero erros nos logs.
- Configuração aplicada só em `bykoji/bykoji_atendimento` (aprovada pelo Marco): `debounce_incompleta_s` nulo → 7; `debounce_curto_s` 1,5 → 2,5; `debounce_s` 4 e `max_wait_s` 12 sem mudança. Demais agentes inalterados.
- Assinatura do webhook da instância (autorizada): eventos `messages, connection` → `messages, connection, presence`, às 00:01:47 UTC; mesma URL (129 caracteres), mesmo `excludeMessages`. Nenhum evento de presença recebido até 00:02. **Pendente:** olhar a amostra e decidir o P3; se não servir, voltar a assinatura (`presenca.py voltar`).
- Botões em produção (contato final 1513, 23:55–00:00 UTC): 2 mensagens saíram como botão ("Entrega / Retirada" e "Adicionar mais / Finalizar pedido"), nenhum plano B; 1 clique recebido ("Adicionar mais", `atual: true`).
- Observado: conversa uazapi do piloto final 7046 pausada até 00:47 UTC por 3 mensagens enviadas pelo celular da instância (23:45–23:47, `fromMe`, origem `android`). Não retomei.

## Presença, retomada do piloto, refazer_turno ligado, campanha (espec.) e teste da lista — 2026-10-11 00:08 UTC

- **Presença:** o piloto digitou ~5 s sem enviar; **nenhum evento chegou** (zero chamadas ao webhook entre 00:01:47 e 00:06:45). Não serve para o P3 como está. Assinatura da instância **voltou** para `messages, connection` às 00:07 (mesma URL). P3 suspenso. Registro na seção 9 de `docs/ESPEC_UX2.md`.
- **Conversa do piloto final 7046:** pausa (até 00:47, criada pelas ofertas enviadas pelo celular da instância) removida por `POST /conversas/{conv}/devolver`, `X-Actor: marco`, 00:07:18 (`conversa.devolvida`).
- **`refazer_turno`:** `on` SÓ em `bykoji/bykoji_atendimento` (`X-Actor: marco`, 00:07:19); os outros 4 agentes seguem `off` (plataforma).
- **Campanha manual:** especificação na seção 10 de `docs/ESPEC_UX2.md` (bloco C1). Nada implementado.
- **B3, teste real autorizado:** UMA lista (`/send/menu`, `type: list`, `listButton: "Ver unidades"`, seção "Unidades", 4 opções `título|id|descrição`) ao piloto final 7046 às 00:07:51 → HTTP 200, `messageType: ListMessage`, id devolvido; gravada como envio do bot; webhook próprio com `wasSentByApi: true`, ignorado. **Pendente: o clique**, para ver o payload.
- Achado de cadastro (não alterado): unidade `einstein`, sábado com segundo turno `19:00–17:00` (cruza a meia-noite até domingo 17h) — provável erro de digitação; por isso a descrição saiu "Aberta até amanhã 17h".

## UX3 — janela do aviso de demora, expiração de carrinho e data do carrinho — 2026-10-11 00:31 UTC

- Status: **implantado**. Código `23e6225`, imagem `gateway:23e6225`, schema **28**. Volta: `gateway:2297054` (roda sobre o schema 28; não sobe do zero nele).
- Aviso de demora: não sai se o bot enviou algo na conversa dentro de `aviso_demora_janela_s` (padrão 120 s; 0 desliga a trava) nem em turno refeito.
- Carrinho: aberto e sem alteração há mais de `carrinho_expira_h` (padrão 6 h) vira `expirado` na próxima mensagem do cliente, antes do pacote, com auditoria `carrinho.expirado`. Pacote: `contexto_modulos.pedidos.carrinho_expirado`, `itens_expirados` (nome e quantidade, por até 24 h ou até novo carrinho) e `carrinho.atualizado_em` (ISO com fuso da empresa). Pedido fechado não é afetado.
- Migração 028 (aditiva; `carrinhos.status` passa a aceitar `expirado`, `carrinhos.expirado_em`), aplicada com a imagem antiga no ar.
- Testes: **210 passed** (2 novos em `tests/test_ux3.py`), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-ux3-20261011T002942Z/gateway.dump` (28 tabelas, 600).
- Conferência: 4 containers `healthy` na imagem nova, fila zerada, admin por fora 404, `/api/v1` sem chave 401, envio real em 1 de 5 agentes (piloto com 2 contatos), zero erros nos logs. Os dois campos novos valem 120 s / 6 h em todos os agentes, herdados da plataforma.
- Na mesma rodada (antes do UX3): diagnóstico do teste de mensagens picotadas (seção 11.1 da `docs/ESPEC_UX2.md`); carrinho aberto da conversa do contato final 1513 esvaziado (`carrinho.esvaziado`, ator `marco`); nenhum clique da lista de teste chegou ao `webhook_raw`; decisões do C1 registradas (seção 10.6); P3 suspenso.
- Ordem: B3 lista (aguarda liberação do Marco e o clique) → C1 → R1 → B4 → carrossel (estudo) → G4.

## UX3.1 — segundo refazer, acoes_recentes e carrinho esvaziado no estado inicial — 2026-10-11 00:55 UTC

- Status: **implantado**. Código `dd6f93b`, imagem `gateway:dd6f93b`, schema **29**. Volta: `gateway:23e6225` (roda sobre o schema 29; não sobe do zero nele).
- Segundo refazer: até 2 por turno (`MAX_REFAZER`); o 2º só se o turno refeito não chamou Skill nem saiu balão, e se o turno original começou há até 30 s (`TETO_REFAZER_S`). Motivos novos em `turnos.refazer_recusado`: `skill_chamada`, `balao_enviado`, `teto_de_espera`. O 1º refazer segue sem teto.
- `acoes_recentes`: sempre no pacote; chamadas de Skill da conversa nos últimos `acoes_recentes_min` minutos (padrão 15; 0–1440), até 20, com `ha_segundos`. Contrato na seção 12.2 de `docs/ESPEC_UX2.md`.
- `esvaziar_carrinho` limpa também unidade, modalidade, endereço, horário, observação e maioridade (pedido do Marco no meio do bloco). Expiração: o agente passa a ver um carrinho novo, inicial.
- Testes: **213 passed** (3 novos em `tests/test_ux31.py`; `tests/test_ux2.py` ajustado ao limite de dois), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-ux31-20261011T005441Z/gateway.dump` (28 tabelas, 600). Migração 029 aplicada com a imagem antiga no ar.
- Conferência: 4 containers `healthy` na imagem nova, fila zerada, admin por fora 404, `/api/v1` sem chave 401, envio real em 1 de 5 agentes, zero erros nos logs.
- B3: a MESMA lista de teste reenviada ao contato final 1513 (celular do Marco) às 00:55:28 UTC → HTTP 200, `ListMessage`. **Pendente: o clique**, para mostrar o payload.

## PED1 — especificação do ciclo de status do pedido — 2026-10-11

- `docs/ESPEC_PED1.md` escrita (levantamento + proposta + 7 decisões na seção 10). **Nada implementado; aguardando aprovação do Marco.** Entra antes do B3.
- Achado do levantamento: o aviso ao cliente de pedido confirmado/recusado não sai quando o bot está pausado por atendente (fica `descartado`); o PED1 corrige. Nome do contato só é gravado na primeira mensagem (5 de 103 conversas do By Koji têm nome).

## PED1-a — ciclo de status do pedido e aviso transacional — 2026-10-11 01:22 UTC

- Status: **implantado**. Código `e5cfc03`, imagem `gateway:e5cfc03`, schema **30**. Volta: `gateway:dd6f93b` (roda sobre o schema 30; não sobe do zero nele).
- `docs/ESPEC_PED1.md` aprovada (decisões na seção 11); contrato final do PED1-a para o painel na seção 12.
- Máquina de status em `modulos/pedidos/servico.py` (`conferir`, `proximos`, `transicionar`), linha do tempo em `pedido_eventos` (reconstruída para os 4 pedidos existentes), `previsao_min`.
- Aviso ao cliente: `avisos.TEXTOS` (textos do Marco, fixos até o PED1-b), trecho opcional `[[ ]]`, `Pipeline.enviar_transacional()` — sai com o bot pausado, respeita piloto e envio de teste, grava no histórico como `sistema`. Corrige o aviso que ficava `descartado` com atendente na conversa.
- Rotas do painel: lista com filtros/busca, detalhe com `proximos_status` e `linha_do_tempo`, `POST .../status`, `GET .../contagem`; `confirmar`/`recusar` mantidos. Skill `consultar_pedido` com situação e linha do tempo; `GET /api/v1/pedidos?conversation_id=` (3 últimos); `ultimos_pedidos` no pacote.
- Testes: **217 passed** (4 novos em `tests/test_ped1a.py`; `tests/test_fase4_avisos.py` ajustado às regras novas), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-ped1a-20261011T012110Z/gateway.dump` (28 tabelas, 600). Migração 030 aplicada com a imagem antiga no ar (health 200).
- Conferência: 4 containers `healthy`, fila zerada, admin por fora 404, `/api/v1` sem chave 401, envio real em 1 de 5 agentes, zero erros nos logs, nenhum aviso pendente. Nenhum pedido de produção foi alterado.
- Próximos: PED1-b (textos por empresa, liga/desliga, prévia, reenvio, `X-Role`) e PED1-c (conversas e contatos), cada um com liberação do Marco. B3 aguarda o toque na lista pelo aparelho do final 1513.

## PED1-b — avisos de pedido por empresa, prévia, reenvio e X-Role — 2026-10-11 01:40 UTC

- Status: **implantado**. Código `84fccaa`, imagem `gateway:84fccaa`, schema **31**. Volta: `gateway:e5cfc03` (roda sobre o schema 31; não sobe do zero nele).
- `pedido_avisos_config` (empresa, status, modalidade, ativo, texto; nulo = padrão). Rotas: `GET/PUT .../pedidos/avisos[/{status}]`, `POST .../avisos/{status}/previa`, `POST .../pedidos/{numero}/avisos/{status}/reenviar`. Contrato na seção 13 de `docs/ESPEC_PED1.md`.
- `X-Role` (`nucleo/autenticacao.py`: `papel`, `exigir_admin`): sem o cabeçalho = membro; membro não altera textos (403).
- Reenvio: só aviso `falhou` e pedido ainda no status; falha incerta exige `mesmo_incerto`. `uazapi.EnvioRecusado.certo` distingue recusa certa (4xx) de falha incerta; a linha do tempo ganhou `incerto` e `pode_reenviar`.
- Texto padrão de concluído em retirada ("retirado", desligado por padrão) foi proposto por mim; o Marco ainda não o aprovou.
- Testes: **220 passed** (3 novos em `tests/test_ped1b.py`), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-ped1b-20261011T013921Z/gateway.dump` (29 tabelas, 600). Migração 031 aplicada com a imagem antiga no ar (health 200).
- Conferência: 4 containers `healthy`, fila zerada, admin por fora 404, `/api/v1` sem chave 401, envio real em 1 de 5 agentes, zero erros nos logs. Nenhuma configuração de aviso gravada em produção (tudo no padrão).
- Diagnóstico (só leitura): transferência da conversa do final 1513 às 01:24 UTC = agente respondeu `transferir_humano` / `pedido_para_confirmar` porque a unidade Vila Nova Conceição não tem preço no catálogo (só o Pátio Guedala tem: 193 de 205 itens).
- Próximos: B3 (lista) liberado pelo Marco para depois do PED1-b; PED1-c aguarda liberação.

## UX1 B3 — lista no WhatsApp — 2026-10-11 02:03 UTC

- Status: **implantado**. Código `ca22728`, imagem `gateway:ca22728`, schema **31** (sem migração). Volta: `gateway:84fccaa`.
- 4 a 10 opções (ou opções com `descricao`) saem como lista (`/send/menu`, `type: list`); 1 a 3 sem descrição continuam botões. `titulo_lista` e `descricao` no campo `botoes` do agente. Opção com `id` de unidade da empresa recebe a situação real como descrição (`horarios.resumo`). Plano B em texto numerado com a descrição; motivo novo `descricao_longa`; `acima_do_limite` deixou de ocorrer.
- Clique `ListResponseMessage` → texto com o título e `interacao.tipo = "lista"`; toque feito no aparelho da instância (`fromMe`), em lista ou botão, é ignorado (não pausa o bot).
- Vale onde `botoes_interativos=on`: só `bykoji/bykoji_atendimento`.
- Testes: **225 passed** (5 novos em `tests/test_botoes_b3.py`, fixture `clique_lista.json` montada com os campos do payload real), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-b3-20261011T020217Z/gateway.dump` (30 tabelas, 600).
- Conferência: 4 containers `healthy`, fila zerada, admin por fora 404, `/api/v1` sem chave 401, envio real em 1 de 5 agentes, zero erros nos logs. O clique real de 11/10 (registro 184) relido pelo código novo é ignorado (era da instância); o mesmo payload vindo do cliente vira "Pátio Guedala" com `tipo: lista`.
- **A conferir no primeiro uso real:** clique em lista vindo do aparelho do cliente.
- Cadastro (não alterado): `einstein` segue com sábado `19:00–17:00`; na lista aparece "Aberta até amanhã 17h".
- Próximos: PED1-c (aguarda liberação) → C1 → R1 → B4 → carrossel → G4.

## PED1-c — conversas e contatos no painel, nome_cliente do agente — 2026-10-11 03:00 UTC

- Status: **implantado**. Código `6f89149`, imagem `gateway:6f89149`, schema **32**. Volta: `gateway:ca22728` (roda sobre o schema 32: a 032 só acrescenta colunas e índice).
- Migração 032: `contatos.nome_cadastro/nome_informado/nome_perfil` (+ quem/quando), `conversas.simulada`, índice `ix_conversas_recentes`. Preenchida: 5 contatos com `nome_perfil` (vindos do `nome` antigo); 104 conversas marcadas como simuladas pela auditoria `agente.simulado` (todas as de canal API).
- `nucleo/contatos.py`: prioridade **cadastro > informado > perfil > telefone**, validação do nome (2–60, duas letras, sem telefone, sem emoji), E.164 e formatado, `canal_info`. Perfil (`senderName`) atualizado a cada mensagem do cliente.
- `GET conversas`: `nome_exibicao`, `nome_origem`, `nomes`, `telefone_e164/formatado`, `canal_info`, `unidade_preferida(_nome)` (da conversa ou do último carrinho), `estado`, `ultima`, `pedido_aberto`; filtros `estado=` e `ordem=recentes` (cursor próprio); `busca` nos três nomes. Contatos: campos novos e `PATCH` do nome de cadastro (qualquer papel, auditado). Anonimização limpa os nomes novos e `por`/`motivo` da linha do tempo dos pedidos.
- Skyone: retorno aceita `nome_cliente` (inválido é ignorado sem erro; nunca sobrescreve cadastro); pacote `cliente` ganha `nome_origem` e o nome nunca é telefone. Pedido, `{nome_cliente}` do aviso e alerta de transbordo usam o mesmo nome. Contrato na seção 14 de `docs/ESPEC_PED1.md`.
- Testes: **230 passed** (5 novos em `tests/test_ped1c.py`; `test_fase2_contrato.py` ajustado para `nome_origem`), só bancos `_test`. Backup `/home/twadmin/backups/gateway/pre-ped1c-20261011T025809Z/gateway.dump` (30 tabelas, 600). Migração aplicada com a imagem antiga no ar (health 200).
- Conferência: 4 containers `healthy`, fila zerada, admin por fora 404, `/api/v1` sem chave 401, envio real em 1 de 5 agentes, piloto com os dois contatos, zero erros nos logs. Em produção: estados 7 encerradas / 3 aguardando / 0 atendente / 93 bot; contatos com origem `perfil`, `telefone` ou `identificador` (ninguém tem cadastro ou informado ainda).
- Clique em lista do final 1513: **nenhum registro** no `webhook_raw` da bykoji desde a implantação do B3 (02:03 UTC) até 03:00 UTC; segue a conferir.
- Próximos: painel consome a seção 14; Skyone passa a devolver `nome_cliente`. Depois C1 → R1 → B4 → carrossel → G4.

## CFG1 — especificação da configuração do agente por empresa — 2026-10-11

- `docs/ESPEC_CFG1.md` escrita: levantamento das 26 configurações com herança, três camadas (prompt base / `perfil_atendimento` / comportamentos do gateway), versionamento, segmentos, prévia, base de conhecimento (CFG1-b) e contratos. **Nada implementado; aguardando aprovação e 10 decisões do Marco (seção 11).**
- Achado: `instrucoes_agente` está vazio nos 5 agentes de produção; tudo o que é do By Koji está no prompt do Skyone.

## CFG1 aprovado; PED1-c conferido — 2026-10-11 13:45 UTC

- Decisões do Marco para o CFG1 registradas na seção 12 de `docs/ESPEC_CFG1.md`. Ordem nova: PED1-c → CFG1-a → G4 → G5/G6 → G3 → B4 → R1 → C1 → CFG1-b → carrossel. **CFG1-a só com liberação do Marco.**
- PED1-c já estava implantado desde 03:00 UTC (`gateway:6f89149`, schema 32). Conferido agora: 4 containers nessa imagem, fila zerada, admin por fora 404, `/api/v1` sem chave 401, zero erros na última hora, árvore do git limpa.

## CFG1-a — perfil de atendimento e comportamentos por empresa e por agente — 2026-10-11 15:12 UTC

- Status: **implantado**. Código `8b921cb`, imagem `gateway:8b921cb`, schema **33**. Volta: `gateway:6f89149` (roda sobre o schema 33; a 033 só acrescenta).
- `nucleo/atendimento.py` (esquema, validação, bloqueio de texto que parece instrução de sistema, herança) e `admin/atendimento.py` (rotas de empresa e de agente: ler, rascunho, publicar, versões, restaurar, prévia; `GET /segmentos`). Contrato final na seção 13 de `docs/ESPEC_CFG1.md`.
- Pacote do agente ganhou `perfil_versao` e `perfil_atendimento` (13 campos, sempre presentes). O Skyone atual os ignora; nenhum comportamento depende deles. `turnos.perfil_versao` gravado.
- Comportamentos novos guardados e versionados, **sem efeito ainda** (`disponivel: false`): reações, fora do horário, transbordo.
- Segmento `delivery_restaurante` criado pela migração. **By Koji migrado** às 15:14 UTC (ator "gateway (ordem do Marco)"): empresa com `segmento`, agente `bykoji_atendimento` do modelo `atendente_delivery` para `delivery_restaurante`. Configuração efetiva comparada campo a campo: 38 campos, 37 iguais em valor e origem; só `modelo_id` mudou. Perfil do By Koji: padrões do segmento (equilibrado, moderado, curtas, leves), `perfil_versao` `e0`, nada publicado.
- **Falha de processo minha:** a suíte completa da primeira rodada teve 1 falha (233 passaram) e a trava do meu comando de implantação não barrou; implantei assim. A falha era expectativa antiga de `tests/test_migrations.py` (contava 2 modelos; agora são 3). Corrigida; segunda rodada: **234 passed**. Nenhum defeito no código implantado.
- Backup `/home/twadmin/backups/gateway/pre-cfg1a-20261011T151114Z/gateway.dump` (30 tabelas, 600). Migração aplicada com a imagem antiga no ar (health 200).
- Conferência: 4 containers `healthy`, fila zerada, admin por fora 404, `/api/v1` sem chave 401, envio real em 1 de 5 agentes, `reacoes` off em todos, zero erros nos logs.
- Não entrou: bloco `recursos` do pacote (fica para o R1).
- Próximos (ordem do Marco): G4 → G5/G6 → G3 → B4 → R1 → C1 → CFG1-b → carrossel.

## CFG1-a.1, trava de implantação e especificação do G4 — 2026-10-11 16:07 UTC

- **Trava de implantação** (regra 16 do `PLANO_CURSOR.md`; uso em `GUIA_CURSOR.md`): `scripts/suite_travada.sh` bloqueia em teste falhando, erro de coleta, teste pulado, zero testes, caminho inexistente ou saída diferente de zero; `scripts/implantar.sh <rotulo>` só constrói depois dela e ainda confere árvore limpa, imagem de volta, backup relido, migração com a imagem antiga no ar e contêineres saudáveis. Provada com arquivos temporários (5 casos bloqueados, 1 liberado), depois removidos.
- **CFG1-a.1 implantado pelo script novo**: código `16c77c0`, imagem `gateway:16c77c0`, schema 33 (sem migração). Volta: `gateway:8b921cb`. Suíte: **234 passed** (trava liberou). Backup `/home/twadmin/backups/gateway/pre-cfg1a1-20261011T155549Z/gateway.dump` (32 tabelas).
- Perfil ganhou `nome_empresa` (padrão: nome cadastrado da empresa; origem `cadastro`) e `descricao_negocio` (até 300, com a análise de texto bloqueado/duvidoso). By Koji: `nome_empresa` = "By Koji", `descricao_negocio` vazio, `perfil_versao` `e0`; nenhum comportamento mudou.
- Decisões do Marco registradas na seção 14 de `docs/ESPEC_CFG1.md` (segmento fica como está, com o procedimento de promover versão; ele publica o perfil do By Koji pelo painel).
- **G4**: o que faltava especificar está na seção 11 de `docs/ESPEC_GESTAO.md`, com 8 decisões (11.8). **Nada implementado; aguarda liberação.**
- Conferência: 4 containers `healthy`, fila zerada, admin por fora 404, `/api/v1` sem chave 401, zero erros nos logs.

## G4-a — cadastro de entrega e pagamento da unidade — 2026-10-11 16:42 UTC

- Status: **implantado** por `scripts/implantar.sh g4a` às 16:42 UTC. Código `2b19ef4`, imagem `gateway:2b19ef4`, schema **34**. Volta: `gateway:16c77c0` (roda sobre o schema 34; a 034 só acrescenta). Suíte travada: **241 passed**. Backup `/home/twadmin/backups/gateway/pre-g4a-20261011T164152Z/gateway.dump` (600).
- O script rodou **duas vezes**: a primeira implantou; a segunda (mesmo comando, iniciada às 16:42) passou a suíte de novo e parou no passo 3 ("o commit 2b19ef4 já é o que está em produção"), sem backup, build nem troca. As duas escreveram no mesmo log em `/tmp`, por isso a saída da primeira ficou incompleta; o estado foi conferido direto (um backup só, contêineres iniciados às 16:42:11, schema 34).
- Conferência (só leitura): 4 contêineres `healthy` na imagem nova, admin por fora 404, `/api/v1` sem chave 401, zero erros nos logs desde a troca. By Koji: 0 taxas, 0 formas, nenhum campo novo preenchido nas 4 unidades: tudo continua "a unidade confirma".
- Decisões do Marco na 11.9 de `docs/ESPEC_GESTAO.md`; contrato final na 11.10; contrato de `consultar_entrega` (G4-b) na 11.11.
- Migração **034** (aditiva): unidade ganha `delivery_ativo`, `taxa_modo`, `frete_gratis_acima`, `tempo_entrega_min`, `tempo_retirada_min`; tabelas `unidade_taxas` (bairro com apelidos ou faixa de CEP) e `unidade_pagamentos`. Colunas de carrinho e pedido ficam para o G4-b (decisão minha, para não surgir nulo novo nas respostas de hoje).
- `modulos/entrega.py` (regras puras: comparação de bairro sem acento/pontuação, CEP, conflitos, pagamentos) e `admin/entrega.py` (rotas de taxas e pagamentos). Escrita só admin/plataforma; na unidade, os campos de taxa/pagamento só travam quando o valor muda.
- `info_unidades` ganhou `entrega`, `retirada` e `pagamentos`; `delivery` agora vem do cadastro. Pacote e carrinho **não mudaram** (teste confere).
- Testes: `tests/test_g4a.py` (7, inclui isolamento entre unidades e empresas); três testes antigos passaram a mandar `X-Role: admin` ao gravar taxa/formas de pagamento.
- Duas expectativas erradas no teste novo, corrigidas lendo o log: código livre `pix_2` sem rótulo (o servidor recusa com razão) e apelidos "Jd. Morumbi"/"Jd Morumbi" na mesma linha (são o mesmo nome; o servidor acusa `bairro_duplicado`, como especificado).
- Próximo: G4-b, depois da conferência do Marco.

## Diagnóstico do teste do piloto 7046 e especificação do SOL1 — 2026-10-11 18:35 UTC

- Teste das 15:13–15:20 (local): no turno 823 o agente devolveu `transferir_humano` / `evento_ou_encomenda` logo depois de o cliente tocar em "Finalizar pedido"; o gateway pausou o bot por 60 min e o "Ok" seguinte foi ignorado (turno 824). Nenhuma Skill de fechamento foi chamada; carrinho 6 segue aberto (1 Edamame, Pátio Guedala, retirada). Dois refazer seguidos (821, 822) funcionaram.
- Os toques "Entrega" (15:14:41) e "Retirada" (15:14:42) chegaram no mesmo lote, nessa ordem; o agente seguiu o primeiro.
- Primeiro clique em lista vindo do aparelho do cliente (final 7046, "Pátio Guedala"): lido como `interacao.tipo = lista`. Fecha a conferência pendente do B3.
- Conversa devolvida ao bot por ordem do Marco (`conversa.devolvida`, ator `marco`, 18:28 UTC).
- `docs/ESPEC_SOL1.md` escrita (ação `notificar_equipe`, tabela de solicitações, proteção, rotas), com 6 decisões. **Nada implementado.**
- Ordem nova do Marco: G4-a (feito) → OPS1 → R1 → SOL1 → G4-b → G5/G6 → G3 → B4 → C1 → CFG1-b → carrossel.

## SOL1 aprovado e OPS1 especificado — 2026-10-11

- Decisões do Marco para o SOL1 na seção 8 de `docs/ESPEC_SOL1.md`; item extra (toques contraditórios no mesmo lote) na seção 9. Nada implementado.
- `docs/ESPEC_OPS1.md` escrita (backup diário cifrado fora da VPS, teste de restauração semanal, alertas pelo Telegram, disponibilidade, teste de carga do Skyone só especificado), com 11 decisões. **Nada implementado; o código só começa quando o Marco confirmar que a outra sessão foi encerrada e aprovar a especificação.**
- Ordem do Marco: G4-a (feito) → OPS1 → SOL1 → R1 → G4-b → G5/G6 → G3 → B4 → C1 → CFG1-b → carrossel.

## OPS1 — backup, teste de restauração, alertas e /health/ops — 2026-10-11 19:31 UTC

- Status: **implantado** por `scripts/implantar.sh ops1`. Código `31fe5b0`, imagem `gateway:31fe5b0`, schema **35**. Volta: `gateway:2b19ef4`. Trava: **247 passed**. Backup de implantação `/home/twadmin/backups/gateway/pre-ops1-20261011T193000Z/gateway.dump` (34 tabelas).
- Decisões do Marco na seção 9 e o que foi implementado na seção 10 de `docs/ESPEC_OPS1.md`; ensaio manual em `docs/RESTAURACAO_MANUAL.md`.
- Scripts: `backup.sh`, `teste_restauracao.sh`, `vigia.sh`, `alerta.sh`, `ops/instalar_cron.sh`, `ops/comum.sh`. No gateway: `nucleo/alertas.py` (worker, fila, Skyone, WhatsApp, pelo agendador a cada 30 s), `GET /health/ops`, tabela `ops_execucoes` (migração 035).
- rclone 1.75.2 em `/home/twadmin/.local/bin/rclone` (download oficial com a soma conferida; sem `sudo` na conta, por isso na pasta do usuário).
- **Inerte nos envios** até o Marco cadastrar as credenciais: primeiro backup real `local` (355 KB de banco, manifesto com contagens exatas), teste de restauração real `ok` (banco temporário criado, comparado e apagado), `/health/ops` interno 503 com motivo `destino_nao_configurado`, alertas desligados (registro local avisa).
- Ensaiado antes do commit, com chave gpg descartável e uma pasta como destino: arquivo cifrado enviado para `diario/` e `semanal/`, decifrado e conferido com o manifesto; sem a chave privada não abre; destino inacessível e chave ilegível geram falha com alerta e não deixam arquivo pela metade.
- Tarefas no crontab do `twadmin`: backup 07:30 UTC (04:30 São Paulo), restauração domingo 08:00 UTC (05:00), vigia a cada 2 min. O cron da VPS não aceita fuso por tarefa (corrigido depois da primeira instalação, que usava `CRON_TZ`).
- Erros meus durante o bloco, corrigidos antes da implantação ou logo depois: nome do banco de produção no script (o contêiner aponta para o de testes), listagem do crontab no instalador, fuso do cron.
- **Pendente do Marco:** credenciais (B2, chave pública, Telegram) no `.env`; regras de ciclo de vida no bucket; liberar `/gateway/health/ops` no proxy do painel (hoje 404 por fora); primeiro ensaio manual.
- Conferência: 4 containers `healthy`, fila zerada, `/gateway/health` 200, admin por fora 404, `/api/v1` sem chave 401, zero erros nos logs.

## OPS1: /health/ops liberado por fora — 2026-10-11 19:46 UTC

- Proxy do painel (Caddy) passou a deixar `/gateway/health/ops`, feito como tarefa do painel, com ordem do Marco (registro em `painel_koji/docs/worklog.md`). O gateway não mudou: por fora já sai só `{"ok": false}` com 503 (falta o destino B2 no `.env`); `motivos` só na chamada interna.
- Conferência por fora: `/gateway/health/ops` 503 `{"ok":false}`, `/gateway/health` 200, admin 404, `/api/v1` sem chave 401.
- `docs/Caddyfile.painel.example` e a 10 da `docs/ESPEC_OPS1.md` atualizados.

## SOL1 — avisar a equipe sem parar o atendimento; toques contraditórios — 2026-10-11 21:05 UTC

- Status: **implantado** por `SUITE_MINIMO=252 scripts/implantar.sh sol1`. Código `85b5481`, imagem `gateway:85b5481`, schema **36**. Volta: `gateway:31fe5b0`. Trava: **252 passed** (0 falhas, 0 pulados). Backup `/home/twadmin/backups/gateway/pre-sol1-20261011T210445Z/gateway.dump` (35 tabelas).
- Ação `notificar_equipe` (motivo + `solicitacao` opcional), tabela `solicitacoes`, alerta ao destino de transbordo, proteção (transferência por evento/encomenda/catering com carrinho com itens ou pedido ativo vira aviso; `turnos.acao_original`), toques contraditórios (`interacao.substituiu`), rotas do painel, métricas. Contrato exato na seção 10 de `docs/ESPEC_SOL1.md`.
- `catering` no catálogo de motivos. Configurações novas: `notificar_equipe` (off na plataforma) e `transbordo_destino_externo` (falso).
- Segmento `delivery_restaurante` versão 2 criado pela migração. Agente `bykoji_atendimento` movido da versão 1 para a 2 às 21:05 UTC (ator "gateway (ordem do Marco)"). Comparação de 49 campos: mudaram só `modelo_versao` (1 → 2) e `notificar_equipe` (off/plataforma → on/modelo); no perfil, só `catering` acrescentado aos motivos. `perfil_versao` do By Koji: `e1` (publicado pelo Marco no painel).
- Trava: primeira rodada do bloco (sem implantar) bloqueou por 1 falha — o teste de migrações contava modelos de agente. Agora o teste confere os modelos esperados por nome e versão, há um teste novo das versões do segmento, e a trava aceita `SUITE_MINIMO`.
- Conferência (só leitura): 4 containers `healthy` na imagem nova, fila zerada, `/gateway/health` 200, `/gateway/health/ops` 503 por fora sem motivos (destino de backup ainda não configurado), admin por fora 404, zero erros nos logs.
- **Pendente:** conferir uma resposta normal do Skyone atual depois da troca. Nenhuma mensagem real do piloto chegou entre 21:05 e 21:15 UTC.
- Próximo: R1 → G4-b → G5/G6 → G3 → B4 → C1 → CFG1-b → carrossel.

## CFG1-c — especificação (R1 reações, H1-a fora do horário, T1 transbordo) — 2026-10-12

- `docs/ESPEC_CFG1C.md` escrita, com o contrato único do Skyone (SOL1 + R1) na seção 5, como o painel sabe que saiu do "em breve" na seção 6 e 9 decisões na seção 8. **Nada implementado; aguarda aprovação.** Pedido agendado fica no G4-b.
- Achados: o segmento `delivery_restaurante` está gravado com `fora_do_horario: so_avisar` (o pedido novo diz "atender"); o alerta de transbordo do By Koji aponta para o número da própria instância; a chave `pedido_com_unidade_fechada` ainda não existe (G4-b).
- SOL1: nenhum turno real do piloto desde a troca (conferido em 12/10 00:30 UTC); a conferência do Skyone atual na imagem nova continua pendente.

## CFG1-c — reações (R1), fora do horário (H1-a) e transbordo (T1) — 2026-10-12 00:58 UTC

- Status: **implantado** por `SUITE_MINIMO=257 scripts/implantar.sh cfg1c`. Código `e5e62c1`, imagem `gateway:e5e62c1`, schema **37**. Volta: `gateway:85b5481`. Trava: **257 passed** (0 falhas, 0 pulados). Backup `/home/twadmin/backups/gateway/pre-cfg1c-20261012T005743Z/gateway.dump` (36 tabelas).
- Decisões do Marco na seção 9 e o que foi implementado (com o formato exato dos campos novos do pacote) na seção 10 de `docs/ESPEC_CFG1C.md`.
- R1: `nucleo/reacoes.py`, `uazapi.reagir`, campo `reacao` do retorno, `turnos.reacao`. H1-a: `nucleo/expediente.py`, modo `so_avisar` sem chamar o agente (`Pipeline._so_avisar`), `conversas.fora_do_horario_avisado_ate`. T1: `avisos.destino_do_alerta`, `tenant_canais.numero_instancia` (lido do `owner` do webhook), `Pipeline._equipe_ausente`, `turnos.equipe_fora_do_horario`. Pacote: `fora_do_horario`, `proxima_abertura`, `equipe_disponivel`, `retorno_equipe`, `recursos`.
- Segmento `delivery_restaurante` versão 3 (reações ligadas ❤️ 😊 👍 🙏; fora do horário `atender`). Agente `bykoji_atendimento` movido da 2 para a 3 às 00:58:29 UTC (ator "gateway (ordem do Marco)"). Comparação de 51 campos: pela versão 3 mudaram `modelo_versao`, `reacoes` (off → on) e `fora_do_horario` (so_avisar → atender); pela imagem nova, os textos padrão das duas mensagens, a ordem da lista padrão de emojis e `transbordo_alerta` (copiado do número antigo da empresa). Perfil idêntico (`e1`).
- Entre a troca dos serviços e a mudança do agente (~35 s) o `so_avisar` do segmento antigo esteve ativo; nenhum turno ocorreu nesse intervalo. O primeiro comando de mudança falhou por erro meu na montagem do script e foi refeito em seguida.
- Conferência (só leitura): 4 containers `healthy`, fila zerada, `/gateway/health` 200, admin por fora 404, zero erros nos logs. Esquema do painel: só `fora_do_horario_aceita_agendado` continua "em breve".
- **Pendente:** o número da própria instância ainda não é conhecido (chega no primeiro evento da uazapi); até lá o alerta de transbordo do By Koji continua indo para a instância e a pendência não aparece. O Marco cadastra o telefone da equipe pelo painel. Nenhuma mensagem real do piloto desde o SOL1.

## Diagnóstico do piloto 1513 (11/10) e especificação do PED2 — 2026-10-12

- 16:59–17:01 (local): os dois "Novo pedido" foram enviados às 16:59:53 e 17:00:34 e a uazapi só entregou os dois webhooks às 17:01:07; o gateway respondeu em 16 s. Sem refazer, sem fila; Skyone 9,5 s.
- 17:56: `definir_entrega` com `horario` e `unidade` depois de um toque em "Escolher horário"; carrinho com o texto "hoje às 19h". 22:06: nenhum `fechar_pedido` registrado e nenhum pedido criado; o agente disse que fechou. Carrinho 8 segue aberto (1 Baterá Shake).
- Agente: `bykoji_atendimento` (By Koji), segmento `delivery_restaurante` v3; perfil da empresa v1 publicado por marco.santos@ticway.com.br em 11/10 16:56 com valores de teste (`nome_empresa` "teste1", `nome_atendente` "Marco").
- Primeiros turnos reais depois do SOL1/CFG1-c (01:03–01:07 UTC de 12/10): 8 turnos `ok`, sem erro — fecha a conferência pendente do Skyone atual nas imagens novas.
- `docs/ESPEC_PED2.md` escrita (horário agendado validado, `horario_vencido`, número do pedido, categorias), com 7 decisões. **Nada implementado.**

## PED2 — horário agendado validado, categorias, registro de Skills e pedido anunciado sem fechar — 2026-10-12

- Decisões do Marco registradas em `docs/ESPEC_PED2.md` (seção 7); contrato final dos motivos na seção 8; o que foi feito na seção 9.
- Migração 038 (aditiva): `carrinhos.agendado_para`, `pedidos.agendado_para`, `turnos.anuncio_falso`, tabela `skill_registro`.
- `definir_entrega` lê a hora do texto (`horario`) ou recebe `agendado_para`; recusas `horario_passado`, `horario_fora_funcionamento`, `horario_invalido`, `horario_muito_distante` (7 dias). `fechar_pedido` confere de novo.
- Carrinho e pacote: `agendado_para` e `horario_vencido`; vencido tira o `pode_fechar`. `fechar_pedido` devolve `numero` na raiz.
- Busca do catálogo: `categorias=true` e `categoria=<nome>` (10 por página, só com preço).
- Registro de todas as chamadas de Skill (`skill_registro`, `GET /admin/v1/tenants/{id}/skills/registro`).
- Pipeline: pedido anunciado sem `fechar_pedido` de sucesso → refaz 1 vez com `aviso_do_gateway`; repetiu → texto fixo com botão "Confirmar pedido", alerta e métrica `anuncios_falsos_segurados`.
- Testes: `tests/test_ped2.py` (5). Dois testes antigos trocaram "20h" por "amanhã às 20h" para não depender da hora do dia.
- **Implantado em 12/10/2026 03:20 UTC:** `gateway:b9b6ff1`, schema 38, trava com 262 passed (0 falhas, 0 pulados). Volta: `gateway:e5e62c1`. Backup: `pre-ped2-20261012T031953Z`. A primeira tentativa foi bloqueada pela trava (teste do contrato do pacote não conhecia `aviso_do_gateway`); corrigido o teste e refeita.
- Conferência de leitura: carrinho do piloto 1513 aparece com `horario_vencido: true`, `pode_fechar: false`; Pátio Guedala com 18 categorias e 193 itens pedíveis.

## CAL1 — calendário no pacote — 2026-10-13

- `docs/ESPEC_CAL1.md` escrita, com 5 decisões. **Nada implementado.** Achados: a biblioteca `holidays` não está na imagem; a unidade não tem campo de cidade (só endereço em texto); não há exceção futura cadastrada no By Koji.
- **CAL1 aprovado e implementado (12/10/2026):** `calendario` na raiz do pacote (hoje + 7 dias; feriados pela `holidays==0.106`, municipais de São Paulo, pontos facultativos e exceções das unidades em `observacao`); migração 039 (`tenant_unidades.cidade`, `uf`; By Koji = São Paulo/SP); `cidade`/`uf` no contrato admin da unidade; `scripts/test_suite.sh` instala as bibliotecas novas do requirements no contêiner de teste. Testes em `tests/test_cal1.py` (5).
- **CAL1 implantado em 12/10/2026 23:15 UTC:** `gateway:7784585`, schema 39, trava com 267 passed (0 falhas, 0 pulados). Volta: `gateway:b9b6ff1`. Backup: `pre-cal1-20261012T231520Z`. Pacote real conferido: `calendario.dias[0]` = 12/10, segunda-feira, hoje, Nossa Senhora Aparecida (nacional).

## DISP1 — disponibilidade de item e paginação de categorias — 2026-10-12

- Diagnóstico do teste de 12/10 20:30 entregue (11 turnos; almoço executivo vendido no feriado; lista com 10 de 18 categorias; perfil ainda `e1` de teste; pedido 12873 aguardando a loja).
- `docs/ESPEC_DISP1.md` escrita, com 9 decisões. **Nada implementado.** Achado: não existe rota de criar/editar item (só CSV e listagem).
- 13/10/2026: diagnóstico do teste com o prompt v6.3 entregue (turno 894: `adicionar_item` com id errado → `sem_preco` → transferência). `docs/ESPEC_DISP1.md` ganhou a seção 6 (registro com `ref`, `unidade_diferente` antes do preço, proteção `item_nao_buscado`, `tem_cardapio`), com as decisões 10 a 14. **Nada implementado.**
- **DISP1 aprovado e implementado (13/10/2026):** disponibilidade do item (dias, feriado, horário) na busca, no carrinho, no `definir_entrega` e no `fechar_pedido`; proteção `item_nao_buscado` (30 min); `unidade_diferente` antes do preço; `ref` no registro das Skills; `tem_cardapio`/`itens_no_cardapio`; paginação e ordem das categorias; PATCH de item e colunas opcionais no CSV. Migração 040. Testes em `tests/test_disp1.py` (5). Contrato final em `docs/ESPEC_DISP1.md`, seção 8.
- **DISP1 implantado em 13/10/2026 01:05 UTC:** `gateway:d9b079c`, schema 40, trava com 272 passed (0 falhas, 0 pulados). Volta: `gateway:7784585`. Backup: `pre-disp1-20261013T010526Z`. Os 4 almoços executivos do By Koji entraram com dias e feriado; **a faixa de horário ficou pendente** (veio em branco na ordem do Marco).

## CAT2 — edição do catálogo pelo painel e disponibilidade atual na API admin — 2026-10-13

- Decisões do Marco (9 itens) implementadas; contrato para o [PAINEL] em `docs/ESPEC_CAT2.md`. Sem migração.
- PATCH do item ampliado (nome, descrição, preço, ativo, categoria, disponibilidade; `atualizado_em` → 409 com o item atual); GET individual; GET de categorias da unidade; prévia de disponibilidade sem gravar; `disponivel_agora`/`indisponivel_motivo`/`data_referencia` nas leituras; histórico em `gestao_alteracoes`.
- Testes: `tests/test_cat2.py` (2). Limites registrados: categoria some quando fica sem itens; reimportar CSV sobrescreve a edição do painel.
- **CAT2 implantado em 13/10/2026 02:44 UTC:** `gateway:39a4a25`, schema 40 (sem migração), trava com 274 passed (0 falhas, 0 pulados). Volta: `gateway:d9b079c`. Backup: `pre-cat2-20261013T024344Z`.

## HOR2 — próxima abertura com cardápio, horário na troca de unidade e transferência sem tentativa — 2026-10-13

- Análise das conversas de 13/10 entregue (transferências sem tentar, unidade sem cardápio oferecida, horário presumido, Skyone com o dobro do tempo, perfil de teste).
- Itens 6, 7 e 8 aprovados e implementados: `proxima_abertura` da raiz só com unidades com cardápio; `horario_aviso` no carrinho e `aviso_horario` no `definir_entrega`; guarda de transferência por `pedido_para_confirmar` sem recusa de Skill (refaz 1 vez; repetiu → segue com alerta). Migração 041. Testes em `tests/test_hor2.py` (2). Contrato em `docs/ESPEC_HOR2.md`.
- **HOR2 implantado em 13/10/2026 16:54 UTC:** `gateway:d34db19`, schema 41, trava com 276 passed (0 falhas, 0 pulados). Volta: `gateway:39a4a25`. Backup: `pre-hor2-20261013T165425Z`.

## Análise de comportamento nº 1 e propostas DEM1, FUP1 e ENC1 — 2026-10-13

- `docs/ANALISE_COMPORTAMENTO_1.md`: análise dos 86 turnos de 12–13/10 (respostas, tom, reações, botões, fluxo, transferências, tempo), 17 problemas com causa, gravidade e correção, linha do tempo turno a turno em apêndice; e as propostas de aviso de demora (DEM1), follow-up (FUP1) e encerramento do atendimento (ENC1), com 17 decisões. **Só leitura; nada implementado.**

## AVS1 — aviso de espera — 2026-10-13

- Decisões do Marco sobre a análise nº 1 recebidas (AVS1, ENC1, FUP1; ordem AVS1 → ENC1-a → FUP1-a → FUP1-b + ENC1-b; instrumentação de `request_id` nas Skills e versão do prompt junto do ENC1-a).
- O `docs/ANALISE_COMPORTAMENTO_1.md` tinha sido reescrito por outra sessão, sem commit, com conversas pessoais de terceiros no Anexo B. A versão integral foi guardada fora do repositório (`/home/twadmin/backups/gateway/docs-fora-do-git/`) e a do repositório ficou sem esse conteúdo.
- AVS1 implementado: `aviso_espera_modo` (padrão `presenca`), "digitando…" renovado a cada 20 s pelo turno inteiro, texto de espera só no modo `presenca_e_texto_contextual` e no meio de algo, três textos aprovados com rodízio, `slow_notice_s` padrão 30. Migração 042. Testes em `tests/test_avs1.py` (3). Contrato em `docs/ESPEC_AVS1.md`.
- **AVS1 implantado em 13/10/2026 18:26 UTC:** `gateway:256169c`, schema 42, trava com 278 passed (0 falhas, 0 pulados). Volta: `gateway:d34db19`. Backup: `pre-avs1-20261013T182637Z`. By Koji herda `aviso_espera_modo: presenca`.

## Auditoria multiagente — 2026-10-15

- `docs/AUDITORIA_MULTIAGENTE.md`: o quanto o gateway está pronto para outros tipos de agente e outras empresas (passos de criação, módulos, canais, isolamento, módulo financeiro, painel). **Só leitura; nada alterado.**

## MULTI1-a — empresa da sessão, modelos de agente como cadastro e módulos declarativos — 2026-10-15

- Decisões do Marco (1–10) registradas em `docs/ESPEC_MULTI1.md`, seção 9; contrato para o [PAINEL] na seção 10. AVS1 já estava implantado (13/10) quando o MULTI1 foi aprovado.
- `X-Tenant-Scope` na API admin (`app/nucleo/escopo_empresa.py`): outra empresa → 404, visões/escritas globais → 403, plataforma vê tudo; sem o cabeçalho, log + alerta `painel_sem_escopo` até `ESCOPO_EMPRESA_OBRIGATORIO=on`.
- Migração 043: modelo de agente com `segmento`, `modulos_permitidos`, `modulos_padrao`, `motivos_transferencia`, `tipos_solicitacao`, `skyone_ref`, `ativo`; `agentes.modulos_ligados`; `delivery_restaurante` v4 ("Delivery", v1–v3 com os mesmos campos novos), `atendimento_geral` v1; `assistente_generico` e `atendente_delivery` inativos (quem usa continua).
- Módulo efetivo = modelo E empresa E agente; empresa sem registro = ligado; chave da empresa e criar/alterar modelo só plataforma. Motivos e tipos em três camadas com rótulo; `converte_em_solicitacao` no motivo. Comportamentos de módulo escondidos e recusados (`modulo_desligado`) sem o módulo.
- Bloco `agente` no pacote do Skyone (sem instruções de módulo). Webhook da instância uazapi cadastrado pelo gateway (criar canal, trocar credenciais, rotacionar segredo; `POST …/canais/{id}/webhook` para refazer).
- Testes: `tests/test_multi1a.py` (7); testes antigos passaram a usar o modelo `generico_teste` (o `assistente_generico` ficou inativo) e `X-Role: plataforma` onde trocam modelo ou chave de módulo.

## MULTI1-b — núcleo sem regra de delivery — 2026-10-15

- Retrato do By Koji gravado antes de mexer (13b328a) e ampliado depois com cenários extraídos do código antigo; o teste prova o resultado idêntico campo a campo (`tests/test_multi1b_retrato.py`, `tests/fixtures/multi1b_retrato.json`).
- Os 21 pontos da seção 3 da `docs/ESPEC_MULTI1.md` foram para declarações e ganchos do registro de módulos (`modulos/pedidos/ganchos.py`, `modulos/catalogo/ganchos.py`); caixa de avisos no núcleo (`nucleo/avisos.py`). Detalhe na seção 11.
- Testes próprios: núcleo sem importar os módulos, agente sem módulos sem campos de delivery, anonimização (LGPD) — achada falha antiga: observação de cada item não é apagada (aguarda decisão).

## AN1 (especificação) e LGPD1 + origem do pedido — 2026-10-16

- `docs/ESPEC_AN1.md`: auditoria dos dados de venda (só WhatsApp gera pedido; By Koji com 0 pedidos reais; itens guardam nome, categoria e preço da venda) e especificação da análise de vendas, com as decisões do Marco (seção 10).
- LGPD1 + origem (migração 044): `pedidos.origem` (real, piloto, simulado, teste) e `pedidos.canal` gravados no fechamento; pedidos anteriores marcados como teste; anonimização passa a apagar a observação de cada item e a levar `skill_chamadas`, `skill_registro` e `catalogo_vistos` para o identificador novo. Testes: `tests/test_lgpd1.py` e o de anonimização em `tests/test_multi1b.py`.

## Proteção contra implantação por fora e ENC1-a — 2026-10-20

- Regras para todos os assistentes em `AGENTS.md`, `CLAUDE.md` e `GEMINI.md`: implantar só por `scripts/implantar.sh`; nunca escrever dado de cliente em docs, PROGRESS.md ou commits.
- `implantar.sh` grava a imagem implantada em `backups/gateway/estado/imagem_implantada`. O `vigia.sh` (cron a cada 2 min) dispara alerta crítico "imagem fora do implantar.sh" quando gateway, worker ou agendador rodam outra imagem por duas conferências seguidas, e manda "normalizado" quando volta.
- ENC1-a (`docs/ESPEC_ENC1A.md`, seções 6 e 7; migração 045):
  - atendimento identificado dentro da conversa, com histórico do pacote só do atendimento atual e resumo factual do anterior;
  - encerramento por pedido concluído com agradecimento, pedido sem retorno em 30 min, agente, conversa vazia ou painel;
  - rotas de atendimentos no painel e eventos `atendimento.aberto`/`atendimento.encerrado`;
  - Skill ligada ao turno e `prompt_versao` no retorno do agente.
- Retrato do By Koji regravado só com acréscimos. Testes em `tests/test_enc1a.py` (7).

## CAT3-a — gestão do cardápio pelo painel — 2026-10-20

- Decisões do Marco na seção 12 de `docs/ESPEC_CAT3.md`; o contrato para o [PAINEL] está na seção 13. Migração 046.
- **Cadastro:** categorias como cadastro (com ordem, nomes anteriores e arquivamento), pratos e sugestões de ligação.
- **Operações:** cópia entre unidades, esgotado com volta pela hora (o membro pode esgotar), lote, código gerado pelo prefixo da unidade e arquivamento do item.
- Para o agente, nada muda sem ação no painel. O retrato do By Koji só ganhou `prefixo_codigo` na lista de unidades do painel.
- Testes: `tests/test_cat3a.py` (6); `tests/test_cat2.py` foi adaptado aos campos novos.

## CAT3-b — opções do item — 2026-10-20

- Migração 047: grupos e opções do item; a linha do carrinho guarda as escolhas copiadas e o preço base.
- **Skills:** `buscar_catalogo` traz `grupos`; `adicionar_item` e `alterar_item` aceitam `opcoes`, só com ids; há cinco recusas novas, com o grupo completo nos detalhes.
- **Carrinho, pedido e unidade:** o preço final é a base mais os extras; o fechamento revalida as escolhas; o texto para a unidade lista as escolhas.
- **Painel:** `GET/PUT …/itens/{id}/opcoes`, com `aplicar_em`.
- Contrato final na seção 14 de `docs/ESPEC_CAT3.md`. O retrato do By Koji mudou só nos campos novos. Testes em `tests/test_cat3b.py` (4).


## ARQ1-a1 — documentos por API (formulário, n8n): receber e ler — 2026-10-21

- Decisões do Marco na seção 14 de `docs/ESPEC_ARQ1.md`. O WhatsApp fica para depois e o canal é a API. O contrato da rota, do webhook e do painel está nas seções 14.3 a 14.6. Migração 048.
- **Credenciais:**
  - chave da empresa (`documentos:enviar`), recusada se vier de navegador;
  - chave de formulário (`X-Form-Key`) presa a empresa e agente, com limite por minuto, tamanho, tipos, modos e sites; revogável.
- **Modos:**
  - `extrair`: devolve o JSON do documento;
  - `agente`: conversa no canal api, com `anexos` no pacote, a Skill `ler_documento` e a resposta pelo webhook `conversa.resposta`.
- **Leitura:** o documento fica cifrado (AES-GCM, chave mestra no `.env`), com retenção de 30 dias e apagamento por LGPD. O leitor é um contêiner isolado (`gateway-leitor`, sem rede para fora e sem credenciais) e lê PDF digital e XML da NF-e.
- Testes em `tests/test_arq1a.py` (7) e `leitor/tests` (5). A capacidade está desligada em todos os modelos, então o By Koji não muda.
- Pendente no painel (Caddy): aceitar até 11 MiB em `/gateway/api/v1/documentos` (hoje 2 MiB).

## ARQ1-a3 — campos por regra, confiança e guarda de pagamento — 2026-10-21

- Fontes oficiais conferidas antes de implementar (seção 15.1 de `docs/ESPEC_ARQ1.md`):
  - NT 2025.001 confirmada, incluindo o exemplo oficial do CNPJ alfanumérico;
  - formato do E2E adotado como o dos PSPs: o manual oficial 2.10.0 não traz a regra, e o gateway confere só o formato.
- **Tipos lidos:** boleto (DVs, fator nas duas bases, partes), concessionária (módulos 10 e 11), NF-e/NFC-e (chave com CNPJ alfanumérico, DANFE, itens, XML), NFS-e (tudo `a_conferir`), comprovante PIX (E2E, nunca pagamento confirmado) e BR Code (CRC16).
- **Guarda:** a resposta que afirma pagamento confirmado é refeita e, se repetir, segurada (migração 049).
- **Medição:** `scripts/medir_documentos.py`, que roda na imagem do leitor e sem rede; o corpus fica em `backups/gateway/corpus-arq1/`.
- Testes em `tests/test_arq1a3.py` (16).
- Teste do Marco: na empresa `bench_passo5`, o agente "Teste Documentos" (`ag_92b3853d724f146f03ecfdb9`, modelo `teste_documentos` v1 com documentos ligados) e o canal api `can_ddefc934b2a21dcca872234c`. O agente está em mock/dry e com o piloto ligado.
