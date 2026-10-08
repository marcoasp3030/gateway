# Migrações do gateway

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

**Não executar esse comando no gateway atual**: ele aponta para `bykoji_test`, ocupado pela versão antiga do serviço. Os novos campos obrigatórios exigem adaptar o pipeline antes de trocar o banco. `app/schema.sql`, Compose e serviço em execução continuam preservados.

Nesta etapa, as migrações foram aplicadas apenas em `gateway_migrations_test`, no Postgres do Compose do gateway. Esse banco adicional permite testar a evolução sem modificar o `bykoji_test` do atendimento simulado. O banco de produção `gateway` e seu usuário não foram criados.

## Testes

`tests/test_migrations.py` exige `MIGRATION_TEST_DATABASE_URL`. Sem essa variável, os testes de integração são explicitamente ignorados. `tests/conftest.py` recusa qualquer `DATABASE_URL` ou `MIGRATION_TEST_DATABASE_URL` cujo banco não termine em `_test`.

Cada cenário usa um schema aleatório em uma transação externa que é revertida no fim. Isso preserva o schema público e os dados já existentes no banco de testes. Os testes validam aplicação/reaplicação, seed seguro, checksum, rollback de falha, recusa de legado populado, referências entre tenants/modelos e campos de autenticação/timeout.

## Limites desta entrega

As FKs impedem referências entre tenants, mas não substituem o filtro de tenant das futuras consultas/API. SEG13 neste estágio tem campos e validações de formato no banco; validação de credencial real, criptografia no serviço e recusa de agente live sem autenticação dependem das próximas tarefas da 0.5.

`cardapio_itens` recebeu `tenant_id`; sua extração física para as migrações do módulo `catalogo` segue pendente. O framework de módulos, API administrativa, pipeline multiempresa, migração dos dados antigos e troca do banco do serviço também continuam pendentes. Nenhuma fase foi declarada concluída.

## Blocos 1–4 implementados — antes da troca de banco

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

**Não executar agora:** o serviço já está no estado anterior. A tag `gateway:fase0` foi reconstruída posteriormente (registro abaixo), mas a validação de importação ficou bloqueada pelas variáveis de conexão ausentes no arquivo de ambiente. Antes de retomar a implantação, é necessário concluir essa verificação, renovar o backup e conferir novamente as filas. Não sobrescrever a cópia do ambiente anterior sem preservar a existente.

Se uma validação falhar depois de uma futura troca, conferir que a imagem de volta existe, restaurar o ambiente e recriar **somente** o gateway usando a imagem preservada, sem build:

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
