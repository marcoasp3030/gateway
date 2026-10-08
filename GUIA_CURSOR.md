# GUIA_CURSOR.md — Como construir o gateway com o Cursor, passo a passo

Este guia é para **você (Marco)**. Ele explica como conduzir o agente do Cursor, conectado à VPS, pelas fases do `PLANO_CURSOR.md`. O `PLANO_CURSOR.md` é o roteiro técnico que o **agente** segue; este arquivo diz **o que você faz e o que você cola no chat** em cada etapa.

```
Você ──► (prompt deste guia) ──► Agente do Cursor ──► lê PLANO_CURSOR.md + .cursor/rules
                                        │
                                        ├─ implementa a fase
                                        ├─ roda validações e testes
                                        ├─ registra no PROGRESS.md
                                        └─ para nos checkpoints 🛑 e te chama
```

---

## Parte 1 — Preparação (uma vez só)

### 1.1 Conectar o Cursor na VPS

1. No Cursor, abra a paleta (`Ctrl/Cmd + Shift + P`) → **Remote-SSH: Connect to Host** → `usuario@ip-da-vps`.
2. Confirme no terminal integrado que está na VPS: `hostname`.

### 1.2 Conferir o que a VPS já tem

No terminal integrado do Cursor:

```bash
docker --version && docker compose version
git --version
ss -ltnp | grep -E ':8100|:5433'      # deve vir vazio
docker ps --format '{{.Names}}' | grep -i caddy   # proxy em uso (Caddy do painel)
```

Se a porta 5433 ou 8100 estiver ocupada, anote para ajustar no `docker-compose.yml`.

### 1.3 Colocar o projeto na VPS

Do seu computador:

```bash
scp luxbrain-gateway.zip usuario@ip-da-vps:~/
```

Na VPS (terminal do Cursor):

```bash
cd ~ && unzip luxbrain-gateway.zip && cd luxbrain-gateway
git init && git add -A && git commit -m "estado inicial"
```

No Cursor: **File → Open Folder → `~/luxbrain-gateway`**.

### 1.4 Conferir as regras do agente

O projeto já traz `.cursor/rules/gateway.mdc` com `alwaysApply: true`. Isso faz o agente carregar as regras do projeto em toda conversa. Confira em **Settings → Rules** que a regra aparece no projeto.

### 1.5 Ajustes de segurança no Cursor

- Em **Settings → Agent**, deixe a execução de comandos **pedindo confirmação** (não use "auto-run" para tudo). Se quiser agilidade, libere só comandos de leitura e teste (`ls`, `cat`, `grep`, `pytest`, `curl -s`, `docker compose ps`, `docker compose logs`).
- Nunca aprove automaticamente: `rm`, `docker compose down -v`, `DROP`, `caddy reload`, `git push`.
- O `.env` com segredos reais só existe na VPS e está no `.gitignore`.

### 1.6 Como trabalhar com o agente

| Faça | Evite |
|---|---|
| **Um chat novo por fase** (contexto limpo) | Pedir várias fases de uma vez |
| Usar o modo **Agent** | Usar o modo Ask para implementar |
| Pedir para ele **mostrar o plano da fase antes** de codar | Deixar ele "sair fazendo" sem você ver o plano |
| Revisar o **diff** antes de aceitar | Aceitar tudo sem ler |
| Commit ao fim de cada fase | Acumular várias fases sem commit |
| Colar erros completos (log, saída do pytest) | Descrever o erro de memória |

---

## Parte 2 — Prompts padrão

Use estes textos como base. Troque `<N>` pela fase.

### Iniciar uma fase

```
Leia PLANO_CURSOR.md (seções 1, 2, 2.1 e a FASE <N>) e o PROGRESS.md.
Antes de alterar qualquer arquivo, me mostre:
1) o plano da fase em passos curtos,
2) os arquivos que vai criar ou alterar,
3) os comandos que vai precisar rodar,
4) qualquer dúvida ou checkpoint 🛑 que dependa de mim.
Espere meu "ok" para começar.
```

### Depois do seu "ok"

```
Ok, pode executar a FASE <N>. Trabalhe tarefa por tarefa, rodando a validação
de cada uma. Se encontrar um checkpoint 🛑, pare e me diga exatamente o que
preciso fazer. Não avance de fase.
```

### Quando uma validação falhar

```
A validação falhou. Aqui está a saída completa:
<cole aqui>
Encontre a causa raiz antes de mudar o código. Me explique a causa em 2-3 linhas,
proponha a correção e rode de novo a validação. Não desative nem enfraqueça testes.
```

### Fechar a fase

```
Rode todas as validações da FASE <N> do zero, inclusive os testes de invariante
da fase. Atualize o PROGRESS.md no formato da seção 3 do plano (com comandos,
resultados e métricas) e faça o commit "fase <N>: <resumo>".
Depois liste o que ficou pendente para mim.
```

### Revisão antes de aceitar (opcional, para fases grandes)

```
Revise o diff desta fase como um revisor exigente: isolamento entre tenants,
segredos em log, invariantes INV01–INV13, queries sem tenant_id, tratamento
de erro, testes faltando. Liste problemas por gravidade e corrija os graves.
```

---

## Parte 3 — Fase a fase

Ordem: **0 → 0.3 → 0.5 → 0.6 → 1 → 2 → 3 → 4 → 5 → 5.5 → 6 → 6.5 → 7 → (8) → 9**.

### Fase 0 — Implantação em modo simulado

**Você:** use o prompt "Iniciar uma fase" com `<N> = 0`.

**O agente vai:** criar `.gitignore`, gerar o `.env` com chaves fortes, subir os containers, rodar o `bench.py` e criar o `PROGRESS.md`.

**Você confere:**
- `curl -s http://127.0.0.1:8100/health` → `{"ok":true,"agente":"mock","envio":"dry"}`
- Bench com 5/5 respondidos e 0 erros.
- Segurança: `/docs` → 404; rota administrativa com `X-Forwarded-For` → 404; `docker compose exec gateway id` não é root; `tests/test_seguranca.py` verde.

**Atenção:** o `.env` gerado tem segredos. Guarde uma cópia no seu cofre de senhas.

### Fase 0.3 — Descobertas no Skyone

**Situação:** parte já respondida por você (ver `docs/SKYONE_RESULTADOS.md`): instruções pelo prompt funcionam, base/Skills só por agente, URL por fluxo (nunca duplicar), contrato de entrada/saída validado, latência causada pelo prompt do agente.

**O agente do Cursor vai:** alinhar o `scripts/skyone_probe.py` ao contrato validado, permitir rodar sem token (o fluxo de teste ainda está sem autenticação) e executar o probe na URL `https://luxtia.api.integrasky.cloud/k3iynwDoOU`.

**🛑 Você faz no Skyone** (roteiro completo na seção "Pendentes" de `docs/SKYONE_RESULTADOS.md`):
1. **Memória:** mesma `session_id` lembra? sessão diferente esquece?
2. **Autenticação:** ligue "Solicitar autenticação" no Webhook e anote o tipo e o nome do cabeçalho; anote o limite máximo de requisições/minuto.
3. **Agente "Teste Rápido"** com prompt curto, para confirmar a latência.
4. Depois (antes da Fase 0.6): simultâneas, tempo limite e callback.

Itens 1 e 2 bloqueiam a Fase 0.5. Conte os resultados ao agente do Cursor para ele registrar.

### Fase 0.5 — Multiempresa, agentes e chaves

**Você:** antes de iniciar, decida e informe no chat:
- Se o Postgres do seu painel será reaproveitado (e o nome do container/host), ou se o gateway usa o próprio.
- O slug do primeiro tenant (`bykoji`) e os modelos de agente iniciais (sugestão: `atendente_delivery` e `assistente_generico`).

**O agente vai:** criar tenants, módulos, modelos de agente, agentes, canais ligados a agentes, chaves de API com escopo, webhooks de saída, mover a configuração do `.env` para o banco, implementar a API `/admin/v1` e os testes de isolamento.

**Você confere:**
- `docker compose exec gateway pytest -q` verde, incluindo `test_INV08_*`, `test_INV17_*`, `test_SEG11_*` e `test_INV13_*`.
- Dois tenants e três agentes criados pela API, com métricas separadas.
- Criar um agente novo a partir de um modelo não exigiu nenhuma linha de código.
- 🛑 **Leia o `docs/API_ADMIN.md`** final. É o contrato do seu painel. Peça ajustes agora, antes de começar as telas.

**Dica:** esta é a fase mais longa. Se o chat ficar grande, peça: *"Resuma o que já foi feito e o que falta da FASE 0.5 no PROGRESS.md"*, e continue num chat novo com o prompt "Iniciar uma fase".

### Fase 0.6 — Robustez

**O agente vai:** separar recepção, workers e agendador; ligar persistência do Redis; fila durável; circuit breaker por agente; adaptador de agente; modo assíncrono por callback.

**Você confere:** o agente do Cursor demonstra (com os testes) que derrubar o Redis ou um worker no meio do processamento não perde mensagem, e que um agente com o Skyone fora do ar não atrasa os outros.

### Fase 1 — uazapi real (publicação já feita)

**O agente vai:** conferir a publicação já feita no Caddy (`https://painel.luxbrain.com.br/gateway/`, só webhooks, API e health públicos), criar o tenant e o canal uazapi pela API e te devolver a `webhook_url`.

**Você confere de fora da VPS (celular ou seu computador):** `https://painel.luxbrain.com.br/gateway/metricas` → 404 e `https://painel.luxbrain.com.br/gateway/health` → `{"ok":true}`.

**🛑 Você faz:**
1. Na instância uazapi **de teste**, configure o webhook com a `webhook_url` informada (evento de mensagens).
2. Do seu celular, mande para o número de teste:
   - texto simples e 3 mensagens picadas;
   - áudio;
   - imagem, e uma imagem logo depois de um texto;
   - **pino de localização**;
   - um PDF;
   - uma reação com emoji;
   - e, num segundo número, **responda pelo WhatsApp do atendente antes de o bot responder**.
3. Avise no chat: *"Mensagens enviadas, pode ler a webhook_raw."*

**Você confere:** mensagens classificadas certo (cliente, humano, localização, reação, anexo); reenvio do mesmo payload responde `duplicada`.

### Fase 2 — Agente Skyone ligado

**🛑 Você faz no Skyone (antes de iniciar):**
1. Crie o fluxo: **Webhook → JavaScript → IF → AI Agent Call → Log → Retorno**.
2. **JavaScript:** compare o `token` recebido com o token esperado e monte o texto para o agente: histórico com datas, mensagem do lote, `tempo_desde_ultima`, unidades abertas.
3. **IF:** token inválido → Retorno de erro, sem chamar o agente.
4. **Prompt do agente:** inclua as regras de saída:
   - responda em JSON com `resposta`, `acao`, `motivo`, `desfecho`;
   - use `acao: "silencio"` com `motivo` para agradecimentos, "ok", emojis soltos;
   - use `acao: "transferir_humano"` com `motivo` quando não puder resolver;
   - use `acao: "encerrar"` com `desfecho` quando o atendimento terminar;
   - se `retomada` for verdadeiro, trate como conversa nova, não continue o assunto antigo.
5. **Retorno:** devolva exatamente o JSON do Apêndice A do plano.
6. Passe ao agente a URL do webhook e o token.

**O agente vai:** configurar o tenant pela API, ligar o modo `live` com envio `dry`, rodar o bench e medir a latência.

**Você confere:** `total_p95` registrado no `PROGRESS.md`. Se passar de 15 s, o agente para e te reporta. Antes de seguir, decida com ele: prompt menor, modelo mais rápido no Skyone ou resposta assíncrona.

### Fase 3 — Conversa humanizada

**O agente vai:** implementar os testes dos invariantes INV01, INV03, INV04, INV05, INV07 e INV09; silêncio declarado; justiça entre tenants; modo piloto; encerramento com desfecho; limpeza de conversa vazia; retomada `#bot`; alerta de transbordo.

**Você faz:**
1. Peça para ativar o **modo piloto** com os números da sua equipe.
2. Peça `send_mode=live` **só** no canal de teste.
3. 🛑 Rode o roteiro de 30 a 50 conversas pelo celular. Inclua: mensagens picadas, "obrigado" no final, cliente voltando "dias depois" (simule alterando o horário com o agente), atendente assumindo, `#bot` para devolver.
4. Ajuste prompt e base de conhecimento no Skyone conforme os resultados.

**Você confere:** "obrigado" após o encerramento não gera resposta e o motivo aparece em `turnos`; atendente assume e o bot silencia; reiniciar o container não perde mensagem.

### Fase 4 — Módulos catálogo e pedidos (By Koji)

**🛑 Você entrega antes:** cardápio real em CSV (`unidade,categoria,nome,descricao,preco,ativo`), WhatsApp de cada unidade, formas de pagamento e regras de entrega.

**O agente vai:** criar carrinho/pedidos, endpoints das Skills, `docs/SKILLS.md`, confirmação pela unidade, webhooks `pedido.*` e aviso ao cliente via `/notificar`.

**Você faz no Skyone:** crie as Skills exatamente como descritas em `docs/SKILLS.md`, usando uma chave de API `skyone` do tenant com os escopos `ferramentas:catalogo` e `ferramentas:pedidos` (crie pela API/painel).

**Você confere:** pedido completo pelo celular chega à unidade com o total correto; nenhum preço divergente.

### Fase 5 — Áudio e imagem

**🛑 Você informa:** como o fluxo Skyone recebe imagem (URL ou base64) para o Processamento OCR.

**O agente vai:** transcrição idempotente (INV02), lote que espera mídia (INV06), imagem depois do texto no mesmo lote, fallback amigável.

**Você confere:** texto + áudio em sequência viram uma resposta só; o mesmo áudio reenviado não é transcrito de novo.

### Fase 5.5 — Memória e follow-ups

**🛑 Você faz no Skyone:** trate `tarefa: "resumir"` no fluxo (um IF que usa um prompt de resumo curto) e defina com o agente as regras de follow-up do By Koji (ex.: carrinho abandonado 30 min → 3 h → encerrar).

**Você confere:** cliente que volta é reconhecido pelo resumo; resposta do cliente cancela os próximos lembretes.

### Fase 6 — Site e e-mail

**Você informa:** domínio do site do cliente (para o CORS) e a caixa de e-mail (IMAP/SMTP) a ser usada.

### Fase 6.5 — Agentes internos e canais corporativos

**🛑 Você decide e providencia:**
- O primeiro agente interno piloto (ex.: analista comercial) e o que ele pode consultar.
- Os usuários de teste e o escopo de cada um (ex.: gerente Morumbi só vê Morumbi).
- O registro do app no Azure/Teams (reaproveitar o do Skyone ou criar um novo para o gateway).
- Acesso ao chat do Luxbrain para apontá-lo ao gateway.

**Você confere:** o agente responde no Teams e no chat do Luxbrain; um usuário não consegue ver dados fora do escopo, mesmo pedindo explicitamente; um relatório longo chega depois, por callback, com PDF.

### Fase 7 — WhatsApp oficial (Meta)

**🛑 Você providencia:** app na Meta, número de produção, tokens e templates aprovados (status de pedido, lembretes, retomada).

**Você confere:** o mesmo roteiro da Fase 3 passa no número oficial, sem mudança no Skyone.

### Fase 8 — iFood e 99Food

Só inicie depois de confirmar credenciamento e tipo de acesso com as plataformas.

### Fase 9 — Piloto e produção

**🛑 Você decide:** prazo de retenção de dados (LGPD), canais de alerta, unidade piloto e horário do piloto.

**Você confere:** alerta de teste chegou; backup restaurado com sucesso; métricas do painel batendo com as conversas.

---

## Parte 4 — Painel web em paralelo

Depois que a Fase 0.5 estiver pronta, você pode construir as telas do painel usando `docs/API_ADMIN.md`. Sugestão de ordem:

1. Empresas e onboarding (criar tenant → habilitar módulos → criar agente a partir de um modelo → testar agente → canal → webhook → chave de API para o Skyone)
2. Agentes (lista por empresa, status, circuito aberto/fechado, configuração, modo piloto)
3. Conversas ao vivo (lista por agente, histórico, assumir, devolver)
4. Chaves de API (criar com escopo, revogar, último uso) e webhooks de saída
5. Modelos de agente (só para você, admin da plataforma)
6. Cardápio e pedidos (após a Fase 4)
7. Usuários internos e escopos (após a Fase 6.5)
8. Métricas por empresa e por agente, com exportação CSV (após a Fase 9)

Lembre: a `PANEL_SERVICE_KEY` fica só no **servidor** do painel, nunca no navegador.

O painel chama o gateway pela **rede interna** (`http://127.0.0.1:8100/admin/v1`). Pelo domínio público a API administrativa responde 404 de propósito (SEG02).

---

## Parte 5 — Problemas comuns

| Sintoma | O que pedir ao agente |
|---|---|
| Bot respondeu duas vezes | "Verifique INV02 e a trava por conversa; mostre os turnos e mensagens dessa conversa." |
| Bot respondeu por cima do atendente | "Verifique INV03 com o payload real desse caso em webhook_raw; crie um teste com ele." |
| Bot ficou mudo | "Mostre o último turno dessa conversa: status, motivo_silencio, erro. Verifique INV04 e INV05." |
| Resposta lenta | "Mostre espera_ms, agente_ms e total_ms dos últimos 50 turnos e onde está o tempo." |
| Erro só em produção | "Leia `docker compose logs gateway --since 30m` e encontre o request_id do erro." |
| Agente "esqueceu" algo do plano | "Releia a seção 2 e 2.1 do PLANO_CURSOR.md e confira o que fez contra elas." |
| Mudança deu errado | `git diff` para ver; `git checkout -- <arquivo>` ou `git reset --hard HEAD` para voltar ao último commit (peça confirmação antes). |

---

## Checklist rápido por fase

- [ ] Chat novo + prompt "Iniciar uma fase"
- [ ] Li o plano que o agente propôs e respondi as dúvidas
- [ ] Fiz minha parte nos checkpoints 🛑
- [ ] Validações e testes verdes (inclusive invariantes da fase)
- [ ] `PROGRESS.md` atualizado com números
- [ ] Revisei o diff
- [ ] Commit `fase N: ...` feito
