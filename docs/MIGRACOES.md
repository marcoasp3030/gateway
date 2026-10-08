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

Troca planejada, ainda não autorizada: criar banco vazio `gateway` com usuário próprio, aplicar migrações 001–005, ajustar infraestrutura no `.env`, reconstruir e recriar somente o serviço gateway e validar saúde/isolamento. `bykoji_test` fica preservado, sem conversão ou remoção. Benchmark de dois tenants/três agentes após a troca ainda não executado.

O processamento novo registra mensagens antes de agendar, usa Redis para debounce/trava e recupera pendências do Postgres na subida. Isso não substitui a Fase 0.6: separação de workers, fila durável completa e garantias de recuperação em falhas durante envio continuam pendentes. Casos live de provedores exigem a validação real prevista no plano.
