# Progresso do gateway

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
