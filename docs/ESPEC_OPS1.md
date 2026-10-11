# OPS1 — Backup, teste de restauração, alertas e disponibilidade

**Situação:** especificação para aprovação. **Nada implementado.** O código só começa depois de o Marco confirmar que a outra sessão do gateway foi encerrada e de aprovar este documento.
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
