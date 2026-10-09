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
