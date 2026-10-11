# Especificação — UX2: mensagens picotadas, reações, lista e carrossel

Situação em 10/10/2026: **aprovada pelo Marco** (decisões na seção 7). Blocos **P1 e P2 implementados** (seção 8); os demais aguardam.
Complementa `docs/ESPEC_BOTOES.md` (UX1).

---

## 1. Diagnóstico das mensagens picotadas

Base: turnos de hoje (10/10/2026) do agente "Atendimento By Koji" no canal WhatsApp, só leitura.

### 1.1 Configuração atual (efetiva no agente)

| Parâmetro | Valor | Origem | O que faz |
|---|---|---|---|
| `debounce_s` | 4 s | modelo | Espera depois de mensagem curta ou "picada" |
| `debounce_curto_s` | 1,5 s | plataforma | Espera depois de mensagem que **parece completa** (termina em `?` `.` `!` `…` ou tem mais de `debounce_palavras` palavras) |
| `debounce_palavras` | 6 | plataforma | Limite de palavras da regra acima |
| `max_wait_s` | 12 s | modelo | Teto: da primeira mensagem do lote até chamar o agente |
| `slow_notice_s` | 20 s | plataforma | Aviso "Só um instante" |

A espera recomeça a cada mensagem nova, **só enquanto o agente ainda não foi chamado**. Depois que o turno começa, mensagem nova espera o turno terminar e vira outro turno.

### 1.2 O que os números mostram

| Medida | Valor |
|---|---|
| Turnos respondidos (cliente, WhatsApp) | 25 |
| Com 1 mensagem / com 2 / com 3 ou mais | 24 / 1 / 0 |
| Espera até chamar o agente | média 4,3 s; mediana 4,2 s; máxima 12,3 s |
| Tempo do agente (Skyone) | média 12,2 s; p95 14,9 s |
| Turnos atropelados (chegou mensagem do cliente com o turno em andamento) | **4 de 25**, com 5 mensagens |
| … com o agente ainda pensando | 4 mensagens |
| … com a resposta já saindo | 1 mensagem |

Intervalo entre duas mensagens seguidas do cliente (16 pares no dia, de todos os contatos do canal):

| Intervalo | Pares |
|---|---|
| até 4 s | 2 |
| 4 a 8 s | 1 |
| 8 a 12 s | 2 |
| 12 a 30 s | 3 |
| mais de 30 s | 8 |

Os quatro casos:

| Turno | Lote enviado ao agente | Espera | O que chegou depois |
|---|---|---|---|
| 760 (12:35) | "50 peças de sushi / 1 ponte de yakissoba" | 1,6 s | "Entrega", **2,0 s depois da anterior** — 0,2 s depois de o agente ser chamado |
| 761 | "Entrega" (sozinha) | 12,3 s (presa atrás do turno 760) | endereço aos +5,5 s (agente pensando) e "13h" aos +10,8 s (resposta saindo) |
| 771 (19:20) | "Oi" | 4,0 s | "Me envia o seu cardápio…" aos +10,7 s (agente pensando) |
| 772 | "Me envia o seu cardápio…" | 10,9 s (presa atrás do 771) | "Pátio" aos +12,3 s (agente pensando) |

### 1.3 Causa

São três causas somadas, nesta ordem de peso:

1. **O agente demora mais do que o cliente leva para mandar a próxima mensagem.** O agente leva 9 a 15 s; nesse intervalo o cliente já escreveu de novo. Como a espera só vale antes de o agente ser chamado, a mensagem nova fica de fora, o cliente recebe uma resposta que ignora o que ele acabou de dizer, e depois outra. É o efeito "responde em partes". Um caso puxa o outro (760 → 761; 771 → 772).
2. **A espera curta de 1,5 s dispara cedo.** Mensagem com mais de 6 palavras ou com quebra de linha conta como "completa" e sai em 1,5 s — mas o cliente mandou "Entrega" 2,0 s depois (turno 760).
3. **O cliente escreve mais devagar do que 4 s.** Só 2 de 16 pares vieram em até 4 s; 5 de 16 em até 12 s. Aumentar a espera fixa resolveria poucos casos e deixaria **todas** as respostas mais lentas.

Conclusão: mexer só no debounce não resolve. O ganho maior está em **refazer o turno quando chega mensagem antes de a resposta sair** (item c), com ajuda da espera adaptativa (a) e, se a uazapi entregar, do "digitando" do cliente (b).

---

## 2. Proposta para as picotadas

### 2.1 (a) Espera adaptativa

A última mensagem do lote decide a espera. Três faixas em vez de duas:

| A mensagem… | Espera | Exemplos |
|---|---|---|
| **Parece incompleta**: termina em vírgula, dois-pontos, reticências, hífen, ou em palavra de ligação (`e`, `mas`, `que`, `tipo`, `com`, `sem`, `de`, `do`, `da`, `para`, `pra`, `ou`, `também`, `tb`, `aí`) | `debounce_incompleta_s` (sugestão **7 s**) | "quero um temaki e", "pode ser…", "tipo" |
| **Parece completa**: termina em `?` `.` `!`, ou tem mais de `debounce_palavras` palavras **e não é incompleta** | `debounce_curto_s` (sugestão subir de 1,5 para **2,5 s**) | "Qual o horário do Guedala amanhã?" |
| Demais (curta, mídia, localização, clique em botão) | `debounce_s` (mantém **4 s**) | "Oi", "Entrega", "Pátio" |

- Reticências deixam de contar como "completa" (hoje contam).
- Clique em botão é resposta decidida: usa `debounce_curto_s`.
- O teto `max_wait_s` (12 s, da primeira mensagem do lote) continua absoluto.
- A lista de palavras de ligação fica no código (português); não é configurável nesta fase.

### 2.2 (b) "Digitando" do cliente

**Levantamento:** a documentação da uazapi lista o evento de webhook `presence` ("alterações no status de presença"), mas **não descreve o formato** nem diz se traz "digitando"/"parou". Hoje a instância assina só `messages` e `connection`, então nenhum evento de presença chega e não há payload real para olhar.

**Para fechar este item é preciso um teste real (não feito; precisa de autorização):** acrescentar `presence` aos eventos do webhook da instância (mudança de configuração da instância, pela API, com a credencial já guardada), o piloto digitar sem enviar, e conferir o que fica em `webhook_raw`. Evento desconhecido já é ignorado pelo gateway com segurança (só fica gravado o bruto).

**Proposta, condicionada ao teste:**
- Evento "digitando" de um contato com lote esperando → a espera é empurrada para `agora + digitando_espera_s` (sugestão **5 s**).
- Evento "parou" → volta à espera normal da última mensagem.
- Limites: nunca passa de `max_wait_s`; presença nunca abre turno, nunca reabre conversa, não entra no histórico e não é guardada além do bruto (retenção normal do `webhook_raw`).
- Se a uazapi não mandar o evento (ou mandar sem distinguir quem digita), o item (b) é descartado sem afetar (a) e (c).

### 2.3 (c) Mensagem que chega enquanto o agente processa

Regra: **se a resposta ainda não começou a sair, ela é descartada e o turno é refeito com o lote completo.**

1. Depois que o agente responde e **antes de enviar o primeiro balão**, o gateway confere se há mensagem nova do cliente na conversa (pendente e fora do lote do turno).
2. Se houver, e o turno ainda não foi refeito:
   - a resposta **não é enviada** (fica guardada no turno, marcada como descartada, para auditoria);
   - o turno é concluído como `refeito` (status novo), sem contar como erro;
   - as mensagens do lote voltam a pendentes e um turno novo começa na hora, **sem nova espera**, com o lote antigo + as mensagens novas;
   - o turno novo fica marcado como "já refez" e **não pode ser refeito de novo** (limite de 1). Se chegar mais uma mensagem durante ele, vale a regra de hoje: ele responde e a mensagem nova vira o turno seguinte.
3. Se já saiu algum balão, **segue como hoje** (nada é desfeito).

Cuidados:
- **Efeitos do agente.** Durante o turno o agente pode ter chamado Skills (colocar item no carrinho, fechar pedido). Isso **não é desfeito**: o turno refeito recebe o pacote novo, com o carrinho como ficou, e o agente continua dali. Para o caso grave, uma trava: se a resposta descartável tem `acao` `pedido_fechado`, `transferir_humano` ou `encerrar`, **não refaz** — envia como hoje.
- **Aviso de demora.** Se "Só um instante" já saiu, ainda pode refazer (não é resposta); o aviso não sai de novo no turno refeito.
- **Custo.** Cada refazer é uma chamada a mais ao Skyone. Com os números de hoje seriam 4 chamadas extras em 25 turnos (16%), dentro do limite de vagas por empresa.
- **Tempo.** O cliente espera a segunda chamada (mais 9 a 15 s), mas recebe **uma** resposta coerente em vez de duas desencontradas. O indicador "digitando" continua ligado nesse intervalo.
- **Só na fila durável** (é o que roda em produção). O caminho antigo não muda.
- Idempotência, travas e vagas não mudam: o refazer acontece dentro da mesma trava da conversa.

### 2.4 (d) Configuração e métricas

Configuração com a herança de sempre (plataforma → modelo → empresa → agente):

| Campo | Faixa | Padrão da plataforma | Sugestão para o By Koji |
|---|---|---|---|
| `debounce_incompleta_s` | 1 a 15 | 7 | 7 |
| `debounce_curto_s` (já existe) | 0,5 a 15 | 1,5 (sem mudança) | 2,5 |
| `debounce_s` (já existe) | 1 a 15 | 4 | 4 |
| `max_wait_s` (já existe) | até 30 | 12 | 12 |
| `refazer_turno` | `on` / `off` | `off` | `on` |
| `digitando_espera_s` (só se o item b existir) | 0 a 15; 0 desliga | 0 | 5 |

Métricas (em `GET /metricas`, por empresa e agente):

| Métrica | Conta |
|---|---|
| `turnos_refeitos` | Turnos concluídos como `refeito` |
| `refazer_recusado` | Vezes em que havia mensagem nova mas não refez, por motivo (`ja_refeito`, `acao_com_efeito`, `resposta_ja_saindo`) |
| `mensagens_por_turno` | Média e distribuição (1, 2, 3+) |
| `espera_media_ms` | Média e p95 de `espera_ms` |
| `atropelados` | Turnos em que chegou mensagem do cliente com o turno em andamento (o indicador do problema; deve cair) |

### 2.5 Blocos e testes

| Bloco | Entrega | Migração | Testes principais |
|---|---|---|---|
| **P1 — Espera adaptativa** | Três faixas; reticências como incompleta; clique como completa; `debounce_incompleta_s` | Aditiva (configuração) | Cada terminação da tabela; teto respeitado; mensagem completa depois de incompleta volta à espera curta; mídia e localização como hoje; valores antigos sem mudança para quem não configurou (regressão) |
| **P2 — Refazer o turno** | Regra do item (c); status `refeito`; `refazer_turno`; métricas | Aditiva (status do turno, configuração) | Mensagem durante o agente → uma resposta só, com o lote completo; segunda mensagem durante o turno refeito → não refaz de novo; balão já enviado → não refaz; `pedido_fechado`/`transferir_humano`/`encerrar` → não refaz; resposta descartada nunca chega ao cliente; nenhuma mensagem do cliente perdida nem respondida duas vezes; worker que cai no meio do refazer é retomado sem duplicar; desligado → comportamento de hoje |
| **P3 — "Digitando" do cliente** | Só se o teste real mostrar o evento | — | Fixture real; digitando empurra a espera; parou devolve; teto respeitado; presença sem lote esperando não faz nada |

Ordem: P1 → P2 → (teste de presença) → P3.

---

## 3. Reações

### 3.1 Levantamento (documentação + teste real)

- **Endpoint:** `POST /message/react`, corpo `{"id": "<id da mensagem>", "text": "<emoji>"}`. Texto vazio remove a reação. O campo `number` é legado e ignorado.
- Regras do provedor: só em mensagem recebida de outra pessoa, com até 7 dias; uma reação ativa por mensagem (a nova substitui a anterior).
- **Teste real (10/10/2026, 20:35, piloto):** 👍 na última mensagem do piloto → HTTP 200, `messageType: "ReactionMessage"`, `status: "Pending"`, com `messageid` próprio. O webhook da própria reação chegou com `fromMe: true` e **`wasSentByApi: true`** e foi ignorado; a conversa não foi pausada.
- No gateway hoje: reação recebida (do cliente ou do atendente) entra no histórico como `[reação: 👍]`, não abre turno e **não pausa o bot**. Então, mesmo no pior caso, a reação do bot não seria tratada como atendente assumindo; o risco é só ficar registrada com a origem errada, e a proposta fecha isso.

### 3.2 Contrato

Campo opcional no Retorno do agente:

```json
{"resposta": ["Perfeito, já anotei!"], "acao": "nenhuma", "reacao": "❤️"}
```

| Regra | Detalhe |
|---|---|
| Emojis aceitos | ❤️ 😊 🙏 👍 😄 (qualquer outro valor é descartado, sem erro) |
| Onde reage | Na **última mensagem do cliente** do lote do turno (texto, mídia ou clique) |
| Limite | No máximo **2 reações por conversa por dia** (dia no fuso da empresa), contado no gateway. Acima disso, descartada |
| Ações | Só com `acao` `nenhuma` ou `pedido_fechado`. Com `silencio`, `transferir_humano` ou `encerrar`, descartada |
| Ordem | A reação sai **antes** dos balões (é o gesto de "vi sua mensagem") |
| Falha | Erro no envio da reação **nunca** afeta o turno nem a resposta: registra e segue. Sem nova tentativa |
| Canal | Só uazapi. Em canal sem reação (API, site, e-mail), descartada |
| Envio de teste | Não envia; fica registrada como "teria reagido" |
| Piloto e pausa | Mesma checagem de qualquer envio (`permitido`): fora do piloto ou com a conversa pausada, não reage |
| Turno refeito (UX2-c) | A reação do turno descartado não sai; vale a do turno final |

- **Histórico:** grava uma mensagem `saida`/`bot` do tipo `reacao` com o id devolvido pelo provedor (`mensagens.message_id`) e `meta = {"reacao_a": "<id>", "emoji": "❤️"}`. Com o id gravado, a **varredura reconhece a reação como envio do gateway** e não a registra como do atendente. Se o provedor não devolver o id, vale a mesma proteção dos envios sem id (`envio_sem_id`).
- A reação não vai no `historico` enviado ao agente como fala (hoje reações já ficam fora do turno); o agente não precisa "lembrar" que reagiu.
- **Configuração:** `reacoes` = `on`/`off`, herança de sempre, **padrão `off`**.
- **Métricas:** `reacoes_enviadas` (por emoji), `reacoes_descartadas` por motivo (`emoji_nao_permitido`, `limite_diario`, `acao`, `canal`, `desligado`, `falha_provedor`).

### 3.3 Bloco e testes

**R1 — Reações** (uma migração aditiva de configuração). Testes: emoji fora da lista descartado; terceira reação do dia descartada e a do dia seguinte aceita; `silencio`/`transferir_humano`/`encerrar` não reagem; falha 4xx/5xx/tempo esgotado do provedor não muda status nem resposta do turno; reação gravada com o id e não reconhecida como atendente pela varredura; webhook da própria reação ignorado (fixture real); contato fora do piloto não recebe; desligado não envia; Retorno sem `reacao` igual a hoje (regressão).

---

## 4. Lista (B3) antecipada

Prioridade nova: **logo depois do B2**, para a pergunta "Em qual unidade?" com as 4 unidades.

Levantamento: `/send/menu` com `type: "list"`, `listButton` (texto do botão que abre a lista) e `choices` com `"[Seção]"` e `"título|id|descrição"`. **Formato do clique ainda não observado** — o bloco começa pelo teste real autorizado (uma mensagem ao piloto, um clique), como foi no B1.

Contrato (estende o campo `botoes` do B2, sem quebrar):

```json
"botoes": {"titulo_lista": "Ver unidades",
           "opcoes": [{"id": "morumbi", "titulo": "Morumbi", "descricao": "Aberta até 22h30"},
                      {"id": "patio_guedala", "titulo": "Pátio Guedala", "descricao": "Abre amanhã às 12h"}]}
```

| Regra | Limite (os da Meta, para valer nos dois) |
|---|---|
| Quando vira lista | 4 a 10 opções válidas, ou qualquer quantidade quando alguma opção tem `descricao` |
| `titulo` da opção | até 24 caracteres (para botão continua 20) |
| `descricao` | opcional, até 72 caracteres |
| `titulo_lista` | opcional, até 20; padrão "Ver opções" |
| Seções | Uma só nesta fase |

- **A descrição vem do agente**, que já recebe a situação das unidades no pacote e pela `info_unidades`. O gateway não inventa texto.
- Plano B em texto numerado: `1. Morumbi — Aberta até 22h30`. Mesmos motivos da tabela 3.1 da ESPEC_BOTOES, mais `descricao_longa`.
- Clique: mesma regra do B1 (`ultima_interacao` com `tipo: "lista"`), depois de conferir o payload real.
- Histórico e métricas como no B2 (`forma: "lista"` já está previsto nas métricas).

Testes: fixture real do clique; 4 opções → lista; 3 opções com descrição → lista; 11 → inválido; título de 25 → texto; lista recusada (4xx) → texto; incerto não duplica; desligado → texto.

---

## 5. Carrossel — levantamento

**Existe.** `POST /send/carousel` (ou `/send/menu` com `type: "carousel"`).

| Item | O que a documentação diz |
|---|---|
| Corpo | `number`, `carousel` (lista de cartões) e `text` opcional |
| Cartão | `text` (obrigatório), `buttons` (obrigatório) e uma mídia opcional: `image`, `video` ou `document` (URL) |
| Botão do cartão | `{"id", "text", "type"}` com `type` = `REPLY` (o `id` volta como resposta), `URL` (o `id` é o link), `COPY` (o `id` é o texto copiado) ou `CALL` (o `id` é o telefone) |
| Limites | **Não informados** (cartões por mensagem, botões por cartão). Referência da Meta: até 10 cartões e até 2 botões por cartão |
| WhatsApp Web | **Não informado** para carrossel. Para botões, a mesma documentação avisa que misturar resposta com link/ligar/copiar não aparece no Web; é prudente supor o mesmo aqui |
| Estabilidade | A documentação avisa que botões e listas "podem ser descontinuados sem aviso"; carrossel usa o mesmo tipo de mensagem |

Não foi enviado nenhum teste de carrossel.

**Proposta de uso futuro (sem data):**
- **Apresentação das unidades com foto** e **destaques do cardápio**.
- Sempre **gerado pelo gateway** a partir do cadastro (nome, endereço, foto, situação; item, preço, foto). O agente só pede "mostrar unidades" ou "mostrar destaques"; nunca manda URL de imagem nem monta cartão.
- Cada cartão só com botão de **resposta** ("Pedir aqui", "Quero este"), para não cair no problema do WhatsApp Web.
- Plano B obrigatório: lista (B3) ou texto numerado.

**O que falta antes de virar bloco:**
1. Campo de imagem no cadastro (unidade e item), com URL `https` em domínio permitido (o mesmo `dominios_de_link` do B5) e tamanho conferido.
2. Um teste real para descobrir limites, comportamento no Web e formato do clique.
3. Definir quem escolhe os "destaques" (campo no cadastro do item).

---

## 6. Ordem proposta

1. **B2** — concluído e ligado no By Koji (10/10/2026).
2. **P1 + P2** — espera adaptativa e refazer o turno. **P3** se o teste de presença mostrar o evento.
3. **B3** — lista (começa pelo teste real).
4. **R1** — reações.
5. **B4** — botões da unidade.
6. **Carrossel** — só estudo, depois de existir imagem no cadastro.

G4 (entrega e pagamento) e B5 (botões de ação, junto do G3) seguem como estavam.

---

## 7. Decisões do Marco (10/10/2026)

1. **Presença:** autorizado acrescentar `presence` aos eventos do webhook da instância para capturar o payload real. Durante o teste, só uma amostra fica em `webhook_raw` (os 20 primeiros eventos de presença); o resto é descartado na entrada, sem processar. Se o formato não servir para o P3, a assinatura volta a `messages` e `connection`.
2. **P2 (refazer o turno):**
   - **Não refazer** se, no turno descartável, foi chamado `fechar_pedido` ou `cancelar_pedido` (com sucesso), ou se a resposta tem `acao` `transferir_humano`/`encerrar`. A resposta segue e a mensagem nova vira o próximo turno.
   - Se houve alteração de carrinho (`adicionar_item`, `alterar_item`, `remover_item`, `esvaziar_carrinho`, `definir_entrega`), refazer é permitido, e o pacote do turno refeito leva `acoes_ja_executadas` (resumo das chamadas de Skill do turno descartado) e o carrinho atualizado.
   - No máximo 1 refazer por turno.
3. **P1:** 7 s para incompleta, 2,5 s para completa, 4 s para as demais, teto 12 s — aprovado para o By Koji.
4. **`refazer_turno` e `reacoes`** nascem `off`; ligar só no agente do By Koji depois de validado.
5. **Reação** sai antes dos balões.
6. **Lista:** quando o `id` de uma opção for o código de uma unidade da empresa, o gateway preenche a descrição pela situação real ("Aberta até 22h30", "Fechada · abre amanhã 12h", "Pedidos pausados"); nos demais casos a descrição vem do agente (até 72 caracteres, validada).

**Ordem:** P1 + P2 → (P3 se a presença servir) → B3 lista → reações → B4 → carrossel (estudo).

## 8. Andamento

| Bloco | Situação |
|---|---|
| P1 — Espera adaptativa | Implementado em 10/10/2026: `pipeline.forma_da_mensagem()` e `debounce_da_mensagem()`; configuração `debounce_incompleta_s` (migração 027) |
| P2 — Refazer o turno | Implementado em 10/10/2026: `Pipeline._deve_refazer()`, `_descartar()`, `_refeito_de()`; tabela `skill_chamadas`; `acoes_ja_executadas` no pacote; `refazer_turno` (padrão `off`); bloco `picotadas` em `GET /metricas`. Testes em `tests/test_ux2.py` |
| Amostra de presença | Implementado: evento `presence` é descartado na entrada do webhook, guardando só os 20 primeiros |
| P3, B3, R1, B4 | Aguardam |

### 8.1 Como as chamadas de Skill são ligadas ao turno

- Cada Skill que muda carrinho ou pedido grava uma linha em `skill_chamadas` no momento da chamada: `tenant_id`, `conversation_id`, `skill`, `dados` (resumo), `resultado` (`ok` ou o motivo da recusa) e `criado_em`.
- O turno não é informado pela Skill (o Skyone só manda o `conversation_id`). A ligação é por **conversa + janela do turno**: valem as chamadas da conversa com `criado_em` entre `agente_inicio` e `agente_fim` do turno descartado. Como a conversa tem trava (um turno por vez), essa janela não se mistura com a de outro turno.
- `dados` nunca guarda endereço nem observação: para item, o nome e a quantidade; para `definir_entrega`, só os nomes dos campos informados; para pedido, o número.
- Limpeza junto da retenção da empresa (`retencao_dias`), como o `webhook_raw`.

### 8.2 Contrato de `acoes_ja_executadas` (gateway → agente)

Campo sempre presente no pacote. **Lista vazia** em turno comum; preenchido só no turno refeito.

```json
"acoes_ja_executadas": [
  {"skill": "adicionar_item", "item": "Edamame", "quantidade": 1, "resultado": "ok"},
  {"skill": "definir_entrega", "item": null, "quantidade": null, "resultado": "ok", "detalhe": "modalidade, unidade"},
  {"skill": "fechar_pedido", "item": null, "quantidade": null, "resultado": "falta_endereco"}
]
```

| Campo | Conteúdo |
|---|---|
| `skill` | `adicionar_item`, `alterar_item`, `remover_item`, `esvaziar_carrinho`, `definir_entrega`, `fechar_pedido`, `cancelar_pedido` |
| `item` | Nome do item do catálogo (nas três Skills de item); senão `null` |
| `quantidade` | Quantidade informada (adicionar e alterar); senão `null` |
| `resultado` | `ok` ou o motivo da recusa (`sem_preco`, `falta_maioridade`, …) |
| `detalhe` | Só quando existe: campos informados em `definir_entrega`, ou `pedido <numero>` |

- Na ordem em que foram chamadas; até 30.
- **Significado para o agente:** "isto já foi feito na tentativa anterior desta mesma resposta; não repita". O carrinho em `contexto_modulos.pedidos.carrinho` já reflete essas ações.
- `fechar_pedido`/`cancelar_pedido` com `resultado: ok` nunca aparecem aqui, porque turno com esse efeito não é refeito.
- No turno refeito, `mensagens_do_lote` e `message` trazem o lote completo (as mensagens do turno descartado + as novas), na ordem.

### 8.3 Detalhes que a implementação fixou

- **Padrões da plataforma:** `debounce_incompleta_s` nasce **nulo** (mensagem incompleta espera o `debounce_s`, como antes) e `debounce_curto_s` continua 1,5. Os valores aprovados (7 s e 2,5 s) são configurados no agente do By Koji. Única mudança para todos: reticências deixaram de contar como frase completa.
- **Status do turno descartado:** `status = 'refeito'`, `etapa = 'concluido'`, com a resposta guardada e nunca enviada. O turno seguinte tem `refeito_de` apontando para ele.
- **Reconhecimento do refazer:** o turno novo é "refeito" quando o lote dele contém mensagens de um turno `refeito`. É lido do banco a cada vez, então vale também depois de queda do worker no meio.
- **Motivos de não refazer** (`turnos.refazer_recusado`): `ja_refeito`, `acao_com_efeito` (`transferir_humano`, `encerrar` ou `pedido_fechado` na resposta), `skill_com_efeito` (`fechar_pedido`/`cancelar_pedido` com sucesso).
- **Não refaz** também turno com erro do agente (a resposta é o fallback), turno de notificação do sistema e turno ignorado.
- O turno descartado **conta no consumo** (o agente foi consultado).
- Antes da segunda consulta, a trava da conversa e a vaga da empresa/agente ganham validade nova.
- O aviso de demora não sai de novo no turno refeito se a tentativa descartada já avisou.

---

## 9. Presença ("digitando" do cliente) — resultado do teste

- 11/10/2026, 00:01 UTC: `presence` acrescentado aos eventos do webhook da instância (mesma URL, mesmo `excludeMessages`).
- A documentação (`/webhook/presence`) descreve o evento: `EventType: "presence"`, `event.State` = `composing` ou `paused`, `event.Chat`/`event.Sender`/`event.chatid`, `event.Media` (pode indicar áudio), `event.IsFromMe`, `event.IsGroup`. Não cita nenhuma condição para o evento ser entregue.
- **Teste real:** o piloto digitou cerca de 5 s sem enviar, na conversa com a instância. **Nenhum evento chegou** (zero chamadas ao webhook entre 00:01:47 e 00:06:45 UTC; zero registros de presença em `webhook_raw`).
- Conclusão: **não serve para o P3 como está.** A assinatura voltou para `messages` e `connection` às 00:07 UTC. O P3 fica suspenso.
- Hipótese, não confirmada: o WhatsApp só entrega "digitando" a quem pediu a presença daquele contato e está "online"; a uazapi não documenta como pedir isso. Se um dia houver esse recurso, o desenho da seção 2.2 continua valendo.
- O código que descarta eventos de presença na entrada (guardando só 20) permanece, inofensivo.

---

## 10. Campanha manual não pausa o bot

Situação: **proposta. Nada implementado.** Pedido do Marco em 11/10/2026.

### 10.1 O problema

Mensagem enviada pelo número da instância fora da API (celular ou WhatsApp Web) conta como atendente assumindo a conversa e pausa o bot por `pause_minutes` (INV03). Isso é certo para atendimento, mas errado para **divulgação**: em 10/10/2026, três mensagens de ofertas enviadas pelo celular da instância pausaram a conversa do piloto por 60 minutos.

### 10.2 Regra

Uma mensagem enviada pelo número da instância fora da API é **campanha** quando as duas condições valem:

1. **Mesmo texto para várias conversas:** o mesmo texto normalizado foi enviado para `campanha_conversas` conversas **diferentes** (padrão **3**) do mesmo canal dentro de `campanha_janela_min` minutos (padrão **10**).
2. **Não é resposta a cliente:** em cada uma dessas conversas, o cliente **não mandou mensagem nos últimos `campanha_silencio_min` minutos** (padrão **30**) antes do envio. Ver 10.4.

Efeitos:

| Momento | O que acontece |
|---|---|
| 1ª e 2ª conversas da série | Como hoje: grava como `humano`, pausa o bot (ainda não dá para saber que é campanha) |
| Conversa que completa o limite (a 3ª) | A série vira campanha. Esta mensagem é gravada com origem `campanha`, **não pausa** e **não marca** aguardando atendente |
| 1ª e 2ª, retroativamente | A mensagem é remarcada como `campanha` e a **pausa é desfeita**, desde que: a pausa em vigor seja a criada por aquela mensagem; nenhum atendente tenha escrito **outra coisa** na conversa depois dela; e a conversa não esteja aguardando atendente por outro motivo |
| Da 4ª em diante, dentro da janela | Campanha direto: não pausa |
| Mensagem manual única (texto diferente, ou para 1–2 conversas) | Continua pausando como hoje |

- **Texto normalizado:** sem maiúsculas/minúsculas, sem acentos, espaços e quebras de linha reduzidos a um espaço, sem emojis e sem formatação do WhatsApp (`*`, `_`, `~`, crase). Comparação por igualdade (hash); textos com menos de 20 caracteres **nunca** são campanha ("ok", "bom dia", "já vou ver").
- **Mídia com legenda:** vale a legenda. Mídia sem legenda não entra na regra (continua pausando), porque não há texto para comparar.
- **Janela:** conta a partir da hora do provedor de cada mensagem, não da hora de chegada (a varredura pode entregar atrasado).
- **Origem `campanha`** é um valor novo de `mensagens.origem`. No histórico enviado ao agente, entra como fala do assistente marcada: `[divulgação enviada pela loja] <texto>`, para o agente saber que o cliente recebeu aquilo e não estranhar um "quero essa oferta".
- A mensagem de campanha **não abre turno** e não reabre conversa encerrada.
- **Cliente que responde à campanha:** é mensagem normal; o bot responde (não está pausado).
- Vale igual para o webhook e para a varredura (o mesmo caminho de entrada), e é idempotente pelo id do provedor.

### 10.3 Configuração e métricas

Por agente, com a herança de sempre:

| Campo | Faixa | Padrão |
|---|---|---|
| `campanha_deteccao` | `on` / `off` | **`on`** |
| `campanha_conversas` | 2 a 50 | 3 |
| `campanha_janela_min` | 1 a 120 | 10 |
| `campanha_silencio_min` | 0 a 1440 (0 desliga a condição 2) | 30 |

Métricas (`GET /metricas`, bloco `campanhas`): `detectadas` (séries), `mensagens` (mensagens marcadas como campanha), `pausas_desfeitas`, `pausas_mantidas` por motivo (`atendente_escreveu`, `aguardando_atendente`, `pausa_mais_nova`).

Auditoria: `campanha.detectada` (uma vez por série, com a quantidade de conversas; sem o texto) e `conversa.pausa_desfeita` em cada conversa liberada.

### 10.4 Frase pronta do atendente × campanha

O caso difícil: o atendente responde a mesma frase pronta ("Seu pedido saiu para entrega") para 3 clientes em 10 minutos. Pelo texto, é igual a uma campanha; pelo sentido, é atendimento, e o bot **deve** ficar pausado.

Diferença proposta — **quem falou por último**:

- Frase pronta é **resposta**: o cliente escreveu há pouco (ou há pedido em andamento) naquela conversa.
- Campanha é **mensagem fria**: chega a conversas em que o cliente estava calado.

Por isso a condição 2: a conversa só conta para a série se o cliente não escreveu nos últimos `campanha_silencio_min` minutos. Reforços:

- Conversa **aguardando atendente** nunca conta (ali a mensagem do número é atendimento por definição).
- Conversa com **pedido em aberto** (`aguardando_loja` ou confirmado há menos de 3 horas) nunca conta.
- Basta **uma** conversa da série não contar para ela não contar; as outras ainda podem formar campanha entre si se chegarem ao limite.

Limite conhecido: frase pronta enviada a 3 clientes que estavam calados há mais de 30 minutos e sem pedido ("Oi, tudo bem? Posso ajudar?") será tratada como campanha e não pausará. É aceitável: se o cliente responder, o bot atende; se o atendente quiser assumir, escreve outra mensagem (que pausa) ou assume pelo painel.

### 10.5 Bloco e testes

**C1 — Campanha manual** (migração aditiva: valor novo de origem, configuração, índice por canal + hash + hora).

| Caso | Esperado |
|---|---|
| Mesmo texto para 3 conversas caladas em 10 min | 3ª não pausa; 1ª e 2ª têm a pausa desfeita; as três ficam com origem `campanha`; uma série detectada |
| 4ª e 5ª conversas dentro da janela | Não pausam |
| Mesmo texto para 2 conversas | As duas pausam, como hoje |
| 3 conversas, mas a 3ª depois da janela | Todas pausam |
| Textos diferentes para 3 conversas | Todas pausam |
| Mesmo texto com emoji, negrito ou espaços diferentes | Conta como o mesmo |
| Texto com menos de 20 caracteres para 3 conversas | Todas pausam |
| "Seu pedido saiu para entrega" para 3 clientes que escreveram há 5 min | Todas pausam (frase pronta) |
| Idem, com pedido em aberto e cliente calado há 2 h | Todas pausam |
| 2 conversas caladas + 1 em que o cliente acabou de escrever, e depois uma 4ª calada | A que tem cliente ativo pausa e fica fora; a 4ª completa a série das três caladas |
| Na 1ª conversa o atendente escreveu outra coisa depois da oferta | A pausa dessa conversa **não** é desfeita |
| 1ª conversa já estava aguardando atendente | Continua aguardando; pausa mantida |
| Conversa pausada por outro motivo mais recente (assumida pelo painel) | Pausa mantida |
| Cliente responde à campanha | Bot responde normalmente |
| Mesma mensagem chega pelo webhook e pela varredura | Conta uma vez |
| Mensagem enviada pela API (bot, painel) com o mesmo texto para 3 conversas | Não entra na regra (não é manual) |
| `campanha_deteccao: off` | Tudo como hoje |
| Outra empresa ou outro canal com o mesmo texto | Não soma (isolamento por empresa e canal) |
| Histórico ao agente | A campanha aparece marcada como divulgação da loja |

Ordem sugerida: logo depois do B3 (é pequeno e evita pausas indevidas no piloto).

### 10.6 Pontos para o Marco decidir

1. **Silêncio do cliente (30 min)** como critério para separar frase pronta de campanha, mais as travas de "aguardando atendente" e "pedido em aberto". Concorda?
2. **Texto mínimo de 20 caracteres** para ser campanha?
3. **Mídia sem legenda** (só a imagem da oferta) continua pausando. Aceita, ou quer que imagem igual enviada a 3 conversas também conte (exige comparar o arquivo)?
4. A campanha aparece para o agente no histórico como `[divulgação enviada pela loja] …`. Concorda?
