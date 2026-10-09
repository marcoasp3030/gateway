# Migrações do gateway

**Estado atual — 09/10/2026:** serviço mock/dry, banco `gateway`, migrações 001–012. Basic Auth, quota por URL e circuito implantados. Volta desta entrega: `gateway:a528a8c` com dump restaurado em base nova de schema 011; procedimento ao final. Registros anteriores são históricos.

O executor `app/migrar.py` aplica `app/migrations/NNN_*.sql` em ordem, com uma transação por arquivo e registro em `schema_migrations` (versão, nome, SHA-256, data). Uma trava consultiva do Postgres serializa executores concorrentes. Arquivo já aplicado não pode ser alterado; adicione uma versão nova. Lacunas, versões desconhecidas e checksums diferentes interrompem a execução.

- `001_base.sql`: cópia integral do `app/schema.sql` da Fase 0; permite registrar a base existente.
- `002_plataforma.sql`: tenants, configurações, modelos versionados, agentes, canais, unidades, auditoria, chaves, webhooks de saída e contatos. Adiciona escopo obrigatório às tabelas legadas e referências compostas para impedir vínculos entre tenants. IDs externos de mensagens são únicos por tenant.
- `003_seed.sql`: dois modelos globais, tenant By Koji, módulos catálogo/pedidos e agente em rascunho, mock/dry, sem credenciais ou canais externos.

A migração 002 recusa qualquer dado nas tabelas legadas antes de mudar o schema. A conversão desses dados requer um roteiro explícito de atribuição de tenant/canal/agente. Nada é apagado ou atribuído automaticamente. Se 002 falhar, 001 pode ficar registrada: cada arquivo tem sua própria transação e a execução pode ser retomada.

## Aplicação explícita

O executável usa `DATABASE_URL` do ambiente. Não passa DSN ou senha em argumentos e não roda automaticamente no startup:

```bash
python /workspace/app/migrar.py --apply
```

**Não aplicar migrações no banco legado `bykoji_test`.** O gateway atual já aponta para `gateway` com versões 001–005 aplicadas. Migrações futuras exigem backup e aplicação explícita; nunca são executadas no startup.

As migrações foram primeiro validadas em `gateway_migrations_test` e depois aplicadas no banco vazio `gateway` com autorização do Marco. Ambos pertencem ao Postgres do Compose do gateway; o banco do painel não foi utilizado.

## Testes

`tests/test_migrations.py` exige `MIGRATION_TEST_DATABASE_URL`. Sem essa variável, os testes de integração são explicitamente ignorados. `tests/conftest.py` recusa qualquer `DATABASE_URL` ou `MIGRATION_TEST_DATABASE_URL` cujo banco não termine em `_test`.

Cada cenário usa um schema aleatório em uma transação externa que é revertida no fim. Isso preserva o schema público e os dados já existentes no banco de testes. Os testes validam aplicação/reaplicação, seed seguro, checksum, rollback de falha, recusa de legado populado, referências entre tenants/modelos e campos de autenticação/timeout.

## Limites desta entrega

As FKs e os filtros da API mínima/pipeline foram testados com dois tenants. Criptografia e autenticação genérica estão implementadas; a autenticação real Skyone ainda depende do teste externo do Marco para concluir a fase.

A migração 006 renomeou `cardapio_itens` para `catalogo_itens`. Framework de módulos, complementos e gestão solicitada estão implantados. Dados antigos não serão convertidos, conforme decisão do Marco. A Fase 0.5 integral aguarda validações externas e revisão do contrato.

## Histórico: blocos 1–4 implementados — antes da troca de banco

- `004_horario_mensagens.sql`: horário real/fallback da mensagem para o histórico.
- `005_processamento.sql`: marcação de mensagens processadas e índice de pendentes.
- `main:app` agora é a aplicação multiempresa e exige versões até 005. O serviço antigo continua com a imagem já em execução e o banco legado. **Não executar `compose up`/recriar o serviço antes da confirmação do passo 5.**
- A nova aplicação exige `PANEL_SERVICE_KEY`, `API_KEY_PEPPER` e `ENCRYPTION_KEY` válidos, mais conexões de infraestrutura. `PANEL_SERVICE_KEY_NEXT` é opcional para rotação. Configurações de atendimento vêm de modelo/tenant/agente.
- `sh scripts/test_suite.sh` reproduz a suíte com Postgres isolado `gateway_migrations_test` e Redis do Compose. Redis usa namespace aleatório para o teste Lua e remove somente as chaves desse cenário. Nenhum teste usa o banco do painel nem o banco de produção.
- `tests/smoke_runtime.py` é exclusivamente um servidor de diagnóstico: fixa o banco isolado e usa chaves sintéticas. Executar somente com porta ligada a loopback, nunca como servidor de produção.

Troca autorizada pelo Marco em 08/10/2026: criar banco vazio `gateway` com usuário próprio, aplicar migrações 001–005, ajustar infraestrutura no `.env`, reconstruir e recriar somente o serviço gateway e validar saúde/isolamento. A tentativa foi interrompida nos pré-requisitos, conforme registro abaixo. `bykoji_test` fica preservado, sem conversão ou remoção. Benchmark de dois tenants/três agentes após a troca ainda não executado.

O processamento novo registra mensagens antes de agendar, usa Redis para debounce/trava e recupera pendências do Postgres na subida. Isso não substitui a Fase 0.6: separação de workers, fila durável completa e garantias de recuperação em falhas durante envio continuam pendentes. Casos live de provedores exigem a validação real prevista no plano.

## Passo 5 — pré-requisitos e volta — 08/10/2026

A troca **não ocorreu**. O container antigo continua funcionando, mas a imagem informada por `docker inspect` (`sha256:61a0f7129afd63f46cf024dea5bb4d2ad8956438bad20796f89b1024d01799f8`) não está disponível no armazenamento de imagens do Docker. `docker tag <id> gateway:fase0` retornou `No such image`; `docker image inspect <id>` confirmou a indisponibilidade. Não substituir essa imagem pela tag `latest`, que pode conter outra versão. A causa da indisponibilidade ainda não foi investigada.

- Backup customizado (`pg_dump -Fc`): `/home/twadmin/backups/gateway/bykoji_test-20261008T192052Z.dump`, 14.498 bytes, permissão 600; diretório 700, fora do repositório. `pg_restore --list` conseguiu listar o arquivo; restauração não foi executada.
- SHA-256 do dump: `078326f86fdb576126979bea4fed86854eabfa1cea5cd113572c532e9b427110`.
- Ambiente anterior: `/home/twadmin/gateway/.env.antes-gateway`, permissão 600, ignorado pelo Git e idêntico ao `.env` atual.
- Redis antigo: zero chaves `buf:*`; nenhuma chave removida.
- Banco `gateway` não criado. Nenhuma migração aplicada ao serviço e nenhum container recriado.

### Procedimento de volta após uma futura troca

**Procedimento de contingência, não executar durante operação normal.** A troca está concluída e a imagem `gateway:fase0` foi validada pelo Compose (seção final). `.env.antes-gateway` continua sendo o ambiente original para retorno ao `bykoji_test`. Não sobrescrever essa cópia.

Se uma validação falhar após a troca, conferir que a imagem de volta existe, restaurar o ambiente e recriar **somente** o gateway usando a imagem preservada, sem build:

```bash
cd /home/twadmin/gateway
docker image inspect gateway:fase0 --format '{{.Id}}'
# Se a imagem não existir, parar: não recriar com outra imagem.
install -m 600 .env.antes-gateway .env
mkdir -p .local
chmod 700 .local
cat > .local/compose.volta.yml <<'YAML'
services:
  gateway:
    image: gateway:fase0
    pull_policy: never
YAML
docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.proxy.yml \
  -f .local/compose.volta.yml \
  up -d --no-deps --no-build gateway
curl --fail --silent http://127.0.0.1:8100/health
curl --fail --silent https://painel.luxbrain.com.br/gateway/health
```

Executar os comandos em sequência, parando em qualquer erro. O ambiente salvo seleciona `docker-compose.yml:docker-compose.proxy.yml` e o banco legado `bykoji_test`. Na volta, o health interno esperado é `{"ok":true,"agente":"mock","envio":"dry"}`; o público deve ser somente `{"ok":true}`. Não usar `down`, não restaurar o dump sobre o banco intacto, não remover bancos/volumes/chaves Redis e não recriar Postgres, Redis ou painel. Preservar também o banco novo para diagnóstico, registrar o motivo em `PROGRESS.md` e parar.

### Reconstrução da imagem de volta — 08/10/2026

- Comparados SHA-256 e conjunto completo dos sete arquivos `.py` de `/app` no container com todos os commits ancestrais anteriores ao Bloco 1. Correspondência exata com `8f388ed`, `faaee65`, `bf7651d`, `52a2f6f`, `1e3f4a0` e `d6c607a`. Não é possível identificar um único commit de origem pelos arquivos Python, que são iguais nesses commits. Escolhido o mais recente, `8f388ed6a2b47ce64acc43f39c93b148a8cac6f3`, como fonte equivalente para reconstrução.
- Build em worktree temporário destacado desse commit, removido ao final. Checkout principal preservado. Não foi utilizado `docker commit`.
- Imagem candidata: `gateway:fase0`, também marcada `gateway:8f388ed`, ID `sha256:7230ce922e07f577e443c734c910451eb306cb88a206fa01d4be8e2e454096ee`.
- Hashes dos sete arquivos Python da imagem iguais aos do container; `pip freeze` ordenado também idêntico, sem diferenças de versões.
- `docker run --rm --network none --env-file .env.antes-gateway gateway:fase0 python -c 'import main'` → **exit 1**, `KeyError: 'DATABASE_URL'`. Nenhum servidor ou agendador adicional foi iniciado. Tanto `DATABASE_URL` quanto `REDIS_URL` estão ausentes no arquivo salvo: o `docker-compose.yml` constrói essas variáveis a partir de `POSTGRES_*` e `REDIS_PASSWORD` para o serviço.
- Parada conforme condição do Marco. A imagem ainda **não está integralmente validada para a troca**. Para retomar, o teste de importação precisa receber também as duas variáveis de conexão sintetizadas pelo Compose, sem exibir seus valores e sem iniciar servidor. O `.env.antes-gateway` original permanece intacto. Nenhum banco novo, migração ou troca de serviço foi executado.
- Regra 15 do plano passa a exigir tag `gateway:<commit>` antes de builds que substituam a imagem em uso e confirmação do Marco antes de `docker image prune` ou `docker builder prune`.


## Operação após a troca concluída — 08/10/2026

O serviço foi recriado com `docker compose up -d --no-deps --no-build gateway`. A seleção local é `COMPOSE_FILE=docker-compose.yml:docker-compose.proxy.yml:docker-compose.database.yml`. O complemento de banco altera somente `gateway.environment.DATABASE_URL`, lendo `GATEWAY_DATABASE_URL` do `.env`. As variáveis `POSTGRES_*` continuam descrevendo o cluster/ambiente legado; não devem ser trocadas pela senha do usuário novo. A senha nova foi gerada com 24 bytes aleatórios e gravada somente no `.env` (600).

O usuário `gateway` é dono apenas do banco `gateway`, sem superuser, createdb, createrole, replication ou bypassrls. Migrações 001–005 aplicadas com esse usuário e conferidas em `schema_migrations`. `bykoji_test` permanece sem essa tabela; não houve conversão, restauração nem exclusão dos dados antigos. Os containers Postgres, Redis e painel não foram recriados.

A validação correta da imagem antiga foi:

```bash
docker compose --env-file .env.antes-gateway \
  -f docker-compose.yml -f docker-compose.proxy.yml -f .local/compose.volta.yml \
  run --rm --no-deps --pull never gateway python -c "import main; print('ok')"
```

Resultado: `ok`, exit 0. O Compose instalado não aceita `--no-build` em `run`; a imagem explícita existente e `--pull never` permitem importar sem build, sem uvicorn e sem agendador. O teste aconteceu antes da troca, quando `.env` e `.env.antes-gateway` eram iguais. Para uma futura verificação do ambiente antigo, o procedimento de volta acima primeiro restaura `.env`, pois `env_file: .env` no serviço fornece também as demais variáveis do container.

Imagem de volta preservada nas tags `gateway:fase0` e `gateway:8f388ed`, ID `sha256:7230ce922e07f577e443c734c910451eb306cb88a206fa01d4be8e2e454096ee`. Arquivos Python e dependências já haviam sido comparados integralmente. Não é necessário restaurar dump para fazer rollback: restaurar `.env.antes-gateway` e usar a imagem antiga com os arquivos base/proxy/override de volta basta para reconectar ao banco legado intacto. O complemento `docker-compose.database.yml` **não entra no comando de rollback**. Nenhum dado do banco novo é convertido para o legado na volta; preserve ambos.

Backup renovado: `/home/twadmin/backups/gateway/bykoji_test-20261008T193845Z.dump`, modo 600, SHA-256 `7e6e12a4473700b22720ca37d12cf76aff63b0cd1da578e15c2e803981838c1b`. Backup anterior preservado. `pg_restore --list` conferido; restauração não testada.

Health interno/público aprovados, rotas administrativas públicas 404, benchmark de 6 conversas em 2 tenants/3 agentes com zero erros e isolamento conferido. Logs do gateway, Postgres, Redis e Caddy sem ocorrências dos segredos pesquisados. Detalhes em `PROGRESS.md` e `VALIDACAO_PASSO5.json`. Cenário sintético preservado: três agentes mock/dry e três canais API; chaves temporárias do benchmark revogadas.

### Suíte automatizada após a troca

Não executar a suíte contra `gateway`. Para reproduzir os testes usando a configuração de teste anterior, selecione explicitamente apenas os arquivos base/proxy, mantendo a conexão do runner no ambiente `_test`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.proxy.yml sh scripts/test_suite.sh
```

O wrapper encaminha as integrações para `gateway_migrations_test` com as credenciais de teste existentes; `conftest.py` recusa qualquer URL de banco sem sufixo `_test`. A suíte desta entrega passou antes da troca: **65 testes**. O benchmark pós-troca no banco novo foi uma validação operacional explicitamente autorizada pelo Marco, não a execução da suíte de testes contra produção.


## Histórico da preparação dos complementos — 09/10/2026

Na preparação, estavam implementados e testados; a implantação posterior está registrada abaixo:

| Migração | Mudança |
|---|---|
| 006 | Renomeia `cardapio_itens` para `catalogo_itens`, preservando os dados |
| 007 | Auditoria de modelos globais permite tenant nulo; negócios continuam com tenant explícito |
| 008 | Outbox de webhooks assinados, tentativas e índice por tenant |
| 009 | Eventos de sistema nas mensagens, origem e uso nos turnos |
| 010 | Avisos mensais persistentes de consumo e índice temporal |
| 011 | Vínculos de contato/backfill, anonimização e FKs diferíveis somente quando solicitado na transação |

Não reconstruir/recriar o serviço sem aplicar a sequência explicitamente e conferir a versão esperada. A aplicação nova verifica a última versão do diretório de migrações no startup. A imagem anterior do passo 5 exige versão 005: **não basta voltar àquela imagem sobre o schema 011**. Uma implantação deve preservar imagem com tag do commit, backup do banco `gateway` e ambiente, e preparar a volta antes de aplicar as novas migrações. O procedimento antigo de volta à Fase 0 usa `bykoji_test` preservado; não recupera automaticamente conversas criadas em `gateway`.

`tests/smoke_runtime.py` agora cria um schema aleatório dentro de `gateway_migrations_test` e um namespace Redis exclusivo, impedindo que seu agendador consuma lotes do serviço publicado. Usa chaves sintéticas e deve publicar porta só em loopback. Em 09/10/2026 o smoke com servidor/Redis/Postgres reais passou: 6/6 conversas, zero erros, dois tenants/três agentes, exportação e anonimização confirmadas. Container temporário encerrado; schema `smoke_482e3057accb41c3a06b81d11dfd0afe` preservado para inspeção, sem comandos destrutivos.


## Implantação 006–011 e volta compatível — 09/10/2026

Complementos agora implantados. Imagem anterior verificada por hashes de 24 arquivos Python contra o commit `ccb261b`, tag `gateway:ccb261b`, ID `sha256:a9bfabd15be399adb0df5784f36f3b6236007a66952f978f810333b0c44c2156`. Imagem nova marcada `gateway:9bcc828`. Somente o gateway foi parado/recriado; Postgres, Redis e painel mantiveram seus IDs.

- Dump `pg_dump -Fc`: `/home/twadmin/backups/gateway/gateway-pre011-20261009T003648Z.dump`, modo 600, fora do Git; SHA-256 `b56a246156f4f49a8609b495b6ec714d7b2043c85c694688dbdb315171d35204`. `pg_restore --list` aprovado.
- Ambiente preservado: `/home/twadmin/gateway/.env.antes011-20261009T003648Z`, modo 600.
- Após fila vazia e parada do gateway, o dump foi restaurado integralmente na **nova base** `gateway_pre011_20261009t003648z`, pertencente ao usuário gateway. Verificada versão 005 nessa cópia antes de aplicar 006–011 em `gateway`. Não houve rebaixamento, exclusão ou sobrescrita de banco.
- Banco principal agora na versão 011; `bykoji_test` intacto. Verificações e métricas em `VALIDACAO_IMPLANTACAO011.json`.

### Volta desta implantação

A base restaurada acima contém o estado imediatamente anterior à migração. Ela é compatível com `gateway:ccb261b`; o banco `gateway` na versão 011 deve ser preservado para diagnóstico. Mensagens posteriores ao dump permanecem nesse banco e exigem reconciliação antes de outra troca; não serão apagadas nem estarão automaticamente na cópia antiga.

O roteiro local `.local/rollback011.py` está preparado e lê `.local/implantacao011.json`. Ele restaura as configurações salvas **substituindo somente o nome da base** em `GATEWAY_DATABASE_URL` pela base restaurada, preserva usuário/senha, acrescenta o override `.local/compose.rollback011.yml` com `image: gateway:ccb261b` e `pull_policy: never`, e executa `docker compose up -d --no-deps --no-build gateway`. Não imprime ambiente. Não usar `.env.antes-gateway` nem `gateway:fase0` para esta volta.

```bash
cd /home/twadmin/gateway
docker image inspect gateway:ccb261b --format '{{.Id}}'
python3 .local/rollback011.py
curl --fail --silent http://127.0.0.1:8100/health
curl --fail --silent https://painel.luxbrain.com.br/gateway/health
```

Se for necessário restaurar novamente, criar **outro banco vazio**, com dono gateway, e usar `pg_restore --exit-on-error --no-owner --role=gateway` com esse dump; conferir versões 001–005 antes de apontar a imagem antiga. Nunca executar downgrade, `--clean`, restauração sobre `gateway`/`bykoji_test`, remoção de volumes ou limpeza do Redis. Conferir filas antes de uma volta posterior; preservar mensagens novas para reconciliação. Esta implantação não precisou de rollback.


## Rotas de gestão sobre schema 011 — 09/10/2026

Implantadas sem migração adicional. Antes do build foi conferida a tag de volta `gateway:9bcc828` da imagem em uso; antes da recriação, filas e entradas pendentes vazias. Dump adicional `/home/twadmin/backups/gateway/gateway-pre-gestao-20261009T125335Z.dump`, modo 600, validado com `pg_restore --list`; SHA-256 `818d5a9d1f593544b809c3e4d0db88b849113ef0ba37c7145d8c874b2f54b454`. Somente gateway foi recriado; banco, Redis e painel preservados.

Para voltar **somente o código deste bloco**, a imagem `gateway:9bcc828` aceita o mesmo banco 011: `.local/rollback-gestao.py` acrescenta ao `COMPOSE_FILE` o override `.local/compose.volta-gestao.yml` com essa imagem e `pull_policy: never`, recria apenas gateway sem build e confirma health. Não restaura banco nem altera mensagens/dados. O roteiro anterior `.local/rollback011.py` é exclusivo da volta ao schema 005 numa base restaurada, não da volta deste bloco. Nenhum rollback foi necessário.

Suíte 82 testes; validação operacional em `VALIDACAO_GESTAO05.json`. Imagem desta entrega: `sha256:8365cc6253988d6a8ac9ef1225b5ec65b402d15602a170ea504399e41cdd46e4`; marcada com a tag do commit ao registrar o bloco.


## Migração 012 — Basic e circuito — 09/10/2026

Usuário Basic cifrado, tipo de autenticação ampliado e tabela `agente_circuitos` com FK composta tenant/agente. Nenhuma migração anterior alterada e nenhum agente ativado em live. Teto por URL: infraestrutura `SKYONE_REQUESTS_PER_MINUTE`, padrão 240.

- Imagem de volta: `gateway:a528a8c`, `sha256:8365cc6253988d6a8ac9ef1225b5ec65b402d15602a170ea504399e41cdd46e4`.
- Ambiente anterior: `/home/twadmin/gateway/.env.antes012-20261009T133216Z`, modo 600.
- Dump: `/home/twadmin/backups/gateway/gateway-pre012-20261009T133216Z.dump`, modo 600, SHA-256 `428dab9dd6fb383d2e17572382f2e3a36796b2a9a7ede6dac9d85f7fc6f72531`, validado com `pg_restore --list`.
- Dump já restaurado em **nova base** `gateway_pre012_20261009t133216z`, dono gateway, schema 011 conferido antes da migração. Principal `gateway` no schema 012 preservado em caso de volta.

Roteiro local `.local/rollback012.py`: lê `.local/implantacao012.json`, restaura ambiente salvo alterando somente o nome da base para a cópia restaurada, acrescenta override `.local/compose.rollback012.yml` com a imagem anterior e recria somente gateway sem build. Confere health interno; conferir público também. Os roteiros anteriores apontam para outros schemas e não devem ser usados nesta entrega.

```bash
cd /home/twadmin/gateway
docker image inspect gateway:a528a8c --format '{{.Id}}'
python3 .local/rollback012.py
curl --fail --silent https://painel.luxbrain.com.br/gateway/health
```

Executar somente se necessário. Nunca downgrade, restauração sobre banco existente ou limpeza de volumes/Redis. Alterações posteriores ao dump permanecem no banco 012 para reconciliação; não estarão automaticamente na cópia restaurada. Nenhum rollback necessário. Relatório `VALIDACAO_SKYONE_BASIC.json`; senha real do Skyone exclusivamente sob controle do Marco.
