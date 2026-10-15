# MULTI1 — Plataforma de agentes de vários segmentos

**Situação:** revisão 2 (15/10/2026), depois da mudança de rumo do Marco no item 2. **Nada implementado.** Implementação só depois do ok, com as decisões da seção 9.
**Meta:** agentes de vários segmentos (delivery, clínica e serviços, financeiro e cobrança, comercial, jurídico, atendimento geral) na mesma plataforma, com o segmento como **cadastro** e o que é de negócio dentro de **módulos**.
**Regra do bloco:** nada muda para o By Koji (pacote, respostas das Skills e comportamento iguais aos de antes, conferido por teste).

---

## 1. O que já existe e o que falta

**Já existe:**
- O "modelo de agente" já é cadastro: tabela `modelos_agente`, versionada (`id` + `versao`), com nome, módulos, comportamento padrão, perfil padrão, desfechos e um campo `skyone_fluxo_modelo`.
- Rotas `POST /admin/v1/modelos-agente` (cria a versão 1) e `PATCH …/{id}` (cria a próxima versão). O agente fica preso a uma versão (`modelo_id` + `modelo_versao`).
- Módulos têm registro próprio (`modulos/__init__.py`) com contexto, rotas e escopos, ligados por empresa (`modulos_tenant`) e pelo modelo.

**Falta:**
- O modelo não declara segmento, módulos permitidos × ligados, motivos de transferência e tipos de solicitação.
- Qualquer papel do painel cria e altera modelos (não há trava de plataforma).
- O módulo não declara motivos, tipos de solicitação, configurações, telas nem regras próprias, e o núcleo tem regras de delivery dentro dele (seção 3).

---

## 2. Modelo de agente como cadastro

### 2.1 Campos

| Campo | Novo? | Regra |
|---|---|---|
| `id` | — | Identificador estável (`[a-z0-9_]{3,80}`) |
| `nome` | — | Nome para o painel ("Delivery", "Atendimento geral") |
| `segmento` | **novo** | Identificador livre (`[a-z0-9_]{2,40}`): `delivery`, `servicos`, `financeiro`, `comercial`, `juridico`, `geral`… Sem lista fixa no código, para criar segmento sem implantação |
| `modulos_permitidos` | **novo** (substitui `modulos`) | Módulos que um agente deste modelo pode usar. Só módulos do registro |
| `modulos_padrao` | **novo** | Subconjunto dos permitidos que nasce ligado em um agente novo |
| `motivos_transferencia` | **novo** | Lista `[{"id", "rotulo"}]` própria do modelo (ver 2.3) |
| `tipos_solicitacao` | **novo** | Lista `[{"id", "rotulo"}]` própria do modelo (ver 2.3) |
| `perfil_padrao` | — | Como hoje |
| `comportamento_padrao` | — | Como hoje |
| `desfechos` | — | Como hoje |
| `skyone_ref` | **novo** (no lugar de `skyone_fluxo_modelo`, que fica como sinônimo) | Texto livre (até 200): nome ou id do agente/fluxo do Skyone que este modelo usa. Só informativo, para o painel mostrar qual fluxo configurar |
| `ativo` | **novo** | `false` = não aparece para criar agente novo; agentes existentes continuam |
| `tipo` | — | `atendimento` / `interno`, como hoje |

Alterar o modelo cria uma versão nova (como hoje). O agente continua preso à versão; passar para a nova é uma ação explícita, como o By Koji fez do v2 para o v3.

### 2.2 Módulos ligados: modelo, agente e empresa

Um módulo vale para um agente quando **as três** valem:

1. está em `modulos_permitidos` do modelo do agente;
2. está ligado no agente: campo novo `agentes.modulos` (lista); nulo = `modulos_padrao` do modelo;
3. está ligado na empresa (`modulos_tenant`, como hoje).

### 2.3 Motivos de transferência e tipos de solicitação

A lista que vale para um agente é a soma de três camadas:

| Camada | Motivos de transferência | Tipos de solicitação |
|---|---|---|
| **Núcleo** (fixos, todo agente) | `pediu_humano`, `reclamacao` | `outro` |
| **Modelo** | Os do cadastro do modelo | Os do cadastro do modelo |
| **Módulos ligados** | Os que cada módulo declara (ex.: pedidos → `pedido_para_confirmar`) | Os que cada módulo declara |

- Cada item tem `id` e `rotulo`; o rótulo aparece no alerta de transbordo e no painel. Hoje os rótulos estão fixos no código do módulo de pedidos.
- **Perfil do agente:** só aceita motivos da lista dele. **Perfil da empresa:** aceita a união das listas dos agentes da empresa.
- **Pacote:** `perfil_atendimento.motivos_transferencia` vem filtrado pela lista do agente.
- **Retorno:** tipo de solicitação fora da lista vira `outro`; motivo de transferência continua livre (o gateway não recusa transferência).
- Regras que hoje dependem de motivo específico passam a ser declaradas: "converter transferência em aviso à equipe" (hoje `evento_ou_encomenda` e `catering`) vira atributo do motivo (`converte_em_solicitacao: true`).

### 2.4 Os dois modelos criados

| | `delivery_restaurante` (existente, versão nova **4**) | `atendimento_geral` (novo, versão 1) |
|---|---|---|
| `nome` / `segmento` | "Delivery" / `delivery` | "Atendimento geral" / `geral` |
| `modulos_permitidos` / `modulos_padrao` | `catalogo`, `pedidos` / `catalogo`, `pedidos` | `[]` / `[]` |
| Motivos do modelo | `pagamento`, `restricao_alimentar`, `evento_ou_encomenda` (converte), `catering` (converte), `fora_do_escopo`, `assunto_proibido` | `pagamento`, `fora_do_escopo`, `assunto_proibido` |
| Tipos do modelo | `evento`, `catering`, `encomenda`, `orcamento`, `parceria` | `orcamento`, `parceria` |
| Comportamento e perfil padrão | Os do v3 | Os do v3 do delivery |
| `skyone_ref` | O fluxo atual do By Koji | Vazio até o Marco informar |

- **O By Koji não muda:** continua preso ao v3. As listas efetivas dele (núcleo + modelo + módulo de pedidos) somam exatamente os 9 motivos e 6 tipos de hoje. O v3 recebe só os campos novos por migração, com os mesmos valores do v4; o v4 existe para os próximos agentes de delivery.
- **Modelos antigos** (`assistente_generico`, `atendente_delivery`): ficam `ativo: false`. Os agentes de teste que usam o primeiro continuam funcionando.

### 2.5 Rotas

| Rota | Quem | O quê |
|---|---|---|
| `GET /admin/v1/modelos-agente` | Todos | Lista (com `?segmento=`, `?ativo=`); traz os campos novos |
| `POST /admin/v1/modelos-agente` | **Só plataforma** (hoje qualquer papel) | Cria a versão 1 |
| `PATCH /admin/v1/modelos-agente/{id}` | **Só plataforma** | Cria a próxima versão |
| `GET /admin/v1/segmentos` | Todos | Já existe; passa a agrupar os modelos por `segmento` |
| `PATCH …/agentes/{id}` com `modulos` | Admin e plataforma | Liga ou desliga módulos no agente, dentro dos permitidos |
| `PATCH …/agentes/{id}` com `modelo_versao` | Plataforma | Passa o agente para outra versão do modelo |

Tela no painel só para plataforma: depois, pelo [PAINEL].

---

## 3. O que é de delivery e ainda está no núcleo

| # | Onde | O que é |
|---|---|---|
| 1 | `nucleo/pipeline.py` (recepção) | WhatsApp da unidade e "OK / NÃO <número>" decidindo pedido |
| 2 | `nucleo/pipeline.py` | Guarda de anúncio falso de pedido (PED2) |
| 3 | `nucleo/pipeline.py` | Guarda de transferência sem recusa, presa ao motivo `pedido_para_confirmar` (HOR2) |
| 4 | `nucleo/pipeline.py` | Expiração do carrinho antes do turno |
| 5 | `nucleo/pipeline.py` | "Skills com efeito" (não refazer turno depois de `fechar_pedido`/`cancelar_pedido`) e ações do turno refeito, lidas só de Skills de carrinho |
| 6 | `nucleo/pipeline.py` | Modo "só avisar" fora do horário deixa o agente atender quem tem pedido ativo |
| 7 | `nucleo/pipeline.py` | Aviso de espera considera "carrinho aberto" como algo em andamento |
| 8 | `nucleo/pipeline.py` + `modulos/pedidos/avisos.py` | O alerta de transbordo (função do núcleo) mora no módulo de pedidos e inclui a linha "Pedido:" |
| 9 | `nucleo/fila.py` | O despachante de avisos internos (transbordo, solicitações, pedidos) mora no módulo de pedidos |
| 10 | `nucleo/solicitacoes.py` | Tipos fixos (`evento`, `catering`, `encomenda`…), motivos que convertem (`evento_ou_encomenda`, `catering`) e proteção de "venda em andamento" (carrinho e pedido ativo) |
| 11 | `nucleo/atendimento.py` | Lista fixa de motivos de transferência com os de restaurante; comportamentos `carrinho_expira_h` e `acoes_recentes_min` |
| 12 | `nucleo/configuracao.py` / `nucleo/banco.py` | Padrões e colunas desses comportamentos |
| 13 | `agentes/skyone.py` | `acoes_recentes` lida do módulo de pedidos; `unidades[].tem_cardapio`/`itens_no_cardapio`; `proxima_abertura` filtrada por cardápio; ação `pedido_fechado` no contrato |
| 14 | `nucleo/horarios.py` | Campos de entrega da unidade no pacote (`taxa_entrega`, `pedido_minimo`, `area_entrega`, `aceita_retirada`, `formas_pagamento`); contagem de itens do cardápio |
| 15 | `nucleo/expediente.py` | Parâmetro "com cardápio" na próxima abertura |
| 16 | `nucleo/botoes.py` | `pedido_fechado` entre as ações que aceitam botões |
| 17 | `nucleo/skill_registro.py` | Mapa fixo das rotas de Skill (catálogo, pedidos, unidades) |
| 18 | `canais/rotas.py` (métricas) | Métricas de anúncio falso e de transferência sem recusa |
| 19 | `admin/gestao.py` | Lista de conversas com "pedido aberto" e unidade do carrinho |
| 20 | `admin/contatos.py` (LGPD) | Anonimização de carrinhos e pedidos |
| 21 | `admin/unidades.py`, `admin/entrega.py`, `modulos/unidades.py` | Campos de entrega da unidade (delivery ativo, taxas, frete, tempos, pedido mínimo, retirada, formas de pagamento) misturados com o cadastro geral da unidade |

**Genéricos e que ficam no núcleo:** unidades (como "locais de atendimento", com horário, exceções e pausa), calendário, fora do horário, horário da equipe, transferência, aviso à equipe, reações, botões, aviso de espera, perfil e comportamentos, fila, consumo e alertas.

---

## 4. O que cada módulo declara

O registro de módulos (`modulos/__init__.py`) passa a ter, por módulo:

| Declaração | Exemplo no módulo `pedidos` |
|---|---|
| `contexto` | `contexto_modulos.pedidos` (carrinho, pedidos) — já existe |
| `skills` | Rotas, escopo, nome da Skill para o registro de chamadas e quais têm efeito para fora (`fechar_pedido`, `cancelar_pedido`) |
| `motivos_transferencia` / `tipos_solicitacao` | `pedido_para_confirmar` |
| `comportamentos` | `carrinho_expira_h`, `acoes_recentes_min` (só aparecem para agente com o módulo) |
| `campos_da_unidade` | Os de entrega (delivery ativo, taxas, pedido mínimo, retirada, pagamentos), no cadastro e no pacote |
| `ganchos` | `antes_do_turno` (expirar carrinho), `numero_interno` (WhatsApp da unidade), `em_andamento` (carrinho/pedido ativo, para o "só avisar", o aviso de espera e a proteção da solicitação), `guardas_da_resposta` (anúncio falso), `resumo_para_alerta` (linha "Pedido:"), `resumo_da_conversa` (painel), `anonimizar` (LGPD) |
| `motivos_com_recusa` | `pedido_para_confirmar`: a guarda genérica de "transferência sem recusa" só vale para motivos declarados assim |
| `telas` | Identificadores para o menu do painel: `cardapio`, `pedidos`, `avisos_de_pedido` |
| `metricas` | Bloco próprio em `GET …/metricas` |

O núcleo chama esses ganchos pelo registro e deixa de importar o módulo de pedidos.

---

## 5. Pacote: bloco `agente`

```json
"agente": {
  "modelo": {"id": "delivery_restaurante", "versao": 3, "nome": "Delivery"},
  "segmento": "delivery",
  "modulos": ["catalogo", "pedidos"],
  "motivos_transferencia": [{"id": "pediu_humano", "rotulo": "Cliente pediu atendente"}, "…"],
  "tipos_solicitacao": [{"id": "evento", "rotulo": "Evento"}, "…", {"id": "outro", "rotulo": "Outro"}]
}
```

- Sempre presente, na raiz. `modulos` = os que valem para o agente (seção 2.2), na mesma ordem do registro.
- `motivos_transferencia` e `tipos_solicitacao`: as listas efetivas (seção 2.3), para o Normalizar genérico montar as instruções sem lista fixa no prompt.
- Os campos de hoje (`agente_id`, `modelo_id`, `contexto_modulos`…) continuam iguais.

---

## 6. Segurança: empresa da sessão na API admin

Sem mudança desde a revisão 1:

- Cabeçalho `X-Tenant-Scope: <empresa da sessão>`, enviado pelo servidor do painel.
- Para `admin` e `membro`: rota de outra empresa responde 404; `GET /tenants` devolve só a própria; escritas globais respondem 403 (criar empresa e criar ou alterar modelo de agente) e as visões de toda a plataforma (`/fila`, `/auditoria/plataforma`) também.
- `plataforma` vê tudo.
- Falta decidir o que acontece quando o cabeçalho não vem (decisão 1).

---

## 7. Demais itens

| Item | Como fica |
|---|---|
| Esconder configuração de pedido | Generalizado: comportamentos declarados por um módulo só aparecem, valem e são aceitos para agente (ou empresa) com o módulo. Hoje: `carrinho_expira_h` e `acoes_recentes_min` |
| Webhook da uazapi | Como na revisão 1: cadastro automático ao criar o canal, trocar credenciais e rotacionar o segredo; rota `POST …/canais/{id}/webhook` para refazer; mesmos eventos da instância do By Koji; falha não desfaz o canal |
| Módulos na API | `GET …/agentes/{id}/modulos`, `modulos` em cada agente e `em_uso` em `GET …/tenants/{t}/modulos`; e mais `telas` (os identificadores declarados pelos módulos em uso) para o menu do painel |

---

## 8. Blocos

| Bloco | Conteúdo | Tamanho | Muda comportamento? |
|---|---|---|---|
| **MULTI1-a** | Segurança da empresa; modelo de agente como cadastro (campos, trava de plataforma, módulos por agente, os dois modelos); motivos e tipos por camadas; bloco `agente` no pacote; configuração de pedido escondida; webhook da uazapi; módulos na API | Grande (1 bloco, migração 043) | Só aditivo. By Koji igual |
| **MULTI1-b** | Tirar o delivery do núcleo: registro de módulos com as declarações da seção 4 e mudança dos 21 pontos da seção 3 para o módulo `pedidos` (e `catalogo`) | Grande (1 bloco, sem migração de dados) | Nenhuma mudança visível: refatoração com teste de "pacote e respostas iguais" |
| Painel | Tela de modelos (plataforma), menu por `telas`, campos de entrega só com pedidos | Do [PAINEL] | — |

Primeiro o MULTI1-a, porque entrega o que um segundo agente precisa (cadastro, pacote, segurança). O MULTI1-b é o que garante "núcleo sem regra de segmento" e pode vir logo depois, sem pressa de produção.

---

## 9. Decisões

| # | Decisão | Proposta |
|---|---|---|
| 1 | O painel de hoje não manda `X-Tenant-Scope` | **A:** valer quando o cabeçalho vem; ligar a exigência (`ESCOPO_EMPRESA_OBRIGATORIO=on`) quando o [PAINEL] publicar. Alternativa B: exigir já, com os usuários não-plataforma sem painel até lá |
| 2 | Onde se liga o módulo | Três chaves: permitido no modelo, ligado no agente (padrão do modelo) e ligado na empresa |
| 3 | Id do modelo de delivery | Mantém `delivery_restaurante` (o By Koji está preso a ele), com `nome` "Delivery" e `segmento` `delivery`; versão 4 nova; v3 recebe os campos novos com os mesmos valores |
| 4 | Modelos antigos | `assistente_generico` e `atendente_delivery` ficam inativos (somem da criação; agentes de teste seguem) |
| 5 | Segmento | Texto livre validado, sem lista no código |
| 6 | Motivos e tipos | Três camadas (núcleo, modelo, módulos), com rótulo; "converte em solicitação" vira atributo do motivo |
| 7 | Bloco `agente` no pacote | Com `modelo`, `segmento`, `modulos` **e** as duas listas efetivas |
| 8 | Referência do Skyone | `skyone_ref`, texto livre só informativo (o gateway continua usando o fluxo cadastrado no agente) |
| 9 | Criar e alterar modelo | Só plataforma (hoje qualquer papel) |
| 10 | Ordem | MULTI1-a primeiro; MULTI1-b (núcleo sem delivery) logo depois, sem mudança visível |
