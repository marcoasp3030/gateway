# PED2 — Horário do pedido validado e categorias do cardápio

**Situação:** especificação para aprovação. **Nada implementado.**
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
