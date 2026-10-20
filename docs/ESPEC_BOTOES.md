# Especificação — botões interativos no WhatsApp (bloco UX1)

Situação em 10/10/2026: **aprovada pelo Marco** (decisões na seção 8). Bloco **B1 (ler o clique) implementado**; os demais aguardam liberação.
Ordem aprovada: B1 → B2 → B4 agora; depois o G4; B3 e B5 junto do G3.

---

## 1. Levantamento

### 1.1 Como o gateway envia hoje

- `app/canais/uazapi.py`, função `enviar()`: `POST /send/text` com `{number, text}`. Devolve o `messageid` do provedor, que é gravado em `mensagens.message_id` (`<canal>:<id>`) para a varredura reconhecer o que o bot enviou.
- Quem chama: `Pipeline.enviar()` (`app/nucleo/pipeline.py`), uma chamada por balão, dentro de `_enviar_partes()`. Antes de enviar, `permitido()` confere piloto, pausa e envio de teste. Só o canal `uazapi` envia de verdade; os demais gravam a mensagem e não enviam.
- Avisos internos (pedido para a unidade, alerta de transbordo): `app/modulos/pedidos/avisos.py`, também por `uazapi.enviar()`.
- **Onde entra o `/send/menu`:** uma função nova `uazapi.enviar_menu()` ao lado de `enviar()`, mesmo retorno (id do provedor). `Pipeline.enviar()` passa a receber as opções do último balão e escolhe a forma pelo tipo do canal. O resto do caminho (travas, etapas do turno, idempotência por parte, varredura) não muda.

### 1.2 `/send/menu` da uazapi (documentação + teste real)

Corpo: `number`, `type`, `text`, `choices` (obrigatórios); `footerText`, `listButton` (opcionais).

| `type` | Sintaxe de cada item de `choices` |
|---|---|
| `button` | Resposta: `"texto\|id"`. Copiar: `"texto\|copy:codigo"`. Ligar: `"texto\|call:+5511..."`. Link: `"texto\|https://..."` |
| `button_legacy` | Só resposta, no formato antigo |
| `list` | `"[Seção]"` abre seção; `"texto\|id\|descrição"` é um item; `listButton` é o texto do botão que abre a lista |

- A documentação **não informa limites** (quantidade de botões, tamanho do título). Os limites desta especificação são os do WhatsApp oficial (Meta), mais restritos, para valerem nos dois.
- A documentação avisa: misturar botão de resposta com link/ligar/copiar faz a mensagem não aparecer no WhatsApp Web; e botões e listas "podem ser descontinuados sem aviso". Por isso o texto numerado é sempre o plano B.
- **Teste real (10/10/2026, 19:39, piloto):** `type: "button"` com 2 botões de resposta → HTTP 200, `messageType: "NativeFlowMessage"`, `status: "Pending"`, e a resposta traz `messageid` (a função atual `id_do_envio()` já o lê). O webhook da própria mensagem chegou com `wasSentByApi: true` e foi ignorado, como os envios de texto.
- **Lista (`type: "list"`):** documentada, **ainda não testada de verdade**. Fica para o bloco B3, com um teste real antes; até lá, mais de 3 opções vira texto numerado.

### 1.3 Formato real do webhook de clique

Clique do piloto em "Sim, funcionou", 10/10/2026 às 19:44 (registro 104 de `webhook_raw`; telefones, nomes e token mascarados; bloco `chat` omitido). Cópia anonimizada usada nos testes: `tests/fixtures/uazapi/clique_botao.json`.

```json
{"EventType": "messages", "chatSource": "updated",
 "message": {
  "messageType": "TemplateButtonReplyMessage",
  "type": "text",
  "text": "",
  "vote": "Sim, funcionou",
  "buttonOrListid": "teste_sim",
  "quoted": "3EB02BEE543170F3B092D5",
  "messageid": "A5C9BB44D679061FAF9C90DCE206ADF6",
  "id": "5511*****99:A5C9BB44D679061FAF9C90DCE206ADF6",
  "chatid": "5511*******46@s.whatsapp.net",
  "sender_pn": "5511*******46@s.whatsapp.net",
  "sender": "8220********33@lid", "sender_lid": "8220********33@lid", "chatlid": "8220********33@lid",
  "senderName": "<nome>", "fromMe": false, "wasSentByApi": false, "isGroup": false, "source": "android",
  "messageTimestamp": 1791672290000,
  "content": {
   "selectedID": "teste_sim",
   "selectedDisplayText": "Sim, funcionou",
   "selectedIndex": 0,
   "contextInfo": {
    "stanzaID": "3EB02BEE543170F3B092D5",
    "participant": "1409*********31@lid",
    "quotedType": 0,
    "quotedMessage": {"interactiveMessage": {
      "body": {"text": "Teste de botões do gateway (UX1). Toque em uma das opções:"},
      "InteractiveMessage": {"NativeFlowMessage": {"buttons": [
        {"name": "<nome>", "buttonParamsJSON": "{\"id\":\"teste_sim\",\"display_text\":\"Sim, funcionou\",\"disabled\":false}"},
        {"name": "<nome>", "buttonParamsJSON": "{\"id\":\"teste_nao\",\"display_text\":\"Não apareceu\",\"disabled\":false}"}]}}}}}}}}
```

O que o payload mostra:

| Dado | Onde vem |
|---|---|
| Tipo do evento | `messageType: "TemplateButtonReplyMessage"` (com `type: "text"` e **`text` vazio**) |
| Título do botão | `content.selectedDisplayText` e `vote` |
| Id do botão | `content.selectedID` e `buttonOrListid` |
| Mensagem a que o clique pertence | `quoted` e `content.contextInfo.stanzaID` = o `messageid` devolvido pelo `/send/menu` (o mesmo que o gateway grava em `mensagens.message_id`) |
| Telefone do cliente | `chatid` / `sender_pn`, como em qualquer mensagem |
| Id próprio do clique | `messageid` (serve à idempotência, como em qualquer mensagem) |

- A mensagem original inteira volta em `quotedMessage`, mas o gateway **não confia nela**: as opções válidas são as que ele mesmo gravou ao enviar.
- Antes do B1, este clique entrou como `[não foi possível ler TemplateButtonReplyMessage: tipo de mensagem não suportado]` e o agente respondeu pedindo texto (ocorrido no teste).
- O formato do clique em **lista** não foi observado; fica para o teste real do B3.
- **Apple / iPhone (16/10/2026):** o mesmo toque em botão de resposta pode chegar como `messageType: "ButtonsResponseMessage"` (`mediaType: "buttons_response"`), com título em `vote` e em `content.Response.SelectedDisplayText`, id em `buttonOrListid` e `content.selectedButtonID` (sem `selectedID`/`selectedDisplayText`). Antes do reconhecimento, a mensagem entrava como `[não foi possível ler ButtonsResponseMessage: tipo de mensagem não suportado]` (ex.: cliente real do By Koji em 16/10). Fixture: `tests/fixtures/uazapi/clique_botao_apple.json`.

### 1.4 Onde o contrato com o Skyone é validado

| Ponto | O que faz com um campo `botoes` hoje |
|---|---|
| JS "Interpretar Resposta" v2 (no Skyone) | Monta o Retorno campo a campo. O código não está no repositório (em `docs/SKYONE.md` consta como 🔲). Um campo que ele não monta **não chega ao gateway**. Precisa ganhar a passagem de `botoes` |
| `app/agentes/skyone.py` (parser do Retorno) | Lê só `resposta`, `acao`, `motivo`, `desfecho` e `uso`. Qualquer outro campo é **descartado em silêncio** (está documentado assim). `botoes` seria ignorado sem erro |

Conclusão: o campo novo não quebra nada em nenhum dos dois lados; para funcionar, precisa de mudança nos dois (seção 6, blocos B1 e B2).

---

## 2. Princípios

1. O agente **nunca** monta payload de canal. Ele devolve um campo opcional e o gateway renderiza.
2. O agente só cria botões de **resposta**. Botões de **ação** (link, ligar, copiar) nascem de dados cadastrados, nunca de texto do modelo.
3. Resposta e ação nunca vão na mesma mensagem: a ação vai em mensagem separada.
4. **Sempre há plano B em texto.** A conversa nunca falha por causa de botão.
5. O clique é uma mensagem do cliente como outra qualquer. Nenhum clique executa nada sozinho na conversa com o agente.
6. Envio real continua só para o piloto. Nada desta especificação muda `send_mode`, piloto ou contatos.

---

## 3. Contratos

### 3.1 Agente → gateway: campo `botoes`

```json
{"resposta": ["Seu pedido ficou assim: ...", "Posso confirmar?"],
 "acao": "nenhuma",
 "botoes": {"opcoes": [{"id": "confirmar_pedido", "titulo": "Confirmar pedido"},
                       {"id": "alterar_pedido", "titulo": "Alterar"}]}}
```

- Opcional. Vale para o **último balão** da resposta. Sem `resposta` (lista vazia) os botões são descartados.
- Só com `acao` `nenhuma` ou `pedido_fechado`. Com `silencio`, `transferir_humano` ou `encerrar`, são descartados (não faz sentido oferecer escolha a quem vai ser transferido).

| Regra | Limite |
|---|---|
| Quantidade para virar botão | 1 a 3 |
| `titulo` | 1 a 20 caracteres, depois de tirar espaços das pontas; sem quebra de linha e sem `\|` |
| `id` | `[a-z0-9_]`, 1 a 40 |
| Duplicados | Nem `id` nem `titulo` repetidos (título comparado sem maiúsculas e sem acento) |
| Máximo absoluto | 10 opções (acima disso, o campo inteiro é inválido) |

O que acontece em cada caso:

| Caso | Resultado | Motivo registrado |
|---|---|---|
| 1 a 3 opções válidas, canal com botão | Botões | — |
| 4 a 10 opções válidas | Lista (quando o bloco B3 existir e o canal suportar) ou texto numerado | `acima_do_limite` |
| Título com mais de 20 caracteres | Texto numerado, com o título inteiro | `titulo_longo` |
| `id` inválido ou duplicado, título vazio ou duplicado, formato errado | Texto numerado com os títulos aproveitáveis; se nenhum servir, só o balão | `invalido` |
| Canal sem botão (site, e-mail, API, teste) | Texto numerado | `canal_sem_botao` |
| Erro 4xx da uazapi no `/send/menu` | O mesmo balão por `/send/text`, com as opções numeradas | `provedor_recusou` |
| Erro 5xx, tempo esgotado ou resposta incerta | **Não** reenvia como texto na hora (poderia duplicar). Segue a regra atual de entrega incerta: a varredura confere se a mensagem saiu; se não saiu, a nova tentativa já vai em texto numerado | `provedor_incerto` |
| Recurso desligado na empresa/agente | Texto numerado | `desligado` |

Em nenhum caso o turno vira erro nem o cliente fica sem resposta.

**Texto numerado (plano B):** o balão, uma linha em branco e as opções:

```
Posso confirmar?

1. Confirmar pedido
2. Alterar
```

A resposta do cliente ("1", "confirmar") é texto comum e vai ao agente, que já vê as opções no histórico.

**Histórico:** a mensagem do bot é gravada com o texto do balão e as opções numeradas (igual ao plano B), para o agente e o painel lerem a mesma coisa em qualquer canal. As opções ficam também em `mensagens.meta.botoes` (`{"opcoes": [...], "forma": "botao|lista|texto", "motivo": null}`).

### 3.2 Gateway → agente: o clique

- O webhook do clique é normalizado como **mensagem de texto do cliente**, com o **título do botão** como texto (ex.: `Confirmar pedido`). `mensagens.tipo` continua `texto`; `mensagens.meta.interacao = {"tipo": "botao", "id": "confirmar_pedido", "mensagem": "<id da mensagem dos botões>"}`.
- Debounce, histórico, idempotência (pelo `message_id` do provedor), limite por contato, piloto e pausa: **iguais aos de qualquer texto**. Clique seguido de texto digitado no mesmo intervalo vira um lote só.
- Pacote: campo novo `ultima_interacao`, e o mesmo dado em cada item de `mensagens_do_lote`.

```json
"ultima_interacao": {"tipo": "botao", "id": "confirmar_pedido", "titulo": "Confirmar pedido", "atual": true},
"mensagens_do_lote": [{"enviada_em": "...", "tipo": "texto", "texto": "Confirmar pedido",
                       "interacao": {"tipo": "botao", "id": "confirmar_pedido", "atual": true}}]
```

- `ultima_interacao` é `null` quando o lote não tem clique.
- **`atual`:** `true` só quando o botão clicado pertence à **última mensagem com botões enviada pelo bot** nessa conversa e nenhuma outra mensagem do bot veio depois dela. Clique em mensagem antiga chega com `atual: false`: o agente trata como texto normal e confere o contexto antes de agir. O gateway não executa nada com base em clique de cliente, atual ou não.
- Se o provedor não informar a qual mensagem o clique pertence, ou o `id` não existir nas opções gravadas, o clique vira texto puro (`ultima_interacao: null`).
- Texto digitado continua funcionando sempre; o botão é atalho, não obrigação.

### 3.3 Liga e desliga

Configuração `botoes_interativos` (`on`/`off`) com a herança de sempre (plataforma → modelo → empresa → agente). Padrão da plataforma: `off`. Desligado: tudo sai em texto numerado e o clique (se houver) continua sendo lido. Serve de freio se a uazapi descontinuar os botões.

---

## 4. Renderização por canal

| Canal | 1 a 3 opções | 4 a 10 opções | Ação (link, ligar, copiar) |
|---|---|---|---|
| **uazapi** | `/send/menu` `type: "button"`, `choices: ["título\|id"]` | `type: "list"` (bloco B3, depois de teste real) ou texto numerado | `type: "button"` só com itens de ação, em mensagem separada |
| **Meta (WhatsApp oficial)** — futuro | `interactive.button` (até 3, título até 20) | `interactive.list` (até 10 linhas, título até 24) | `cta_url` (um link por mensagem); ligar e copiar só em modelo aprovado |
| **Site, e-mail, API, teste** | Texto numerado | Texto numerado | Texto com o dado por extenso (telefone, link) |

O mapeamento fica numa função por canal (`renderizar(opcoes, canal) → forma`), para a Meta entrar depois sem mexer no pipeline.

No envio de teste (`send_mode=dry`) nada é enviado; a mensagem é gravada com a forma que **teria** saído, para o painel e os testes conferirem.

---

## 5. Botões gerados pelo gateway

### 5.1 Ação para o cliente

Sempre em mensagem separada, depois do texto. Nunca a pedido do agente.

| Botão | De onde vem o dado | Quando aparece |
|---|---|---|
| **Ligar para a loja** | `telefone` da unidade (cadastro), só se for um número válido (10 a 15 dígitos com DDI depois de limpar) | Mensagem automática de fora do horário (G3); depois do pedido confirmado ou recusado pela unidade |
| **Ver cardápio** | URL cadastrada na empresa ou na unidade (campo novo `url_cardapio`), `https` e com o domínio na lista permitida da empresa (`dominios_de_link`, campo novo) | Mensagem automática de fora do horário (G3) |
| **Copiar Pix** | Chave Pix da forma de pagamento da unidade | **Só depois do G4.** Fora deste bloco |

- Dado ausente ou inválido → o botão não é gerado (sem erro).
- Plano B: uma linha de texto com o dado ("Telefone da loja: (11) 3624-7710").
- Como a mensagem de fora do horário é do G3, o **Ligar** e o **Ver cardápio** só passam a aparecer nela quando o G3 existir; o que entra agora é o gerador e o uso no pós-pedido.

### 5.2 Notificação à unidade: "Aceitar pedido" / "Recusar"

- O aviso de pedido novo (hoje texto com "Para confirmar, responda: OK 48213") ganha dois botões de resposta: **Aceitar pedido** (`pedido_ok_48213`) e **Recusar** (`pedido_nao_48213`). O texto com as instruções `OK`/`NÃO` continua na mensagem.
- O clique só vale vindo do **WhatsApp de pedidos cadastrado** de uma unidade dona do pedido — a mesma checagem do `OK <numero>` de hoje (`numero_interno()`), feita pelo telefone do remetente, nunca pelo `id` do botão.
- Efeito: exatamente a função atual `decidir()`. Portanto idempotente; quem decide primeiro (painel, texto ou botão) vale.
- Respostas à unidade:

| Situação | Resposta |
|---|---|
| Aceito agora | "Pedido 48213 confirmado. O cliente foi avisado." |
| Toque em **Recusar** (`pedido_nao_48213`) | Não recusa ainda: o gateway responde com 3 botões de motivo — **Item em falta** (`pedido_nao_48213_falta`), **Loja lotada** (`pedido_nao_48213_lotada`) e **Outro motivo** (`pedido_nao_48213_outro`) |
| **Item em falta** ou **Loja lotada** | Recusa na hora, com o motivo correspondente em texto amigável ao cliente; responde "Pedido 48213 recusado. O cliente foi avisado." |
| **Outro motivo** | Não recusa: "Para recusar o pedido 48213, responda: NÃO 48213 e o motivo." |
| Já decidido | "O pedido 48213 já foi confirmado às 19:42 pelo painel." (ou recusado, com quem e quando) |
| Pedido de outra unidade ou inexistente | "Não encontrei o pedido 48213 para esta unidade." |

- **Sem estado guardado:** tudo o que é preciso vem no `id` do botão (ação, número do pedido e motivo). Cada clique é validado de novo pelo telefone do remetente (`numero_interno()`) e passa por `decidir()`.
- `OK <numero>` e `NÃO <numero> <motivo>` digitados **continuam valendo**.
- Plano B: se o botão não puder ser enviado, sai o texto de hoje, sem mudança.
- Este é o único lugar em que um clique tem efeito direto, e é em conversa interna (unidade), não com cliente. Clique em aviso antigo cai em "já decidido".
- Ajuste necessário: hoje mensagem de número interno com tipo diferente de texto é ignorada; o clique normalizado como texto resolve.

---

## 6. Métricas

Contadores por empresa e agente, na mesma área das métricas atuais do painel:

| Métrica | Conta |
|---|---|
| `botoes_enviados` | Mensagens que saíram com botão ou lista (por forma: `botao`, `lista`) |
| `botoes_texto` | Mensagens em que as opções saíram numeradas, por motivo (`canal_sem_botao`, `acima_do_limite`, `titulo_longo`, `invalido`, `provedor_recusou`, `provedor_incerto`, `desligado`) |
| `botoes_clicados` | Cliques recebidos, separados em `atual` e `antigo` |
| `acoes_enviadas` | Botões de ação enviados, por tipo (`ligar`, `link`) |
| `pedidos_por_botao` | Pedidos aceitos pelo botão da unidade |

Origem: `mensagens.meta` (envio e clique), sem tabela nova. Cada plano B gera um aviso no log (sem telefone e sem texto do cliente) e um evento `botoes.fallback` com o motivo.

Taxa de clique = `botoes_clicados.atual` ÷ `botoes_enviados`.

---

## 7. Blocos e testes

Cada bloco: um commit, testes, tag de volta, backup, implantação, parada para validação. Envio real só no piloto em todos.

| Bloco | Entrega | Migração | Testes principais |
|---|---|---|---|
| **B1 — Ler o clique** | Normalizador reconhece o clique (formato da seção 1.3) e entrega o título como texto; `meta.interacao`; `ultima_interacao` e `atual` no pacote; clique da unidade tratado como texto | — | Fixture real do clique; clique = uma mensagem só (mesmo `message_id` duas vezes não duplica); clique + texto no mesmo debounce = um lote; clique em mensagem antiga → `atual: false`; clique sem mensagem de origem → texto puro; contato fora do piloto não recebe resposta; pacote sem clique traz `ultima_interacao: null` (contrato) |
| **B2 — Botões de resposta do agente** | Parser aceita `botoes`; validação; `uazapi.enviar_menu()`; plano B em texto; `botoes_interativos` (padrão `off`); métricas | Aditiva (configuração) | Cada linha da tabela 3.1 (limite, título longo, id inválido, duplicado, canal sem botão, 4xx → texto, incerto não duplica); botões só no último balão; `silencio`/`transferir_humano` descartam; envio de teste grava a forma sem enviar; desligado → texto; Retorno sem `botoes` continua igual (regressão); isolamento entre empresas |
| **B3 — Lista** | `type: "list"` para 4 a 10 opções, **depois de um teste real** com o piloto (envio e clique) | — | Fixture real do clique de lista; 4 opções → lista; 11 → inválido; lista recusada → texto |
| **B4 — Botões da unidade** | "Aceitar pedido" / "Recusar" no aviso à unidade | — | Clique do número da unidade confirma; de outro número não tem efeito; pedido de outra unidade; já decidido (painel antes, botão depois, e o contrário); dois cliques seguidos = uma decisão; `OK <numero>` continua valendo; plano B = texto atual |
| **B5 — Botões de ação** | Gerador de "Ligar" e "Ver cardápio"; campos `url_cardapio` e `dominios_de_link`; uso no pós-pedido (e no G3 quando existir) | Aditiva | Link fora da lista permitida não gera botão; `http` recusado; telefone inválido não gera; nunca na mesma mensagem que botão de resposta; nada gerado a partir de texto do agente; plano B em texto |

Ordem sugerida: B1 → B2 → B4 → B3 → B5. O B1 vem primeiro porque sem ler o clique nenhum botão serve; o B4 antes do B3 porque tem uso imediato e não depende de lista.

**No Skyone (com o Marco), junto do B2:** o JS "Interpretar Resposta" passa a repassar `botoes` quando o modelo devolver, e o prompt ganha a orientação de quando oferecer opções (perguntas fechadas com até 3 respostas; nunca para listar cardápio).

---

## 8. Decisões do Marco (10/10/2026)

1. **Recusar pelo botão:** o toque em "Recusar" abre 3 botões de motivo — "Item em falta" (`pedido_nao_<numero>_falta`), "Loja lotada" (`pedido_nao_<numero>_lotada`) e "Outro motivo" (`pedido_nao_<numero>_outro`). Os dois primeiros recusam na hora com o motivo correspondente (texto amigável ao cliente); "Outro motivo" pede `NÃO <numero> e o motivo`. Sem estado guardado: tudo vem no `id`, sempre validado pelo telefone do remetente (`numero_interno`) e por `decidir()` (idempotente). `OK`/`NÃO` digitados continuam valendo.
2. **Histórico** grava as opções numeradas junto do balão: aprovado.
3. **`botoes_interativos`** nasce `off`; ligar apenas no agente do By Koji depois do B2 validado.
4. **Lista (B3):** teste real com o piloto autorizado quando chegar a vez (uma mensagem).
5. **`url_cardapio`:** na empresa, com substituição opcional por unidade. **`dominios_de_link`:** só na empresa.

**Ordem:** B1 → B2 → B4 agora; depois o G4; B3 e B5 junto do G3.

## 9. Andamento

| Bloco | Situação |
|---|---|
| B1 — Ler o clique | Implementado em 10/10/2026: `uazapi.normalizar()` reconhece `TemplateButtonReplyMessage`; `mensagens.meta.interacao`; `ultima_interacao` no pacote (`skyone.interacao()`); testes em `tests/test_botoes.py`. Em 20/10/2026: também `ButtonsResponseMessage` (clique típico de aparelhos Apple; fixture `clique_botao_apple.json`, id em `selectedButtonID`/`buttonOrListid`, título em `vote`/`Response.SelectedDisplayText`) |
| B2 — Botões de resposta do agente | Implementado em 10/10/2026: `nucleo/botoes.py` (leitura do Retorno e tabela 3.1), `uazapi.enviar_menu()`, plano B em texto, `meta.botoes`, configuração `botoes_interativos` (migração 026, padrão `off`), bloco `botoes` em `GET /metricas`; testes em `tests/test_botoes_b2.py`. **Desligado em todos os agentes** até o Marco pedir |
| B4 | Aguarda liberação |
| B3 — Lista | Implementado em 11/10/2026 (seção 11): `botoes.planejar()` devolve `lista`; `uazapi.enviar_lista()`; descrição das unidades por `horarios.resumo()`; clique `ListResponseMessage` → `interacao.tipo = "lista"`; testes em `tests/test_botoes_b3.py` |
| B5 | Junto do G3 |

Detalhes do B1 que a implementação fixou:
- O item de `mensagens_do_lote` só ganha a chave `interacao` quando é um clique; as mensagens comuns continuam com as três chaves de sempre.
- Mensagem "de origem" válida = enviada **pelo bot** (não pelo atendente), **nesta conversa**, com o id do provedor gravado. Fora disso, o clique vai como texto puro.
- `atual` = nenhuma mensagem saiu (do bot ou do atendente) depois da mensagem dos botões.
- Clique sem título nenhum (não observado) continua como tipo não suportado, e o agente pede texto.

Detalhes do B2 que a implementação fixou:
- **Erro incerto (5xx, 408, tempo esgotado):** segue a regra que já vale para qualquer envio — a parte fica `incerta`, **não é reenviada** (nem em texto) e a conversa vai para revisão humana (`entrega_incerta`). Não há nova tentativa automática em texto, ao contrário do que a tabela 3.1 sugeria; é o comportamento mais seguro contra duplicidade.
- **Recusa certa (4xx, menos 408):** nada saiu; o mesmo balão vai por `/send/text` com as opções numeradas, motivo `provedor_recusou`.
- **Ordem dos motivos** quando mais de um se aplica: `invalido` → `acima_do_limite` → `titulo_longo` → `desligado` → `canal_sem_botao`.
- **Opção com `id` vazio** é descartada em silêncio (decisão do Marco); se sobrar alguma, vale a tabela.
- **Aviso de plano B:** linha no log (`Botões enviados como texto numerado: <motivo>`, sem telefone e sem texto) e registro `botoes.fallback` na auditoria da empresa; o motivo fica em `mensagens.meta.botoes.motivo`.
- **Envio de teste (`dry`):** nada é enviado; a mensagem fica gravada com a forma que teria saído.
- **Métricas:** `enviados` conta só o que saiu como botão; tudo que tem motivo entra em `em_texto.por_motivo` (inclusive `provedor_incerto`). Clique cuja mensagem de origem não é do bot conta como `antigo`.

## 10. B3 — payload real da lista (teste de 11/10/2026)

**Envio** (`POST /send/menu`, `type: "list"`, `listButton: "Ver unidades"`, `choices: ["[Unidades]", "Título|id|descrição", …]`): HTTP 200, `messageType: "ListMessage"`, id do provedor devolvido. O webhook do próprio envio chega com `wasSentByApi: true` e é ignorado, como nos botões.

**Clique** (webhook `messages`, registro 184 do `webhook_raw`, 00:56:43 UTC). Campos que importam, com dados mascarados:

```json
{
  "EventType": "messages",
  "message": {
    "messageType": "ListResponseMessage",
    "type": "media",
    "mediaType": "list_response",
    "text": "",
    "vote": "Pátio Guedala",
    "buttonOrListid": "patio_guedala",
    "quoted": "3EB0…1249",
    "content": {
      "title": "Pátio Guedala",
      "description": "Aberta até 22h",
      "listType": 1,
      "singleSelectReply": {"selectedRowID": "patio_guedala"},
      "contextInfo": {
        "stanzaID": "3EB0…1249",
        "quotedMessage": {"listMessage": {
          "buttonText": "Ver unidades",
          "description": "Teste de lista do gateway (B3). Em qual unidade você quer pedir?",
          "sections": [{"title": "Unidades", "rows": [
            {"rowID": "einstein", "title": "Einstein", "description": "Aberta até amanhã 17h"},
            {"rowID": "morumbi", "title": "Morumbi", "description": "Aberta até 22h30"},
            {"rowID": "patio_guedala", "title": "Pátio Guedala", "description": "Aberta até 22h"},
            {"rowID": "vila_nova_conceicao", "title": "Vila Nova Conceição", "description": "Aberta até 23h"}]}]}}
      }
    },
    "chatid": "*******1513@s.whatsapp.net",
    "sender_pn": "*******9899@s.whatsapp.net",
    "fromMe": true,
    "wasSentByApi": false,
    "source": "android",
    "messageid": "ACE8…8A89",
    "messageTimestamp": 1791680203000
  }
}
```

O que o B3 vai ler:

| Informação | Campo | Observação |
|---|---|---|
| É clique de lista | `messageType == "ListResponseMessage"` (ou `mediaType == "list_response"`) | Diferente do botão (`TemplateButtonReplyMessage`) |
| Id da opção | `buttonOrListid` | Mesmo campo do clique em botão. Reserva: `content.singleSelectReply.selectedRowID` |
| Título da opção | `vote` | `text` vem **vazio**. Reserva: `content.title` |
| Mensagem de origem | `quoted` | Id do provedor da lista enviada. Reserva: `content.contextInfo.stanzaID` |
| Descrição da opção | `content.description` | Não vai para o agente |

**Ressalva deste teste:** o clique registrado veio com `fromMe: true` e `sender_pn` igual ao número da **instância** (final 9899), origem `android`: o toque foi feito no aparelho logado como a instância, dentro da conversa com o final 1513, e não no aparelho do cliente. Por isso o gateway o tratou como mensagem de atendente (gravou como `humano`, tipo não lido, e pausou o bot — regra INV03). O formato serve para escrever a leitura; o clique vindo do **cliente** (`fromMe: false`) ainda não foi observado e deve ser igual, trocando remetente. Fica como primeira conferência do B3 em produção.

Consequências para o B3:
- `ListResponseMessage` entra em `CLIQUES_DE_BOTAO`, com o título lido de `vote`.
- Clique de lista com `fromMe: true` (feito no aparelho da instância) **não** deve pausar o bot nem virar mensagem de atendente: é ignorado, com registro.
- A fixture de teste sai deste payload (anonimizado), nas duas versões: do cliente e da instância.

## 11. B3 — implementado (11/10/2026)

Sem migração: usa a mesma chave `botoes_interativos` do B2 (ligada só no agente do By Koji).

### 11.1 Quando o balão sai como lista

| Caso | Resultado | Motivo |
|---|---|---|
| 4 a 10 opções válidas | **Lista** | — |
| 1 a 3 opções, alguma com `descricao` | **Lista** | — |
| 1 a 3 opções sem descrição | Botões (como no B2) | — |
| Título com mais de 24 caracteres | Texto numerado | `titulo_longo` |
| Descrição com mais de 72 | Texto numerado, com a descrição inteira | `descricao_longa` |
| Recurso desligado no agente | Texto numerado | `desligado` |
| Canal sem lista (site, e-mail, API) | Texto numerado | `canal_sem_botao` |
| `id` inválido ou repetido, título vazio ou repetido | Texto numerado com o que se aproveita | `invalido` |
| Mais de 10 opções | Campo inteiro inválido: só o balão | `invalido` |
| Provedor recusa a lista (4xx) | O mesmo balão em texto numerado | `provedor_recusou` |
| Falha incerta (5xx, tempo esgotado) | Não reenvia; revisão humana | `provedor_incerto` |

O motivo `acima_do_limite` deixou de acontecer: mais de 3 opções agora é lista.

### 11.2 Contrato com o agente

```json
"botoes": {"titulo_lista": "Ver unidades",
           "opcoes": [{"id": "morumbi", "titulo": "Morumbi"},
                      {"id": "patio_guedala", "titulo": "Pátio Guedala"},
                      {"id": "einstein", "titulo": "Einstein"},
                      {"id": "vila_nova_conceicao", "titulo": "Vila Nova Conceição"}]}
```

- `titulo_lista`: texto do botão que abre a lista; opcional, até 20 caracteres; fora disso ou ausente, "Ver opções".
- `descricao`: opcional, até 72 caracteres.
- **Unidades:** quando o `id` da opção é o código de uma unidade ativa da empresa, o gateway escreve a descrição com a situação real do momento — "Aberta até 22h30", "Aberta até amanhã 1h", "Fechada · abre amanhã 12h", "Pedidos pausados · volta 22h", "Horário a confirmar". Se o agente mandou descrição para essa opção, ela é substituída. Nas demais opções vale a do agente.
- Isso só acontece quando o balão já seria lista. Duas ou três unidades sem descrição continuam saindo como botões.
- Histórico e plano B: `1. Morumbi — Aberta até 22h30`.
- Clique: chega como mensagem de texto com o título da opção e `ultima_interacao = {"tipo": "lista", "id", "titulo", "atual"}`, com as mesmas regras do botão (só vale se a lista foi enviada pelo gateway nesta conversa e o `id` estava nela).

### 11.3 Envio e clique

- Envio: `POST /send/menu` com `type: "list"`, `listButton`, `choices: ["[Opções]", "título|id|descrição", …]` (uma seção, chamada "Opções").
- Clique: formato da seção 10. O título vem de `vote` (ou `content.title`), o id de `buttonOrListid` (ou `content.singleSelectReply.selectedRowID`), a origem de `quoted` (ou `contextInfo.stanzaID`).
- **Toque feito no aparelho da instância** (`fromMe: true`), em lista ou em botão: ignorado. Não vira mensagem de atendente nem pausa o bot (foi o que aconteceu no teste de 11/10).
- Métricas: `botoes.enviados.lista` e `botoes.clicados` (que agora soma botão e lista).
- **A conferir no primeiro uso real:** o clique vindo do aparelho do cliente (`fromMe: false`) ainda não foi observado; a leitura foi escrita a partir do clique feito no aparelho da instância.
