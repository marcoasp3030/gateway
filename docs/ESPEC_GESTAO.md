# Especificação — gestão de unidades, horários, entrega, pagamento e cardápio

> **Situação: aprovada pelo Marco em 10/10/2026, com as decisões da seção 9. Implementação em blocos (seção 8); o andamento fica no `PROGRESS.md`.**
> Vale o que já rege o projeto: `PLANO_CURSOR.md`, `GUIA_CURSOR.md`, invariantes INV/SEG, tudo isolado por
> `tenant_id`, `/admin/v1` só na rede interna, `/api/v1` só com `X-API-Key` (a empresa vem da chave).
> As decisões que dependem do Marco estão reunidas na seção 9.

## 1. Levantamento do que existe hoje

Última migração aplicada em produção: **023** (`gateway:9a77e6d`).

### 1.1 Tabelas

| Tabela | Migração | Colunas relevantes |
|---|---|---|
| `tenant_unidades` | 002, 021 | `id`, `tenant_id`, `nome`, `ativo`, `codigo` (liga aos itens), `horarios` (JSON: dia → lista de faixas `["12:00","15:00"]`), `endereco`, `telefone`, `whatsapp_pedidos`, `formas_pagamento` (texto), `taxa_entrega` (valor único), `pedido_minimo`, `area_entrega` (texto), `aceita_retirada`, `regras` (texto), `telefone_whatsapp` (legado, sem uso) |
| `catalogo_itens` | 001, 006, 021 | `id`, `tenant_id`, `codigo` (único por empresa), `unidade`, `categoria` (texto), `nome`, `descricao`, `preco` (nulo = sem preço; zero proibido), `tipo` (`item`, `adicional`, `opcao_executivo`, `regra`), `alcoolico`, `delivery` (`sim`, `nao`, `a_confirmar`), `ativo`, `observacao`, `atualizado_em` |
| `carrinhos` | 021 | `id`, `tenant_id`, `conversation_id`, `unidade`, `modalidade` (`delivery`, `retirada`), `endereco`, `horario` (texto), `observacao`, `maioridade_confirmada`, `status` (`aberto`, `fechado`, `cancelado`); um aberto por conversa |
| `carrinho_itens` | 021 | `id`, `carrinho_id`, `item_id`, `quantidade` (1–99), `preco_unitario` (copiado do catálogo), `observacao` |
| `pedidos` | 021, 022 | `numero`, `conversation_id`, `carrinho_id`, `unidade`, `modalidade`, `endereco`, `horario`, `observacao`, `itens` (JSON, cópia), `subtotal`, `taxa_entrega`, `total`, `status` (`aguardando_loja`, `confirmado`, `recusado`, `cancelado`), `motivo`, `cliente_nome`, `cliente_telefone`, `tem_alcoolico`, `maioridade_confirmada`, `decidido_em`, `decidido_por` |
| `avisos_internos` | 023 | caixa de saída de avisos (unidade, cliente, transbordo) |
| `tenants` | 002, 023 | `timezone` (padrão `America/Sao_Paulo`), `whatsapp_alerta_transbordo` |
| `tenant_config` / `agentes` | 002… | comportamento herdado (debounce, piloto, limites…); **não há** nada sobre horário de atendimento |

Um item pertence a **uma** unidade (o `codigo` do By Koji já traz o prefixo: `MOR-`, `EIN-`, `VNC-`, `GUE-`). Preço e disponibilidade "por unidade" já existem por construção: o mesmo prato em duas unidades são duas linhas.

Categoria é só um texto no item: não há tabela nem ordem de exibição.

A auditoria (`audit_log`) grava quem, quando, ação e alvo, mas **não** grava antes/depois (regra atual: nunca conteúdo nem segredo no log de auditoria).

### 1.2 Rotas

**Painel (`/admin/v1/tenants/{t}/...`)**

| Rota | Função |
|---|---|
| `GET/POST unidades`, `GET/PATCH/DELETE unidades/{id}` | CRUD da unidade (DELETE desativa). Aceita todos os campos da tabela |
| `POST catalogo/importar?simulacao=` | CSV inteiro; simulação por padrão; upsert por `codigo`; o que some fica inativo; erro em uma linha não grava nada |
| `GET catalogo/itens`, `GET catalogo/resumo` | Lista com filtros e contagens por unidade e tipo. **Não há** criar/editar/remover item |
| `GET pedidos`, `GET pedidos/{numero}`, `POST pedidos/{numero}/confirmar` e `/recusar` | Pedidos e decisão da unidade |
| `GET avisos` | Avisos internos e situação |
| `PATCH /admin/v1/tenants/{t}` | Inclui `whatsapp_alerta_transbordo` |

**Skills (`/api/v1/...`, `X-API-Key`)**

| Rota | Escopo | Função |
|---|---|---|
| `GET catalogo/busca` | `ferramentas:catalogo` | Busca tolerante, só ativos, filtro de unidade |
| `GET carrinho/{conversa}`; `POST/PATCH/DELETE .../itens`; `DELETE carrinho/{conversa}`; `PUT .../entrega` | `ferramentas:pedidos` | Carrinho |
| `POST pedidos`, `GET pedidos/{numero}`, `POST pedidos/{numero}/cancelar` | `ferramentas:pedidos` | Fechar, consultar, cancelar |

### 1.3 Onde entra a checagem de horário no pipeline

O ponto é `Pipeline._responder` (caminho da fila durável, em `app/nucleo/pipeline.py`), logo depois da checagem `permitido` (piloto, pausa do atendente) e **antes** de `skyone.pacote(...)`. Ali o turno já está aberto e as mensagens do lote já foram lidas; decidir "fora do horário" nesse ponto permite responder sem chamar o agente, marcar as mensagens como tratadas e fechar o turno pelo caminho normal (com envio por partes, que já é idempotente). O caminho antigo (`processar`, usado só com `FILA_DURAVEL=off`) tem o ponto equivalente e recebe a mesma função.

Hoje o agente recebe no pacote, em `unidades`, `aberta_agora` e `proxima_abertura` calculados por `nucleo/horarios.estado()` com o fuso **da empresa**. Essa função já trata vários turnos por dia e turno que cruza a meia-noite; não trata exceções por data nem pausa manual.

### 1.4 Como o carrinho calcula total e taxa hoje

- `subtotal` = soma de `preco_unitario × quantidade` das linhas.
- `taxa_entrega` = `tenant_unidades.taxa_entrega` **somente** se a modalidade é `delivery` e a unidade tem valor cadastrado; senão nula.
- `total` = subtotal + taxa (taxa nula conta como zero; o agente diz que a unidade confirma).
- `definir_entrega` valida: unidade existe e só muda com o carrinho vazio; modalidade `delivery`/`retirada`; retirada recusada se `aceita_retirada = false`. Endereço, horário e observação são texto livre, sem validação. Maioridade é um booleano informado pelo agente.
- `fechar_pedido` valida: carrinho não vazio; cada item ainda vendável (ativo, com preço, `delivery ≠ nao`); item da mesma unidade; preço igual ao do catálogo (senão `preco_alterado`); modalidade; endereço em delivery; maioridade se houver álcool; `pedido_minimo`; retirada aceita.
- **Não existe** checagem de horário, de área de entrega nem de forma de pagamento.

---

## 2. Princípios da proposta

1. **Regra no servidor.** Horário, área, mínimo, pagamento e falta de item são recusados pelo gateway com motivo; o prompt só explica.
2. **Vazio continua significando "não informado".** Campo não preenchido não vira regra: o agente diz que a unidade confirma. Uma regra só passa a recusar depois de configurada.
3. **Compatível com o que está no ar.** Colunas atuais ficam; as novas são acrescentadas. Os contratos atuais das Skills só ganham campos e motivos.
4. **Uma função só para horário**, usada pelo pacote ao agente, pelo carrinho, pelo pipeline e pela Skill nova.
5. **Tudo auditado com antes/depois**, numa tabela própria da gestão (ver 3.6), sem misturar com o `audit_log` atual.

---

## 3. Modelo de dados e migrações

Numeração a partir da **024**. Cada migração é aditiva e roda com a imagem anterior no ar. **Os números abaixo são a ordem de desenho; na implementação cada bloco recebe o próximo número livre** (o bloco G1 usa a 024 com as colunas de horário da unidade, as exceções e o histórico de alterações; as colunas de entrega da 3.1 entram no bloco G4).

### 3.1 Migração 024 — unidade: fuso, pausa, entrega e tempos

`tenant_unidades`, colunas novas:

| Coluna | Tipo | Regra |
|---|---|---|
| `fuso` | texto, nulo | Nulo herda `tenants.timezone`. Validado como fuso IANA |
| `pausa_ate` | data/hora, nula | "Fechar agora até HH:MM" |
| `pausa_indefinida` | booleano, padrão falso | "Fechar agora até reabrir" |
| `pausa_motivo` | texto, nulo | Opcional; vai ao agente |
| `delivery_ativo` | booleano, nulo | Nulo = não informado; falso = não entrega |
| `taxa_modo` | `fixa` \| `bairro` \| `cep`, nulo | Nulo = como hoje (valor único se houver) |
| `frete_gratis_acima` | valor, nulo | Subtotal a partir do qual a taxa é zero |
| `tempo_entrega_min`, `tempo_retirada_min` | inteiro, nulo | Minutos estimados |

`aceita_retirada`, `taxa_entrega` (usada no modo `fixa`), `pedido_minimo`, `area_entrega` (texto informativo) e `regras` continuam como estão.

Tabela nova `unidade_excecoes`:

| Coluna | Regra |
|---|---|
| `id`, `tenant_id`, `unidade_id` | chave estrangeira composta com a unidade |
| `data` | única por unidade |
| `fechado` | verdadeiro = fechado o dia todo |
| `faixas` | JSON no mesmo formato da grade, quando há horário especial |
| `motivo` | texto opcional ("feriado") |

Restrição: `fechado` **ou** `faixas` preenchidas, nunca os dois.

### 3.2 Migração 025 — taxas por área e formas de pagamento

Tabela `unidade_taxas`:

| Coluna | Regra |
|---|---|
| `id`, `tenant_id`, `unidade_id` | |
| `tipo` | `bairro` \| `cep` |
| `bairro` | texto como digitado; `bairro_busca` = versão sem acento e minúscula (comparação) |
| `apelidos` | lista de outros nomes do mesmo bairro ("Jd. Morumbi", "Jardim Morumbi"), comparados do mesmo jeito |
| `atendido` | falso = bairro marcado como **não atendido** (recusa com certeza) |
| `cep_inicio`, `cep_fim` | 8 dígitos; faixa inclusiva; `inicio ≤ fim` |
| `taxa` | valor ≥ 0 |
| `ativo` | |

Tabela `unidade_pagamentos`:

| Coluna | Regra |
|---|---|
| `id`, `tenant_id`, `unidade_id` | |
| `codigo` | `pix`, `cartao_entrega`, `dinheiro` ou outro identificador curto (único por unidade) |
| `rotulo` | texto mostrado ao cliente ("Pix", "Vale-refeição") |
| `aceita_troco` | só faz sentido em `dinheiro` |
| `instrucoes` | texto opcional (ex.: chave Pix) |
| `ativo`, `ordem` | |

`tenant_unidades.formas_pagamento` (texto livre atual) fica só como observação; a regra passa a ser a tabela.

### 3.3 Migração 026 — carrinho e pedido com os dados novos

`carrinhos` e `pedidos`, colunas novas: `bairro`, `cep`, `pagamento` (código), `troco_para` (valor), `agendado_para` (data/hora, modo "pedido agendado"). Em `pedidos` também: `frete_gratis` (booleano) e `tempo_estimado_min` (cópia do valor da unidade no fechamento).

### 3.4 Migração 027 — horário de atendimento e fora do horário

Entram no comportamento herdado (`tenant_config` e `agentes`, como debounce e piloto):

| Campo | Valores | Padrão |
|---|---|---|
| `atendimento_horario_modo` | `sempre` \| `unidades` \| `proprio` | `sempre` (não muda nada para quem não configurar) |
| `atendimento_horarios` | grade no formato das unidades (modo `proprio`) | vazio |
| `fora_horario_modo` | `mensagem` \| `sem_pedido` \| `agendado` | `mensagem` |
| `fora_horario_texto` | texto com `{proxima_abertura}` e `{nome_empresa}` | texto padrão da plataforma |
| `fora_horario_intervalo_h` | 1–72 | 6 |

`conversas`: `fora_horario_avisada_em` (quando a mensagem automática saiu pela última vez).

No modo `unidades`, a empresa está aberta quando **alguma** unidade ativa está aberta, e a próxima abertura é a mais próxima entre elas.

### 3.5 Migração 028 — cardápio: categorias, ordem e "em falta"

Tabela `catalogo_categorias`: `id`, `tenant_id`, `nome`, `ordem`, `ativo`; única por (`tenant_id`, `nome`) — **categorias são da empresa, não da unidade**; o item continua sendo de uma unidade. Criada a partir das categorias que já existem nos itens (ordem inicial = ordem em que aparecem; nomes iguais em unidades diferentes viram uma categoria só).

`catalogo_itens`, colunas novas: `ordem` (dentro da categoria), `em_falta_ate` (data/hora), `em_falta_motivo`.

"Em falta hoje" = `em_falta_ate` no futuro. O retorno é automático: passado o horário, o item volta a valer sem nenhum job. O painel oferece "até o fim do dia" (meia-noite no fuso da unidade), "até HH:MM" e "até eu reativar" (data distante).

A categoria continua gravada no item como texto (a importação CSV não muda); a tabela de categorias só acrescenta ordem e ativação.

### 3.6 Migração 029 — histórico de importações e de alterações

Tabela `catalogo_importacoes`: `id`, `tenant_id`, `actor`, `modo` (`mesclar` \| `substituir`), `unidade` (se a importação foi restrita a uma), `arquivo_sha256`, `linhas`, `inseridos`, `atualizados`, `desativados`, `inalterados`, `anterior_csv` (exportação do catálogo como estava antes, para desfazer), `criado_em`.

Tabela `gestao_alteracoes`: `id`, `tenant_id`, `actor`, `entidade` (`unidade`, `excecao`, `taxa`, `pagamento`, `item`, `categoria`, `atendimento`), `entidade_id`, `acao` (`criado`, `alterado`, `removido`, `pausado`, `reaberto`, `em_falta`, `importado`), `antes` e `depois` (JSON só com os campos que mudaram), `criado_em`.

Esses dados são de configuração do negócio (preço, horário, taxa), não de cliente; por isso podem guardar antes/depois sem ferir a regra do `audit_log`. `whatsapp_pedidos` é gravado mascarado. O `audit_log` continua recebendo o evento resumido, como hoje.

---

## 4. Regras de negócio

### 4.1 Unidade aberta agora

Função única `situacao_da_unidade(unidade, excecoes, agora)` em `nucleo/horarios.py`, devolvendo:

```json
{"aberta_agora": false, "motivo": "pausa | excecao | horario | inativa | sem_horario",
 "fecha_as": null, "proxima_abertura": "2026-10-11T12:00:00-03:00", "pausada_ate": "2026-10-10T20:00:00-03:00"}
```

Ordem de decisão:
1. Unidade inativa → fechada (`inativa`), sem próxima abertura.
2. Pausa manual vigente (`pausa_indefinida` ou `pausa_ate` no futuro) → fechada (`pausa`). Com `pausa_ate`, a próxima abertura é o primeiro instante aberto pela grade a partir desse horário; pausa indefinida não tem próxima abertura.
3. Exceção para a data → vale a exceção (fechado ou faixas especiais) no lugar da grade daquele dia.
4. Grade semanal → vários turnos por dia; turno com fim menor que o início cruza a meia-noite e pertence ao dia em que começa.
5. Grade vazia → `sem_horario`: **não informado**, tratado como aberto para as regras de pedido (princípio 2), e o agente diz que a unidade confirma.

Tudo no fuso da unidade (`fuso`, ou o da empresa). A pausa com `pausa_ate` vencida é ignorada (não precisa de job para "reabrir").

### 4.2 Fora do horário de atendimento (bot)

Vale só para canais de clientes (`publico = clientes`) e só quando `atendimento_horario_modo` ≠ `sempre`.

| Modo | O que acontece |
|---|---|
| `mensagem` (padrão) | O agente **não é chamado**. A mensagem automática (texto configurável, com a próxima abertura) é enviada uma vez por conversa a cada `fora_horario_intervalo_h` horas e fica no histórico como mensagem do bot. Mensagens do cliente ficam gravadas e marcadas como tratadas; o turno fecha com `acao = fora_horario`. Dentro do intervalo, novas mensagens não geram outra resposta |
| `sem_pedido` | O agente atende normalmente e recebe no pacote `atendimento: {"aberto": false, "proxima_abertura": ..., "pedidos": "bloqueados"}`. `adicionar_item` e `fechar_pedido` recusam com `unidade_fechada` |
| `agendado` | O agente atende; `fechar_pedido` aceita e grava `agendado_para` = próxima abertura da unidade. O aviso à unidade e ao cliente diz que é pedido agendado |

Conversa assumida por atendente, piloto e pausa continuam valendo antes de tudo isso (a checagem vem depois de `permitido`).

Quando o bot está no ar mas a **unidade** do carrinho está fechada (ex.: bot `sempre`, unidade com grade): vale o modo de pedido da empresa — `agendado` agenda; os outros dois recusam com `unidade_fechada`.

### 4.3 Entrega

`definir_entrega` passa a aceitar `bairro`, `cep`, `pagamento` e `troco_para`, e o carrinho calcula:

| `taxa_modo` | Taxa | Fora da área |
|---|---|---|
| nulo | como hoje: `taxa_entrega` se houver, senão nula ("a unidade confirma") | não se aplica |
| `fixa` | `taxa_entrega` | não se aplica |
| `bairro` | taxa do bairro informado (comparação sem acento e sem maiúsculas, incluindo apelidos) | bairro marcado como não atendido → `fora_area`. Bairro **não reconhecido não recusa**: devolve `bairro_nao_reconhecido` para o agente pedir o CEP |
| `cep` | taxa da faixa que contém o CEP | CEP fora de todas as faixas → `fora_area` |

- Nos modos `bairro` e `cep`, o campo correspondente passa a ser obrigatório em delivery (`falta_bairro`, `falta_cep` em `faltando`).
- `frete_gratis_acima`: subtotal igual ou maior → taxa 0 e `frete_gratis: true`.
- `delivery_ativo = false` → delivery recusado (`delivery_indisponivel`); `aceita_retirada = false` → retirada recusada (já existe).
- `pedido_minimo` → `abaixo_minimo` (o motivo atual `abaixo_do_minimo` é renomeado no bloco G4).
- `tempo_entrega_min` / `tempo_retirada_min` aparecem no carrinho e no pedido como `tempo_estimado_min`. Só então o aviso de confirmação ao cliente pode citar prazo ("previsão de cerca de 40 minutos").
- A taxa de 13% do Einstein ("pedidos entregues nos quartos") continua só como texto em `regras`. Não entra no cálculo nesta fase (ver 9.6).

### 4.4 Pagamento

- Se a unidade tem formas de pagamento ativas, `fechar_pedido` exige `pagamento` com um dos códigos ativos; senão `pagamento_invalido` (a resposta lista os aceitos). Sem nenhuma forma cadastrada: campo opcional, "a unidade confirma".
- `troco_para` só é aceito com `dinheiro` que tenha `aceita_troco`; precisa ser maior ou igual ao total.
- O gateway **não processa pagamento**: registra a forma escolhida e mostra `instrucoes` ao agente.

### 4.5 Item em falta

Item com `em_falta_ate` no futuro continua aparecendo na busca, com `em_falta: true` e `volta_em`, para o agente poder dizer "hoje não temos". `adicionar_item` e `fechar_pedido` recusam com `item_em_falta`.

### 4.6 Motivos de recusa novos

| Motivo | HTTP | Onde | Acompanha |
|---|---|---|---|
| `unidade_fechada` | 422 | `adicionar_item`, `fechar_pedido` | `proxima_abertura`, `motivo_fechada` |
| `fora_area` | 422 | `definir_entrega`, `fechar_pedido` | modo da taxa. Só quando há certeza: CEP fora de todas as faixas ou bairro marcado como não atendido |
| `bairro_nao_reconhecido` | 422 | `definir_entrega`, `fechar_pedido` | O agente pede o CEP; com faixas de CEP cadastradas o CEP decide; sem faixas, a taxa fica "a confirmar" e o pedido segue |
| `abaixo_minimo` | 422 | `fechar_pedido` | `pedido_minimo`, `falta` (quanto falta) |
| `pagamento_invalido` | 422 | `definir_entrega`, `fechar_pedido` | `aceitos` (códigos e rótulos) |
| `item_em_falta` | 422 | `adicionar_item`, `fechar_pedido` | `volta_em` |
| `delivery_indisponivel` | 422 | `definir_entrega`, `fechar_pedido` | — |

Os motivos atuais ficam: `sem_preco`, `item_inativo`, `sem_delivery`, `unidade_diferente`, `falta_maioridade`, `preco_alterado` etc. A ordem de checagem em `adicionar_item` é: item existe → ativo → **em falta** → sem preço → delivery → unidade diferente → **unidade fechada**.

Os dados que acompanham o motivo vão em `erro.detalhes` (valores calculados pelo gateway, nunca eco do que o agente enviou).

---

## 5. Contratos para o painel (`/admin/v1/tenants/{t}/...`)

Todas as rotas: rede interna, chave do painel, `X-Actor`; escrita gera linha em `gestao_alteracoes` e evento no `audit_log`.

### 5.1 Unidades

| Rota | Função |
|---|---|
| `GET unidades`, `GET unidades/{id}` | Como hoje, mais `situacao` (resultado de 4.1), `fuso`, pausa, modos de entrega e tempos |
| `POST unidades`, `PATCH unidades/{id}` | Campos atuais + `fuso`, `delivery_ativo`, `taxa_modo`, `frete_gratis_acima`, `tempo_entrega_min`, `tempo_retirada_min` |
| `POST unidades/{id}/pausar` | `{"ate": "2026-10-10T20:00:00-03:00"}` ou `{"indefinida": true}`, `motivo` opcional |
| `POST unidades/{id}/reabrir` | Tira a pausa |
| `PUT unidades/{id}/horarios` | Substitui a grade semanal inteira (validação: horas `HH:MM`, até 12 turnos por dia, sem turnos sobrepostos) |
| `GET/POST unidades/{id}/excecoes`, `DELETE .../excecoes/{eid}` | Exceções por data; `GET` aceita `desde` e `ate` |
| `GET/POST unidades/{id}/taxas`, `PATCH/DELETE .../taxas/{tid}` | Linhas de bairro ou de faixa de CEP |
| `PUT unidades/{id}/taxas` | Substitui todas de uma vez (para colar uma tabela de bairros) |
| `GET/PUT unidades/{id}/pagamentos` | Lista ordenada de formas de pagamento |
| `GET unidades/{id}/agenda?dias=7` | Prévia: para cada dia, os intervalos em que a unidade estará aberta (grade + exceções), para o painel mostrar e conferir |

### 5.2 Horário de atendimento da empresa

Pelos campos de comportamento já existentes: `PUT /config` (empresa) e `PATCH /agentes/{a}` (por agente), com os cinco campos de 3.4. Mais:

| Rota | Função |
|---|---|
| `GET atendimento/situacao` | `{"aberto", "proxima_abertura", "modo", "fora_horario_modo"}` como o pipeline enxerga agora |
| `POST atendimento/previa-mensagem` | Devolve o texto de fora do horário já com as variáveis preenchidas |

### 5.3 Cardápio

| Rota | Função |
|---|---|
| `GET catalogo/itens` | Como hoje, mais filtros `categoria`, `em_falta` e ordenação por categoria/ordem |
| `POST catalogo/itens`, `GET/PATCH catalogo/itens/{id}` | Criar e editar item: `codigo`, `unidade`, `categoria`, `nome`, `descricao`, `preco` (nulo permitido; zero recusado), `tipo`, `alcoolico`, `delivery`, `ativo`, `observacao`, `ordem` |
| `DELETE catalogo/itens/{id}` | Desativa (nunca apaga: pedidos antigos apontam para o item) |
| `POST catalogo/itens/{id}/em-falta` | `{"ate": ...}` ou `{"hoje": true}` ou `{"indefinido": true}`; `motivo` opcional |
| `POST catalogo/itens/{id}/disponivel` | Tira o "em falta" |
| `POST catalogo/itens/lote` | `{"ids": [...], "acao": "ativar | desativar | em_falta | disponivel | mudar_categoria", ...}`; até 500 itens; tudo ou nada |
| `POST catalogo/itens/{id}/copiar` | Copia o item para outra unidade com `codigo` novo |
| `GET/POST catalogo/categorias`, `PATCH/DELETE catalogo/categorias/{id}` | Categorias da empresa (valem para todas as unidades); renomear atualiza os itens de todas; desativar esconde os itens da busca. `GET` aceita `unidade=` para contar os itens de cada categoria naquela unidade |
| `PUT catalogo/categorias/ordem` | Lista de ids na ordem de exibição |

### 5.4 CSV

| Rota | Função |
|---|---|
| `GET catalogo/modelo.csv` | Arquivo-modelo com o cabeçalho e duas linhas de exemplo |
| `GET catalogo/exportar.csv?unidade=` | Catálogo atual no mesmo formato (serve de backup e de base para edição) |
| `POST catalogo/importar?simulacao=true&modo=mesclar|substituir&unidade=` | Como hoje (simulação por padrão), com o modo. A prévia passa a listar **quais** itens são novos, alterados (com os campos que mudam) e desativados, além dos erros por linha |
| `GET catalogo/importacoes`, `GET catalogo/importacoes/{id}` | Histórico: quem, quando, modo, contagens |
| `POST catalogo/importacoes/{id}/desfazer` | Reaplica o `anterior_csv` daquela importação (só a mais recente) |

- **`substituir`** = comportamento atual: o arquivo é o catálogo inteiro (ou o da `unidade` informada); o que não está nele fica inativo.
- **`mesclar`** = só cria e atualiza; o que não está no arquivo fica como está.
- Padrão: `mesclar`. `substituir` só com confirmação explícita: além de `modo=substituir`, a aplicação exige `confirmar_desativacao=<quantidade>` igual ao número de itens que a simulação informou que serão desativados; sem isso (ou com número diferente) nada é gravado. A simulação sempre informa quantos e quais itens serão desativados.
- Formato: exatamente as 11 colunas atuais (`codigo, unidade, categoria, nome, descricao, preco, tipo, alcoolico, delivery, ativo, observacao`), na mesma ordem. O arquivo do By Koji continua valendo sem mudança. `em_falta` e `ordem` ficam fora do CSV (são operação do dia a dia, não cadastro).
- Tudo em uma transação; erro em qualquer linha não grava nada (já é assim).

### 5.5 Histórico

| Rota | Função |
|---|---|
| `GET gestao/alteracoes?entidade=&entidade_id=&desde=` | Quem mudou o quê, com antes e depois |

---

## 6. Contratos para as Skills (`/api/v1/...`)

### 6.1 Skill nova `info_unidades`

`GET /api/v1/unidades?conversation_id=...&unidade=...` — escopo `ferramentas:catalogo` (a chave atual já serve).

```json
{"atendimento": {"aberto": true, "proxima_abertura": null},
 "unidades": [
  {"codigo": "patio_guedala", "nome": "Pátio Guedala", "endereco": "Av. Comendador Adibo Ares, 9 — Morumbi", "telefone": null,
   "aberta_agora": false, "motivo_fechada": "horario", "fecha_as": null, "proxima_abertura": "2026-10-10T19:00:00-03:00",
   "horarios": {"seg": [["12:00","15:00"],["19:00","22:00"]]},
   "excecoes_proximas": [{"data": "2026-10-12", "fechado": true, "motivo": "feriado"}],
   "delivery": true, "retirada": true,
   "taxa": {"modo": "bairro", "valor": null, "bairros": [{"bairro": "Morumbi", "taxa": 12.0}], "frete_gratis_acima": 300.0},
   "pedido_minimo": 80.0,
   "pagamentos": [{"codigo": "pix", "rotulo": "Pix", "instrucoes": null}, {"codigo": "dinheiro", "rotulo": "Dinheiro", "aceita_troco": true}],
   "tempo_entrega_min": 50, "tempo_retirada_min": 25,
   "regras": "..."}
 ]}
```

- Nulo = não informado (o agente diz que a unidade confirma).
- No modo `cep`, `taxa` traz só `{"modo": "cep"}`: o agente pede o CEP e usa `definir_entrega` para saber o valor. Tabelas longas de bairro são cortadas em 50 linhas.
- O WhatsApp de pedidos nunca vai ao agente.
- O bloco `unidades` do pacote continua indo a cada turno, na versão resumida (situação, endereço, horários); a Skill serve para o detalhe sob demanda.

### 6.2 Mudanças nas Skills existentes

| Skill | Mudança |
|---|---|
| `buscar_catalogo` | Cada item ganha `em_falta` e `volta_em`; ordenação de empate por categoria/ordem |
| `ver_carrinho` e toda resposta com carrinho | Ganha `bairro`, `cep`, `pagamento`, `troco_para`, `frete_gratis`, `tempo_estimado_min`, `agendado_para`, `unidade_aberta`, `proxima_abertura`. `faltando` pode trazer `bairro`, `cep`, `pagamento` |
| `definir_entrega` | Aceita `bairro`, `cep`, `pagamento`, `troco_para`. Recusa com `fora_area`, `pagamento_invalido`, `delivery_indisponivel` |
| `adicionar_item` | Recusa com `item_em_falta` e `unidade_fechada` |
| `fechar_pedido` | Recusa com todos os motivos de 4.6; no modo `agendado`, devolve `agendado_para` |

Nada do contrato atual é removido. `docs/SKILLS.md` é atualizado em cada bloco.

### 6.3 Pacote ao agente

- `unidades[]`: `aberta_agora` e `proxima_abertura` passam a considerar exceções e pausa; ganham `motivo_fechada`.
- Campo novo `atendimento`: `{"aberto", "proxima_abertura", "pedidos": "liberados | bloqueados | agendados"}`.

---

## 7. Mudanças no pipeline

1. `nucleo/horarios.py`: função única de 4.1; `pacote()` passa a usá-la.
2. `Pipeline._responder` e o ponto equivalente do caminho antigo: depois de `permitido` e antes de montar o pacote, chamar `fora_do_horario(tenant, conv, cfg, canal)`.
   - Modo `mensagem` e fora do horário: não chama o agente. Se `fora_horario_avisada_em` é nulo ou mais antigo que o intervalo, grava a resposta do turno como o texto automático (enviado pelo caminho normal de partes, então respeita envio de teste e nunca duplica) e atualiza `fora_horario_avisada_em`. Senão, turno sem resposta. Em ambos, `acao = fora_horario` e as mensagens ficam tratadas.
   - Outros modos: segue para o agente, com `atendimento` no pacote.
3. Sem chamada externa nova e sem mudança na fila, nas travas ou no debounce.
4. Métrica: turnos com `acao = fora_horario` aparecem separados nas métricas, para não contarem como resposta do agente.
5. Aviso de confirmação ao cliente (bloco 3 da Fase 4): passa a citar `tempo_estimado_min` quando a unidade tiver esse dado, e "pedido agendado para …" quando for o caso.

---

## 8. Blocos e testes

Cada bloco: um commit de código, testes, tag de volta, backup, migração com a imagem antiga no ar, implantação, parada para validação.

| Bloco | Entrega | Migração | Testes principais |
|---|---|---|---|
| **G1 — Horários da unidade** | Função única, exceções, pausa, fuso; rotas de unidade (pausar, reabrir, horários, exceções, agenda); pacote com a situação nova; base do histórico `gestao_alteracoes` | 024, 029 (parte de alterações) | Vários turnos; turno cruzando meia-noite (aberto às 00:30 do dia seguinte); exceção fechado e horário especial; pausa até HH:MM e indefinida; pausa vencida ignorada; próxima abertura atravessando dias fechados; fuso diferente do da empresa; virada de horário de verão em fuso que tem; grade vazia = não informado; isolamento entre empresas; alteração gera antes/depois |
| **G2 — Skill `info_unidades`** (+ G1.1, seção 10.1 e 10.2) | `GET /api/v1/unidades`; `docs/SKILLS.md`; histórico com `desde`/`ate`/`unidade_id`; telefone E.164 | só se preciso (10.1, 10.2) | Contrato exato; nulo para o que não foi informado; WhatsApp de pedidos ausente; escopo e conversa de outra empresa → 403/404 |
| **G3 — Fora do horário** | Configuração de atendimento, três modos, mensagem com variáveis, uma vez a cada N horas | 027 | Modo `mensagem`: agente não é chamado, mensagem sai uma vez, segunda mensagem dentro do intervalo não responde, depois do intervalo responde de novo, mensagem fica no histórico, envio de teste não envia; conversa pausada ou fora do piloto não recebe; canal interno não é afetado; modos `sem_pedido` e `agendado` chamam o agente com `atendimento` no pacote; `sempre` não muda nada (regressão) |
| **G4 — Entrega e pagamento** | Taxas (fixa, bairro, CEP), área, frete grátis, mínimo, tempos, formas de pagamento, troco; regras no carrinho e no fechamento; motivos novos | 025, 026 | Taxa por bairro com acento e maiúsculas diferentes; CEP no limite da faixa; fora da área; frete grátis exatamente no valor; mínimo; pagamento inválido lista os aceitos; troco menor que o total; unidade sem nada configurado continua como hoje (regressão); `unidade_fechada` em adicionar e fechar com `proxima_abertura`; pedido agendado grava `agendado_para`; total = subtotal + taxa |
| **G5 — Cardápio** | CRUD de itens, categorias com ordem, em falta com retorno automático, lote, copiar para outra unidade | 028 | Preço zero recusado e vazio aceito; `codigo` duplicado → 409; em falta até um horário volta sozinho; `item_em_falta` em adicionar e fechar; item em falta aparece na busca marcado; lote tudo ou nada; renomear categoria atualiza itens; desativar item não quebra pedido antigo; regras atuais preservadas (`sem_preco`, `sem_delivery`, álcool, `unidade_diferente`) |
| **G6 — CSV** | Modelo, exportação, modos `mesclar`/`substituir`, prévia detalhada, histórico, desfazer | 029 (parte de importações) | Exportar e reimportar não muda nada; `mesclar` não desativa; `substituir` restrito a uma unidade não toca as outras; prévia não grava; erro em uma linha não grava nada; desfazer volta ao estado anterior; arquivo atual do By Koji importa sem alteração (888 inalterados) |

Ordem sugerida: G1 → G2 → G4 → G5 → G6 → G3. O G3 (fora do horário) fica por último porque é o único que muda o que o cliente recebe sem o agente; os outros só acrescentam dados e recusas.

Para o [PAINEL], cada bloco entrega o contrato das rotas em `docs/API_ADMIN.md` antes de a tela ser feita.

---

## 9. Decisões do Marco (10/10/2026)

1. **Horário do bot:** padrão `sempre` para empresas novas; By Koji configurado como `unidades`.
2. **Unidade fechada com o bot no ar:** segue o modo de pedido da empresa. By Koji = recusa (`unidade_fechada`); o agendamento fica implementado como opção, desligado.
3. **Motivo do pedido mínimo:** `abaixo_do_minimo` passa a se chamar `abaixo_minimo` (bloco G4).
4. **Importação CSV:** padrão `mesclar`. `substituir` só com confirmação explícita, e a simulação informa quantos itens serão desativados.
5. **`info_unidades`:** escopo `ferramentas:catalogo`.
6. **Taxa de 13% do Einstein:** continua só como texto; o cálculo fica fora desta fase.
7. **Cópia do catálogo a cada importação:** guardar as 20 últimas por empresa, com "desfazer".
8. **Texto padrão de fora do horário:** "Oi! 😊 No momento a {nome_empresa} está fechada. Voltamos {proxima_abertura}. Quando abrirmos, é só mandar uma mensagem por aqui que a gente te atende!"
9. **Bairro:** comparação sem acento, com apelidos por bairro. Bairro não reconhecido **não recusa**: devolve `bairro_nao_reconhecido` para o agente pedir o CEP. `fora_area` só com certeza (CEP fora das faixas ou bairro marcado como não atendido).
10. **Categorias por empresa** (não por unidade), com ordem de exibição; o item continua por unidade.

---

## 10. Pendências vindas do painel (10/10/2026)

Origem: `docs/ESPEC_TELAS_GESTAO.md` do painel, seção 15. Registradas aqui para serem fechadas no bloco indicado; o contrato final de cada uma sai em `docs/API_ADMIN.md` antes de a tela ser feita. Nada desta seção está implementado ainda.

### 10.1 G1.1 — histórico de alterações (entra junto com o G2)

`GET gestao/alteracoes` passa a aceitar, além de `entidade`, `entidade_id`, `limite` e `cursor`:

- `desde` e `ate`: data e hora ISO 8601 **com fuso** (ex.: `2026-10-10T00:00:00-03:00`). Sem fuso → 400. `desde` inclusivo, `ate` exclusivo; `desde` maior que `ate` → 400.
- `unidade_id`: traz as alterações da unidade **e de todas as exceções dela**, na mesma lista e na mesma ordem (mais recente primeiro), inclusive exceções já excluídas. Unidade de outra empresa ou inexistente → 404. Não combina com `entidade`/`entidade_id` (os dois juntos → 400).
- `criado_em` sempre com offset na resposta.
- `limite`: de 1 a **200**, padrão 50 (já é o comportamento de hoje; falta documentar). Fora disso → 400.

Sem migração se a ligação exceção → unidade puder ser lida do que já está gravado; se não puder, coluna aditiva `unidade_id` em `gestao_alteracoes`, preenchida para o passado.

### 10.2 Telefone `whatsapp_pedidos` (entra junto com o G2)

Alinhar com E.164: só dígitos, **10 a 15**, com DDI, sem `+`. Hoje a rota e o banco aceitam de 8 a 16. Muda a validação da rota e a restrição da coluna (migração aditiva: troca do CHECK, depois de conferir que nenhum valor gravado fica fora da regra nova). O mesmo formato vale para `whatsapp_alerta_transbordo`. Documentar em `docs/API_ADMIN.md`.

### 10.3 G3 — fora do horário

- Campo próprio para o **modo de pedido com a unidade fechada**: `recusar` | `agendar`, separado do modo de fora do horário do bot (seção 4.2). Padrão `recusar` (decisão 2 da seção 9). Nome do campo e onde fica (empresa) a fechar no bloco.
- Contrato da **prévia da mensagem**: corpo de entrada (texto e, se preciso, a unidade usada no exemplo), limite de tamanho do texto, e variável desconhecida → 400 com o nome da variável. A prévia não grava e não envia nada.

### 10.4 G4 — entrega e pagamento

- Fechar os envelopes de leitura e escrita de taxas e de formas de pagamento.
- Faixas de CEP **sobrepostas** na mesma unidade → 400, indicando as faixas em conflito.
- **Apelidos de bairro duplicados** na mesma unidade (comparação sem acento e sem maiúsculas, igual à da decisão 9) → 400.
- Campos novos no `GET pedidos` e `GET pedidos/{numero}` (taxa, forma de pagamento, troco, `agendado_para`, tempo estimado): nomes e nulos a fechar no bloco.

### 10.5 G5 — cardápio

- Nomes e semântica dos filtros da lista: `sem_preco`, `delivery`, `alcoolico`, `em_falta`, `categoria` (o que significa verdadeiro, falso e ausente em cada um; como combinam).
- Corpos das operações em lote, da cópia para outra unidade e da ordem das categorias.
- Campo `volta_em` do item em falta (data e hora com fuso; nulo = até alguém reativar).

### 10.6 G6 — CSV

- **Desfazer só a importação mais recente.** As 20 últimas continuam guardadas, mas apenas para consulta (ajusta a decisão 7 da seção 9; a seção 5.4 já dizia "só a mais recente"). Desfazer outra → 409.
- **Confirmação de `substituir`:** a aplicação envia a contagem de desativações que a simulação informou; se a contagem atual for diferente (o catálogo mudou entre a simulação e a aplicação), **409** e nada é gravado. Contagem ausente → 400.

---

## 11. G4 — entrega e pagamento: o que faltava especificar (11/10/2026)

**Situação:** para aprovação. **Nada implementado.** Fecha as pendências da seção 10.4 e junta o G4 com o que mudou desde então (PED1, CFG1). As regras das seções 3.1–3.3, 4.3, 4.4 e 4.6 continuam valendo; aqui está só o que faltava.

### 11.1 Onde estamos

- A unidade já tem `taxa_entrega`, `pedido_minimo`, `aceita_retirada`, `area_entrega`, `formas_pagamento` (texto) e `regras`. **Não existem ainda:** `delivery_ativo`, `taxa_modo`, `frete_gratis_acima`, tempos, as tabelas de taxas e de formas de pagamento, nem bairro/CEP/pagamento/troco no carrinho e no pedido.
- **No By Koji nada disso está preenchido** (as 4 unidades têm taxa, mínimo e retirada nulos). Hoje todo pedido sai com "a unidade confirma". O G4 não muda isso até o cadastro ser feito: unidade sem nada configurado continua exatamente como hoje.
- O carrinho não confere horário, área nem pagamento. O motivo do mínimo ainda se chama `abaixo_do_minimo`.

### 11.2 Taxas de entrega — contrato

`GET /tenants/{t}/unidades/{id}/taxas`

```json
{"modo": "bairro", "taxa_fixa": null, "frete_gratis_acima": 200.0, "delivery_ativo": true,
 "itens": [
   {"id": 12, "tipo": "bairro", "bairro": "Jardim Morumbi", "apelidos": ["Jd. Morumbi", "Jd Morumbi"], "atendido": true, "taxa": 12.0, "ativo": true},
   {"id": 13, "tipo": "bairro", "bairro": "Paraisópolis", "apelidos": [], "atendido": false, "taxa": null, "ativo": true},
   {"id": 14, "tipo": "cep", "cep_inicio": "05650000", "cep_fim": "05659999", "taxa": 15.0, "ativo": true}],
 "resumo": {"bairros": 2, "faixas_cep": 1, "inativos": 0}}
```

| Rota | Corpo | Resposta |
|---|---|---|
| `POST …/taxas` | uma linha (sem `id`) | 201 + a linha |
| `PATCH …/taxas/{tid}` | só os campos enviados | 200 + a linha |
| `DELETE …/taxas/{tid}` | — | 200 `{"ok": true}` |
| `PUT …/taxas` | `{"itens": [linhas]}` — **substitui todas**, tudo ou nada | 200 + o `GET` |

`modo`, `taxa_fixa`, `frete_gratis_acima`, `delivery_ativo` e os tempos mudam por `PATCH …/unidades/{id}` (campos `taxa_modo`, `taxa_entrega`, `frete_gratis_acima`, `delivery_ativo`, `tempo_entrega_min`, `tempo_retirada_min`).

| Regra | Limite |
|---|---|
| `bairro` | 2–80 caracteres |
| `apelidos` | até 10 por bairro, cada um 2–80 |
| `cep_inicio`, `cep_fim` | aceitos com ou sem hífen; guardados com 8 dígitos; `inicio ≤ fim` |
| `taxa` | 0 a 999,99; obrigatória em linha atendida; nula em bairro não atendido |
| Linhas por unidade | até 500 |
| `frete_gratis_acima` | maior que zero |
| `tempo_entrega_min`, `tempo_retirada_min` | 1 a 600 |

**Erros de validação (400)**, sempre com tudo o que está errado numa resposta só:

```json
{"erro": {"codigo": "VALIDATION_ERROR", "detalhes": {"campo": "itens", "motivo": "faixas_sobrepostas",
  "conflitos": [{"a": {"linha": 0, "cep_inicio": "05650000", "cep_fim": "05659999"},
                 "b": {"linha": 2, "cep_inicio": "05655000", "cep_fim": "05660000"}}]}}}
```

```json
{"erro": {"codigo": "VALIDATION_ERROR", "detalhes": {"campo": "itens", "motivo": "bairro_duplicado",
  "conflitos": [{"nome": "jd morumbi", "a": {"linha": 0, "bairro": "Jardim Morumbi"}, "b": {"linha": 3, "bairro": "Morumbi"}}]}}}
```

- **Faixas de CEP sobrepostas** na mesma unidade → 400 `faixas_sobrepostas`. Faixas vizinhas (uma termina em …999 e a outra começa em …000) não são sobreposição.
- **Bairro ou apelido duplicado** na mesma unidade → 400 `bairro_duplicado`. Comparação sem acento, sem maiúsculas, sem pontuação e com espaços repetidos reduzidos a um ("Jd. Morumbi" = "jd morumbi"). Vale entre nomes, entre apelidos e entre nome e apelido, inclusive dentro da mesma linha.
- Só linhas **ativas** entram na conferência e no cálculo. `linha` é a posição no corpo enviado (no `POST`/`PATCH`, o conflito aponta a linha existente pelo `id`).
- Outros motivos: `cep_invalido`, `taxa_obrigatoria`, `limite_de_linhas`, `tipo_invalido`.
- Modo `bairro` ou `cep` sem nenhuma linha ativa: aceito, com `avisos: ["modo_sem_linhas"]` na resposta (na prática fica "a unidade confirma").

### 11.3 Formas de pagamento — contrato

`GET /tenants/{t}/unidades/{id}/pagamentos`

```json
{"itens": [
  {"codigo": "pix", "rotulo": "Pix", "aceita_troco": false, "instrucoes": "Chave: 11.222.333/0001-44", "ativo": true},
  {"codigo": "cartao_entrega", "rotulo": "Cartão na entrega", "aceita_troco": false, "instrucoes": null, "ativo": true},
  {"codigo": "dinheiro", "rotulo": "Dinheiro", "aceita_troco": true, "instrucoes": null, "ativo": true}],
 "sugestoes": ["pix", "cartao_entrega", "dinheiro", "vale_refeicao", "pagamento_online"]}
```

`PUT …/pagamentos` com `{"itens": [...]}` substitui a lista inteira; a ordem do corpo é a ordem mostrada ao cliente. Resposta: o `GET`.

| Regra | Limite |
|---|---|
| `codigo` | `[a-z0-9_]`, 2–30; único na unidade (repetido → 400 `codigo_duplicado`) |
| `rotulo` | 2–40 |
| `aceita_troco` | só pode ser verdadeiro em `dinheiro` (senão 400 `troco_so_em_dinheiro`) |
| `instrucoes` | até 300; vai ao agente. Passa pela mesma análise das regras da casa (CFG1): instrução de sistema → 400 `texto_bloqueado`, com o trecho |
| Quantidade | até 12 |

Lista vazia = nenhuma forma cadastrada → o campo `pagamento` do pedido é opcional e "a unidade confirma", como hoje.

### 11.4 Carrinho, fechamento e motivos de recusa (decididos no servidor)

Todos 422, em `erro.detalhes.motivo`, com dados calculados pelo gateway:

| Motivo | Onde | Quando | `detalhes` |
|---|---|---|---|
| `fora_area` | `definir_entrega`, `fechar_pedido` | CEP fora de todas as faixas (modo `cep`, ou modo `bairro` com faixas cadastradas e bairro não reconhecido), ou bairro marcado como não atendido | `modo`, `aceita_retirada` |
| `bairro_nao_reconhecido` | `definir_entrega`, `fechar_pedido` | Modo `bairro`, bairro não bate com nenhum nome nem apelido, e não veio CEP | `pedir`: `"cep"` se a unidade tem faixas de CEP; senão `"confirmar_bairro"` |
| `abaixo_minimo` | `fechar_pedido` | Subtotal menor que o mínimo | `pedido_minimo`, `subtotal`, `falta` |
| `pagamento_invalido` | `definir_entrega`, `fechar_pedido` | Código que não é forma ativa da unidade; ou unidade com formas e nenhuma escolhida ao fechar | `aceitos: [{"codigo", "rotulo"}]` |
| `troco_invalido` | `definir_entrega`, `fechar_pedido` | Troco com forma que não aceita, ou menor que o total | `total`, `aceita_troco` |
| `delivery_indisponivel` | `definir_entrega`, `fechar_pedido` | `delivery_ativo = false` | `aceita_retirada` |
| `unidade_fechada` | `adicionar_item`, `fechar_pedido` | Unidade do carrinho fechada agora (grade, exceção ou pausa) | `proxima_abertura`, `motivo_fechada` |

- **`abaixo_do_minimo` deixa de existir**: passa a ser `abaixo_minimo` (decisão 9.3). O prompt do Skyone precisa trocar o nome no mesmo dia.
- **Bairro não reconhecido sem faixa de CEP** (`pedir: "confirmar_bairro"`): o agente confirma a grafia uma vez; se o cliente mantiver, o agente reenvia com `bairro_confirmado: true`, a taxa fica "a confirmar" e o pedido segue, marcado `taxa_a_confirmar: true` para a unidade.
- **Modo `bairro` com faixas de CEP também cadastradas:** primeiro o bairro; não reconhecido e com CEP → o CEP decide.
- `faltando` no carrinho pode trazer `bairro`, `cep` e `pagamento` (além dos atuais).
- **Unidade fechada:** no G4 só existe a recusa. O pedido agendado (`agendado_para`) depende do modo da empresa, que é do G3; a coluna já entra agora, vazia.
- **`unidade_fechada` muda o piloto:** com o By Koji configurado para recusar (decisão 9.2), não será mais possível montar pedido de teste com a unidade fechada. Ver decisão 3 abaixo.
- Ordem das conferências em `fechar_pedido`: carrinho vazio → itens vendáveis → preço alterado → **unidade fechada** → modalidade → **delivery indisponível** / retirada indisponível → endereço → **bairro/CEP e área** → maioridade → **mínimo** → **pagamento** → **troco**.

### 11.5 Campos novos nos pedidos (`GET pedidos`, `GET pedidos/{numero}`) e no carrinho

| Campo | Tipo | Nulo significa |
|---|---|---|
| `bairro` | texto | não informado, ou retirada |
| `cep` | 8 dígitos | não informado |
| `taxa_entrega` | número (já existe) | a unidade confirma (ver `taxa_origem`) |
| `taxa_origem` | `fixa`, `bairro`, `cep`, `frete_gratis`, `a_confirmar`; nulo em retirada | — |
| `frete_gratis` | booleano | sempre presente |
| `pagamento` | `{"codigo", "rotulo"}` | não escolhido (unidade sem formas cadastradas) |
| `troco_para` | número | sem troco |
| `tempo_estimado_min` | inteiro | a unidade não cadastrou tempo |
| `agendado_para` | data/hora com fuso | pedido para agora |

- No detalhe, esses campos ficam também dentro de `entrega` (junto de `modalidade`, `endereco`, `horario`, `observacao`). Os campos antigos não mudam.
- `tempo_estimado_min` é a estimativa da unidade copiada no fechamento; `previsao_min` (PED1) é o que a unidade informa ao confirmar. O aviso de confirmado usa `previsao_min`; se a unidade não informar, ver decisão 4.
- O aviso de novo pedido à unidade (WhatsApp) ganha bairro/CEP, forma de pagamento, troco e a marca "taxa a confirmar".
- Anonimização do contato apaga também `bairro` e `cep`.

### 11.6 `info_unidades` (Skill) e pacote

Cada unidade ganha:

```json
{"delivery": true,
 "entrega": {"taxa_modo": "bairro", "taxa_fixa": null, "frete_gratis_acima": 200.0, "tempo_min": 45,
             "bairros_atendidos": 38, "precisa_de": "bairro"},
 "retirada": {"aceita": true, "tempo_min": 20},
 "pedido_minimo": 60.0,
 "pagamentos": [{"codigo": "pix", "rotulo": "Pix", "aceita_troco": false, "instrucoes": "Chave: …"}]}
```

- `delivery` deixa de vir sempre nulo: verdadeiro, falso, ou nulo quando a unidade não informou.
- `precisa_de`: `bairro`, `cep` ou nulo — o que o agente deve pedir ao cliente para calcular a taxa.
- A lista de bairros **não** vai inteira (pode ter centenas). Para "vocês entregam no Morumbi? quanto é?", ver decisão 2.
- Os campos atuais (`taxa_entrega`, `aceita_retirada`, `formas_pagamento`, `area_entrega`) continuam, para não quebrar o prompt de hoje.
- O bloco `unidades` do pacote não cresce; o agente usa a Skill.

### 11.7 Banco e blocos

Migração aditiva (próximo número livre): colunas da unidade (`delivery_ativo`, `taxa_modo`, `frete_gratis_acima`, `tempo_entrega_min`, `tempo_retirada_min`), tabelas `unidade_taxas` e `unidade_pagamentos`, colunas de carrinho e pedido (`bairro`, `cep`, `pagamento`, `troco_para`, `agendado_para`; no pedido também `frete_gratis`, `tempo_estimado_min`, `taxa_origem`, `pagamento_rotulo`). A imagem anterior segue funcionando.

| Bloco | Conteúdo | O que muda para o cliente |
|---|---|---|
| **G4-a — cadastro** | Campos da unidade, taxas, formas de pagamento (rotas e validações da 11.2 e 11.3), `info_unidades`, histórico de alterações | Nada. O agente passa a poder informar taxa e pagamento |
| **G4-b — regras** | `definir_entrega` com bairro/CEP/pagamento/troco, cálculo da taxa, frete grátis, motivos da 11.4, campos da 11.5, aviso à unidade | Recusas novas; total com taxa calculada |

Parada e relatório entre os dois. Toda escrita de taxa e de pagamento entra no histórico de gestão (`gestao_alteracoes`) e na auditoria.

**Testes (além dos da seção 8):**
- Taxas: sobreposição total, parcial, faixa dentro de outra, vizinhas (aceitas), linha inativa não conflita; conflito no `POST` e no `PATCH` contra linha existente; `PUT` é tudo ou nada (um erro não grava nenhuma linha).
- Bairros: duplicado por acento, maiúscula, pontuação e espaço; apelido igual ao nome de outro bairro; apelido repetido na mesma linha.
- CEP com hífen e sem; no primeiro e no último número da faixa; um abaixo e um acima.
- Pagamentos: código repetido; troco fora de dinheiro; instrução bloqueada; ordem preservada; lista vazia volta ao "a unidade confirma".
- Cada motivo da 11.4 com seus `detalhes`; ordem das conferências; `abaixo_do_minimo` não aparece mais.
- Frete grátis exatamente no valor e um centavo abaixo; total = subtotal + taxa em todos os modos; taxa "a confirmar" não soma.
- Unidade sem nada configurado: carrinho, fechamento e respostas idênticos aos de hoje (regressão).
- `unidade_fechada` em adicionar e em fechar, com `proxima_abertura`; pausa e exceção contam.
- Campos novos no `GET pedidos`, no detalhe e no carrinho, com os nulos da tabela 11.5.
- `info_unidades` nos três estados de `delivery` e de `precisa_de`.
- Isolamento: taxas, pagamentos e conflitos nunca cruzam unidades nem empresas.
- Anonimização limpa bairro e CEP.

### 11.8 Decisões que preciso de você

1. **Quem pode alterar taxas e formas de pagamento?** Hoje a gestão de unidade aceita qualquer papel com `X-Actor`. Proposta: só `admin` e `plataforma` (`membro` lê), como nos textos de aviso.
2. **Consulta de entrega antes do carrinho:** incluir uma Skill nova `consultar_entrega` (unidade + bairro ou CEP → atende ou não, taxa, tempo, frete grátis a partir de quanto)? Sem ela, o agente só descobre a taxa depois de montar o carrinho.
3. **`unidade_fechada` e o piloto:** com a recusa ligada, não dá para testar pedido com a unidade fechada. Opções: (a) valer para todos, inclusive o piloto; (b) uma chave por agente, `pedido_com_unidade_fechada: recusar | permitir`, nascendo `recusar` e ficando `permitir` no By Koji até você mandar virar. Proponho (b).
4. **Previsão no aviso de confirmado:** quando a unidade confirma sem informar `previsao_min`, usar o `tempo_estimado_min` cadastrado da unidade no aviso ("Previsão: cerca de 45 minutos"), ou só mostrar previsão quando alguém a informar?
5. **Bairro não reconhecido sem faixa de CEP:** o fluxo de confirmar a grafia uma vez e seguir com "taxa a confirmar" está bom, ou prefere recusar o delivery nesse caso?
6. **Formas de pagamento:** os códigos sugeridos (`pix`, `cartao_entrega`, `dinheiro`, `vale_refeicao`, `pagamento_online`) servem? Código livre além deles é permitido (proposta) ou a lista é fechada?
7. **Troca de `abaixo_do_minimo` por `abaixo_minimo`:** sem período com os dois nomes (proposta: troca seca, junto com o ajuste do prompt), ou manter os dois por um tempo?
8. **Divisão:** G4-a (cadastro) e G4-b (regras) com parada entre eles, como proposto?
