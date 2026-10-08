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

URL: `https://luxtia.api.integrasky.cloud/k3iynwDoOU`. Usar sessões novas em cada teste.

**1. Memória (bloqueia Fase 0.5)** — enviar em sequência:
```json
{"conversation_id":"m1","session_id":"s-mem","message":"Meu nome é Marco e meu processo é o 1234."}
{"conversation_id":"m1","session_id":"s-mem","message":"Qual é meu nome e o número do meu processo?"}
{"conversation_id":"m1","session_id":"s-outra","message":"Qual é meu nome e o número do meu processo?"}
```
Esperado: lembra na 2ª, não lembra na 3ª. Resultado: _pendente_

**2. Autenticação do Webhook (bloqueia Fase 0.5)** — ligar "Solicitar autenticação" no Webhook e anotar o tipo (chave em cabeçalho? qual nome? token Bearer?) e como gerar a credencial. Anotar também o **valor máximo** aceito no limite de requisições por minuto (padrão 10 — aumentar). Resultado: _pendente_

**3. Agente "Teste Rápido"** — mesmo modelo, prompt de sistema curto (persona + 5 regras), sem base e sem Skills, max_tokens 1000, temperature 0,2, frequency_penalty 0. Trocar o ID do Agente no fluxo e medir 3 vezes. Resultado: _pendente_

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
