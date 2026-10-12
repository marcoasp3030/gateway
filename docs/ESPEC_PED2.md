# PED2 — Horário do pedido validado e categorias do cardápio

**Situação:** aprovado em 12/10/2026 com as decisões da seção 7 e implementado (seções 8 a 10). Onde as seções 2 a 6 divergem das decisões, **valem as seções 7 a 10**.
**Data:** 12/10/2026. Produção: `gateway:e5e62c1`, schema 37.

---

## 1. O que o teste mostrou (piloto final 1513, 11/10)

- Às 17:56 o cliente tocou em "Escolher horário" e não disse hora nenhuma. O agente chamou `definir_entrega` com `horario` e `unidade`, e o carrinho ficou com o texto **"hoje às 19h"** (a hora em que a unidade abria).
- Às 22:03 o carrinho ainda dizia "hoje às 19h", três horas no passado. O agente seguiu com esse horário até o fim.
- Às 22:06 o cliente tocou em "Confirmar pedido". O agente respondeu "Acabei de fechar seu pedido…", mas **nenhuma chamada a `fechar_pedido` chegou a ser registrada** e nenhum pedido foi criado: o carrinho continua aberto, com 1 Baterá Shake.
- O cliente pediu "o cardápio completo" e recebeu três nomes de sushi e uma lista de categorias de cabeça: o agente não tem como listar categorias.

**Por que o gateway deixou passar:** `horario` é um **texto livre** de até 100 caracteres. O gateway o guarda como veio e não sabe que "hoje às 19h" é uma hora, muito menos que já passou.

---

## 2. Horário agendado como dado, não como texto

### 2.1 Campo novo `agendado_para`

`definir_entrega` ganha um campo estruturado; `horario` continua existindo como texto que o cliente vê.

```json
{"modalidade": "retirada", "agendado_para": "2026-10-12T19:00", "horario": "hoje às 19h"}
```

| Campo | Regra |
|---|---|
| `agendado_para` | Data e hora locais da unidade, `AAAA-MM-DDTHH:MM` (aceita também com fuso, `2026-10-12T19:00:00-03:00`). `null` limpa o agendamento ("o quanto antes") |
| `horario` | Texto livre, como hoje. Se `agendado_para` vier e `horario` não, o gateway escreve o texto ("hoje às 19h", "amanhã às 12h") |

**Rede de proteção para o prompt de hoje (decisão 1):** quando vier só `horario` com uma hora reconhecível — "hoje às 19h", "amanhã 12h", "19:00", "às 20h30" —, o gateway deriva o `agendado_para` (sem dia, vale hoje). Texto sem hora ("o quanto antes", "assim que der") segue sem agendamento. Assim a validação vale mesmo antes de o prompt mudar.

### 2.2 Recusas novas (422, em `erro.detalhes.motivo`)

| Motivo | Onde | Quando | `detalhes` |
|---|---|---|---|
| `horario_passado` | `definir_entrega`, `fechar_pedido` | `agendado_para` é anterior a agora (tolerância de 5 minutos) | `agora`, `agendado_para`, `proxima_abertura` |
| `horario_fora_funcionamento` | `definir_entrega`, `fechar_pedido` | A unidade não está aberta naquele dia e hora (grade e exceções por data) | `agendado_para`, `aberta_no_dia` (faixas daquele dia), `proxima_abertura` |

- `proxima_abertura` no formato do pacote: `{"unidade", "codigo", "dia_semana", "data", "hora"}` — a primeira abertura a partir do horário pedido (ou de agora, em `horario_passado`).
- `fechar_pedido` **confere de novo**: o horário podia ser válido quando foi definido e já ter passado. Foi exatamente o caso do teste.
- Limite: agendamento até 7 dias à frente (`horario_muito_distante`). (Decisão 3.)
- Unidade sem horário cadastrado: só vale `horario_passado`.
- A resposta de recusa traz o carrinho atualizado, como as demais.
- Pedido **sem** agendamento com a unidade fechada continua sendo assunto da chave `pedido_com_unidade_fechada`, do G4-b. Aqui só se valida o horário que foi pedido.

### 2.3 Carrinho e pacote

`contexto_modulos.pedidos.carrinho` (e toda resposta de Skill com carrinho) ganha:

```json
"horario": "hoje às 19h",
"agendado_para": "2026-10-12T19:00:00-03:00",
"horario_vencido": true
```

- `horario_vencido`: `true` quando há `agendado_para` e ele já passou; `false` nos demais casos. Sempre presente.
- Com `horario_vencido: true`, `pode_fechar` vira `false` e `faltando` ganha `"horario"`: o agente precisa perguntar o horário de novo.
- Esvaziar e expirar o carrinho limpam o agendamento (já limpam o `horario`).
- O pedido fechado guarda `agendado_para` (a coluna já estava prevista no G4-b e entra aqui).

---

## 3. `fechar_pedido` sempre devolve o número

Hoje o sucesso já traz o número dentro de `pedido`:

```json
{"ok": true, "pedido": {"numero": "55978", "status": "aguardando_loja", "…": "…"}, "carrinho": {"…": "…"}}
```

Mudanças:

- `numero` também na **raiz** da resposta (`"numero": "55978"`), para o agente não depender de ler dentro de `pedido`. Teste que garante número não vazio em toda resposta de sucesso.
- **Proteção no gateway (decisão 2):** quando o agente responder ao cliente que o pedido foi fechado — `acao: "pedido_fechado"`, ou `desfecho: "pedido_feito"` — e **não houver pedido criado naquele turno** nesta conversa, o gateway registra a divergência (`pedido_anunciado_sem_fechar` nas métricas e na auditoria). Proponho só registrar e alertar, sem bloquear a resposta; a alternativa é segurar os balões e mandar o texto de falha.
- **Registro de todas as chamadas de Skill**, inclusive as recusadas antes de chegar à regra (corpo inválido, conversa não encontrada) e as consultas (`buscar_catalogo`, `ver_carrinho`, `info_unidades`): rota, resultado e tempo, sem conteúdo. Hoje só ficam registradas as Skills que mudam carrinho ou pedido, e por isso não dá para afirmar se, no teste, o agente não chamou `fechar_pedido` ou chamou com o corpo errado.

---

## 4. Categorias do cardápio

Hoje `buscar_catalogo` devolve `categoria` em cada item, mas não há como listar as categorias nem pedir "os itens desta categoria". No Pátio Guedala são 18 categorias e 205 itens.

### 4.1 Skill nova `listar_categorias`

`GET /api/v1/catalogo/categorias?conversation_id=…&unidade=patio_guedala` — escopo `ferramentas:catalogo`.

```json
{"unidade": "patio_guedala", "total_itens": 193,
 "categorias": [{"categoria": "Baterá", "itens": 6}, {"categoria": "Sashimis", "itens": 14}, {"categoria": "Coquetéis autorais", "itens": 11}]}
```

- Conta só o que pode ser pedido: item ativo, com preço e que não seja "não entrega".
- Ordem: a do cardápio (ordem das categorias, quando o G5 a trouxer); até lá, alfabética.
- `unidade` obrigatória; unidade inexistente → 404. Categoria sem item vendável não aparece.

### 4.2 Itens de uma categoria

`GET /api/v1/catalogo/itens?conversation_id=…&unidade=patio_guedala&categoria=Baterá&limite=10&pagina=1`

```json
{"unidade": "patio_guedala", "categoria": "Baterá", "total": 6, "pagina": 1, "tem_mais": false,
 "itens": [{"item_id": 812, "codigo": "GUE-0412", "nome": "Baterá Shake", "descricao": "…", "preco": 58.0, "alcoolico": false, "categoria": "Baterá"}]}
```

- `limite` de 1 a 10 (padrão 10); `pagina` a partir de 1.
- `categoria` comparada sem acento e sem maiúsculas ("batera" acha "Baterá"). Categoria inexistente → lista vazia, com as categorias parecidas em `sugestoes`.
- Só itens vendáveis, sempre com preço. O `item_id` é o mesmo usado em `adicionar_item`.
- `buscar_catalogo` não muda.

---

## 5. Banco, bloco e testes

Migração aditiva: `carrinhos.agendado_para` e `pedidos.agendado_para`; tabela de registro das chamadas de Skill ganha as consultas e as recusas de entrada. Sem mudança de configuração.

Um bloco só (PED2), com parada e relatório.

Testes:
- `agendado_para` no passado → `horario_passado`, em `definir_entrega` e em `fechar_pedido`; cinco minutos de tolerância.
- Horário válido ao definir e vencido ao fechar → `fechar_pedido` recusa e nenhum pedido é criado.
- Fora do funcionamento naquele dia (antes de abrir, depois de fechar, dia fechado, exceção por data) → `horario_fora_funcionamento` com `proxima_abertura`; dentro → aceito; turno que cruza a meia-noite.
- Texto com hora ("hoje às 19h", "amanhã 12h", "19:00") deriva o agendamento; texto sem hora não.
- Unidade sem horário cadastrado; mais de 7 dias à frente.
- Pacote e carrinho: `agendado_para` e `horario_vencido` sempre presentes; vencido tira `pode_fechar` e põe `horario` em `faltando`.
- `fechar_pedido` com sucesso sempre traz `numero` na raiz e em `pedido`.
- Anúncio de pedido fechado sem pedido criado é registrado.
- Categorias: só itens vendáveis; contagem certa; comparação sem acento; paginação; categoria inexistente; unidade de outra empresa → 404; escopo errado → 403.
- Regressão: carrinho sem horário e com "o quanto antes" continua como hoje.
- Isolamento entre empresas.

---

## 6. Decisões que preciso de você

1. **Ler a hora do texto livre** quando o agente mandar só `horario` ("hoje às 19h" → 19:00 de hoje): incluir (proposta), para a validação valer já com o prompt atual, ou exigir sempre o campo `agendado_para`?
2. **Agente anuncia pedido fechado sem ter fechado:** só registrar e alertar (proposta), ou o gateway segura a resposta e manda o texto de falha?
3. **Limite de agendamento:** até 7 dias à frente serve?
4. **Carrinho com horário vencido:** `pode_fechar: false` até o cliente escolher outro horário (proposta), ou o gateway limpa o horário sozinho e o pedido vira "o quanto antes"?
5. **Registro de todas as chamadas de Skill** (consultas e recusas de entrada, sem conteúdo): incluir neste bloco?
6. **Categorias:** duas rotas novas (`listar_categorias` e itens por categoria), como proposto, ou prefere um parâmetro `categoria` na busca que já existe?
7. **Onde entra na fila:** antes do G4-b (proposta, porque é pedido se perdendo), ou junto dele?

---

## 7. Decisões do Marco (12/10/2026)

1. **Hora em texto livre:** o gateway interpreta quando vier só `horario` ("hoje às 19h", "19h", "19:30", "amanhã ao meio-dia", "o quanto antes"). O que não der para interpretar é recusado com `horario_invalido`. `agendado_para` continua aceito.
2. **Pedido anunciado sem fechar:** SEGURAR. Detecta `desfecho: pedido_feito`, `acao: pedido_fechado` ou texto de fechamento sem `fechar_pedido` de sucesso no mesmo turno. (a) refaz o turno 1 vez com aviso no pacote; (b) se repetir, não envia o texto do agente: envia o texto fixo com o botão "Confirmar pedido" e alerta. Métrica `anuncios_falsos_segurados`.
3. **Agendamento:** até 7 dias à frente.
4. **Horário vencido:** `pode_fechar: false` até o cliente escolher outro horário.
5. **Registro de todas as chamadas de Skill** (inclusive as recusadas na entrada): entra neste bloco.
6. **Categorias:** sem rotas novas; parâmetros `categorias=true` e `categoria=<nome>` na busca que já existe.
7. **`numero` na raiz** da resposta de sucesso de `fechar_pedido`: sim.

Prioridade 1, antes do G4-b.

## 8. Contrato final dos motivos de horário

Todos vêm em `erro.detalhes.motivo`, HTTP 422, com `erro.detalhes.campo = "horario"` e o `carrinho` atual ao lado de `erro`. A recusa nunca altera o carrinho.

| Motivo | Onde | Quando | Campos a mais em `erro.detalhes` |
|---|---|---|---|
| `horario_passado` | `definir_entrega`, `fechar_pedido` | O horário marcado é anterior a agora (5 minutos de tolerância) | `agendado_para`, `agora`, `aberta_agora` (true/false/null), `proxima_abertura` |
| `horario_fora_funcionamento` | `definir_entrega`, `fechar_pedido` | A unidade não funciona naquele dia e hora (grade e exceções por data) | `agendado_para`, `agora`, `aberta_no_dia` (faixas daquele dia), `proxima_abertura` |
| `horario_invalido` | `definir_entrega` | `horario` sem hora reconhecível, ou `agendado_para` que não é data e hora | — |
| `horario_muito_distante` | `definir_entrega` | Mais de 7 dias à frente | `agendado_para`, `agora`, `max_dias` |

```json
{"erro": {"codigo": "VALIDATION_ERROR", "mensagem": "O horário marcado já passou. Pergunte ao cliente outro horário e chame definir_entrega de novo.",
          "detalhes": {"campo": "horario", "motivo": "horario_passado",
                       "agendado_para": "2026-10-12T19:00:00-03:00", "agora": "2026-10-12T22:06:00-03:00",
                       "aberta_agora": true, "proxima_abertura": null}},
 "carrinho": {"horario": "hoje às 19h", "agendado_para": "2026-10-12T19:00:00-03:00", "horario_vencido": true, "pode_fechar": false, "faltando": ["horario"], "…": "…"}}
```

```json
{"erro": {"codigo": "VALIDATION_ERROR", "mensagem": "A unidade não funciona nesse dia e horário. Ofereça um horário dentro do funcionamento (veja proxima_abertura).",
          "detalhes": {"campo": "horario", "motivo": "horario_fora_funcionamento",
                       "agendado_para": "2026-10-12T18:30:00-03:00", "agora": "2026-10-12T17:56:00-03:00",
                       "aberta_no_dia": [["19:00", "23:00"]],
                       "proxima_abertura": {"unidade": "Pátio Guedala", "codigo": "patio_guedala", "dia_semana": "segunda-feira", "data": "2026-10-12", "hora": "19:00"}}},
 "carrinho": {"…": "…"}}
```

Regras:

- `proxima_abertura` tem o formato do pacote (`unidade`, `codigo`, `dia_semana`, `data`, `hora`). Em `horario_fora_funcionamento` é a primeira abertura a partir do horário pedido; em `horario_passado`, a partir de agora (nula se a unidade está aberta agora ou não tem horário cadastrado).
- Unidade sem horário cadastrado: só vale `horario_passado`.
- Pausa manual da unidade não entra nesta conferência (vale para agora, não para um horário marcado adiante).
- A conferência acontece em `definir_entrega` só quando `horario` ou `agendado_para` vêm na chamada; mudar modalidade ou endereço com horário vencido não é barrado. `fechar_pedido` confere sempre.
- O que o gateway lê do texto: "hoje/amanhã/depois de amanhã", dia da semana, "dia 15", "15/10"; "19h", "19h30", "19:30", "20 horas", "às 19", "7 da noite", "meio-dia", "meia-noite"; "daqui a 40 minutos", "em 2 horas". Sem dia, vale hoje. "o quanto antes", "agora", "já", "assim que possível" e texto vazio = sem agendamento. O resto ("mais tarde", "à noite") = `horario_invalido`.
- Carrinho anterior ao PED2 (só com o texto) é lido em relação ao dia em que foi escrito.

## 9. O que foi implementado

- **Banco (migração 038, aditiva):** `carrinhos.agendado_para`, `pedidos.agendado_para`, `turnos.anuncio_falso`, tabela `skill_registro`.
- **Carrinho e pacote:** `agendado_para` (ISO no fuso da unidade) e `horario_vencido` sempre presentes; vencido → `pode_fechar: false` e `"horario"` em `faltando`. Esvaziar limpa o agendamento. O pedido guarda `agendado_para`.
- **`fechar_pedido`:** `numero` na raiz e em `pedido.numero`.
- **Busca do catálogo:** `categorias=true` → `{"unidade","total_itens","categorias":[{"categoria","qtd_itens"}]}`; `categoria=<nome>` → `{"unidade","categoria","categorias_encontradas","pagina","por_pagina":10,"total","tem_mais","itens":[…]}` (com `sugestoes` quando a categoria não existe). Só itens que podem ser pedidos (ativos, com preço, não exclusivos do salão). Sem `unidade`, vale a do carrinho aberto; sem carrinho, 400 com `campo: unidade`. Sem os parâmetros novos, a resposta é a de sempre.
- **Registro de Skills:** toda chamada às rotas das Skills grava em `skill_registro` o nome da Skill, o método, os nomes dos campos recebidos, o status HTTP, o motivo (`ok`, o motivo da regra, `campo:<nome>` ou o código do erro) e o tempo. Nunca valores. Consulta: `GET /admin/v1/tenants/{id}/skills/registro?conversation_id=&skill=&limite=&cursor=`. Chamada com chave inválida (sem empresa identificada) fica só no log. Segue a retenção da empresa.
- **Pedido anunciado sem fechar** (só na fila durável, só para agente com o módulo de pedidos, só em turno de cliente):
  - Sinal: `acao: pedido_fechado`, `desfecho: pedido_feito`, texto que afirma o fechamento ("fechei", "acabei de fechar", "pedido feito/registrado/confirmado/enviado…") ou número de pedido citado. Pergunta e frase no futuro ("vou fechar", "para confirmar", "assim que") não contam.
  - Com `acao` `transferir_humano`, `notificar_equipe` ou `silencio`, o texto não conta ("seu pedido foi enviado para a equipe confirmar").
  - Não é anúncio falso quando: houve `fechar_pedido` de sucesso no turno (contando desde a primeira tentativa, se foi refeito); o texto cita o número de um pedido que existe na conversa; ou não há carrinho aberto com itens e a conversa tem pedido criado nas últimas 12 h (é conversa sobre o pedido real, como o "obrigado" depois de fechar).
  - 1ª vez: a resposta é descartada (`turnos.anuncio_falso = "refeito"`) e o turno é refeito com `aviso_do_gateway` no pacote.
  - Repetiu: `turnos.anuncio_falso = "segurado"`; o cliente recebe "Opa, tive um probleminha para registrar seu pedido. Pode tocar em Confirmar de novo?" com o botão `confirmar_pedido` ("Confirmar pedido"); o que o agente respondeu fica em `turnos.resposta.segurado`; auditoria `pedido.anuncio_segurado`; log e alerta no Telegram (empresa, agente, turno e os 4 últimos dígitos da conversa — sem telefone inteiro, nome ou texto).
  - Métricas: `anuncios_falsos_refeitos` e `anuncios_falsos_segurados` em `GET /admin/v1/tenants/{id}/metricas`.
- **Pacote:** campo novo `aviso_do_gateway` (texto ou nulo), sempre presente.

## 10. Limites conhecidos

- A detecção por texto é por padrões: uma frase fora deles ("está tudo encaminhado com a cozinha") passa. Os sinais estruturados (`acao`, `desfecho`) não dependem do texto.
- Carrinho novo em aberto + o agente falando de um pedido antigo sem citar o número ("seu pedido já foi confirmado") é tratado como anúncio: refaz uma vez e, se repetir, segura. Citar o número evita.
- No modo sem fila durável (`FILA_DURAVEL=off`) a trava do anúncio não roda. Produção usa a fila durável.
