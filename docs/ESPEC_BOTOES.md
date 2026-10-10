# Especificação — botões interativos no WhatsApp (bloco UX1)

Situação em 10/10/2026: **levantamento e proposta. Nada implementado.** Aguarda aprovação do Marco.
Pendente para fechar a seção 1.3: o payload real do clique (mensagem de teste enviada ao piloto às 19:39 de 10/10/2026).

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

🔲 **Pendente.** Preencher com o payload gravado em `webhook_raw` (mascarado) depois do clique do Marco. Nada aqui será inferido.

O que já se sabe do código atual: um `messageType` que o normalizador não conhece vira anexo não suportado (`[não foi possível ler ...]`), entra como mensagem do cliente e vai ao agente. Ou seja, **hoje o clique não se perde, mas chega ilegível**.

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
| Recusa pelo botão | Não recusa na hora: "Para recusar o pedido 48213, responda: NÃO 48213 e o motivo." (o motivo é obrigatório para avisar o cliente) |
| Já decidido | "O pedido 48213 já foi confirmado às 19:42 pelo painel." (ou recusado, com quem e quando) |
| Pedido de outra unidade ou inexistente | "Não encontrei o pedido 48213 para esta unidade." |

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

## 8. Pontos para o Marco decidir

1. **Recusar pelo botão pede o motivo em texto** (seção 5.2), em vez de recusar na hora sem motivo. Concorda?
2. **Histórico grava as opções numeradas** junto do balão (seção 3.1), mesmo quando saíram como botão. Concorda?
3. **`botoes_interativos` nasce desligado** e você liga só no agente do By Koji. Concorda?
4. **Lista (B3)** exige mais um teste real com o piloto (uma mensagem). Autoriza quando chegar a vez?
5. **`url_cardapio` e `dominios_de_link`** são campos novos de cadastro (B5): por empresa, por unidade, ou os dois?
