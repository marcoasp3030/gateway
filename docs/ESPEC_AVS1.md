# AVS1 — Aviso de espera: "digitando…" contínuo e texto só no meio de algo

**Situação:** decidido pelo Marco em 13/10/2026 (D1 a D4 de `docs/ANALISE_COMPORTAMENTO_1.md`) e implementado.
**Banco:** migração 042, aditiva (`aviso_espera_modo` e `aviso_espera_textos` em `tenant_config` e `agentes`).

## 1. O problema

Quando o cliente voltava depois de um tempo e mandava "oi", recebia "Só um instante, estou verificando.". A regra antiga só olhava o relógio: 20 s sem resposta do agente e o bot calado há mais de 2 minutos. Quem volta depois de uma pausa sempre cai nesse caso. Saiu 4 vezes em 13/10, 3 delas em saudação.

## 2. Decisões

| # | Decisão |
|---|---|
| D1 | Padrão: só "digitando…", sem texto. O modo com texto fica disponível por agente, desligado |
| D2 | No modo com texto: 30 s |
| D3 | Textos: "Já te respondo 😊", "Só mais um instantinho", "Um momento, por favor" |
| D4 | "Digitando…" o turno inteiro, renovado a cada 20 s. O Marco confirma no celular que aparece |

## 3. Como ficou

### 3.1 Modos (`aviso_espera_modo`, por agente, com herança)

| Modo | O que o cliente vê enquanto o agente processa |
|---|---|
| `presenca` (**padrão**) | Só "digitando…" |
| `presenca_e_texto_contextual` | "Digitando…" e, depois de `slow_notice_s`, um texto curto, **só** nas condições da seção 3.3 |
| `off` | Nada |

### 3.2 "Digitando…" contínuo

- Começa quando a consulta ao agente começa e é **renovado a cada 20 s** (cada pedido vale 25 s) até a resposta ficar pronta. Antes era um pedido só, de 25 s.
- Para sozinho no limite de tempo do agente (`timeout_s` + 5 s).
- O envio da resposta encerra o "digitando" no WhatsApp; quando nada vai ser enviado (silêncio, transferência sem texto), o gateway o retira.
- Só com envio real em canal uazapi. Falha ao pedir o "digitando" nunca atrasa, derruba ou gera mensagem no lugar; se o canal recusar, o gateway para de pedir naquele turno.

### 3.3 Texto de espera (só no modo `presenca_e_texto_contextual`)

Sai **um** texto, como parte 0 do turno, quando **todas** valem:

1. o agente não respondeu em `slow_notice_s` (padrão da plataforma agora **30 s**; era 20);
2. turno de cliente, e não é turno refeito;
3. o lote não é saudação ("oi", "olá", "bom dia", "opa, tudo bem?", só emoji) nem agradecimento ("obrigado", "ok", 👍);
4. o bot falou nesta conversa nos últimos **10 minutos** (senão é conversa nova ou retomada: sem texto);
5. o bot **não** falou nos últimos `aviso_demora_janela_s` (120 s; regra que já existia, para não interromper troca rápida);
6. há algo em andamento: a última fala do bot foi pergunta ou trouxe opções, **ou** há carrinho aberto com itens, **ou** alguma Skill já foi chamada neste turno;
7. não saiu outro texto de espera nesta conversa nos últimos **5 minutos**.

Na dúvida ou em qualquer erro ao avaliar, não manda (fica só o "digitando").

**Textos** (`aviso_espera_textos`): lista de 1 a 6 textos, de 2 a 160 caracteres. Padrão: os três da decisão D3. O gateway sorteia um, sem repetir o último usado na conversa.

### 3.4 Configuração

| Campo | Tipo | Padrão | Observação |
|---|---|---|---|
| `aviso_espera_modo` | `off` / `presenca` / `presenca_e_texto_contextual` | `presenca` | Novo. Grupo `mensagens` |
| `aviso_espera_textos` | lista de textos | os 3 aprovados | Novo. Grupo `mensagens`, tipo `lista_textos` |
| `slow_notice_s` | 0 a 300 | **30** | Já existia; só vale no modo com texto. 0 = sem texto |
| `aviso_demora_janela_s` | 0 a 3600 | 120 | Já existia |
| `aviso_demora` | texto | — | **Não é mais usado.** Continua aceito para não quebrar quem envia; o texto vem de `aviso_espera_textos` |

Os dois campos novos entram onde os demais comportamentos já entram: `PATCH /admin/v1/tenants/{t}/agentes/{id}` e `PUT …/config`, no rascunho e na publicação do atendimento, em `configuracao_efetiva` (com a origem) e no esquema de `GET …/atendimento`. `null` volta a herdar.

### 3.5 Métrica

`avisos_de_espera_enviados` em `GET /admin/v1/tenants/{t}/metricas`: turnos em que o texto de espera saiu. No modo padrão fica em zero. O campo `aviso_demora` de cada turno continua dizendo se saiu.

## 4. O que muda para o By Koji

O agente herda o padrão: **só "digitando…"**. O "Só um instante, estou verificando." deixa de sair. Nos turnos de 30 a 47 s, o "digitando" agora cobre a espera inteira.

## 5. Limites

- A exibição do "digitando…" no aparelho não é confirmada pelo gateway: a uazapi aceita o pedido, mas quem mostra é o WhatsApp do cliente. Fica para a conferência do Marco.
- As regras 4 e 5 juntas fazem o texto sair só quando a última fala do bot tem entre 2 e 10 minutos. É de propósito: abaixo disso o cliente acabou de receber resposta; acima, é retomada.
- O conceito de "atendimento novo" entra no ENC1; até lá, "conversa retomada" é o bot calado há mais de 10 minutos.
- No modo sem fila durável não há renovação do "digitando" (produção usa a fila durável).

## 6. Testes

`tests/test_avs1.py`: saudações reconhecidas e o que não é saudação; padrões; "digitando" renovado durante a espera e nenhum texto no modo padrão; um pedido só em resposta rápida; retirada em silêncio; modo `off`; validação e herança da configuração; esquema para o painel; e, no modo com texto: primeira mensagem, logo depois de o bot falar, meio da conversa (sai um, da lista), espaçamento de 5 minutos, rodízio sem repetir, saudação e agradecimento, retomada depois de 10 minutos, resposta rápida, sem pendência, carrinho com itens, Skill no turno, texto próprio, turno refeito e métrica. O teste antigo do aviso (`test_ux3`) foi substituído por estes; dois testes da Fase 3 foram ajustados ao padrão novo.
