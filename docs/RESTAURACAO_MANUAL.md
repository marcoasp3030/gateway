# Ensaio manual de restauração (trimestral)

**Para quê:** provar que o backup que está no Backblaze B2 abre com a chave privada e volta a ser um banco inteiro. O teste automático da VPS não consegue fazer isso, porque a chave privada não fica lá.
**Quando:** a cada 3 meses, e sempre que a chave ou o destino mudarem. O primeiro logo depois da implantação do OPS1.
**Onde:** no seu computador. **Nunca** copie a chave privada para a VPS.
**Tempo:** uns 15 minutos.

Anote o resultado no fim (seção 7).

---

## 1. O que você precisa no computador

- A **chave privada gpg** dos backups (a que faz par com a pública que está na VPS).
- `gpg` instalado.
- Docker (para subir um Postgres temporário), ou um Postgres 16 local.
- Acesso à conta do B2 pelo navegador, ou o `rclone`/`b2` configurado com uma chave **só de leitura**.

## 2. Baixar o backup

Pelo navegador: B2 → *Browse Files* → bucket → pasta `diario/` → baixe o arquivo mais recente, `gateway-backup-AAAAMMDDTHHMMSSZ.tar.gpg`.

Na VPS, o arquivo `envio.json` da pasta do mesmo backup tem o tamanho e a soma esperados:

```
cat /home/twadmin/backups/gateway/diario/<data>/envio.json
```

No computador, confira a soma do que você baixou (tem de ser igual ao `sha256` do `envio.json`):

```
sha256sum gateway-backup-*.tar.gpg
```

## 3. Decifrar e abrir

```
mkdir ensaio && cd ensaio
gpg --decrypt ../gateway-backup-*.tar.gpg > backup.tar
tar -xf backup.tar
ls -la
```

Devem aparecer quatro arquivos: `gateway.dump`, `redis.rdb`, `manifesto.json` e `.env`.

- Se o `gpg` pedir a senha da chave privada e abrir, a chave está certa.
- Se disser que não há chave secreta, a chave privada deste computador não é o par da pública da VPS: **pare e resolva isso antes de mais nada**, porque nenhum backup seria recuperável.

Confira as somas com o manifesto:

```
sha256sum gateway.dump redis.rdb .env
cat manifesto.json
```

## 4. Restaurar num banco temporário

```
docker run -d --name ensaio-pg -e POSTGRES_PASSWORD=ensaio -p 127.0.0.1:55432:5432 postgres:16-alpine
sleep 8
docker exec ensaio-pg createdb -U postgres gateway_ensaio
docker exec -i ensaio-pg pg_restore -U postgres -d gateway_ensaio --no-owner --exit-on-error < gateway.dump
```

Sem mensagem de erro, a restauração terminou.

## 5. Conferir com o manifesto

```
docker exec ensaio-pg psql -U postgres -d gateway_ensaio -At -c "SELECT json_build_object('esquema',(SELECT max(versao) FROM schema_migrations),'tabelas',(SELECT count(*) FROM information_schema.tables WHERE table_schema='public'),'tenants',(SELECT count(*) FROM tenants),'agentes',(SELECT count(*) FROM agentes),'tenant_canais',(SELECT count(*) FROM tenant_canais),'conversas',(SELECT count(*) FROM conversas),'mensagens',(SELECT count(*) FROM mensagens),'turnos',(SELECT count(*) FROM turnos),'pedidos',(SELECT count(*) FROM pedidos),'contatos',(SELECT count(*) FROM contatos))"
```

Compare com o bloco `"contagens"` do `manifesto.json`: os dez números têm de ser iguais. Se `"contagens_exatas"` estiver `false` no manifesto, os números podem ficar entre `"contagens_antes"` e `"contagens"`.

Leitura de verificação (opcional): veja se a conversa mais recente tem mensagens.

```
docker exec ensaio-pg psql -U postgres -d gateway_ensaio -c "SELECT count(*) FROM mensagens WHERE conversation_id=(SELECT conversation_id FROM conversas ORDER BY atualizado_em DESC LIMIT 1)"
```

Confira também que o `.env` baixado tem a linha `ENCRYPTION_KEY=` preenchida (não mostre o valor a ninguém). Sem ela, um banco restaurado não lê os tokens dos canais nem do Skyone.

## 6. Apagar tudo

```
docker rm -f ensaio-pg
cd .. && rm -rf ensaio gateway-backup-*.tar.gpg
```

O `.env` e o banco aberto contêm segredos e dados de clientes: não deixe cópias no computador.

## 7. Registro do ensaio

| Data | Backup usado | Decifrou | Somas conferem | Restaurou | Contagens batem | Quem fez |
|---|---|---|---|---|---|---|
| | | | | | | |

---

## Se for uma recuperação de verdade (VPS perdida)

1. Numa VPS nova, instale Docker e copie a pasta do gateway (do git) para `/home/twadmin/gateway`.
2. No seu computador, decifre o backup mais recente (seções 2 e 3) e envie para a VPS nova, por canal seguro, **só** o `.env`, o `gateway.dump` e o `redis.rdb`. A chave privada continua no seu computador.
3. Ponha o `.env` na pasta do gateway e suba só o banco e o Redis: `docker compose up -d postgres redis`.
4. Crie o banco de produção e restaure: `createdb gateway` e `pg_restore -d gateway --no-owner < gateway.dump` (dentro do contêiner do Postgres).
5. O `redis.rdb` guarda a fila do momento do backup. Depois de horas ela não vale mais: suba o Redis vazio. As mensagens que ficaram sem resposta são recuperadas pela reconciliação com o banco.
6. Construa e suba o gateway (`docker compose build gateway` e `docker compose up -d`), confira `/health` e reaponte o endereço (DNS ou proxy).
7. Reinstale as tarefas agendadas: `sh scripts/ops/instalar_cron.sh`.

O que se perde: tudo o que aconteceu depois do último backup (no máximo 24 horas).
