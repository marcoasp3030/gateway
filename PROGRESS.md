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
