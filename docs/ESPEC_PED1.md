# PED1 — Ciclo de status do pedido e avisos ao cliente

**Situação:** especificação para aprovação. **Nada implementado.** Entra depois do UX3.1 e antes do B3.
**Data:** 11/10/2026. Base: produção em `gateway:dd6f93b`, schema 29.

Este documento tem três partes: o que existe hoje (seções 1 e 2), a proposta (seções 3 a 9) e o que precisa de decisão (seção 10). O contrato das rotas para o painel está na seção 5; os textos padrão, na seção 4.3.

---

## 1. Levantamento — pedidos

### 1.1 Status e transições que existem hoje

| Status | Como se chega | Quem | O que acontece junto |
|---|---|---|---|
| `aguardando_loja` | `fechar_pedido` (Skill) | agente | Aviso à unidade (WhatsApp de pedidos, se cadastrado), webhook `pedido.criado`, auditoria |
| `confirmado` | Painel (`POST .../confirmar`) ou "OK 12345" vindo do WhatsApp da unidade | unidade | Aviso ao cliente, webhook `pedido.confirmado`, auditoria |
| `recusado` | Painel (`POST .../recusar`, motivo opcional) ou "NÃO 12345 motivo" | unidade | Aviso ao cliente, itens voltam ao carrinho (se o cliente não abriu outro), webhook, auditoria |
| `cancelado` | `cancelar_pedido` (Skill), só enquanto `aguardando_loja` | cliente | Webhook `pedido.cancelado`, auditoria. **Sem aviso** (o agente responde na hora) |

- Só existem essas quatro situações. Depois de `confirmado` o pedido não anda mais: não há preparo, saída nem conclusão.
- Quem decide primeiro vale; a segunda tentativa recebe 409 com o status atual.
- O pedido guarda um único `decidido_em` / `decidido_por` / `motivo`. **Não há linha do tempo.**
- Em produção hoje: 2 `aguardando_loja`, 1 `confirmado`, 1 `cancelado`.

### 1.2 Aviso ao cliente hoje

- Sai só em `confirmado` e `recusado`, gravado na caixa `avisos_internos` na mesma transação da decisão e enviado pelo agendador.
- Um aviso por pedido e status (chave `numero:status`): nunca sai duas vezes. Se o envio falha, **não é reenviado** e fica como `falhou`.
- Textos fixos no código (não editáveis):

| Status | Texto |
|---|---|
| `confirmado` | `Seu pedido nº {numero} foi confirmado pela unidade {unidade}. ✅` |
| `recusado` | `Seu pedido nº {numero} não pôde ser aceito pela unidade {unidade}.` + `Motivo: {motivo}` (se houver) + `Os itens voltaram para o seu carrinho, caso queira ajustar e pedir de novo.` |

- **Problema encontrado:** o aviso usa o mesmo caminho das respostas do bot, então **não sai quando o bot está pausado por atendente** (fica `descartado`). O único aviso ao cliente já registrado em produção terminou assim. O PED1 corrige isso (seção 4.1).
- O aviso entra no histórico como mensagem do bot, sem marca de que foi automático.

### 1.3 Rotas de pedido que o painel já tem

`GET /tenants/{t}/pedidos` (filtros `status`, `unidade`; cursor), `GET .../pedidos/{numero}`, `POST .../confirmar`, `POST .../recusar`, `GET /tenants/{t}/avisos`. Faltam: período, busca, linha do tempo, contagem por unidade, demais transições, textos.

---

## 2. Levantamento — conversas e contatos para o painel

### 2.1 O que `GET /tenants/{t}/conversas` devolve hoje

`conversation_id`, `agente_id`, `canal_id`, `canal` (o tipo), `nome`, `telefone`, `bot_pausado_ate`, `atualizado_em`, `aguardando_atendente_desde`, `aguardando_motivo`, `encerrada_em`, `desfecho`, `limitado_ate`, `status` (`ativa`/`pausada`), `ultima_mensagem`, `ultima_em`.

`GET /tenants/{t}/contatos` devolve `id`, `telefone`, `email`, `nome`, `anonimizado_em`.

### 2.2 Por que o painel não mostra direito nome, WhatsApp e canal

| Campo | Situação hoje | O que falta |
|---|---|---|
| **Nome** | Vem só do nome de perfil do WhatsApp (`senderName`), e **só é gravado na primeira mensagem** da conversa; nunca é atualizado. Em produção, 5 de 103 conversas do By Koji têm nome. Não existe "nome informado pelo cliente" nem "nome de cadastro" | Três origens separadas, regra de prioridade e um campo pronto para exibir |
| **Telefone** | Só dígitos, sem `+` (`5511947281513`). Em canal API/site o campo guarda o identificador que o sistema de origem mandou, que pode não ser telefone | Campo em E.164 e indicação de quando não é telefone |
| **Canal** | Vem o id e o tipo. **Não vem o nome do canal** (ex.: "WhatsApp Loja") | Nome do canal e ícone sugerido |
| **Unidade preferida** | Existe na conversa, **não é devolvida** na lista | Devolver |
| **Última mensagem** | Vem texto e hora. Não diz de quem foi (cliente, bot, atendente) nem o tipo (áudio, imagem) | Direção, origem e tipo |
| **Estado** | O painel precisa montar a partir de quatro campos (`bot_pausado_ate`, `aguardando_atendente_desde`, `encerrada_em`, `status`) | Um campo único `estado` |
| **Ordem e paginação** | Ordenada pelo identificador da conversa, não pela mais recente | Ordenar por última atividade |
| **Papéis** | O gateway não conhece papéis: recebe a chave do painel e o `X-Actor` (texto livre). Quem filtra o que cada pessoa vê é o painel | Ver seção 7 |

Tipos de canal que existem: `uazapi`, `meta`, `site`, `email`, `teams`, `luxbrain`, `api`. **Não existe tipo `test`**; conversas de simulação entram por canal `api`. Hoje há 1 canal `uazapi` e 5 `api`.

---

## 3. Máquina de status

```
aguardando_loja → confirmado → em_preparo → pronto_retirada (só retirada) → concluido
                                          → saiu_entrega    (só entrega)  → concluido
aguardando_loja → recusado
qualquer um antes de concluido → cancelado (motivo obrigatório)
```

| Regra | Como fica |
|---|---|
| Andar para frente | Permitido, inclusive pulando etapas (ex.: `aguardando_loja` → `em_preparo`, `confirmado` → `concluido`) |
| Voltar | Nunca. 409 |
| `pronto_retirada` | Só pedido de retirada. Em pedido de entrega: 409 `modalidade_incompativel` |
| `saiu_entrega` | Só pedido de entrega (`delivery`). Em retirada: 409 `modalidade_incompativel` |
| `recusado` | Só a partir de `aguardando_loja`. Motivo opcional (como hoje). Os itens voltam ao carrinho (como hoje) |
| `cancelado` | A partir de qualquer status que não seja final. **Motivo obrigatório** quando vem do painel (3–300 caracteres). Os itens **não** voltam ao carrinho |
| Finais | `concluido`, `recusado`, `cancelado`: nada mais muda |
| Mesmo status de novo | 409 (o painel recarrega e mostra o atual) |
| Transição inválida | **409** `{"erro": {"codigo": "CONFLICT", "detalhes": {"campo": "status", "status": "<atual>", "motivo": "<regra>"}}}` |
| Concorrência | O pedido é travado na transição; duas pessoas clicando ao mesmo tempo → a primeira vale, a segunda recebe 409 |

- `motivo` do 409: `status_final`, `nao_pode_voltar`, `mesmo_status`, `modalidade_incompativel`, `recusa_so_aguardando`.
- Pular etapas grava **uma** linha na linha do tempo (de → para), e avisa só o status de chegada.
- **Cliente cancelando pelo agente** (`cancelar_pedido`): proposta de manter como hoje, só em `aguardando_loja`. Depois de confirmado, o agente transfere para a unidade. (Decisão 2, seção 10.)
- **WhatsApp da unidade**: "OK 12345" e "NÃO 12345 motivo" continuam valendo. Comandos para as demais etapas ficam fora deste bloco.
- **Tempo estimado:** na transição para `confirmado`, campo opcional `previsao_min` (inteiro, 1–600). Fica gravado no pedido e entra no aviso como `{previsao}`. Aceito também quando se pula direto para `em_preparo`.
- Webhooks de saída ganham `pedido.em_preparo`, `pedido.pronto_retirada`, `pedido.saiu_entrega`, `pedido.concluido`, no mesmo formato dos atuais.

---

## 4. Avisos ao cliente

### 4.1 Regras

| Ponto | Regra |
|---|---|
| Quando | A cada transição para um status com aviso ligado |
| Padrão | Todos ligados, **menos `em_preparo`** |
| Transacional | Sai **mesmo com o bot pausado** por atendente ou aguardando atendente. Não retoma o bot nem muda a pausa |
| Piloto e teste | Respeita envio de teste (`send_mode=dry` → não sai, fica `simulado`) e a lista do piloto (contato fora da lista → `descartado`) |
| Histórico | Gravado como mensagem de saída com origem **`sistema`** e marca `aviso_pedido: {numero, status}`. O agente vê o texto no histórico e sabe o que o cliente já recebeu |
| Uma vez só | Um aviso por pedido e status. Como não se volta de status, a mesma transição nunca avisa duas vezes, nem em clique duplo nem em reprocessamento |
| Falha | A transição **não é desfeita**. O aviso fica `falhou` com o tipo do erro, aparece na linha do tempo e no detalhe do pedido. Não é reenviado sozinho |
| Reenvio manual | `POST .../avisos/{status}/reenviar`: só para aviso `falhou` e só enquanto o pedido ainda está naquele status (Decisão 4) |
| Cancelado pelo cliente | Sem aviso (o agente responde na hora) |
| Conversa encerrada | O aviso sai normalmente; não reabre a conversa |
| Canal | Só canais com envio (`uazapi` hoje). Em canal `api`/`site`, o aviso fica `simulado` e o sistema de origem acompanha pelo webhook de saída |
| Varredura | O aviso é gravado com o id do provedor, para a varredura não confundir com atendente e pausar o bot |

### 4.2 Variáveis

| Variável | Conteúdo | Se estiver vazia |
|---|---|---|
| `{numero}` | Número do pedido | nunca vazia |
| `{unidade}` | Nome da unidade | código da unidade |
| `{previsao}` | "cerca de 40 minutos" (de `previsao_min`; a partir de 60: "cerca de 1 hora", "cerca de 1h30") | vazia |
| `{motivo}` | Motivo da recusa ou do cancelamento | vazia |
| `{nome_cliente}` | Primeiro nome do nome de exibição (seção 6); nunca o telefone | vazia |

**Trecho opcional:** o que estiver entre `[[` e `]]` some inteiro quando alguma variável dentro dele estiver vazia. Serve para não sair "Previsão: ." ou "Olá, !". Variável desconhecida no texto → 400 ao salvar. Texto de 1 a 1000 caracteres.

### 4.3 Textos padrão

| Status | Ligado | Texto padrão |
|---|---|---|
| `confirmado` | sim | `Seu pedido nº {numero} foi confirmado pela unidade {unidade}. ✅[[ Previsão: {previsao}.]]` |
| `em_preparo` | **não** | `Seu pedido nº {numero} já está sendo preparado. 🍣` |
| `pronto_retirada` | sim | `Seu pedido nº {numero} está pronto para retirada na unidade {unidade}. 🛍️` |
| `saiu_entrega` | sim | `Seu pedido nº {numero} saiu para entrega. 🛵` |
| `concluido` | sim | `Pedido nº {numero} concluído. Obrigado pela preferência[[, {nome_cliente}]]! 🙏` |
| `recusado` | sim | `Seu pedido nº {numero} não pôde ser aceito pela unidade {unidade}.[[ Motivo: {motivo}.]] Os itens voltaram para o seu carrinho, caso queira ajustar e pedir de novo.` |
| `cancelado` | sim | `Seu pedido nº {numero} foi cancelado pela unidade {unidade}.[[ Motivo: {motivo}.]]` |

Os textos de `confirmado` e `recusado` são os de hoje, só com a previsão acrescentada.

---

## 5. Contrato das rotas `/admin/v1` (para o painel)

Todas exigem a chave do painel; as de escrita exigem `X-Actor`. Só rede interna, como hoje. Erros no formato de sempre (`{"erro": {"codigo", "mensagem", "detalhes"}}`).

### 5.1 Listar pedidos

`GET /tenants/{t}/pedidos`

| Parâmetro | Regra |
|---|---|
| `status` | Um ou vários separados por vírgula |
| `unidade` | Código da unidade |
| `modalidade` | `delivery` ou `retirada` |
| `desde`, `ate` | Data/hora com fuso, sobre `criado_em` |
| `busca` | Número do pedido, nome do cliente ou telefone (só dígitos são comparados no telefone); 2–100 caracteres |
| `limite` | 1–100 (padrão 50) |
| `cursor` | O `proximo_cursor` da página anterior. Ordem: mais recente primeiro |

```json
{"itens": [{
  "id": 41, "numero": "59859", "status": "em_preparo", "modalidade": "retirada",
  "unidade": "patio_guedala", "unidade_nome": "Pátio Guedala",
  "cliente": {"nome": "Marco", "telefone": "+5511947281513", "canal": {"id": "can_…", "tipo": "uazapi", "nome": "WhatsApp Loja", "icone": "whatsapp"}},
  "conversation_id": "bykoji:can_…:5511947281513",
  "numero_itens": 3, "total": "102.00", "previsao_min": 40,
  "criado_em": "2026-10-11T00:40:12Z", "atualizado_em": "2026-10-11T00:52:03Z",
  "aviso": {"status": "enviado"}
}], "proximo_cursor": 40}
```

`aviso` é o do status atual: `enviado`, `simulado`, `pendente`, `falhou`, `descartado`, `desligado` ou `null` (status sem aviso).

### 5.2 Detalhe

`GET /tenants/{t}/pedidos/{numero}` → os campos da lista mais:

```json
{
  "itens": [{"codigo": "GUE-0001", "nome": "Edamame", "quantidade": 1, "preco_unitario": "34.00", "subtotal": "34.00", "observacao": null, "alcoolico": false}],
  "subtotal": "34.00", "taxa_entrega": null, "total": "34.00", "tem_alcoolico": false,
  "entrega": {"modalidade": "retirada", "endereco": null, "horario": "20h", "observacao": null},
  "motivo": null,
  "proximos_status": ["pronto_retirada", "concluido", "cancelado"],
  "linha_do_tempo": [
    {"de": null, "para": "aguardando_loja", "por": "agente", "origem": "agente", "em": "2026-10-11T00:40:12Z", "motivo": null, "previsao_min": null, "aviso": null},
    {"de": "aguardando_loja", "para": "confirmado", "por": "joana", "origem": "painel", "em": "2026-10-11T00:42:50Z", "motivo": null, "previsao_min": 40,
     "aviso": {"status": "enviado", "em": "2026-10-11T00:42:55Z", "erro": null, "pode_reenviar": false}},
    {"de": "confirmado", "para": "em_preparo", "por": "joana", "origem": "painel", "em": "2026-10-11T00:52:03Z", "motivo": null, "previsao_min": null,
     "aviso": {"status": "desligado", "em": null, "erro": null, "pode_reenviar": false}}
  ]
}
```

- `origem`: `agente`, `cliente`, `painel`, `unidade_whatsapp`, `sistema`.
- `proximos_status` já vem filtrado pela modalidade: o painel só desenha os botões que valem.
- Pedidos antigos ganham a linha do tempo reconstruída com o que existe (criação e decisão).

### 5.3 Mudar o status

`POST /tenants/{t}/pedidos/{numero}/status`

```json
{"status": "confirmado", "previsao_min": 40}
{"status": "cancelado", "motivo": "Faltou salmão"}
```

- 200 → o detalhe completo (seção 5.2), já com a linha nova.
- 400 → `motivo` ausente em `cancelado`; `previsao_min` fora de 1–600 ou enviado em status que não o aceita; status desconhecido.
- 404 → pedido não existe nesta empresa.
- 409 → transição inválida, com o status atual (seção 3).
- `POST .../confirmar` e `POST .../recusar` continuam funcionando (atalhos da mesma regra); `confirmar` passa a aceitar `{"previsao_min": 40}`.

### 5.4 Contagem de pedidos novos

`GET /tenants/{t}/pedidos/contagem`

```json
{"aguardando_loja": {"total": 3, "mais_antigo_em": "2026-10-11T00:31:00Z",
  "por_unidade": [{"unidade": "patio_guedala", "nome": "Pátio Guedala", "total": 2, "mais_antigo_em": "2026-10-11T00:31:00Z"},
                  {"unidade": "morumbi", "nome": "Morumbi", "total": 1, "mais_antigo_em": "2026-10-11T00:44:10Z"}]},
 "em_andamento": {"confirmado": 1, "em_preparo": 4, "pronto_retirada": 0, "saiu_entrega": 2},
 "avisos_com_falha": 0}
```

Leve, feita para consulta a cada poucos segundos. Unidade sem pedido novo não aparece em `por_unidade`.

### 5.5 Textos e liga/desliga dos avisos

`GET /tenants/{t}/pedidos/avisos`

```json
{"variaveis": ["numero", "unidade", "previsao", "motivo", "nome_cliente"],
 "itens": [{"status": "confirmado", "ativo": true, "texto": "Seu pedido nº {numero} …", "padrao": true, "texto_padrao": "Seu pedido nº {numero} …", "ativo_padrao": true}]}
```

`PUT /tenants/{t}/pedidos/avisos/{status}` com `{"ativo": false}` e/ou `{"texto": "…"}`. `{"texto": null}` volta ao padrão. Auditoria `pedido.aviso_configurado`. Papel exigido: seção 7.

`POST /tenants/{t}/pedidos/avisos/{status}/previa` com `{"texto": "…"}` → `{"texto": "…já com valores de exemplo…"}`, para o painel mostrar a prévia sem enviar nada.

### 5.6 Reenviar aviso que falhou

`POST /tenants/{t}/pedidos/{numero}/avisos/{status}/reenviar` → 202. 409 se o aviso não está `falhou` ou se o pedido já saiu daquele status.

---

## 6. Conversas e contatos — contrato completo para o painel

### 6.1 Nome de exibição

Três origens guardadas separadas no contato:

| Origem | De onde vem | Atualização |
|---|---|---|
| `informado` | O cliente disse o nome na conversa; o agente devolve no retorno (campo novo opcional `nome_cliente`) | A cada vez que o agente devolver |
| `cadastro` | Digitado no painel (`PATCH .../contatos/{id}` com `{"nome": "…"}`) | Manual, com auditoria |
| `perfil` | Nome de perfil do WhatsApp (`senderName`) | **A cada mensagem recebida** (hoje só na primeira) |

Prioridade: **informado > cadastro > perfil > telefone formatado** (`+55 11 94728-1513`). O gateway devolve o resultado pronto e de onde veio. (Decisão 1: você escreveu essa ordem; em muitos sistemas o cadastro manual vence o informado. Mantive a sua.)

### 6.2 Campos novos em `GET /tenants/{t}/conversas`

Os campos de hoje continuam (nada quebra). Acrescenta:

```json
{
  "contato_id": "ct_…",
  "nome_exibicao": "Marco", "nome_origem": "informado",
  "nomes": {"informado": "Marco", "cadastro": null, "perfil": "Marco A."},
  "telefone_e164": "+5511947281513", "telefone_formatado": "+55 11 94728-1513",
  "canal_info": {"id": "can_…", "tipo": "uazapi", "nome": "WhatsApp Loja", "icone": "whatsapp", "teste": false},
  "unidade_preferida": "patio_guedala", "unidade_preferida_nome": "Pátio Guedala",
  "estado": "bot",
  "ultima": {"texto": "Pra retirar", "em": "2026-10-11T00:40:02Z", "direcao": "entrada", "origem": "cliente", "tipo": "texto"},
  "pedido_aberto": {"numero": "59859", "status": "em_preparo"}
}
```

| Campo | Regra |
|---|---|
| `telefone_e164` | Só quando o identificador é telefone (canais `uazapi` e `meta`; nos demais, se tiver 10–15 dígitos). Senão `null`, e o painel mostra `nome_exibicao` |
| `canal_info.icone` | `uazapi`/`meta` → `whatsapp`; `site` → `globe`; `api` → `code`; `email` → `mail`; `teams` → `teams`; `luxbrain` → `luxbrain` |
| `canal_info.teste` | `true` quando a empresa é de teste ou a conversa nasceu de simulação. É o que hoje se chama de canal "test" |
| `estado` | Um só, nesta ordem: `encerrada` → `aguardando_atendente` → `atendente` (bot pausado) → `bot` |
| `pedido_aberto` | Último pedido da conversa que não está em status final; senão `null` |

Parâmetros novos: `estado=` (filtro), `ordem=recentes` (por última atividade, com cursor próprio; a ordem atual continua sendo o padrão para não quebrar o painel), e `busca` passa a procurar também nos três nomes.

### 6.3 Contatos

`GET /tenants/{t}/contatos` ganha `nome_exibicao`, `nome_origem`, `nomes`, `telefone_e164`, `telefone_formatado`, `ultima_conversa_em`, `conversas` (quantidade). `PATCH /tenants/{t}/contatos/{id}` aceita `{"nome": "…"}` (nome de cadastro; `null` apaga).

### 6.4 Anonimização

Contato anonimizado: os três nomes e os telefones vêm nulos e `nome_exibicao` vira "Contato anonimizado". A rotina de anonimização passa a limpar também os campos novos e a linha do tempo dos pedidos (`por` e `motivo`).

---

## 7. Permissões

O gateway hoje não sabe o papel de quem está no painel. Proposta:

- O painel passa a enviar o cabeçalho **`X-Role`** com `plataforma`, `admin` ou `membro`, junto do `X-Actor`.
- O gateway aplica:

| Ação | `membro` (atendente) | `admin` | `plataforma` |
|---|---|---|---|
| Ver e listar pedidos, contagem | sim | sim | sim |
| Mudar status do pedido | sim | sim | sim |
| Reenviar aviso que falhou | sim | sim | sim |
| Ver textos dos avisos | sim | sim | sim |
| **Alterar textos e liga/desliga** | **não (403)** | sim | sim |
| Editar nome de cadastro do contato | sim | sim | sim |

- "Da própria empresa" continua garantido pelo painel (ele escolhe o `{tenant}` da rota pelo login) e pelo gateway (toda consulta filtra pela empresa da rota).
- Sem `X-Role`, o gateway trata como `membro` **só nas rotas novas** do PED1; as rotas antigas não mudam.
- Dados sensíveis: `X-Role` também decide se telefone e endereço vêm inteiros. Proposta: todos os três papéis veem (o atendente precisa do telefone e do endereço para o pedido). Se existir papel só de leitura no painel, digo como mascarar (Decisão 3).

O `X-Role` é confiável porque `/admin/v1` só aceita a chave do painel e só na rede interna; não é um controle contra o próprio painel.

---

## 8. Skill `consultar_pedido`

Mesma rota (`GET /api/v1/pedidos/{numero}?conversation_id=`), mesma regra (só a conversa dona do pedido). O `pedido` ganha:

```json
{"numero": "59859", "status": "saiu_entrega", "status_descricao": "Saiu para entrega",
 "previsao_min": 40, "previsao_ate": "2026-10-10T22:22:00-03:00",
 "atualizado_ha_segundos": 310,
 "linha_do_tempo": [
   {"status": "aguardando_loja", "em": "2026-10-10T21:40:12-03:00"},
   {"status": "confirmado", "em": "2026-10-10T21:42:50-03:00"},
   {"status": "saiu_entrega", "em": "2026-10-10T21:58:30-03:00"}],
 "motivo": null}
```

- A linha do tempo é resumida: status e hora (no fuso da empresa). **Sem** nome de quem mexeu nem situação do aviso.
- `previsao_ate` = hora da confirmação + `previsao_min`; `null` sem previsão.
- O bloco `contexto_modulos.pedidos.ultimo_pedido` do pacote ganha `status_descricao` e `atualizado_em`, para o agente responder "cadê meu pedido?" muitas vezes sem chamar a Skill.
- Nova rota para o caso "qual o status do meu pedido?" sem número: `GET /api/v1/pedidos?conversation_id=` devolve os 5 últimos pedidos da conversa (número, status, hora). (Decisão 5.)

---

## 9. Banco, bloco e testes

**Migração 030 (aditiva; a imagem atual segue funcionando):**

- `pedidos.status` aceita também `em_preparo`, `pronto_retirada`, `saiu_entrega`, `concluido`; coluna `previsao_min`.
- Tabela `pedido_eventos` (linha do tempo): pedido, de, para, por, origem, motivo, previsão, quando. Preenchida para os pedidos existentes.
- Tabela `pedido_avisos_config` (empresa, status, ativo, texto); vazia = padrões.
- `avisos_internos` ganha o tipo de aviso transacional de status.
- `contatos`: `nome_informado`, `nome_cadastro`, `nome_perfil` (o `nome` atual vira `nome_perfil`).

**Divisão sugerida** (cada um com commit, testes, implantação e parada):

| Bloco | Conteúdo |
|---|---|
| PED1-a | Máquina de status, linha do tempo, rotas 5.1–5.4, `consultar_pedido` |
| PED1-b | Avisos transacionais, textos por empresa, rotas 5.5–5.6, `X-Role` |
| PED1-c | Conversas e contatos (seção 6) |

**Testes:**

- Cada transição válida e cada inválida (voltar, final, mesmo status, modalidade errada, recusa fora de `aguardando_loja`), com 409 e status atual.
- Pular etapas: uma linha na linha do tempo, um aviso.
- Cancelar sem motivo → 400; com motivo → aviso com o motivo.
- Aviso único por transição (clique duplo, duas pessoas ao mesmo tempo, agendador rodando duas vezes).
- Aviso sai com o bot pausado e com conversa aguardando atendente; a pausa não muda.
- Piloto respeitado (contato fora da lista não recebe; `dry` não envia).
- Falha de envio: transição mantida, aviso `falhou` visível, reenvio manual funciona uma vez.
- Aviso desligado por status; texto editado; variável vazia some com o trecho `[[ ]]`; variável desconhecida → 400.
- Histórico: aviso gravado como `sistema`; a varredura não pausa o bot por causa dele.
- `X-Role: membro` não altera textos (403) e muda status (200).
- Isolamento: pedido, contagem, textos e linha do tempo de outra empresa → 404 ou vazio.
- `consultar_pedido` com linha do tempo; pedido de outra conversa → 404.
- Conversas: prioridade do nome nas quatro combinações, `senderName` atualizado em mensagem posterior, E.164, `estado` nos quatro casos, canal com nome e ícone, contato anonimizado.
- Regressão: "OK 12345" / "NÃO 12345" da unidade, `cancelar_pedido`, webhooks de saída.

---

## 10. Decisões que preciso de você

1. **Prioridade do nome:** informado > cadastro > perfil > telefone (como você pediu). Confirma, mesmo com o cadastro manual perdendo para o que o cliente disse? E o nome informado vem do agente por um campo novo `nome_cliente` no retorno — pode ser assim?
2. **Cliente cancelar depois de confirmado:** mantenho só em `aguardando_loja` (depois disso o agente transfere)?
3. **`X-Role`:** o painel consegue enviar? Existe papel só de leitura que não deva ver telefone/endereço?
4. **Reenvio manual de aviso que falhou:** incluir (rota 5.6) ou deixar só visível?
5. **Lista dos últimos pedidos da conversa** para o agente (rota nova na seção 8): incluir?
6. **Aviso de `concluido`:** ligado por padrão, como você definiu. Em retirada ele chega logo depois do "pronto para retirada"; quer ligado mesmo assim?
7. **Divisão em três blocos** (PED1-a, b, c) com parada entre eles, ou tudo de uma vez?
