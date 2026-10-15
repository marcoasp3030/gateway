# MULTI1 — Pronto para outros tipos de agente e outras empresas

**Situação:** aprovado pelo Marco em 15/10/2026 (6 decisões). Uma decisão de transição está em aberto (seção 1.4). **Implementação só depois dela.**
**Origem:** `docs/AUDITORIA_MULTIAGENTE.md`, seção 7 ("ajuste pequeno").
**Regra do bloco:** nada muda para o By Koji.

---

## 1. Escopo de empresa na API admin (segurança)

### 1.1 Cabeçalho

O servidor do painel passa a enviar, em toda chamada a `/admin/v1`, a empresa do usuário logado:

```
X-Tenant-Scope: <id da empresa da sessão>
```

Vem da sessão, como `X-Role` e `X-Actor`; nunca do navegador.

### 1.2 Regras

| Papel (`X-Role`) | Rota com empresa no caminho (`/admin/v1/tenants/{t}/…`) | Rota sem empresa no caminho |
|---|---|---|
| `plataforma` | Vê todas, como hoje. O cabeçalho é ignorado | Como hoje |
| `admin` / `membro` com `X-Tenant-Scope` | Só a própria empresa. Outra empresa responde **404** (`NOT_FOUND`, sem dizer que existe) | `GET /tenants`: devolve só a própria empresa. Leituras de catálogo da plataforma (`/modulos`, `/segmentos`, `/modelos-agente`): permitidas; com `?tenant=` de outra empresa, 404. Escritas (`POST /tenants`, `POST`/`PATCH /modelos-agente`) e visões de toda a plataforma (`/fila`, `/auditoria/plataforma`): **403** |
| `admin` / `membro` sem `X-Tenant-Scope` | Ver 1.4 | Ver 1.4 |

- A conferência fica no gateway, antes de qualquer rota, para todas as rotas `/admin/v1`.
- Os papéis continuam valendo como hoje dentro da empresa (membro lê; admin edita o que é de admin).
- `X-Tenant-Scope` com empresa que não existe: tudo responde 404.

### 1.3 Testes

Membro e admin da empresa A: leem e escrevem só na A; recebem 404 em qualquer rota da B (agentes, conversas, pedidos, catálogo, unidades, atendimento, skills/registro, gestão); veem só a A em `GET /tenants`; recebem 403 ao criar empresa ou segmento e na fila da plataforma. Plataforma vê A e B, com ou sem cabeçalho.

### 1.4 Decisão em aberto: o painel de hoje ainda não manda o cabeçalho

Assim que o gateway exigir o cabeçalho, todo usuário do painel que não é `plataforma` (inclusive os do By Koji) fica sem acesso até o painel publicar a mudança.

| Opção | Como fica | Efeito |
|---|---|---|
| **A (proposta)** | Implanta com a conferência valendo **quando o cabeçalho vem**. Sem ele, comportamento de hoje (e registro no log). Depois que o [PAINEL] publicar o envio, ligo `ESCOPO_EMPRESA_OBRIGATORIO=on` (variável do gateway, sem nova implantação de código), e aí a ausência passa a ser **403** | Nada quebra; a proteção completa vale quando o painel publicar |
| B | Exige o cabeçalho desde a implantação | Proteção imediata; usuários não-plataforma ficam sem painel até o [PAINEL] publicar |

---

## 2. Tipo de agente `atendimento_geral`

Segmento novo, versão 1, criado por migração:

| Campo | Valor |
|---|---|
| `id` / `nome` | `atendimento_geral` / "Atendimento geral" |
| `tipo` | `atendimento` |
| `modulos` | `[]` (sem catálogo, pedidos nem carrinho) |
| `comportamento_padrao` | O mesmo do `delivery_restaurante` v3 (botões, refazer turno, reações, aviso à equipe ligados; fora do horário "atender"; envio de teste) |
| `perfil_padrao` | O mesmo do delivery (tom equilibrado, emojis moderados, respostas curtas) |
| `desfechos` | `[]` (como o delivery) |
| `motivos_transferencia` | `pediu_humano`, `reclamacao`, `pagamento`, `fora_do_escopo`, `assunto_proibido` |
| `tipos_solicitacao` | `orcamento`, `parceria`, `outro` |

Aparece na lista de modelos do painel (`GET /modelos-agente`). Nenhum agente existente muda.

---

## 3. Motivos de transferência e tipos de solicitação por tipo de agente

`modelos_agente` ganha duas colunas: `motivos_transferencia` e `tipos_solicitacao`. **Nulo = a lista completa de hoje**, que é o caso do `delivery_restaurante` (todas as versões), do `atendente_delivery` e do `assistente_generico`. Por isso nada muda para o By Koji.

| Onde | Efeito |
|---|---|
| Perfil do **agente** (`…/agentes/{id}/atendimento`) | O esquema mostra só os motivos do tipo do agente; motivo de fora é recusado (`motivo: opcao`) |
| Perfil da **empresa** | Continua aceitando a lista completa (a empresa pode ter agentes de tipos diferentes) |
| Pacote (`perfil_atendimento.motivos_transferencia`) | O que a empresa ou o agente definiu, **filtrado pelos motivos do tipo do agente**. `pediu_humano` e `reclamacao` continuam fixos |
| Aviso à equipe (`solicitacao.tipo` no Retorno) | Tipo fora da lista do tipo do agente vira `outro` (é o que já acontece com tipo desconhecido) |
| `configuracao_efetiva` do agente | Ganha `motivos_transferencia_permitidos` e `tipos_solicitacao_permitidos` (as listas do tipo, já resolvidas) |
| `GET /modelos-agente` | Cada modelo traz as duas listas (nulo = todas) |

Motivo de transferência devolvido pelo agente no Retorno continua livre: o gateway não recusa transferência por motivo.

---

## 4. Configuração de pedido escondida para quem não tem o módulo

Campos: **`carrinho_expira_h`** e **`acoes_recentes_min`** (a lista de ações recentes só conta Skills de carrinho e pedido).

"Tem o módulo" = o tipo do agente inclui `pedidos` **e** o módulo está ligado na empresa.

| Onde | Agente sem o módulo de pedidos |
|---|---|
| `configuracao_efetiva` (`GET …/agentes/{id}`) | Os dois campos não aparecem (nem em `origem_config`) |
| Esquema e efetivo de `GET …/agentes/{id}/atendimento` | Não aparecem |
| `PATCH …/agentes/{id}` e rascunho do atendimento do agente | 400 com `campo` e `motivo: modulo_desligado` |
| Atendimento da **empresa** e `PUT …/config` | Aparecem e são aceitos só se a empresa tiver o módulo de pedidos ligado; senão, mesmo tratamento |
| Pacote | Inalterado: `acoes_recentes` e `acoes_ja_executadas` continuam presentes (listas vazias) |

---

## 5. Endereço de webhook cadastrado na uazapi pelo gateway

### 5.1 Quando

| Evento | O gateway faz |
|---|---|
| Canal uazapi **criado** com credenciais | Cadastra o webhook na instância |
| Canal uazapi **alterado** com `credenciais` novas | Cadastra de novo (pode ser outra instância) |
| **Rotação do segredo** (`…/rotacionar-segredo`) | Cadastra o endereço novo (antes era manual) |
| **Refazer à mão** (nova rota, seção 5.3) | Cadastra de novo, com o segredo atual |

Alterar só nome, agente ou ativo não chama a uazapi. **O canal do By Koji não é tocado** (nenhum desses eventos acontece com ele).

### 5.2 O que é cadastrado

Igual ao que a instância do By Koji tem hoje (conferido por leitura):

```json
{"url": "<webhook_url do canal, com o segredo>", "events": ["messages", "connection"],
 "excludeMessages": ["isGroupYes"], "enabled": true, "addUrlEvents": false, "addUrlTypesMessages": false}
```

- Antes de gravar, o gateway lê o webhook atual da instância (`GET /webhook`) e, se houver um, **atualiza o mesmo** (envia o `id`) em vez de criar outro.
- A chamada passa pela mesma proteção de saída das demais (https, sem redirecionamento, sem IP interno).
- **Falha na uazapi não desfaz o canal:** o canal é salvo e a resposta diz o que houve.

### 5.3 Contrato

Resposta de criação ou alteração de canal uazapi, rotação de segredo e refazer:

```json
"webhook_uazapi": {"registrado": true}
"webhook_uazapi": {"registrado": false, "motivo": "sem_credenciais" | "provedor_recusou" | "provedor_inacessivel" | "endereco_publico_ausente"}
```

Nova rota para refazer à mão (só `admin` ou `plataforma`):

```
POST /admin/v1/tenants/{t}/canais/{id}/webhook   →   {"id": "...", "webhook_uazapi": {...}}
```

O endereço com o segredo nunca volta nessa rota (só na criação e na rotação, como hoje). Toda tentativa entra na auditoria (`canal.webhook_registrado` ou `canal.webhook_falhou`).

---

## 6. Módulos ligados por agente, para o menu do painel

| Rota | Novo |
|---|---|
| `GET /admin/v1/tenants/{t}/agentes/{id}/modulos` | **Nova.** `{"agente_id", "segmento": {"id", "versao"}, "modulos": [...], "do_segmento": [...], "da_empresa": [...]}`. `modulos` = os que o agente usa de fato (do tipo **e** ligados na empresa) |
| `GET /admin/v1/tenants/{t}/agentes` e `…/agentes/{id}` | Cada agente ganha `modulos` (os de fato) |
| `GET /admin/v1/tenants/{t}/modulos` | Ganha `em_uso`: a união dos `modulos` dos agentes ativos da empresa. **É o que o menu usa** |

Sugestão para o [PAINEL]: mostrar Cardápio, Pedidos, Avisos de pedido e o contador de pedidos só com `catalogo`/`pedidos` em `em_uso`. Unidades e horários serve a qualquer empresa, mas os campos de entrega (taxa, pedido mínimo, retirada) só com `pedidos`.

---

## 7. Banco, testes e implantação

- **Migração 043, aditiva:** colunas `motivos_transferencia` e `tipos_solicitacao` em `modelos_agente`; segmento `atendimento_geral` v1.
- **Variável nova:** `ESCOPO_EMPRESA_OBRIGATORIO` (padrão conforme a decisão 1.4).
- **Testes:** isolamento por cabeçalho em todas as famílias de rotas; segmento novo; listas por tipo (perfil, pacote, solicitação); campos de pedido escondidos e recusados; cadastro do webhook com provedor simulado (criação, troca de credencial, rotação, refazer, falha, atualização do webhook existente); módulos por agente; By Koji inalterado (pacote, configuração efetiva e esquema iguais aos de antes).
- Implantação pelo `scripts/implantar.sh` depois da decisão 1.4.
