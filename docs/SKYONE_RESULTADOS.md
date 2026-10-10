# SKYONE_RESULTADOS.md — Resultados dos testes no Skyone (Fase 0.3)

Resultados obtidos pelo Marco diretamente no Skyone Studio em 08/10/2026. Complementa o `docs/SKYONE.md` (instruções do probe, criado na VPS). Ao final da Fase 0.3, o agente do Cursor consolida os dois arquivos.

## Fluxo-modelo validado (08/10/2026)

Integração **POC - Luxbrain Omnichannel** → fluxo **gateway** → URL `https://luxtia.api.integrasky.cloud/k3iynwDoOU`

```
Webhook → JavaScript "Normalizar Entrada" → AI Agent Call → Retorno
```

| Bloco | Configuração |
|---|---|
| Webhook | Application/JSON; "Utilizar os dados da requisição" ligado; limite de requisições por minuto **ajustável (padrão 10 — aumentar)**; opção "Solicitar autenticação" disponível (ainda não testada) |
| Normalizar Entrada | Parâmetro `payload` = `body` do Webhook (tipo Objeto). Monta `prompt` = instruções do agente + mensagem; devolve `conversation_id`, `session_id`, `request_id`, `prompt`, `tenant_id`, `agent_id` |
| AI Agent Call | Campos disponíveis: **ID do Agente**, **Chave de contexto** (= `session_id`), **Prompt**, **Anexos**. Não há escolha de base de conhecimento, Skills ou modelo por chamada |
| Retorno | 200, `Content-Type: application/json; charset=utf-8`; corpo: `resposta` (caminho `text.body`, Texto), `request_id`, `conversation_id`, `status`, `tipo`, `acao`="nenhuma"; JSONata `$` |

Corpo enviado pelo gateway:
```json
{"conversation_id":"...","session_id":"...","request_id":"...","message":"...","instrucoes_agente":"..."}
```
Resposta recebida:
```json
{"resposta":"...","request_id":"...","conversation_id":"...","status":"success","tipo":"text","acao":"nenhuma"}
```

## Descobertas

| # | Pergunta | Resultado |
|---|---|---|
| 1 | Instruções pelo payload funcionam? | **Sim.** O mesmo agente respondeu em inglês ou em português com ≤10 palavras conforme `instrucoes_agente` |
| 1a | Base de conhecimento / Skills / modelo por chamada? | **Não.** Vêm da configuração do agente no Skyone. Personalização por chamada só via texto do prompt |
| 2 | URL do webhook | **Por fluxo.** Fluxo criado do zero ganha URL própria. Duplicar fluxo (ou recriar o Webhook na cópia) mantém a URL do original → **nunca duplicar**; criar do zero |
| 3 | Memória | AI Agent Call recebe "Chave de contexto" (`conversationSessionId`) → o Skyone guarda memória por sessão. **Teste 1b pendente** |
| 4 | Formato de saída do AI Agent Call | `{status, type: "text", text: {body}}` |
| 5 | Latência (agente "assistente de advocacia") | Ver abaixo |
| 6 | Limite de tempo do webhook síncrono | **Pendente** (Teste 2) |
| 7 | Chamadas simultâneas | **Pendente** (Teste 4) |
| 8 | Callback via REST | **Pendente** (Teste 3) |

### Latência medida (mesma mensagem, mesma sessão `s-t2`)

| Execução | Postman | Logger total | AI Agent Call | Resto do fluxo |
|---|---|---|---|---|
| 1 | 9,74 s | 9,12 s | 9 s | ~30 ms |
| 2 | 14,90 s | 14,31 s | 14 s | ~100 ms |
| 3 | 10,91 s | 10,31 s | 10 s | ~50 ms |

- **~99% do tempo é o AI Agent Call.** Webhook, JavaScript e Retorno somam dezenas de ms; rede + gateway do Skyone ≈ 0,6 s.
- Resposta de 10 palavras levando 9–14 s indica custo no agente (modelo, prompt de sistema, base de conhecimento consultada a cada chamada, Skills, memória da sessão), não no fluxo.
- Com debounce de 4 s no gateway, o total ficaria em 13–18 s: **acima da meta p95 ≤ 15 s** para agentes de atendimento.

## Decisões provisórias

- Estratégia: **`por_modelo` é viável** (instruções no prompt), mas base de conhecimento e Skills são por agente do Skyone → na prática, um agente Skyone por cliente quando houver base/Skills próprias; o fluxo pode ser reaproveitado criando-se do zero com a mesma estrutura.
- O fluxo-modelo acima é o padrão para todo agente novo.
- Latência precisa ser reduzida no agente antes do go-live de atendimento (ver diagnóstico pendente).

### Causa provável da latência (análise da configuração do agente, 08/10/2026)

| Item | Valor encontrado | Efeito |
|---|---|---|
| Prompt do sistema | ~50 mil caracteres (≈ 13–15 mil tokens): a base inteira do escritório (54 processos, prazos, audiências) | Enviado em **toda** chamada, até num "oi" → lento e caro |
| Modelo | GPT-5 mini / GPT OSS 20b | Ambos modelos de raciocínio: "pensam" antes de responder, somando segundos |
| max_tokens | 8192 | Alto para chat |
| temperature | 0,8 | Alta para respostas factuais |
| frequency_penalty | 1,1 | Muito alta: penaliza repetir palavras/números (pode distorcer nº de processo e nomes) |
| Limite de mensagens de contexto | 15 | Ok |

Regra derivada: **dados não vão no prompt do sistema.** Prompt do sistema = persona + regras (1–3 mil caracteres). Dados vêm por Skill (consulta sob demanda) ou pelo contexto enviado pelo gateway.


## Pendentes (roteiro para o Marco, no fluxo "gateway")

> Item 1 (memória) concluído — ver resultado abaixo.

URL: `https://luxtia.api.integrasky.cloud/k3iynwDoOU`. Usar sessões novas em cada teste.

**1. Memória (bloqueia Fase 0.5)** — enviar em sequência:
```json
{"conversation_id":"m1","session_id":"s-mem","message":"Meu nome é Marco e meu processo é o 1234."}
{"conversation_id":"m1","session_id":"s-mem","message":"Qual é meu nome e o número do meu processo?"}
{"conversation_id":"m1","session_id":"s-outra","message":"Qual é meu nome e o número do meu processo?"}
```
Esperado: lembra na 2ª, não lembra na 3ª.

**Resultado (08/10/2026):** **não lembrou** na 2ª (mesma `session_id`) nem na 3ª. Tempos: 17,1 s / 12,1 s / 7,4 s. Ressalva: o agente de Advocacia tem regras no prompt que podem levá-lo a recusar dados fora do cadastro; reconfirmar com o agente "Teste Rápido" e uma pergunta neutra ("Minha cor favorita é azul" → "Qual é minha cor favorita?").

**Decisão:** o **gateway é o dono do histórico** (tabela `mensagens` no Postgres do gateway), independentemente do resultado da reconfirmação:
- funciona igual para qualquer provedor de agente (adaptador);
- o painel mostra a conversa completa; transbordo humano e auditoria usam a mesma fonte;
- retenção e exclusão (LGPD) ficam sob controle do gateway;
- o gateway decide quanto histórico enviar (últimos N turnos + resumo), controlando tempo e custo.

Consequências no contrato:
- o gateway envia `historico` (últimos N turnos, com data/hora) no corpo;
- o JavaScript "Normalizar Entrada" monta o prompt com instruções + histórico + mensagem atual;
- `session_id` enviado = `request_id` (sessão nova a cada turno), para que uma eventual memória do Skyone não duplique o histórico;
- no agente do Skyone, "Limite de mensagens de contexto" pode ficar baixo (ex.: 1–2), já que o contexto vem do gateway.

**2. Autenticação do Webhook (bloqueia Fase 0.5)** — ligar "Solicitar autenticação" no Webhook e anotar o tipo (chave em cabeçalho? qual nome? token Bearer?) e como gerar a credencial. Anotar também o **valor máximo** aceito no limite de requisições por minuto (padrão 10 — aumentar). Resultado: _pendente_

**3. Agente "Teste Rápido"** — prompt de sistema de 4 linhas, sem base e sem tools, modelo GPT OSS 20b (embarcado).

**Resultado (08/10/2026):**
- **Histórico enviado pelo gateway funciona:** com `historico` no corpo, a resposta foi "Seu nome é Marco e o número do seu processo é 1234." (3/3).
- **Latência continuou alta: 14,9 s / 10,3 s / 11,9 s** mesmo com prompt mínimo. Antes, com o agente de Advocacia (prompt de ~15 mil tokens, GPT-5 mini): 9–14 s; com histórico, um pico de 64 s.
- **Conclusão corrigida:** o prompt grande piora e causa picos, mas **não é a causa principal**. Há um piso de ~10 s no AI Agent Call para os modelos testados (GPT-5 mini e GPT OSS 20b, ambos de raciocínio). Próximo passo: testar outros modelos da lista do Skyone, de preferência sem raciocínio, e verificar no Logger/Monitoramento de IA onde o tempo é gasto.

**Comparação de modelos (mesmo agente "Teste Rápido", mesmo corpo com `historico`, 08/10/2026):**

| Modelo | Raciocínio? | Tempos (3 chamadas) | Resposta correta? |
|---|---|---|---|
| GPT OSS 20b (embarcado) | sim | 14,9 / 10,3 / 11,9 s | sim |
| GPT-5 | sim | 7,9 / 11,6 / 9,8 s | sim |
| GPT-5 mini (chave própria) | sim | 7,7 / 8,8 / 8,6 s | sim |
| **GPT-4.1 mini** | **não** | **8,3 / 8,6 / 5,7 s** | sim |

- Trocar para um modelo **sem raciocínio** melhorou pouco (média ~7,5 s). A mesma chamada direto na API do provedor costuma levar 1–2 s, o que indica **custo fixo do próprio Skyone de ~4–6 s** por AI Agent Call (orquestração do agente, montagem de contexto, eventual fila). **Confirmado no Logger (08/10/2026): todo o atraso está no AI Agent Call** em todas as execuções; Webhook, JavaScript e Retorno levam milissegundos. Falta separar quanto é o modelo e quanto é a orquestração do agente no Skyone: chamar o mesmo modelo direto na API da OpenAI (Postman) com o mesmo prompt e comparar.
- Pendentes deste item: testar um modelo de outro provedor (Claude Haiku 4.5 ou Gemini Flash) para confirmar se o piso é da plataforma; teste de qualidade com a pergunta de recepção do escritório.
- **Decisão provisória:** modelo padrão para atendimento no Skyone = GPT-4.1 mini. Se o piso de ≥5 s se confirmar, avaliar na Fase 0.5/0.6 um **adaptador direto ao provedor** no gateway (sem Skyone) só para agentes de atendimento sensíveis a tempo; agentes que usam Skills/base/integrações do Skyone continuam nele.
- Impacto no gateway: até haver um modelo mais rápido, contar com 6–15 s por resposta → indicador "digitando" imediato, aviso de demora, `SKYONE_TIMEOUT_S` ≥ 90 s e debounce menor quando a mensagem estiver completa.

**4. Simultâneas (antes da Fase 0.6)** — 10 chamadas paralelas; anotar se os tempos ficam parecidos ou crescem em escada. Resultado: _pendente_

**5. Tempo limite e callback (antes da Fase 0.6/6.5)** — Delay de 60/120/300 s entre AI Agent Call e Retorno; anotar status e tempo. Módulo REST antes do Retorno chamando um webhook.site. Resultado: _pendente_

## Recomendação para o agente de Advocacia (fora do escopo do gateway)

- Prompt do sistema só com persona e regras; dados do escritório via Skills (`consultar_processo`, `listar_prazos`, `buscar_cliente`, `listar_audiencias`), com a regra de segredo de justiça aplicada dentro da Skill.
- max_tokens 800–1000, temperature 0,2, frequency_penalty 0; preferir modelo sem raciocínio para atendimento.

## Probe executado na VPS — 08/10/2026

- Escopo autorizado: dois testes de instrução e três medições de latência, sequenciais, no fluxo de teste informado pelo Marco; sem alterar o fluxo/agente no Skyone.
- Relatórios finalizados em 2026-10-08T17:25:55.689096+00:00 (idiomas) e 2026-10-08T17:26:27.771992+00:00 (latência), UTC.
- `SKYONE_PROBE_URL` configurada no `.env`, mantido com permissão 600 e ignorado pelo Git.
- **Autenticação ausente nas cinco chamadas**, explicitamente registrada pelo probe. Corpo contém somente `message`, `session_id`, `conversation_id`, `request_id`, `instrucoes_agente`; nenhuma credencial no corpo ou cabeçalho.
- `SKYONE_PROBE_TOKEN` vazio é permitido somente na ferramenta de diagnóstico. Não há fallback para `SKYONE_TOKEN`/URL de produção; token preenchido interrompe a execução até o formato de autenticação ser confirmado pelo Marco, sem presumir Bearer ou nome de cabeçalho.
- Mesma mensagem sintética em todos os casos: "Diga uma frase curta explicando como você pode ajudar." Instrução: responder no idioma solicitado em até 10 palavras (português nas três medições).
- Cada chamada recebeu `request_id`, `conversation_id` e `session_id` próprios. Sessões novas evitam carregar contexto entre medições; isso **não valida isolamento de memória nem define a estratégia de sessão**.

| Caso | HTTP | Duração | Resposta | request_id |
|---|---|---|---|---|
| ingles | 200 | 14,106 s | I provide case updates, deadline tracking, and schedule coordination. | `038d6fa9-d8d6-4809-afec-09b89f99263a` |
| portugues | 200 | 9,703 s | Posso fornecer informações sobre processos e prazos do seu caso. | `e6e48247-b3d4-45d4-b801-8f4daced4ff7` |
| latencia_1 | 200 | 6,844 s | Posso fornecer prazos, audiências e movimentações dos processos. | `684b3bf0-d32d-4502-9695-ef96ba4c6740` |
| latencia_2 | 200 | 15,110 s | Posso organizar prazos, audiências e processos do seu escritório. | `38114563-3555-4622-becf-7954d2127d36` |
| latencia_3 | 200 | 7,991 s | Auxilio na gestão de prazos e audiências processuais. | `721bd49f-59c8-4936-a4e6-dd4ab05e2b77` |

Três medições de latência: **média 9,982 s**, **mediana 7,991 s**, mínimo 6,844 s e máximo 15,110 s.

- **5/5 HTTP 200 e contrato válido**: texto não vazio, `request_id` e `conversation_id` correspondentes aos enviados, `status=success`, `tipo=text`, `acao=nenhuma`.
- Inspeção das respostas: inglês e português corretos; 9 e 10 palavras, respectivamente. As três respostas de latência também respeitaram até 10 palavras.
- Duração medida do início da validação DNS até a leitura completa da resposta, incluindo rede e processamento no Skyone. Não inclui debounce, fila do gateway nem envio ao WhatsApp. Três amostras não permitem concluir p95 de produção ou causa da latência; não houve acesso ao Logger nesta execução.
- Relatórios sanitizados locais (ignorados pelo Git, permissão 600): `.local/skyone-probe-idiomas-2026-10-08.json` e `.local/skyone-probe-latencia-2026-10-08.json`.

Comandos executados (cada caso uma vez):

```bash
docker compose run --rm --no-deps -v /home/twadmin/gateway:/workspace:ro gateway python /workspace/scripts/skyone_probe.py --caso idiomas
docker compose run --rm --no-deps -v /home/twadmin/gateway:/workspace:ro gateway python /workspace/scripts/skyone_probe.py --caso latencia
```

`--caso tempo --espera-s 60|120|300` permanece disponível para diagnóstico futuro; o Delay deverá ser configurado manualmente no fluxo pelo Marco. O probe não envia `probe_delay_s` fora do contrato validado.

**Ponto de parada:** aguardando os testes do Marco de memória, autenticação e limite de requisições. Estratégias de sessão e autenticação não consolidadas; Fase 0.5 não iniciada. Simultâneas, Delay/callback e agente "Teste Rápido" não foram executados nesta rodada. Atendimento mantido em `mock/dry`.


## Autenticação Basic e limite — resultado informado pelo Marco em 09/10/2026

Fluxo “gateway”: `https://luxtia.api.integrasky.cloud/k3iynwDoOU`.

- Autenticação do Webhook: **Basic Auth**. Usuário fixo gerado pelo Skyone, formato `flow-<id-do-fluxo>@luxtia`; senha definida pelo Marco na tela do Webhook.
- Sem credencial: **401**, corpo `{"message":"Unauthorized"}`, duração **1,88 s**. Senha errada: **401**.
- Credencial correta: **200**, contrato completo (`resposta`, `request_id`, `conversation_id`, `status: success`, `tipo: text`, `acao: nenhuma`); histórico enviado respeitado.
- Limite **configurado** no Webhook: **300 requisições/minuto**. Esse resultado informa o valor adotado, não uma medição do máximo comercial/técnico do provedor.
- Estas evidências foram fornecidas pelo Marco; o Codex não recebeu a senha nem repetiu as chamadas reais. Substituem a pendência de descobrir o formato de autenticação dos registros anteriores.

Gateway: tipo `basic`, usuário/senha cifrados separadamente com Fernet; cabeçalho Basic montado somente no envio. Tipos Bearer e cabeçalho customizado preservados. Limite local padrão **240/minuto por URL**, compartilhado entre agentes/tenants, com espera incluída em `timeout_s`. HTTP 401/403 gera “credencial do Skyone recusada”, alerta persistente e contagem do circuito, sem retry. Detalhes operacionais e PATCH em `API_ADMIN.md`.

Próxima validação real prevista naquele registro (concluída abaixo): Marco cadastra as credenciais pelo túnel com `PATCH` e executa `/testar`. Para chamar o Skyone nesse teste, configurar `provedor=skyone`, `agente_mode=live`, mantendo `send_mode=dry`. A implantação pelo Codex mantém todos os agentes mock/dry; nenhuma senha real é cadastrada ou lida. O probe antigo não foi convertido para Basic nesta tarefa; usar `/testar` para essa validação.


## Validação real pelo gateway concluída — 09/10/2026

**Autenticação e limite concluídos para a Fase 0.5.** Teste executado e resultado informado pelo Marco, usando `POST /admin/v1/tenants/postman_teste/agentes/ag_dba38a6fae985abf584d512f/testar`:

- Agente com `provedor=skyone`, `agente_mode=live`, `send_mode=dry`; Basic Auth cadastrado pelo próprio Marco via túnel.
- **HTTP 200**, `ok: true`, **8.194 ms (8,194 s)**, resposta em português do fluxo `k3iynwDoOU`. É uma amostra individual, não p50/p95.
- Marco confirmou que a senha não apareceu na resposta do PATCH.
- Testes diretos no Skyone: sem credencial → 401; senha errada → 401; credencial correta → 200.
- Limites adotados: **300/min no Webhook**, **240/min por URL compartilhada no gateway**, este último também conferido no ambiente em execução. Isso confirma a configuração, não um ensaio de saturação ou o máximo comercial do Skyone.

Verificação independente na VPS: auditoria registra um `agente.testado` para esse tenant/agente, em **2026-10-09T13:48:08.992228+00:00**. A latência e a resposta acima são as fornecidas pelo Marco; nenhuma chamada real foi repetida nesta conferência.

Verificação de logs: examinados stdout/stderr disponíveis do container atual, iniciado em **2026-10-09T13:32:46.777594627Z**. Usuário e senha foram decifrados somente em memória dentro de um processo de verificação, que montou o valor Basic para comparação sem exibir nem gravar credenciais ou logs brutos. **Zero ocorrências** da senha literal, representações JSON/URL, token Base64, valor completo Basic e nome `Authorization`. O resultado cobre os logs disponíveis desse container, não arquivos históricos já removidos. Relatório local sanitizado: `.local/verificacao-auth-real.json`.

Agente mantido intacto em **live/dry** para os próximos testes. Consulta da configuração efetiva confirmou **zero outros agentes live** e **zero agentes com envio live**. Não houve PATCH, alteração de credencial/configuração, reinício ou envio ao WhatsApp nesta verificação.

Para fechar formalmente a 0.5 faltam somente a revisão de `docs/API_ADMIN.md` pelo Marco e a confirmação dos modelos iniciais `atendente_delivery` e `assistente_generico`. Simultâneas e timeout/callback continuam antes da 0.6; não são pendências de autenticação da 0.5.


## Teste de carga direto no fluxo — 09/10/2026

Executado pelo Marco na VPS com `.local/teste_skyone_carga.py` (biblioteca padrão do Python), direto no Webhook do fluxo **`k3iynwDoOU`** com **Basic Auth** (usuário `flow-k3iynwDoOU@luxtia`; senha digitada sem eco, não gravada). Mesmo pacote do gateway: `message`, `historico` com 2 turnos, `instrucoes_agente`, `session_id = request_id`. Resposta considerada correta quando HTTP 200, `request_id` devolvido igual e a resposta cita o processo `1234` (memória pelo `historico`). Resultado salvo sem senha em `.local/resultado_carga_20261009_181055.json` (600, fora do Git), conferido nesta entrada.

| Teste | Corretas | Tempos | Observação |
|---|---|---|---|
| 3 chamadas em sequência | 3/3, HTTP 200 | 7,27 s · 5,10 s · 8,96 s (mediana 7,27 s) | Referência individual |
| 10 chamadas simultâneas | **10/10**, todas HTTP 200 | 6,95–7,87 s (mediana 7,53 s) | **Lote inteiro em 7,89 s** |
| OpenAI direta | — | — | Não executada (chave não informada); comparação da 2.5 continua pendente |

**Conclusão:** o Skyone processou as 10 chamadas **em paralelo, sem fila**: o lote levou o mesmo que uma chamada isolada, e nenhuma resposta veio fora do contrato ou com 429/5xx. Para o gateway, a concorrência por agente pode ser dimensionada pela cota (240/min por URL) e pelo `timeout_s`, não por uma fila no provedor. Amostra única de 10, abaixo do limite configurado (300/min); não é ensaio de saturação nem mede o máximo do Skyone.

Pendente antes da 0.6: comportamento com tempo limite/callback (concluído abaixo para o tempo limite).

## Teste de tempo limite — 09/10/2026

Executado pelo Marco no fluxo **`k3iynwDoOU`** (Basic Auth), com espera artificial no JavaScript “Normalizar Entrada” controlada pelo campo `segundos_teste`, **já removido do fluxo** depois do teste. Resultado `.local/resultado_tempo_20261009_191510.json` (600, fora do Git), conferido nesta entrada.

| Espera pedida | Resultado | Duração |
|---|---|---|
| 0 s | HTTP 200, resposta correta | 7,0 s |
| 30 s | HTTP 200, resposta correta | 36,9 s |
| 60 s | HTTP 200, resposta correta | 66,2 s |
| 120 s | **nenhuma resposta e nenhum código HTTP** até o cliente desistir | 210,9 s |

**Conclusão:** modo síncrono comprovado até ~66 s. Existe um teto entre ~66 s e ~127 s em que o Skyone **segura a conexão sem responder** (pode ser limite do módulo JavaScript; ainda não confirmado no Logger). Consequências adotadas:

- `timeout_s` padrão do agente passa a **60 s** (migração 013; agentes no valor antigo de 90 s ajustados para 60 s). O máximo aceito pela API continua 600 s.
- **Atendimento usa 60 s.** Tarefas que precisem de mais tempo só no **modo assíncrono** (callback, Fase 0.6). Com 60 s, o gateway desiste antes do teto em que o Skyone fica mudo e entrega o fallback.
- O callback será implementado e testado na 0.6 com o provedor simulado; a validação com um **callback real do Skyone** fica para a Fase 6.5.

## Comparação de modelos no agente "Atendimento By Koji" (Fase 2)

Fluxo `atendente_delivery` (mesma URL), agente sem base de conhecimento e sem Skills, mesmo prompt; só o modelo muda entre as rodadas. Medido pelo gateway em produção, com IA real e **envio de teste** (nada sai ao WhatsApp).

- **Bench:** 5 clientes simulados ao mesmo tempo, cada um enviando "oi" e "vocês entregam no Morumbi?" (um turno por cliente; debounce de 4 s incluído no total). `agente` = tempo da chamada ao Skyone; `total` = da última mensagem do cliente até o fim do envio.
- **Simular:** conversas novas a cada rodada (sem histórico): "oi, vocês entregam no Morumbi?" → "obrigado" na mesma conversa; e, em outra conversa, "quero falar com uma pessoa".
- **Fora do formato JSON:** o gateway não enxerga se o "Interpretar Resposta" precisou tratar a saída do modelo como texto puro, porque o Retorno chega igual nos dois casos. A coluna conta só **indícios visíveis** no texto entregue (chaves, aspas de JSON ou cercas de código). Para contar com exatidão é preciso um marcador no Retorno.

### Bench (5 clientes)

| Rodada (UTC) | Modelo | Turnos | Erros | agente_p50 | agente_p95 | total_p50 | total_p95 | Aviso de demora |
|---|---|---|---|---|---|---|---|---|
| 10/10 04:25 | não informado (configuração anterior ao teste A/B) | 5 | 0 | 16,2 s | 24,2 s | 21,1 s | 29,0 s | 3 de 5 |
| 10/10 14:18 | **não informado na mensagem — a preencher** | 5 | 0 | 8,8 s | 10,6 s | 14,4 s | 15,8 s | 0 de 5 |

Tempos do agente por turno: 04:25 → 16,2 · 22,9 · 24,6 · 10,1 · 10,1 s; 14:18 → 9,4 · 6,8 · 10,9 · 8,8 · 7,8 s.

### Conversas do Simular

| Rodada (UTC) | Modelo | Mensagem | Agente | Total | Texto exato (um balão por linha) | Idioma | `acao` / motivo |
|---|---|---|---|---|---|---|---|
| 10/10 04:30 | não informado (anterior) | oi, vocês entregam no Morumbi? | 28,9 s | 33,0 s | "Boa noite, Jussara!"<br>"Sim, entregamos no Morumbi. Você pode me dizer o que gostaria de pedir, a quantidade e o endereço completo?" | português | `nenhuma` / — |
| 10/10 04:30 | não informado (anterior) | obrigado | 15,2 s | 15,4 s | (nada; saiu só o aviso de demora) | — | `silencio` / `agradecimento_final` |
| 10/10 05:00 | não informado (anterior, "Interpretar Resposta" v2) | quero falar com uma pessoa | 20,4 s | 24,8 s | "Boa noite! A equipe vai continuar o atendimento em instantes." | português | `transferir_humano` / `pediu_humano` |
| 10/10 14:20 | **a preencher** | oi, vocês entregam no Morumbi? | 9,0 s | 13,1 s | "Bom dia! Entregamos sim, inclusive temos uma unidade no Morumbi."<br>"Como posso te ajudar com o seu pedido hoje? 🍣" | português | `nenhuma` / — |
| 10/10 14:20 | **a preencher** | obrigado | 9,9 s | 14,0 s | (nada enviado) | — | `silencio` / `agradecimento_final` |
| 10/10 14:21 | **a preencher** | quero falar com uma pessoa | 7,6 s | 11,8 s | "Bom dia! Vou te transferir para um de nossos atendentes agora mesmo."<br>"Um momento, por favor, a equipe já vai continuar o atendimento com você em instantes." | português | `transferir_humano` / `pediu_humano` (conversa pausada e aguardando atendente) |

Respostas do bench das 14:18 (todas em português, dois balões cada):

1. "Bom dia! Entregamos no Morumbi sim, inclusive temos uma unidade lá. 🍣" / "Para começar, você gostaria de fazer um pedido para entrega ou retirada?"
2. "Bom dia! Tudo bem?" / "Entregamos no Morumbi sim, inclusive temos uma unidade lá. Como posso te ajudar com seu pedido hoje? 🍣"
3. "Bom dia! Sim, entregamos no Morumbi e temos uma unidade por lá." / "Você gostaria de fazer um pedido para entrega ou para retirada? 🍣"
4. "Bom dia! Entregamos sim, temos inclusive uma unidade no Morumbi. 🍣" / "Poderia me passar o seu endereço completo para eu confirmar a disponibilidade para a sua localização?"
5. "Bom dia! Entregamos sim, temos inclusive uma unidade no Morumbi para te atender 🍣" / "Você gostaria de fazer um pedido para entrega ou retirada?"

### Fora do formato JSON (indícios visíveis)

| Rodada (UTC) | Modelo | Respostas avaliadas | Com indício de texto fora do formato |
|---|---|---|---|
| 10/10 04:25–05:00 | não informado (anterior) | 8 | 0 |
| 10/10 14:18–14:21 | **a preencher** | 8 | 0 |

Observações da rodada das 14:18: nenhuma resposta passou de 15 s no agente (sem aviso de demora); `total_p95` ficou em 15,8 s, ainda 0,8 s acima da meta de 15 s (o debounce de 4 s está dentro desse total); o modelo desta rodada afirma que há "uma unidade no Morumbi" e usa emoji, o que a rodada anterior não fazia.
