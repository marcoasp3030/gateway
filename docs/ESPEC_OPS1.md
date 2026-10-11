# OPS1 — Backup, teste de restauração, alertas e disponibilidade

**Situação:** **aprovada em 11/10/2026** (decisões na seção 9, que valem sobre o texto anterior onde houver diferença) e **implementada** (seção 10). O teste de carga do Skyone (seção 6) continua só especificado.
**Data:** 11/10/2026. Produção: `gateway:2b19ef4`, schema 34.

---

## 1. Como está hoje

| Assunto | Situação |
|---|---|
| Backup | Só antes de cada implantação (`scripts/implantar.sh`), guardado em `/home/twadmin/backups/gateway`, **no mesmo disco**. Sem agendamento, sem cópia fora da VPS, sem criptografia |
| Restauração | Nunca testada de ponta a ponta. O script só confere que o arquivo pode ser lido |
| Redis | Persistência ligada (AOF a cada segundo), no volume `gateway_redisdata`. Sem cópia |
| Alertas | Fila atrasada e cota alta do Skyone viram registro na auditoria e linha no log. **Ninguém é avisado** |
| Disponibilidade | `GET /gateway/health` público devolve `{"ok": true}` depois de conferir banco e Redis. Sem monitor externo |
| Ferramentas na VPS | `gpg`, `curl` e `crontab` instalados. `age`, `rclone` e cliente S3 **não** instalados |
| Tamanho | Banco com 15 MB; backup atual com ~300 KB |

Ponto que o backup do banco **não** cobre: a chave de criptografia das credenciais (`ENCRYPTION_KEY`, no `.env`). Sem ela, um banco restaurado não consegue ler os tokens dos canais e do Skyone. Ver decisão 1.

---

## 2. Backup diário

### 2.1 O que entra

| Item | Como | Arquivo |
|---|---|---|
| Postgres | `pg_dump -Fc` do banco `gateway`, pelo contêiner do banco | `gateway-AAAAMMDDTHHMMZ.dump` |
| Redis | `redis-cli --rdb`, que pede um snapshot sem parar o serviço | `redis-AAAAMMDDTHHMMZ.rdb` |
| Manifesto | Versão do esquema, commit em produção, tamanho, soma de verificação e a contagem de linhas de 8 tabelas no momento do backup | `manifesto-AAAAMMDDTHHMMZ.json` |

O manifesto não tem dado de cliente; é o que o teste de restauração usa para comparar.

### 2.2 Criptografia

- Os três arquivos são juntados e **cifrados na VPS com a chave pública** antes de sair. A chave privada nunca fica na VPS: sem ela, quem invadir a máquina ou o armazenamento não lê os backups.
- Proposta: **`gpg`**, que já está instalado (cifragem só com a chave pública, sem senha na VPS). `age` é mais simples de operar, mas exige instalar um pacote. Ver decisão 2.
- Você gera o par de chaves no seu computador e me passa **só a pública** (arquivo em `/home/twadmin/gateway/.local/`, fora do git). O script recusa rodar sem ela.

### 2.3 Destino externo

- Compatível com S3, configurável por variável de ambiente no `.env` da VPS (nunca no repositório):

| Variável | Conteúdo |
|---|---|
| `BACKUP_S3_ENDPOINT` | endereço do serviço (R2 ou B2) |
| `BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIXO` | onde gravar |
| `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY` | credenciais, cadastradas por você direto na VPS |
| `BACKUP_CHAVE_PUBLICA` | caminho do arquivo da chave pública |

- Ferramenta de envio: proposta **`rclone`** (um pacote, fala com R2 e B2, confere a soma de verificação depois do envio). Exige instalar na VPS. A alternativa sem instalar nada é o `curl`, que envia mas torna a retenção e a conferência mais frágeis. Ver decisão 3.
- Recomendo criar a credencial **sem permissão de apagar** (o B2 permite; no R2, usar a trava de retenção do bucket). Assim, mesmo com a VPS comprometida, os backups antigos não podem ser destruídos.

### 2.4 Retenção: 7 diários, 4 semanais, 6 mensais

| Pasta no bucket | Quando recebe | Guarda |
|---|---|---|
| `diario/` | todo dia | 7 dias |
| `semanal/` | domingo | 4 semanas |
| `mensal/` | dia 1 | 6 meses |

- A expiração fica a cargo das **regras de ciclo de vida do próprio bucket** (R2 e B2 têm), por pasta. A VPS não precisa apagar nada lá, o que combina com a credencial sem permissão de apagar.
- Na VPS ficam os **3 últimos** backups sem cifrar (permissão 600), para restauração rápida e para o teste semanal. Ver decisão 4.
- Os backups de antes de cada implantação continuam como hoje; passam a ter limpeza automática dos mais velhos que 30 dias, **só com a sua confirmação** (decisão 5).

### 2.5 Execução

- Script `scripts/backup.sh`, chamado todo dia às **04:30 (horário de Brasília)** pelo `crontab` do usuário `twadmin` (não precisa de root).
- Passos: backup → manifesto → cifrar → enviar → conferir tamanho e soma no destino → registrar resultado → limpar locais antigos.
- Qualquer passo que falhe interrompe, grava o motivo e dispara o alerta. Sucesso grava data, tamanho e duração.
- O resultado fica em `/home/twadmin/backups/gateway/estado/ultimo_backup.json` e numa tabela nova `ops_execucoes` (tipo, início, fim, resultado, detalhe), para o painel mostrar depois.
- Tempo esperado hoje: poucos segundos. O backup não para o atendimento.

---

## 3. Teste de restauração semanal

Domingo às **05:00**, script `scripts/teste_restauracao.sh`:

1. Pega o backup mais recente.
2. Cria um banco temporário no mesmo Postgres, com nome terminado em `_test` (`gateway_restauracao_test`).
3. Restaura nele.
4. Compara com o manifesto: número de tabelas, versão do esquema e contagem de linhas de `tenants`, `agentes`, `tenant_canais`, `conversas`, `mensagens`, `turnos`, `pedidos`, `contatos`. Tem de bater exatamente.
5. Faz uma leitura de verificação (uma conversa com suas mensagens).
6. Apaga o banco temporário, sempre, inclusive se algum passo falhar.
7. Registra o resultado e avisa em caso de falha.

**Limite desta automação:** como a chave privada não fica na VPS, o teste automático **não consegue abrir o arquivo cifrado** que está no bucket. Ele usa a cópia local do mesmo backup e, do lado externo, confere que o arquivo cifrado existe e tem o tamanho e a soma registrados. A prova completa (baixar do bucket, decifrar e restaurar) só pode ser feita por você, com a chave privada. Proponho um **ensaio manual a cada 3 meses**, com roteiro passo a passo no documento de operação, e o primeiro logo depois da implantação do OPS1. Ver decisão 6.

O banco de produção nunca é tocado pelo teste.

---

## 4. Alertas pelo Telegram

### 4.1 Onde cada alerta nasce

| Quem vigia | Por quê | Alertas |
|---|---|---|
| **Script na VPS** (`scripts/vigia.sh`, a cada 2 min pelo `crontab`) | Precisa funcionar mesmo com o gateway fora do ar | Backup falhou ou atrasou; teste de restauração falhou; contêiner parado, reiniciando ou não saudável; disco acima do limite; agendador sem dar sinal |
| **Agendador do gateway** (a cada 30 s) | Enxerga a fila e as chamadas ao agente | Worker sem dar sinal; fila acima do limite; erro do Skyone acima do limite |

Os dois usam o mesmo bot e o mesmo chat. Credenciais só no `.env` da VPS: `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`. Sem elas, os alertas ficam desligados e o log avisa uma vez.

### 4.2 Lista e limites (padrões propostos, todos configuráveis)

| Alerta | Dispara quando | Normaliza quando |
|---|---|---|
| Falha de backup | O script terminou com erro, ou não há backup com sucesso nas últimas 26 h | Próximo backup com sucesso |
| Falha no teste de restauração | O teste terminou com erro ou diferença | Próximo teste com sucesso |
| Worker parado | Um worker sem dar sinal há mais de 90 s, ou menos workers ativos que o configurado | Todos de volta por 2 min |
| Serviço parado | Contêiner do gateway, agendador, banco ou Redis parado, reiniciando em laço ou não saudável por mais de 2 min | Saudável por 2 min |
| Fila acima do limite | Mensagem esperando na fila há mais de 60 s, ou mais de 50 conversas aguardando, em 2 conferências seguidas | Abaixo do limite por 2 min |
| Erro do Skyone | Mais de 20% de erros ou de tempo esgotado em 10 min, com pelo menos 5 chamadas no período | Abaixo de 10% por 10 min |
| Disco | Uso acima de 80% (aviso) e acima de 90% (crítico) | Abaixo de 78% |

Extras que sugiro incluir (decisão 7): **WhatsApp desconectado** (canal da uazapi fora do ar por mais de 5 min) e **memória** (menos de 150 MB disponíveis por 5 min).

### 4.3 Regras

- **Antirrepetição:** o mesmo alerta, para o mesmo alvo, no máximo **1 vez a cada 30 min**. Enquanto o problema durar, o lembrete repete nesse intervalo.
- **Normalizado:** quando o problema acaba, sai uma mensagem só, dizendo quanto tempo durou.
- **Sem dado de cliente:** as mensagens trazem serviço, empresa (identificador), número e horário. Nunca telefone, nome ou texto de conversa.
- **Teste:** `sh scripts/alerta.sh teste` envia uma mensagem de teste, para você confirmar que o bot está certo.
- Todo alerta e toda normalização ficam também na auditoria da plataforma.

Exemplos:

```
🔴 GATEWAY — fila acima do limite
Empresa: bykoji · 3 conversas esperando · a mais antiga há 74 s
11/10 15:42

🟢 GATEWAY — normalizado: fila acima do limite
Empresa: bykoji · durou 4 min
11/10 15:46
```

---

## 5. Disponibilidade para o UptimeRobot

- `GET https://painel.luxbrain.com.br/gateway/health` já serve: responde `{"ok": true}` (200) só depois de conferir banco e Redis, e 503 quando algo falha. Sem dados. Fica como está.
- Proposta de mais um endereço público, também só com status: `GET /gateway/health/ops` → `{"ok": true}` (200) ou `{"ok": false}` (503), sem dizer o motivo. Fica 503 quando: não há backup com sucesso nas últimas 26 h, o último teste de restauração falhou, ou o agendador está sem dar sinal. Assim o UptimeRobot também avisa se **o próprio sistema de alertas parar**. Ver decisão 8.
- Os dois com o limite de requisições que as rotas públicas já têm. Sugestão no UptimeRobot: conferir a cada 5 min e procurar a palavra `true`.

---

## 6. Teste de carga no Skyone — especificação (não executar)

**Objetivo:** saber quantas conversas simultâneas o fluxo do Skyone aguenta, e como a latência se comporta, antes de colocar mais empresas no mesmo fluxo.

| Ponto | Proposta |
|---|---|
| Onde | Em produção, numa empresa de teste nova (`carga_skyone`, marcada como teste), com um agente apontando para o **mesmo fluxo** do Skyone, canal do tipo API e **envio de teste** (nada sai por WhatsApp) |
| Como gerar a carga | Pela rota de conversa da API (`/api/v1/conversas/mensagens`), com identificadores fictícios. Cada conversa segue um roteiro de 4 mensagens: saudação, escolha da unidade, pedido de um item, pergunta de horário. As Skills são chamadas de verdade contra um cardápio de teste |
| Degraus | 10, 25, 50, 75 e 100 conversas simultâneas, 5 min em cada, com 3 min de pausa entre eles |
| O que medir | Latência do agente (mediana e p95) por degrau; erros e tempo esgotado; respostas fora do formato; turnos refeitos; espera na fila do gateway; uso da cota por minuto; chamadas de Skill por turno |
| Critério de aprovação | Em 50 simultâneas: p95 do agente ≤ 25 s, erros < 2%, nenhuma resposta perdida ou duplicada |
| Critério de parada | Erros acima de 10% ou p95 acima de 60 s num degrau: para ali, e esse degrau é o teto |
| Quando | Fora do horário de atendimento do By Koji, com a sua autorização no dia, porque a cota de 240 chamadas por minuto é do fluxo e é dividida com o atendimento real |
| Custo | Cerca de 4 000 a 5 000 chamadas ao modelo no total. Vale confirmar o custo no Skyone antes |
| Limpeza | A empresa de teste e as conversas ficam marcadas como teste e podem ser apagadas depois, com a sua confirmação |
| Saída | Tabela por degrau em `docs/CAPACIDADE.md` e a recomendação: quantas vagas configurar e quantas empresas por fluxo |

Para passar de 240 chamadas por minuto durante o teste, o limite do gateway para aquela URL precisa ser levantado temporariamente; isso só com a sua ordem.

---

## 7. Blocos e testes

| Bloco | Conteúdo | Muda o atendimento? |
|---|---|---|
| **OPS1-a** | Backup diário cifrado e enviado, manifesto, retenção local, teste de restauração semanal, `ops_execucoes`, alertas do script da VPS (backup, restauração, serviço parado, disco) | Não |
| **OPS1-b** | Alertas do agendador (worker, fila, Skyone), antirrepetição e "normalizado", `/health/ops`, roteiro de operação (`docs/OPERACAO.md`) com o ensaio manual de restauração | Não |

O teste de carga fica só especificado (seção 6).

Testes automáticos:
- Antirrepetição: o mesmo alerta duas vezes em menos de 30 min sai uma vez; depois de 30 min sai o lembrete; alvos diferentes não se bloqueiam.
- "Normalizado" sai uma vez, com a duração, e só se houve alerta antes.
- Cada limite: logo abaixo não dispara, logo acima dispara; erro do Skyone com menos de 5 chamadas não dispara.
- Mensagens sem telefone, nome nem texto de conversa.
- Sem credencial do Telegram: nada é enviado e nada quebra.
- `/health/ops`: 200 e 503 nos três casos; resposta sem nenhum dado além de `ok`.
- Manifesto e comparação: restauração com uma linha a menos é reprovada; igual é aprovada; o banco temporário é apagado nos dois casos.

Testes dos scripts (feitos na implantação, com registro no relatório):
- Backup real de ponta a ponta, com o arquivo cifrado conferido no destino.
- Falha provocada em cada passo (sem chave pública, destino inacessível, disco de trabalho cheio simulado) gera alerta e não deixa arquivo pela metade.
- Teste de restauração real no banco temporário.
- `sh scripts/alerta.sh teste` chegando no seu Telegram.

Nenhum teste escreve no banco de produção.

---

## 8. Decisões que preciso de você

1. **Chave do `.env` (`ENCRYPTION_KEY` e demais segredos):** o backup do banco não serve sem ela. Proposta: ela **não** entra no backup automático; você guarda uma cópia do `.env` fora da VPS (cofre de senhas), e o roteiro de operação lembra de atualizar essa cópia quando algum segredo mudar. Alternativa: cifrar o `.env` com a mesma chave pública e enviar junto.
2. **Criptografia:** `gpg` (já instalado; proposta) ou `age` (exige instalar)?
3. **Envio:** posso instalar o `rclone` na VPS (`apt install rclone`), ou prefere sem instalar nada, com `curl`?
4. **Cópias locais sem cifrar:** manter as 3 últimas na VPS para restauração rápida e para o teste semanal (proposta), ou nenhuma cópia legível na VPS? Sem cópia local, o teste automático de restauração deixa de ser possível.
5. **Backups antigos de implantação** (hoje 40 arquivos, 7 MB): apagar automaticamente os de mais de 30 dias?
6. **Ensaio manual completo** (baixar, decifrar com a chave privada e restaurar): a cada 3 meses, com o primeiro logo depois do OPS1?
7. **Alertas extras:** incluir WhatsApp desconectado e memória baixa?
8. **`/health/ops`** público, só com `ok`: incluir?
9. **Limites** da seção 4.2 (fila 60 s ou 50 conversas; Skyone 20% em 10 min; disco 80%): servem como ponto de partida?
10. **Horários:** backup às 04:30 e teste de restauração aos domingos às 05:00 (Brasília)?
11. **Destino:** R2 ou B2? Recomendo o B2 se a credencial sem permissão de apagar for importante para você; o R2 se preferir ficar na Cloudflare.

---

## 9. Decisões do Marco (11/10/2026) — aprovada

1. **`.env`:** cifrado com a mesma chave pública e enviado dentro de cada backup (o manifesto traz o tamanho e a soma dele). O Marco também guarda uma cópia fora da VPS.
2. **Criptografia:** `gpg`. Chave pública num arquivo que o Marco coloca na VPS; caminho em `BACKUP_CHAVE_PUBLICA`.
3. **Envio:** `rclone` autorizado, instalado pelo pacote oficial. Envio conferido por tamanho e soma.
4. **Cópias locais sem cifrar:** as 3 últimas, permissão 600, do usuário do gateway.
5. **Backups de implantação:** apagar os de mais de 30 dias, mantendo sempre os 5 mais recentes.
6. **Ensaio manual trimestral:** sim, com roteiro em `docs/RESTAURACAO_MANUAL.md`. O primeiro logo depois do OPS1.
7. **Alertas extras:** WhatsApp desconectado (uazapi) e memória baixa (disponível abaixo de 10% por 5 min).
8. **`/health/ops` público:** incluído, só `ok` com 200 ou 503, sem dados.
9. **Limites:** fila com espera acima de 60 s ou mais de 50 conversas; Skyone acima de 20% de erro em 10 min **com mínimo de 5 chamadas**; disco com aviso em 80% e crítico em 90%. Todos configuráveis.
10. **Horários:** backup 04:30; teste de restauração domingo 05:00 (America/Sao_Paulo).
11. **Destino:** Backblaze B2, retenção por regras de ciclo de vida por pasta, chave da VPS sem permissão de apagar.
12. **Inerte até as credenciais existirem:** backup local e teste de restauração funcionam; envio e alertas ficam desligados, com aviso no registro e `/health/ops` em 503.

## 10. OPS1 — implementado (11/10/2026)

### 10.1 O que existe

| Peça | Onde | Faz |
|---|---|---|
| `scripts/backup.sh` | cron, 04:30 | Backup do banco, snapshot do Redis, manifesto; cifra e envia quando o destino está configurado; limpa as cópias locais antigas |
| `scripts/teste_restauracao.sh` | cron, domingo 05:00 | Restaura o backup local mais recente em `gateway_restauracao_test`, compara com o manifesto, apaga o banco e confere o arquivo no destino |
| `scripts/vigia.sh` | cron, a cada 2 min | Contêineres, disco, memória e atraso do backup |
| `scripts/alerta.sh` | chamado pelos três | Telegram, com repetição de 30 min e "normalizado"; `teste` envia uma mensagem de teste |
| `scripts/ops/instalar_cron.sh` | manual | Instala ou remove as três tarefas no crontab do usuário, sem tocar nas outras linhas |
| `app/nucleo/alertas.py` | agendador, a cada 30 s | Worker parado, fila, erro do Skyone, WhatsApp desconectado |
| `GET /health/ops` | gateway | Situação da operação |
| `ops_execucoes` (migração 035) | banco | Histórico de backups e testes de restauração |

- **Horários no cron:** a VPS está em UTC e o cron dela não aceita fuso por tarefa; as tarefas estão gravadas como 07:30 UTC (04:30 em São Paulo) e domingo 08:00 UTC (05:00).
- **`/health/ops` por fora:** liberado no proxy do painel em 11/10/2026 (linha `@publico` do Caddy, feita como tarefa do painel). Por fora responde só `{"ok": true}` (200) ou `{"ok": false}` (503): o Caddy envia `X-Forwarded-For` e o gateway trata como chamada externa, sem `motivos`.
- **rclone:** versão 1.75.2, em `/home/twadmin/.local/bin/rclone`, baixado de `downloads.rclone.org` com a soma oficial conferida. Foi instalado na pasta do usuário porque a conta não tem `sudo` sem senha; a versão do `apt` do Ubuntu 22.04 (1.53.3) é de 2020.
- As credenciais do destino vão ao `rclone` só por variáveis de ambiente no momento do envio: não há arquivo de configuração do `rclone` com segredo, e nada aparece na linha de comando.
- O token do Telegram vai ao `curl` pela entrada padrão, não pela linha de comando; nenhum script imprime valor de variável.

### 10.2 Conteúdo do backup

Pasta local `/home/twadmin/backups/gateway/diario/<data>/` (700, arquivos 600): `gateway.dump`, `redis.rdb`, `manifesto.json` e, depois do envio, `envio.json` (nome, tamanho e soma do arquivo cifrado).
Arquivo enviado: `gateway-backup-<data>.tar.gpg`, com os três arquivos **mais o `.env`**, cifrado com a chave pública.

O manifesto traz: data, commit, versão do esquema, número de tabelas, contagem de `tenants`, `agentes`, `tenant_canais`, `conversas`, `mensagens`, `turnos`, `pedidos`, `contatos`, e tamanho e soma de cada arquivo. A contagem é lida antes e depois do `pg_dump`; se mudar no meio, o backup é refeito (até 3 vezes), para o manifesto descrever exatamente o arquivo.

### 10.3 Estados do backup

| Resultado | Significa | `/health/ops` |
|---|---|---|
| `ok` | Feito, cifrado, enviado e conferido no destino | 200 |
| `local` | Feito só na VPS: falta configurar o destino (o registro diz o que falta) | **503** (`destino_nao_configurado`) |
| `falha` | Não concluiu; o registro diz o passo. Falha ao cifrar ou enviar mantém a cópia local | **503** e alerta |

`/health/ops` também fica 503 com: nenhum backup ainda, último backup concluído há mais de 26 h, último teste de restauração com falha, ou agendador sem sinal. Para fora só `{"ok": true}` ou `{"ok": false}`; a chamada interna (`http://127.0.0.1:8100/health/ops`) traz `motivos`.

### 10.4 Variáveis de ambiente (só nomes; os valores ficam no `.env` da VPS)

| Variável | Para quê | Obrigatória para |
|---|---|---|
| `BACKUP_CHAVE_PUBLICA` | Caminho do arquivo da chave pública gpg | enviar |
| `BACKUP_S3_ENDPOINT` | `https://s3.<região>.backblazeb2.com` | enviar |
| `BACKUP_S3_REGIAO` | Região do bucket (ex.: `us-west-004`) | enviar (B2) |
| `BACKUP_S3_BUCKET` | Nome do bucket | enviar |
| `BACKUP_S3_PREFIXO` | Pasta dentro do bucket (opcional) | — |
| `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY` | keyID e applicationKey da chave sem permissão de apagar | enviar |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Bot e chat dos alertas | alertar |
| `ALERTA_*` | Limites (padrões na seção 9) | — |

Depois de cadastrar as do Telegram ou mudar algum `ALERTA_*`, o agendador precisa reler o `.env`: `cd /home/twadmin/gateway && docker compose up -d --no-deps agendador`. Os scripts da VPS leem o `.env` a cada execução.

### 10.5 Regras de ciclo de vida no bucket do B2

No bucket (privado), em *Lifecycle Settings* → *Use custom lifecycle rules*, três regras. Se usar `BACKUP_S3_PREFIXO`, ponha-o na frente (ex.: `gateway/diario/`).

| Prefixo do nome do arquivo | Dias até ocultar (`daysFromUploadingToHiding`) | Dias até apagar depois de oculto (`daysFromHidingToDeleting`) | Resultado |
|---|---|---|---|
| `diario/` | 7 | 1 | 7 diários |
| `semanal/` | 28 | 1 | 4 semanais |
| `mensal/` | 180 | 1 | 6 mensais |

**Chave de aplicação da VPS:** restrita a esse bucket, com as permissões `listBuckets`, `listFiles`, `readFiles` e `writeFiles` — **sem `deleteFiles`**. A tela do B2 só oferece "Read and Write", que inclui apagar; a chave sem apagar é criada pelo programa de linha de comando do B2, no seu computador:

```
b2 key create --bucket <nome-do-bucket> gateway-vps listBuckets,listFiles,readFiles,writeFiles
```

Ele mostra o `keyID` (vai em `BACKUP_S3_ACCESS_KEY`) e a `applicationKey` (vai em `BACKUP_S3_SECRET_KEY`), uma vez só. O endereço S3 e a região aparecem na página do bucket.

### 10.6 Comandos

| Para | Comando |
|---|---|
| Testar o alerta do Telegram | `sh /home/twadmin/gateway/scripts/alerta.sh teste` |
| Fazer um backup agora (e o primeiro envio) | `sh /home/twadmin/gateway/scripts/backup.sh` |
| Testar a restauração agora | `sh /home/twadmin/gateway/scripts/teste_restauracao.sh` |
| Ver a situação com os motivos | `curl -s http://127.0.0.1:8100/health/ops` |
| Ver o que aconteceu | `tail -n 30 /home/twadmin/backups/gateway/estado/ops.log` |
| Reinstalar ou remover as tarefas agendadas | `sh scripts/ops/instalar_cron.sh` / `sh scripts/ops/instalar_cron.sh remover` |

### 10.7 Limites conhecidos

- O teste automático de restauração usa a cópia local; o arquivo cifrado do destino só é conferido por existência e tamanho. A prova completa é o ensaio manual (`docs/RESTAURACAO_MANUAL.md`).
- O `.env` só é protegido pelo backup **externo** (cifrado). Na VPS ele continua sendo o arquivo de sempre.
- O alerta de erro do Skyone conta turnos reais que terminaram com erro do agente; conversa em modo simulado não entra.
- "WhatsApp desconectado" depende do estado que a uazapi informa (evento de conexão ou varredura a cada 90 s).
- Se a VPS inteira cair, quem avisa é o UptimeRobot; os alertas daqui dependem de a máquina estar no ar.
