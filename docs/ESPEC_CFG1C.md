# CFG1-c — Ativar Reações (R1), Fora do horário (H1-a) e Transbordo (T1)

**Situação:** **aprovada em 12/10/2026** (decisões na seção 9, que valem sobre o texto anterior onde houver diferença) e **implementada** (seção 10).
**Data:** 12/10/2026. Produção: `gateway:85b5481`, schema 36 (SOL1).
**Fora deste bloco:** pedido agendado (fica no G4-b).

Os três recursos já existem como configuração guardada e versionada desde o CFG1-a, marcados como "em breve" (`disponivel: false`). Este bloco faz o gateway **executá-los**.

---

## 1. Onde estamos

| Recurso | O que já existe | O que falta |
|---|---|---|
| Reações | Configurações `reacoes`, `reacoes_emojis`, `reacoes_max_dia`. Envio de reação pela uazapi testado em 10/10 (`POST /message/react`, HTTP 200) | Ler o campo `reacao` do agente, validar, enviar, registrar |
| Fora do horário | Configurações `fora_do_horario`, `fora_do_horario_mensagem`, `fora_do_horario_aceita_agendado`. Situação de cada unidade (grade, exceção, pausa) já calculada | Decidir "fora do horário" da empresa, informar ao agente ou responder sem ele |
| Transbordo | Alerta de transferência para um número da empresa (`whatsapp_alerta_transbordo`). Configurações `transbordo_alerta`, `transbordo_horario_equipe`, `transbordo_fora_do_horario`, `transbordo_destino_externo` | Usar o destino configurável, o horário da equipe e a mensagem de equipe ausente |

Três fatos de hoje que mudam as decisões (seção 8):

1. **O segmento `delivery_restaurante` (versões 1 e 2) está gravado com `fora_do_horario: so_avisar`**, por decisão sua no CFG1. Como nada executava, não fazia diferença. O seu pedido de agora diz "Atender (padrão do segmento)". Se o bloco entrar sem acertar isso, **o By Koji passa a responder só com o aviso quando as 4 unidades estiverem fechadas**.
2. **O alerta de transbordo do By Koji aponta hoje para o número da própria instância** (final 9899). A regra nova recusa esse número; o cadastro precisa de outro telefone.
3. **A chave "unidade fechada por agente" (`pedido_com_unidade_fechada`) ainda não existe no código**: ela foi decidida para o G4-b. Hoje o gateway não confere horário ao fechar pedido; pedido feito com a unidade fechada é aceito. O H1-a não muda isso.

---

## 2. R1 — Reações

### 2.1 Campo no retorno do agente

```json
{"resposta": ["Perfeito, já anotei!"], "acao": "nenhuma", "reacao": "❤️"}
```

`reacao`: texto com **um** emoji, ou `null`/ausente. Qualquer outra coisa (dois emojis, texto, número) é ignorada, sem erro.

### 2.2 Travas, nesta ordem

A primeira que barrar define o motivo registrado.

| # | Trava | Motivo registrado |
|---|---|---|
| 1 | Recurso ligado no agente (`reacoes = on`) | `desligado` |
| 2 | Emoji está na lista configurada (`reacoes_emojis`) | `emoji_fora_da_lista` |
| 3 | Menos de N reações nesta conversa hoje (`reacoes_max_dia`, padrão 2; o dia vira à meia-noite no fuso da empresa) | `limite_diario` |
| 4 | Pelo menos 3 mensagens do cliente desde a última reação do bot nesta conversa | `intervalo` |
| 5 | `acao` não é `transferir_humano` e `motivo` não é `reclamacao` | `acao_ou_motivo` |
| 6 | Há mensagem de **texto** do cliente no lote (clique em botão ou lista, áudio, imagem e localização não contam) | `sem_texto` |

Depois das travas, ainda pode não sair por: canal sem reação (`canal`), conversa pausada ou contato fora do piloto (`nao_permitido`), envio de teste (`simulada`), ou falha da uazapi (`falha_provedor`).

- A trava 4 conta mensagens de entrada do cliente (de qualquer tipo) gravadas depois da última reação. Na primeira reação da conversa ela não se aplica.
- Com `silencio`, `encerrar` e `notificar_equipe` a reação **é permitida** (um ❤️ no "obrigado" sem responder texto é o caso de uso). Isso muda a regra antiga da `ESPEC_UX2.md`, que proibia; sigo o que você definiu agora.

### 2.3 Envio

| Ponto | Regra |
|---|---|
| Alvo | A **última mensagem de texto do cliente** no lote do turno |
| Ordem | A reação sai **antes** dos balões |
| Falha | Registra e segue: sem erro no turno, sem nova tentativa, e os balões saem normalmente |
| Turno refeito | A reação da resposta descartada não sai; só a do turno que de fato responde |
| Envio de teste | Não envia; fica registrada como `simulada` |
| Histórico | Mensagem de saída do bot do tipo `reacao`, com o id devolvido pelo provedor (a varredura não a confunde com atendente) |
| Efeitos colaterais | A reação não conta como "o bot falou há pouco" (aviso de demora) nem torna "antigo" o botão da mensagem anterior |
| Agente | A reação não entra no histórico enviado ao agente como fala |

### 2.4 Padrões e segmento

| Nível | `reacoes` | `reacoes_emojis` | `reacoes_max_dia` |
|---|---|---|---|
| Plataforma | `off` | ❤️ 😊 👍 🙏 | 2 |
| Segmento `delivery_restaurante` (versão nova) | **`on`** | ❤️ 😊 👍 🙏 | 2 |

- Emojis que a plataforma permite escolher: ❤️ 😊 👍 🙏 😂 👏 🎉 ✅.
- Entra uma **versão 3** do segmento, e o agente do By Koji é movido para ela, com a comparação antes e depois no relatório.
- Ligado no By Koji, nada acontece até o prompt passar a devolver `reacao`.

### 2.5 Métricas (`GET /metricas`, bloco novo `reacoes`)

```json
"reacoes": {"aplicadas": {"total": 12, "por_emoji": {"❤️": 7, "👍": 5}},
            "bloqueadas": {"total": 4, "por_motivo": {"limite_diario": 2, "intervalo": 1, "sem_texto": 1}}}
```

Cada turno guarda o que aconteceu com a reação pedida (emoji, resultado, motivo), para diagnóstico.

---

## 3. H1-a — Fora do horário

### 3.1 Quando a empresa está fora do horário

**Todas as unidades ativas da empresa estão fechadas agora**, pela situação que o gateway já calcula (grade semanal, exceções por data e pausa).

| Caso | Tratamento |
|---|---|
| Pelo menos uma unidade aberta | Não está fora do horário |
| Alguma unidade **sem horário cadastrado** | Não dá para afirmar que está fechada: a empresa **não** é tratada como fora do horário |
| Todas pausadas | Fora do horário; a próxima abertura é o fim da pausa ou a próxima abertura da grade |
| Empresa sem unidade ativa | Nunca fora do horário |

`proxima_abertura` é a abertura mais próxima entre as unidades.

### 3.2 Modo `atender`

O agente é chamado normalmente. O pacote ganha dois campos na raiz, **sempre presentes**:

```json
"fora_do_horario": true,
"proxima_abertura": {"unidade": "Pátio Guedala", "codigo": "patio_guedala",
                     "dia_semana": "terça-feira", "data": "2026-10-13", "hora": "12:00"}
```

- Com a empresa aberta: `"fora_do_horario": false, "proxima_abertura": null`.
- `proxima_abertura` também é `null` quando não se sabe (unidade pausada sem prazo e sem grade).
- Data e hora no fuso da unidade.
- **Pedido fora do horário:** este bloco não muda nada. Hoje o pedido é aceito mesmo com a unidade fechada; a chave por agente que recusa (`pedido_com_unidade_fechada`) entra no G4-b.

### 3.3 Modo `so_avisar`

O gateway responde sozinho, **sem chamar o Skyone**.

| Ponto | Regra |
|---|---|
| Mensagem | "Mensagem fora do horário" (`fora_do_horario_mensagem`), com as variáveis da 3.4 |
| Quantas vezes | **Uma por conversa por período fechado.** O período vai até a próxima abertura; se ela for desconhecida, vale por 12 horas |
| Mensagens seguintes no mesmo período | Ficam gravadas na conversa, sem resposta e sem chamar o agente |
| Quando a loja abre | O que chegou com a loja fechada **não** é respondido depois. O cliente escreve de novo (a mensagem padrão já diz isso) |
| Conversa com atendente (bot pausado) | Nada é enviado: quem está atendendo é a pessoa |
| Piloto e envio de teste | Respeitados, como em qualquer envio |
| Histórico | Saída com origem `sistema` e marca de fora do horário; o painel pode mostrar como aviso automático |
| Avisos de pedido | Continuam saindo normalmente (são transacionais) |
| Notificações que o sistema pede ao agente (`/notificar`) | Continuam chamando o agente |
| Registro | O turno fica com resultado `fora_do_horario`; conta nas métricas (`fora_do_horario: {avisos_enviados, mensagens_sem_resposta}`) |

**Exceção proposta (decisão 3):** se a conversa tem **pedido ativo** (aguardando a loja, confirmado, em preparo, pronto ou a caminho), o agente é chamado mesmo em `so_avisar`. Sem isso, quem pergunta "cadê meu pedido?" depois do fechamento recebe só "estamos fechados".

### 3.4 Texto e variáveis

| Variável | Vira |
|---|---|
| `{proxima_abertura}` | "hoje às 18h", "amanhã às 12h", "terça-feira às 12h" |
| `{nome_empresa}` | O nome da empresa do perfil |
| `{abre}` | O mesmo que `{proxima_abertura}` (nome antigo, mantido para não quebrar texto já salvo) |

- Trecho opcional `[[ … ]]`, como nos avisos de pedido: some quando a variável de dentro está vazia.
- Texto padrão (o que você aprovou na gestão, decisão 9.8): `Oi! 😊 No momento a {nome_empresa} está fechada.[[ Voltamos {proxima_abertura}.]] Quando abrirmos, é só mandar uma mensagem por aqui que a gente te atende!`
- O texto passa pela mesma análise das regras da casa ao publicar.

### 3.5 Pedido agendado

`fora_do_horario_aceita_agendado` continua "em breve" (`disponivel: false`).

---

## 4. T1 — Transbordo

### 4.1 Telefone de alerta da equipe

- Destino, por WhatsApp, dos alertas de `transferir_humano` e de `notificar_equipe`. Passa a ser a configuração `transbordo_alerta` (versionada, com herança); o número que já está em `whatsapp_alerta_transbordo` é copiado para ela, e o campo antigo continua aceito.
- **Validação:** telefone brasileiro em E.164 — `55` + DDD + 8 ou 9 dígitos (12 ou 13 dígitos). Aceita com `+`, espaços e traços; guarda só os dígitos.
- **Recusa o número da própria instância** (400, motivo `numero_da_instancia`). Para isso o gateway passa a guardar o número de cada canal da uazapi, lido do provedor.
- O número já cadastrado que for o da instância **não é apagado**: o painel mostra o aviso, o `GET` devolve `transbordo_alerta_invalido: true`, e o alerta para ele deixa de ser enviado (hoje ele vai para o próprio bot e ninguém vê).
- **Mascarar:** os dois alertas respeitam `transbordo_destino_externo`. Destino interno (padrão): nome e telefone inteiros. Grupo externo: telefone mascarado. Isso muda o alerta de transferência de hoje, que sempre mascara.

### 4.2 Horário da equipe

- `transbordo_horario_equipe`: grade semanal no mesmo formato das unidades (vários turnos por dia, turno que cruza a meia-noite).
- **Vazio = usa o horário das unidades:** a equipe está disponível quando a empresa não está fora do horário (seção 3.1).
- Exceções por data e pausa não se aplicam à grade própria da equipe nesta fase.

### 4.3 `transferir_humano` fora do horário da equipe

| Passo | O que acontece |
|---|---|
| Balões do agente | Saem normalmente |
| Mensagem ao cliente | Em seguida sai a "Mensagem com a equipe fora do horário" (`transbordo_fora_do_horario`) |
| Alerta à equipe | **Enviado mesmo assim**, com a marca "fora do horário da equipe" |
| Conversa | Marcada como **aguardando atendente** |
| Bot | **Não é pausado**, em nenhum motivo. O cliente continua sendo atendido pelo agente |

- Dentro do horário da equipe, nada muda: pausa o bot, marca aguardando e alerta.
- Variáveis: `{retorno_equipe}` ("amanhã às 9h", "segunda-feira às 9h") e `{horario}` (nome antigo, igual). Texto padrão: `Nossa equipe não está disponível agora.[[ Voltamos {retorno_equipe}.]] Deixei seu recado e, enquanto isso, sigo te ajudando por aqui.`
- A mensagem sai **uma vez** enquanto a conversa estiver aguardando; novas transferências no mesmo período não a repetem.
- A proteção do SOL1 vem antes: transferência por evento ou catering com venda em andamento já vira aviso à equipe, sem esta mensagem.
- `notificar_equipe` fora do horário: o alerta sai normalmente; nenhuma mensagem extra ao cliente.
- Transferências que o próprio gateway faz (erro do agente, entrega incerta) seguem como hoje.
- O pacote informa `equipe_disponivel` (sim/não) e `retorno_equipe`, para o agente não prometer atendimento imediato.

### 4.4 Métricas (`GET /metricas`, bloco novo `transbordo`)

```json
"transbordo": {"transferencias": {"dentro_do_horario": 14, "fora_do_horario": 3}}
```

---

## 5. Contrato único para o Skyone (depois do SOL1 e do R1)

Tudo abaixo é **opcional e compatível com o fluxo atual**: sem nenhuma mudança no Skyone, o atendimento segue igual.

### 5.1 O que o fluxo devolve

Envelope de sempre: `request_id`, `conversation_id`, `status: "success"`, `tipo: "text"`.

| Campo | Tipo | Valores e regras |
|---|---|---|
| `resposta` | lista de textos (ou um texto) | Os balões. Pode vir vazia com `silencio`, `transferir_humano`, `encerrar` e `notificar_equipe` |
| `acao` | texto | `nenhuma`, `silencio`, `transferir_humano`, `encerrar`, `pedido_fechado`, **`notificar_equipe`** |
| `motivo` | texto | Obrigatório com `silencio`. Com `transferir_humano` e `notificar_equipe`, um de: `pediu_humano`, `reclamacao`, `pedido_para_confirmar`, `pagamento`, `restricao_alimentar`, `evento_ou_encomenda`, `catering`, `fora_do_escopo`, `assunto_proibido`. Outro valor não é erro (vira `fora_do_escopo`, com o texto guardado) |
| `solicitacao` | objeto | Só lido com `notificar_equipe` ou `transferir_humano`. Campos abaixo |
| `reacao` | texto com 1 emoji, ou `null` | Um dos emojis de `recursos.emojis_de_reacao`. Fora disso é ignorado |
| `botoes` | objeto | `{"opcoes": [{"id", "titulo", "descricao"?}], "titulo_lista"?}`. Aceito com `nenhuma`, `pedido_fechado` e `notificar_equipe` |
| `nome_cliente` | texto | 2 a 60 caracteres, quando o cliente disser o nome |
| `desfecho`, `uso` | como hoje | |

**`solicitacao`** (todos os campos opcionais; campo inválido é ignorado, sem erro):

| Campo | Tipo | Limite |
|---|---|---|
| `tipo` | texto | `evento`, `catering`, `encomenda`, `orcamento`, `parceria`, `outro` (outro valor vira `outro`) |
| `data` | **texto livre** | até 80 caracteres, como o cliente disse ("25 de dezembro"). Não é data ISO |
| `pessoas` | inteiro | 1 a 100000 (aceita `"50"`) |
| `local` | texto | até 160 |
| `observacao` | texto | até 500 |

Exemplo com tudo:

```json
{"request_id": "…", "conversation_id": "…", "status": "success", "tipo": "text",
 "resposta": ["Fazemos eventos sim! Já avisei nossa equipe.", "Enquanto isso, quer finalizar seu pedido?"],
 "acao": "notificar_equipe", "motivo": "evento_ou_encomenda",
 "solicitacao": {"tipo": "evento", "data": "25 de dezembro", "pessoas": 50, "local": null, "observacao": "Só queria saber se fazem."},
 "reacao": "😊",
 "botoes": {"opcoes": [{"id": "finalizar_pedido", "titulo": "Finalizar pedido"}]}}
```

O `Interpretar Resposta` só precisa **deixar passar** `acao: "notificar_equipe"`, `solicitacao` e `reacao` como vieram. Se ele valida `acao` contra uma lista fechada, `notificar_equipe` entra nela.

### 5.2 O que entra de novo no pacote

| Campo | Onde | Conteúdo |
|---|---|---|
| `fora_do_horario` | raiz | `true` / `false`. Sempre presente |
| `proxima_abertura` | raiz | `{"unidade", "codigo", "dia_semana", "data", "hora"}` ou `null`. Sempre presente |
| `equipe_disponivel` | raiz | `true` / `false`: a equipe humana está no horário dela |
| `retorno_equipe` | raiz | Mesmo formato de `proxima_abertura`, sem `unidade` e `codigo`; `null` se disponível ou desconhecido |
| `recursos` | raiz | `{"botoes": true, "lista": true, "reacoes": true, "emojis_de_reacao": ["❤️", "😊", "👍", "🙏"], "notificar_equipe": true}` — o que está ligado neste agente |
| `contexto_modulos.solicitacoes` | — | Solicitações abertas da conversa (SOL1). Só existe com `notificar_equipe` ligado |
| `mensagens_do_lote[].interacao.substituiu` | — | Ids dos toques trocados (SOL1), quando houve troca |

`recursos` passa a ser o lugar único para o agente saber o que pode usar; a presença de `contexto_modulos.solicitacoes` continua valendo como sinal do `notificar_equipe`.

### 5.3 Para o prompt

- Reagir é opcional e raro: só quando combina (agradecimento, elogio, confirmação). Nunca em reclamação. O gateway limita a 2 por dia por conversa e ignora o que passar.
- Com `fora_do_horario: true`: avisar que a loja está fechada e quando abre (`proxima_abertura`); pode tirar dúvidas e mostrar o cardápio.
- Com `equipe_disponivel: false`: ao transferir, não prometer atendimento imediato; o gateway manda a mensagem de equipe ausente.

---

## 6. Como o painel sabe que saiu do "em breve"

Pelo que já está no contrato do CFG1-a: em `GET {escopo}/atendimento`, cada campo de `esquema.comportamentos` traz `disponivel`. O gateway passa a devolver `true` para:

| Campo | Depois deste bloco |
|---|---|
| `reacoes`, `reacoes_emojis`, `reacoes_max_dia` | `disponivel: true` |
| `fora_do_horario`, `fora_do_horario_mensagem` | `disponivel: true` |
| `transbordo_alerta`, `transbordo_horario_equipe`, `transbordo_fora_do_horario` | `disponivel: true` |
| `fora_do_horario_aceita_agendado` | continua `false` ("em breve") |

O painel não precisa de versão nova para isso: se ele já lê `disponivel` do esquema, os campos se liberam sozinhos. A conferência a pedir ao [PAINEL]: os oito campos aparecem editáveis, e o de pedido agendado continua como "em breve".

O `esquema` também passa a trazer, para os dois textos, a lista de variáveis aceitas (`variaveis`), e para `reacoes_emojis` as opções permitidas (já traz).

---

## 7. Banco, implantação e testes

**Migração aditiva** (próximo número livre):
- `turnos`: o que aconteceu com a reação (`reacao`), e se a transferência caiu fora do horário da equipe.
- `conversas`: até quando o aviso de fora do horário já foi dado; se a mensagem de equipe ausente já saiu.
- `tenant_canais`: número da instância.
- `tenant_config.transbordo_alerta` recebe o valor atual de `whatsapp_alerta_transbordo`, onde estiver vazio.
- Versão 3 do segmento `delivery_restaurante`: reações ligadas com a lista ❤️ 😊 👍 🙏 e `fora_do_horario` conforme a decisão 1.

**Implantação:** um bloco só, por `scripts/implantar.sh` com a trava; depois, mover o agente do By Koji para a versão 3, comparando a configuração efetiva antes e depois.

**Testes**

R1:
- Cada trava, na ordem, com o motivo certo; a primeira que barra vence.
- Terceira reação do dia bloqueada; a do dia seguinte (fuso da empresa) aceita.
- Menos de 3 mensagens do cliente desde a última reação: bloqueada; com 3, aceita; primeira reação da conversa não depende disso.
- `transferir_humano` e motivo `reclamacao` nunca reagem; `silencio`, `encerrar` e `notificar_equipe` reagem.
- Lote só com clique, áudio ou imagem: não reage. Lote com texto e clique: reage no texto.
- A reação sai antes dos balões; falha da uazapi (4xx, 5xx, tempo esgotado) não muda o turno nem a resposta e não é tentada de novo.
- Gravada com o id; a varredura não a trata como atendente; não conta como "bot falou há pouco"; não torna o botão anterior "antigo".
- Turno refeito: só a reação do turno final. Envio de teste: `simulada`. Dois emojis ou texto: ignorado.
- Métricas por emoji e por motivo.

H1-a:
- Fora do horário só com todas as unidades fechadas; unidade sem horário impede; todas pausadas contam; exceção por data conta.
- `atender`: pacote com `fora_do_horario` e `proxima_abertura` nos três casos (aberta, fechada, desconhecida); o agente é chamado.
- `so_avisar`: uma mensagem por período; a segunda mensagem no mesmo período não responde nem chama o agente; depois da abertura e de novo fechamento, avisa de novo.
- `so_avisar` com conversa pausada por atendente, fora do piloto e em envio de teste; com pedido ativo (conforme a decisão 3).
- Variáveis e trecho opcional; texto antigo com `{abre}` continua funcionando.
- Avisos de pedido saem em `so_avisar`.

T1:
- Telefone: formatos aceitos, 12 e 13 dígitos, DDD inválido, número de outro país recusado, número da instância recusado.
- Alerta com telefone inteiro e mascarado conforme `transbordo_destino_externo`, nos dois tipos de alerta.
- Horário da equipe vazio segue as unidades; grade própria com turno que cruza a meia-noite.
- Transferência fora do horário: mensagem ao cliente uma vez, alerta enviado, conversa aguardando, **bot não pausado**, para cada motivo; dentro do horário, tudo como hoje (regressão).
- Métricas dentro e fora.

Gerais:
- Compatibilidade: retorno sem nenhum campo novo se comporta exatamente como hoje.
- `disponivel` no esquema: oito campos `true`, pedido agendado `false`.
- Isolamento entre empresas em tudo (limite diário de reação, período de aviso, horário da equipe).
- Versão 3 do segmento: só mudam os campos previstos.

---

## 8. Decisões que preciso de você

1. **Padrão de fora do horário no segmento de delivery.** Está gravado `so_avisar` (sua decisão 8 do CFG1); agora você escreveu "Atender (padrão do segmento)". Qual vale para a versão 3? Proposta: **`atender`**, e o By Koji herda `atender`. Se ficar `so_avisar`, o By Koji para de chamar o agente quando as 4 unidades fecham.
2. **Reação com `silencio`, `encerrar` e `notificar_equipe`:** permitida (como você definiu agora), revogando a regra antiga que só deixava reagir com `nenhuma` e `pedido_fechado`. Confirma?
3. **`so_avisar` com pedido ativo na conversa:** chamar o agente mesmo assim (proposta), para o cliente conseguir perguntar pelo pedido depois do fechamento?
4. **Mensagens recebidas com a loja fechada, em `so_avisar`:** ficam sem resposta para sempre (como você descreveu), ou o agente deve responder quando a loja abrir? Proposta: sem resposta; o cliente escreve de novo.
5. **Alerta de transbordo do By Koji:** hoje aponta para o número da própria instância (final 9899), que a regra nova recusa. Você cadastra o telefone da equipe pelo painel depois do bloco (proposta), ou me passa o número para eu gravar com auditoria? Até lá, os alertas de transferência e de solicitação do By Koji não chegam a ninguém.
6. **Alerta de transferência com telefone inteiro** quando o destino é interno (hoje sempre mascara): confirma a mudança?
7. **Transferência fora do horário da equipe não pausa o bot em nenhum motivo**, inclusive `pediu_humano` e `reclamacao`: confirmo que é isso? O cliente que pediu uma pessoa continua falando com o agente até a equipe voltar.
8. **Intervalo de 3 mensagens entre reações:** fixo (proposta) ou configurável por agente, como o limite diário?
9. **Um bloco só** (R1 + H1-a + T1 numa implantação), ou três implantações separadas com parada entre elas? Proposta: um só, porque dividem a migração e a versão 3 do segmento.

---

## 9. Decisões do Marco (12/10/2026) — aprovada

1. **Padrão do segmento `delivery_restaurante` para fora do horário:** `atender` (revoga o `so_avisar` do CFG1). Entra na versão 3 do segmento, com a comparação antes e depois.
2. **Reação** permitida com `nenhuma`, `silencio`, `encerrar`, `notificar_equipe` e `pedido_fechado`. Proibida com `transferir_humano` e com motivo `reclamacao`.
3. **`so_avisar` com pedido ativo na conversa:** chama o agente.
4. **Mensagens com a loja fechada (`so_avisar`):** ficam sem resposta.
5. **Telefone da equipe:** o Marco cadastra pelo painel depois do bloco. Enquanto o destino for o número da própria instância, o alerta **não é enviado** e a pendência fica visível no painel e no log.
6. **Telefone inteiro no alerta de transferência** quando o destino é interno: confirmado (igual ao SOL1).
7. **Fora do horário da equipe:** não pausa o bot em nenhum motivo, inclusive `pediu_humano` e `reclamacao`. O pacote leva `equipe_disponivel: false` e `retorno_equipe`.
8. **Intervalo de 3 mensagens entre reações:** fixo.
9. **Um bloco só**, numa implantação, com as três chaves independentes (`reacoes`, `fora_do_horario`, e o horário/telefone do transbordo).

## 10. Implementado (12/10/2026)

Migração 037 (aditiva). Código: `nucleo/reacoes.py`, `nucleo/expediente.py`, `nucleo/pipeline.py`, `agentes/skyone.py`, `modulos/pedidos/avisos.py`, `admin/atendimento.py`. Testes em `tests/test_cfg1c.py`.

### 10.1 Formato exato dos campos novos do pacote

Todos na raiz do pacote e **sempre presentes** (o `Normalizar Entrada` pode contar com eles):

```json
"fora_do_horario": true,
"proxima_abertura": {"unidade": "Pátio Guedala", "codigo": "patio_guedala", "dia_semana": "terça-feira", "data": "2026-10-13", "hora": "12:00"},
"equipe_disponivel": false,
"retorno_equipe": {"dia_semana": "terça-feira", "data": "2026-10-13", "hora": "12:00"},
"recursos": {"botoes": true, "lista": true, "reacoes": true, "emojis_de_reacao": ["❤️", "😊", "👍", "🙏"], "notificar_equipe": true}
```

| Campo | Tipo | Valores |
|---|---|---|
| `fora_do_horario` | booleano | `true` só quando **todas** as unidades ativas estão fechadas agora. Unidade sem horário cadastrado impede o `true` |
| `proxima_abertura` | objeto ou `null` | `null` com a empresa aberta, ou quando não se sabe quando abre. `unidade` = nome; `codigo` = código da unidade; `dia_semana` por extenso, em minúsculas (`segunda-feira` … `domingo`); `data` = `AAAA-MM-DD`; `hora` = `HH:MM` (24 h). Data e hora no fuso da unidade |
| `equipe_disponivel` | booleano | A equipe humana está no horário dela agora. Sem grade própria, acompanha as unidades (`true` quando `fora_do_horario` é `false`) |
| `retorno_equipe` | objeto ou `null` | `null` com a equipe disponível ou sem previsão. Mesmas três chaves de data: `dia_semana`, `data`, `hora` (sem `unidade` nem `codigo`) |
| `recursos.botoes`, `recursos.lista` | booleano | Botões e listas ligados no agente **e** canal que os suporta |
| `recursos.reacoes` | booleano | Reações ligadas no agente **e** canal que as suporta |
| `recursos.emojis_de_reacao` | lista de textos | Os emojis que o agente pode devolver em `reacao`. Lista vazia quando `reacoes` é `false` |
| `recursos.notificar_equipe` | booleano | A ação `notificar_equipe` está ligada |

- Com a empresa aberta: `"fora_do_horario": false, "proxima_abertura": null, "equipe_disponivel": true, "retorno_equipe": null`.
- `contexto_modulos.solicitacoes` (SOL1) continua existindo só com `notificar_equipe` ligado.

### 10.2 O que o fluxo devolve — `reacao`

Campo opcional na raiz do retorno: **texto com um emoji** (`"reacao": "❤️"`) ou `null`/ausente. Texto, número, dois emojis ou objeto são ignorados, sem erro. O resto do contrato é o da seção 5.

Antes deste bloco o gateway já ignorava `reacao` (o leitor só lê os campos do contrato), então o `Interpretar Resposta` pode repassá-la a qualquer momento.

### 10.3 Como ficou cada recurso

**Reações**
- Travas na ordem: `desligado` → `emoji_fora_da_lista` → `limite_diario` → `intervalo` → `acao_ou_motivo` → `sem_texto`; depois `canal`, `nao_permitido` (conversa pausada ou fora do piloto), envio de teste (`simulada`) e `falha_provedor`.
- Cada turno guarda `{"emoji", "resultado": "aplicada" | "simulada" | "bloqueada", "motivo"}` em `turnos.reacao`.
- A reação é marcada no turno antes de falar com o provedor: se o processo cair no meio, a retomada não reage de novo.
- Fica no histórico como mensagem do bot do tipo `reacao`; não vai ao agente como fala, não conta como "bot falou há pouco" e não torna o botão anterior "antigo".
- Só no caminho da fila durável (o que está em produção).

**Fora do horário**
- `atender`: o agente é chamado; os campos da 10.1 dizem a situação.
- `so_avisar`: mensagem enviada uma vez por período fechado (origem `sistema`, marca `fora_do_horario`), sem chamar o agente; o turno fica com `acao: silencio` e motivo `fora_do_horario`. Quando a empresa reabre, o período é zerado.
- Com pedido ativo na conversa, o agente é chamado mesmo em `so_avisar`.

**Transbordo**
- Destino: `transbordo_alerta` (com herança) e, na falta, o número antigo da empresa. Validado como telefone brasileiro (`55` + DDD + 8 ou 9 dígitos); o número da própria instância é recusado ao salvar (`numero_da_instancia`).
- O número da instância é lido do campo `owner` de cada evento da uazapi e guardado no canal. **Até o primeiro evento chegar depois da implantação, o gateway ainda não o conhece.**
- Destino igual ao da instância: o alerta não é enviado; fica o registro `transbordo.alerta_pendente` na auditoria da empresa, um aviso no log, e `GET {escopo}/atendimento` devolve `pendencias: [{"campo": "comportamentos.transbordo_alerta", "motivo": "numero_da_instancia"}]`.
- Transferência fora do horário da equipe: balões do agente, depois a mensagem de equipe ausente (uma vez enquanto a conversa aguarda), alerta com a linha "Fora do horário da equipe", conversa aguardando e **bot sem pausa**. `turnos.equipe_fora_do_horario` registra dentro ou fora.

### 10.4 Painel

- `esquema.comportamentos.<campo>.disponivel` agora é `true` para `reacoes`, `reacoes_emojis`, `reacoes_max_dia`, `fora_do_horario`, `fora_do_horario_mensagem`, `transbordo_alerta`, `transbordo_horario_equipe`, `transbordo_fora_do_horario`. Continua `false` só em `fora_do_horario_aceita_agendado`.
- `fora_do_horario_mensagem` e `transbordo_fora_do_horario` trazem `variaveis` no esquema. Variável fora da lista → 400 com `motivo: variavel_desconhecida`. Os dois textos passam pela análise de instrução de sistema ao publicar.
- `transbordo_alerta` aceita o número com `+`, espaços, parênteses e traço; o gateway guarda só os dígitos.
- Campo novo na resposta do `GET`: `pendencias`.
- `GET /metricas` ganhou `reacoes`, `fora_do_horario` e `transbordo`.
