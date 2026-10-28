# PAPEL1 — chave de plataforma, papéis por empresa e escopo por unidade (com o CHAVES1)

**Situação: proposta para revisão do Marco, 28/10/2026. Nada implementado.**
- **Base:**
  - `docs/ESPEC_CHAVES1.md` (chave de plataforma separada, `X-Role` exato, rotas globais só da plataforma);
  - a especificação do painel `painel_koji/docs/ESPEC_USUARIOS1.md`, lida só como referência;
  - a lista real das rotas do gateway, tirada do app em 28/10 (`gateway:623c9e5`).
- **O PAPEL1 junta o CHAVES1 e os papéis num bloco só.** Implantar o CHAVES1 sozinho e depois mexer de novo em todas as rotas seria fazer o trabalho duas vezes.

## 1. Situação de hoje

- **Papéis no gateway:** três (`plataforma`, `admin`, `membro`), lidos do `X-Role`. Valor desconhecido ou ausente vira `membro`.
- **Trava de papel por rota:** existe em 46 das 159 rotas admin (`exigir_admin` ou `exigir_plataforma`). As outras 113 aceitam qualquer papel do painel; quem decide é o painel.
- **Rotas de escrita sem trava hoje** que precisam dela (gestor ou plataforma):
  - criar e alterar empresa;
  - criar, alterar e apagar unidade, e trocar os horários dela;
  - webhooks de saída (criar, alterar, apagar, testar, rotacionar segredo);
  - **anonimizar contato**;
  - revogar chave de API;
  - importar cardápio;
  - rotacionar o segredo do canal;
  - fechar o circuito do agente;
  - `PUT /config`;
  - criar e alterar entidades genéricas (agentes, canais, chaves).
  - Os campos técnicos já foram fechados em 26/10.
- **Unidade:** o gateway não sabe a unidade de quem chama. Toda lista devolve a empresa inteira.
- **Telefone:** sai sempre completo para o painel.

## 2. Cabeçalhos

| Cabeçalho | Regra |
|---|---|
| `Authorization` | `Bearer` da **chave de plataforma** (`PANEL_PLATFORM_KEY` e a próxima) se `X-Role` for `plataforma`. `Bearer` da **chave de empresa** (`PANEL_SERVICE_KEY` e a próxima) para os outros três. Chave de empresa com `X-Role: plataforma`: 403 `papel_sem_credencial` e alerta (CHAVES1, depois da transição) |
| `X-Actor` | e-mail da sessão, como hoje (obrigatório em escrita) |
| `X-Role` | **comparação exata**: `plataforma`, `gestor`, `supervisor`, `funcionario`. Qualquer outro valor, ausente ou com maiúscula/espaço: **403 `papel_desconhecido`** |
| `X-Tenant-Scope` | id da empresa; obrigatório para gestor, supervisor e funcionário (já existe; a exigência liga junto com este bloco) |
| `X-Unidades` | `*` para plataforma e gestor. Para supervisor e funcionário, os **códigos** das unidades separados por vírgula, sem espaço (`centro,jardim`). Supervisor ou funcionário com `*` ou sem o cabeçalho: 403 `unidades_invalidas`. Código que não existe na empresa: ignorado e registrado em log. Se não sobrar nenhum: 403 `sem_unidade` na rota que depende de unidade |

**Nível dos papéis:** plataforma (40) > gestor (30) > supervisor (20) > funcionário (10). Cada rota diz o **nível mínimo**, e o papel acima pode tudo o que o de baixo pode.

**Gestor e plataforma** veem todas as unidades da empresa. O `*` vale também para unidade criada depois.

## 3. Onde fica a unidade de cada coisa

| Coisa | Unidade |
|---|---|
| Pedido | `pedidos.unidade` |
| Carrinho | `carrinhos.unidade` |
| Conversa | a unidade **preferida** da conversa (`conversas.unidade_preferida`); senão a do **último carrinho ou pedido**; senão **nenhuma** |
| Item do cardápio, categoria | `catalogo_itens.unidade`, `catalogo_categorias.unidade` |
| Unidade, exceção, horário, taxa, pagamento | a própria (`tenant_unidades.codigo` pelo `uid`) |
| Solicitação, follow-up, atendimento, mensagem | a da conversa |
| Contato | as unidades das conversas dele |
| Aviso interno | o do pedido (aviso de pedido) ou o da conversa |
| Evento do painel (`/eventos`) | o do pedido ou da conversa do evento |

**Conversa sem unidade** (o cliente ainda não escolheu a loja): ver a decisão 1.

## 4. Regras

- **Rota de recurso** (um pedido, uma conversa, um item, uma unidade): se a unidade do recurso não estiver nas `X-Unidades`, a resposta é **404** (como para outra empresa: não confirma que existe). A ação negada pelo papel, dentro da unidade, é **403 `papel_insuficiente`**.
- **Lista** (pedidos, conversas, solicitações, follow-ups, itens, contatos, eventos, avisos, relatórios): o filtro é aplicado no SQL (`unidade = ANY($unidades)`). A paginação e as contagens já saem filtradas.
- **Supervisor no item do cardápio:** só os campos de disponibilidade e horário (`disponibilidade`, horários do item). Preço, nome, categoria, criar e apagar são do gestor (403 `papel_insuficiente` com `campos`).
- **Cancelar pedido já confirmado:** supervisor ou acima, com motivo. Para o funcionário, `status` só vale nas transições que não cancelam um pedido confirmado.
- **Campos técnicos** (envio, modo do agente, piloto, limites, fluxo e credencial do Skyone, provedor, instruções, modelo; e, na empresa, status, plano, limite, retenção, empresa de teste, fuso): só plataforma. Já existe desde 26/10; este bloco só troca o nome do papel.
- **Rota sem regra na tabela:** só plataforma (falha fechada). Um teste garante que **toda** rota admin está na tabela, e rota nova sem regra quebra a suíte.
- **Auditoria:** toda recusa por papel ou por unidade registra `acesso.negado`, com a rota (sem ids), o papel, as unidades pedidas e o motivo. É limitada a um registro por minuto por sessão e rota.

## 5. Telefone para o funcionário

- **Nas respostas para o funcionário**, todo telefone sai com só os 4 últimos dígitos (`55 11 •••••-1513`): conversas, contatos, pedidos (`cliente_telefone`), mensagens (o identificador da conversa também) e eventos.
- **Também no `conversation_id`:** ele contém o telefone. Para o funcionário, o gateway devolve um **apelido estável** da conversa (`cv_<hash>`), aceito de volta nas rotas.
- **Busca:** para o funcionário, só por nome ou pelos **4 últimos dígitos**. Busca com 5 ou mais dígitos: 400 `busca_restrita`. Assim, buscar o número inteiro não serve para confirmar quem é o cliente.
- **Supervisor, gestor e plataforma** veem o telefone completo, como hoje.
- **O mascaramento é na saída**, num ponto único (o serializador das rotas admin). O dado gravado não muda.

## 6. Transição

- **`PAPEIS_LEGADOS=on`** (padrão no começo):
  - `admin` vale como `gestor`;
  - `membro` vale como `funcionario` com `X-Unidades: *`, que é o que o membro vê hoje;
  - chave de empresa com `plataforma` continua aceita, com aviso em log (CHAVES1, fase A).
- Os nomes novos já valem desde o primeiro dia.
- **Desligar (por ordem sua):** `PAPEIS_LEGADOS=off` faz `admin`/`membro` darem 403 `papel_desconhecido`. `CHAVE_PLATAFORMA_OBRIGATORIA=on` faz a chave de empresa com `plataforma` dar 403.
- **Hoje não há usuário de empresa usando o painel** (segundo o painel, só contas de plataforma), então o risco da troca é baixo.

## 7. Matriz por rota

Coluna "hoje": a trava que o gateway aplica hoje. Coluna "proposta": o nível mínimo. Coluna "unidade": de onde vem a unidade ("filtro" = lista filtrada; "—" = rota sem dado de unidade).

| Rota | Hoje | Proposta | Unidade |
|---|---|---|---|
| `GET /auditoria/plataforma` | qualquer papel | **plataforma** | — |
| `GET /fila` | qualquer papel | **plataforma** | — |
| `GET /modelos-agente` | qualquer papel | **plataforma** | — |
| `POST /modelos-agente` | plataforma | **plataforma** | — |
| `GET /modelos-agente/{modelo_id}` | qualquer papel | **plataforma** | — |
| `PATCH /modelos-agente/{modelo_id}` | plataforma | **plataforma** | — |
| `GET /modulos` | qualquer papel | **plataforma** | — |
| `GET /segmentos` | qualquer papel | **plataforma** | — |
| `GET /segmentos/{segmento_id}` | qualquer papel | **plataforma** | — |
| `GET /tenants` | qualquer papel | **plataforma** | — |
| `POST /tenants` | qualquer papel | **plataforma** | — |
| `GET /tenants/{tenant_id}` | qualquer papel | **funcionario** (campos técnicos só para plataforma) | — |
| `PATCH /tenants/{tenant_id}` | qualquer papel | **gestor** (nome, alerta da equipe); **plataforma** nos técnicos (status, plano, limite, retenção, empresa de teste, fuso) | — |
| `GET /tenants/{tenant_id}/agentes/{agente_id}/atendimento` | qualquer papel | **supervisor** | — |
| `POST /tenants/{tenant_id}/agentes/{agente_id}/atendimento/previa` | qualquer papel | **supervisor** | — |
| `POST /tenants/{tenant_id}/agentes/{agente_id}/atendimento/publicar` | admin | **gestor** | — |
| `DELETE /tenants/{tenant_id}/agentes/{agente_id}/atendimento/rascunho` | admin | **gestor** | — |
| `PUT /tenants/{tenant_id}/agentes/{agente_id}/atendimento/rascunho` | admin | **gestor** | — |
| `GET /tenants/{tenant_id}/agentes/{agente_id}/atendimento/versoes` | qualquer papel | **supervisor** | — |
| `GET /tenants/{tenant_id}/agentes/{agente_id}/atendimento/versoes/{versao}` | qualquer papel | **supervisor** | — |
| `POST /tenants/{tenant_id}/agentes/{agente_id}/atendimento/versoes/{versao}/restaurar` | admin | **gestor** | — |
| `GET /tenants/{tenant_id}/agentes/{agente_id}/modulos` | qualquer papel | **supervisor** | — |
| `POST /tenants/{tenant_id}/agentes/{agente_id}/simular` | qualquer papel | **supervisor** | — |
| `DELETE /tenants/{tenant_id}/api-keys/{identidade}` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant_id}/atendimento` | qualquer papel | **supervisor** | — |
| `POST /tenants/{tenant_id}/atendimento/previa` | qualquer papel | **supervisor** | — |
| `POST /tenants/{tenant_id}/atendimento/publicar` | admin | **gestor** | — |
| `DELETE /tenants/{tenant_id}/atendimento/rascunho` | admin | **gestor** | — |
| `PUT /tenants/{tenant_id}/atendimento/rascunho` | admin | **gestor** | — |
| `GET /tenants/{tenant_id}/atendimento/versoes` | qualquer papel | **supervisor** | — |
| `GET /tenants/{tenant_id}/atendimento/versoes/{versao}` | qualquer papel | **supervisor** | — |
| `POST /tenants/{tenant_id}/atendimento/versoes/{versao}/restaurar` | admin | **gestor** | — |
| `GET /tenants/{tenant_id}/auditoria` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant_id}/avisos` | qualquer papel | **funcionario** (decisão 4) | aviso → unidade (filtro) |
| `GET /tenants/{tenant_id}/catalogo/categorias` | qualquer papel | **funcionario** | item.unidade (filtro) |
| `POST /tenants/{tenant_id}/catalogo/categorias` | admin | **gestor** | — |
| `PUT /tenants/{tenant_id}/catalogo/categorias/ordem` | admin | **gestor** | — |
| `PATCH /tenants/{tenant_id}/catalogo/categorias/{categoria_id}` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/catalogo/categorias/{categoria_id}/arquivar` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/catalogo/categorias/{categoria_id}/desarquivar` | admin | **gestor** | — |
| `PUT /tenants/{tenant_id}/catalogo/categorias/{categoria_id}/ordem` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/catalogo/copiar` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/catalogo/disponibilidade/previa` | qualquer papel | **supervisor** | item.unidade |
| `POST /tenants/{tenant_id}/catalogo/importar` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant_id}/catalogo/itens` | qualquer papel | **funcionario** | item.unidade (filtro) |
| `POST /tenants/{tenant_id}/catalogo/itens` | admin | **gestor** | — |
| `GET /tenants/{tenant_id}/catalogo/itens/{item_id}` | qualquer papel | **funcionario** | item.unidade (filtro) |
| `PATCH /tenants/{tenant_id}/catalogo/itens/{item_id}` | admin | **supervisor** | item.unidade (supervisor: só disponibilidade/horários; resto gestor) |
| `POST /tenants/{tenant_id}/catalogo/itens/{item_id}/arquivar` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/catalogo/itens/{item_id}/desarquivar` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/catalogo/itens/{item_id}/duplicar` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/catalogo/itens/{item_id}/esgotar` | qualquer papel | **funcionario** | item.unidade |
| `GET /tenants/{tenant_id}/catalogo/itens/{item_id}/opcoes` | qualquer papel | **funcionario** | item.unidade (filtro) |
| `PUT /tenants/{tenant_id}/catalogo/itens/{item_id}/opcoes` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/catalogo/itens/{item_id}/repor` | qualquer papel | **funcionario** | item.unidade |
| `POST /tenants/{tenant_id}/catalogo/lote` | admin | **gestor** | — |
| `GET /tenants/{tenant_id}/catalogo/pratos` | qualquer papel | **funcionario** | — |
| `POST /tenants/{tenant_id}/catalogo/pratos` | admin | **gestor** | — |
| `GET /tenants/{tenant_id}/catalogo/pratos/sugestoes` | qualquer papel | **funcionario** | — |
| `POST /tenants/{tenant_id}/catalogo/pratos/sugestoes/confirmar` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/catalogo/pratos/sugestoes/ignorar` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/catalogo/pratos/sugestoes/restaurar` | admin | **gestor** | — |
| `DELETE /tenants/{tenant_id}/catalogo/pratos/{prato_id}` | admin | **gestor** | — |
| `GET /tenants/{tenant_id}/catalogo/pratos/{prato_id}` | qualquer papel | **funcionario** | — |
| `PATCH /tenants/{tenant_id}/catalogo/pratos/{prato_id}` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/catalogo/pratos/{prato_id}/itens` | admin | **gestor** | — |
| `DELETE /tenants/{tenant_id}/catalogo/pratos/{prato_id}/itens/{item_id}` | admin | **gestor** | — |
| `GET /tenants/{tenant_id}/catalogo/resumo` | qualquer papel | **funcionario** | item.unidade (filtro) |
| `GET /tenants/{tenant_id}/config` | qualquer papel | **supervisor** | — |
| `PUT /tenants/{tenant_id}/config` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant_id}/consumo` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant_id}/contatos` | qualquer papel | **supervisor** | contato → conversas → unidade (filtro) |
| `DELETE /tenants/{tenant_id}/contatos/{cid}` | qualquer papel | **gestor** | — |
| `PATCH /tenants/{tenant_id}/contatos/{cid}` | qualquer papel | **supervisor** | contato → conversas → unidade |
| `GET /tenants/{tenant_id}/contatos/{cid}/exportar` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant_id}/conversas/{conversa_id}/mensagens` | qualquer papel | **funcionario** (histórico: todos os papéis) | conversa → unidade (filtro); funcionário: apelido `cv_` e telefone mascarado |
| `GET /tenants/{tenant_id}/gestao/alteracoes` | qualquer papel | **supervisor** | unidade (filtro, quando houver) |
| `GET /tenants/{tenant_id}/metricas` | qualquer papel | **supervisor** | unidade (filtro, quando houver) |
| `GET /tenants/{tenant_id}/modulos` | qualquer papel | **gestor** | — |
| `PUT /tenants/{tenant_id}/modulos/{modulo}` | plataforma | **plataforma** | — |
| `GET /tenants/{tenant_id}/pedidos` | qualquer papel | **funcionario** | pedido.unidade (filtro) |
| `GET /tenants/{tenant_id}/pedidos/avisos` | qualquer papel | **supervisor** | — |
| `PUT /tenants/{tenant_id}/pedidos/avisos/{status}` | admin | **gestor** | — |
| `POST /tenants/{tenant_id}/pedidos/avisos/{status}/previa` | qualquer papel | **supervisor** | — |
| `GET /tenants/{tenant_id}/pedidos/contagem` | qualquer papel | **funcionario** | pedido.unidade (filtro) |
| `GET /tenants/{tenant_id}/pedidos/{numero}` | qualquer papel | **funcionario** | pedido.unidade (filtro) |
| `POST /tenants/{tenant_id}/pedidos/{numero}/avisos/{status}/reenviar` | qualquer papel | **supervisor** | pedido.unidade |
| `POST /tenants/{tenant_id}/pedidos/{numero}/confirmar` | qualquer papel | **funcionario** | pedido.unidade (cancelar confirmado: supervisor) |
| `POST /tenants/{tenant_id}/pedidos/{numero}/recusar` | qualquer papel | **funcionario** | pedido.unidade (cancelar confirmado: supervisor) |
| `POST /tenants/{tenant_id}/pedidos/{numero}/status` | qualquer papel | **funcionario** | pedido.unidade (cancelar confirmado: supervisor) |
| `GET /tenants/{tenant_id}/skills/registro` | qualquer papel | **supervisor** | unidade (filtro, quando houver) |
| `GET /tenants/{tenant_id}/solicitacoes` | qualquer papel | **funcionario** | conversa → unidade (filtro) |
| `GET /tenants/{tenant_id}/solicitacoes/contagem` | qualquer papel | **funcionario** | conversa → unidade (filtro) |
| `GET /tenants/{tenant_id}/solicitacoes/{sid}` | qualquer papel | **funcionario** | conversa → unidade (filtro) |
| `PATCH /tenants/{tenant_id}/solicitacoes/{sid}` | qualquer papel | **supervisor** | conversa → unidade (filtro) |
| `GET /tenants/{tenant_id}/unidades` | qualquer papel | **funcionario** | unidade (filtro) |
| `POST /tenants/{tenant_id}/unidades` | qualquer papel | **gestor** | — |
| `DELETE /tenants/{tenant_id}/unidades/{uid}` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant_id}/unidades/{uid}` | qualquer papel | **funcionario** | unidade (filtro) |
| `PATCH /tenants/{tenant_id}/unidades/{uid}` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant_id}/unidades/{uid}/agenda` | qualquer papel | **funcionario** | unidade (uid) |
| `GET /tenants/{tenant_id}/unidades/{uid}/excecoes` | qualquer papel | **supervisor** | unidade (uid) |
| `POST /tenants/{tenant_id}/unidades/{uid}/excecoes` | qualquer papel | **supervisor** | unidade (uid) |
| `DELETE /tenants/{tenant_id}/unidades/{uid}/excecoes/{eid}` | qualquer papel | **supervisor** | unidade (uid) |
| `PUT /tenants/{tenant_id}/unidades/{uid}/horarios` | qualquer papel | **gestor** | unidade (uid) |
| `GET /tenants/{tenant_id}/unidades/{uid}/pagamentos` | qualquer papel | **supervisor** | unidade (uid) |
| `PUT /tenants/{tenant_id}/unidades/{uid}/pagamentos` | admin | **gestor** | unidade (uid) |
| `POST /tenants/{tenant_id}/unidades/{uid}/pausar` | qualquer papel | **supervisor** | unidade (uid) |
| `POST /tenants/{tenant_id}/unidades/{uid}/reabrir` | qualquer papel | **supervisor** | unidade (uid) |
| `GET /tenants/{tenant_id}/unidades/{uid}/taxas` | qualquer papel | **supervisor** | unidade (uid) |
| `POST /tenants/{tenant_id}/unidades/{uid}/taxas` | admin | **gestor** | unidade (uid) |
| `PUT /tenants/{tenant_id}/unidades/{uid}/taxas` | admin | **gestor** | unidade (uid) |
| `DELETE /tenants/{tenant_id}/unidades/{uid}/taxas/{tid}` | admin | **gestor** | unidade (uid) |
| `PATCH /tenants/{tenant_id}/unidades/{uid}/taxas/{tid}` | admin | **gestor** | unidade (uid) |
| `GET /tenants/{tenant_id}/webhooks-saida` | qualquer papel | **gestor** | — |
| `POST /tenants/{tenant_id}/webhooks-saida` | qualquer papel | **gestor** | — |
| `DELETE /tenants/{tenant_id}/webhooks-saida/{wid}` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant_id}/webhooks-saida/{wid}` | qualquer papel | **gestor** | — |
| `PATCH /tenants/{tenant_id}/webhooks-saida/{wid}` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant_id}/webhooks-saida/{wid}/entregas` | qualquer papel | **gestor** | — |
| `POST /tenants/{tenant_id}/webhooks-saida/{wid}/rotacionar-segredo` | qualquer papel | **gestor** | — |
| `POST /tenants/{tenant_id}/webhooks-saida/{wid}/testar` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant_id}/{entidade}` | qualquer papel | **supervisor** | — (agentes: ver = supervisor; canais e api-keys: gestor; campos técnicos: plataforma) |
| `POST /tenants/{tenant_id}/{entidade}` | qualquer papel | **gestor** | — (agentes: ver = supervisor; canais e api-keys: gestor; campos técnicos: plataforma) |
| `GET /tenants/{tenant_id}/{entidade}/{identidade}` | qualquer papel | **supervisor** | — (agentes: ver = supervisor; canais e api-keys: gestor; campos técnicos: plataforma) |
| `PATCH /tenants/{tenant_id}/{entidade}/{identidade}` | qualquer papel | **gestor** | — (agentes: ver = supervisor; canais e api-keys: gestor; campos técnicos: plataforma) |
| `GET /tenants/{tenant}/agentes/{agente_id}/capacidades` | qualquer papel | **supervisor** | — |
| `PUT /tenants/{tenant}/agentes/{agente_id}/capacidades/documentos` | admin | **gestor** | — |
| `POST /tenants/{tenant}/agentes/{agente_id}/simular-documento` | qualquer papel | **supervisor** | — |
| `GET /tenants/{tenant}/agentes/{agente}/circuito` | qualquer papel | **supervisor** | — |
| `POST /tenants/{tenant}/agentes/{agente}/circuito/fechar` | qualquer papel | **gestor** | — |
| `POST /tenants/{tenant}/agentes/{agente}/testar` | qualquer papel | **supervisor** | — |
| `GET /tenants/{tenant}/anexos` | qualquer papel | **gestor** | — |
| `DELETE /tenants/{tenant}/anexos/{aid}` | admin | **gestor** | — |
| `GET /tenants/{tenant}/anexos/{aid}` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant}/anexos/{aid}/original` | qualquer papel | **gestor** | — |
| `GET /tenants/{tenant}/anexos/{aid}/texto` | qualquer papel | **gestor** | — |
| `POST /tenants/{tenant}/canais/{canal}/rotacionar-segredo` | qualquer papel | **gestor** | — |
| `POST /tenants/{tenant}/canais/{canal}/webhook` | admin | **gestor** | — |
| `GET /tenants/{tenant}/capacidades` | qualquer papel | **gestor** | — |
| `PUT /tenants/{tenant}/capacidades/documentos` | plataforma | **plataforma** | — |
| `GET /tenants/{tenant}/chaves-formulario` | qualquer papel | **gestor** | — |
| `POST /tenants/{tenant}/chaves-formulario` | admin | **gestor** | — |
| `DELETE /tenants/{tenant}/chaves-formulario/{kid}` | admin | **gestor** | — |
| `DELETE /tenants/{tenant}/contatos/{cid}/opt-out-follow-up` | admin | **gestor** | contato → conversas → unidade |
| `GET /tenants/{tenant}/contatos/{cid}/opt-out-follow-up` | qualquer papel | **supervisor** | contato → conversas → unidade |
| `PUT /tenants/{tenant}/contatos/{cid}/opt-out-follow-up` | qualquer papel | **supervisor** | contato → conversas → unidade |
| `GET /tenants/{tenant}/conversas` | qualquer papel | **funcionario** (histórico: todos os papéis) | conversa → unidade (filtro); funcionário: apelido `cv_` e telefone mascarado |
| `POST /tenants/{tenant}/conversas/{conv}/assumir` | qualquer papel | **funcionario** | conversa → unidade |
| `GET /tenants/{tenant}/conversas/{conv}/atendimentos` | qualquer papel | **funcionario** (histórico: todos os papéis) | conversa → unidade (filtro); funcionário: apelido `cv_` |
| `POST /tenants/{tenant}/conversas/{conv}/devolver` | qualquer papel | **supervisor** | conversa → unidade |
| `POST /tenants/{tenant}/conversas/{conv}/encerrar` | qualquer papel | **supervisor** | conversa → unidade |
| `GET /tenants/{tenant}/conversas/{conv}/follow-ups` | qualquer papel | **funcionario** (histórico: todos os papéis) | conversa → unidade (filtro); funcionário: apelido `cv_` |
| `POST /tenants/{tenant}/conversas/{conv}/follow-ups/cancelar` | qualquer papel | **supervisor** | conversa → unidade |
| `POST /tenants/{tenant}/conversas/{conv}/mensagens` | qualquer papel | **funcionario** | conversa → unidade |
| `POST /tenants/{tenant}/conversas/{conv}/reabrir` | qualquer papel | **supervisor** | conversa → unidade |
| `GET /tenants/{tenant}/eventos` | qualquer papel | **funcionario** (histórico: todos os papéis) | evento → unidade (filtro); funcionário: apelido `cv_` e telefone mascarado |
| `GET /tenants/{tenant}/eventos/cursor` | qualquer papel | **funcionario** | evento → unidade (filtro) |
| `GET /tenants/{tenant}/fila` | qualquer papel | **supervisor** | — |

**Observações da matriz:**
- **Rotas globais** (`/tenants` sem id, `/modelos-agente`, `/fila`, `/auditoria/plataforma`, `/segmentos`, `/modulos`): só plataforma, inclusive leitura (decisão 3 do CHAVES1). A criação de agente pela empresa usa a rota nova `GET /tenants/{t}/modelos-disponiveis` (ativos globais + os da empresa), que entra neste bloco.
- **Rota genérica `/{entidade}`:** agentes, canais e chaves de API. Ver agente: supervisor. Criar ou alterar agente, canal ou chave: gestor. Os campos técnicos continuam só da plataforma.
- **Relatórios** (`/metricas`, `/skills/registro`, `/gestao/alteracoes`): supervisor, filtrado por unidade onde o dado tem unidade. **Exportar** (contato e, depois, resultado de lote): gestor.

## 8. Testes

- **Uma matriz de testes gerada da tabela:** para cada rota × cada papel, o status esperado (permitido / 403 / 404 por unidade), com dados de duas unidades (`centro`, `jardim`). Supervisor e funcionário só de `centro`.
- **Lista:** funcionário de `centro` não vê pedido, conversa, solicitação, follow-up nem evento de `jardim`. Contagens e paginação conferem.
- **Recurso de outra unidade:** 404 em detalhe e em ação (confirmar pedido, assumir conversa, esgotar item, pausar loja).
- **Cabeçalhos:**
  - `X-Role` exato (`Gestor`, ` gestor`, `admin` com legado desligado → 403);
  - `X-Unidades` ausente, `*` para supervisor, código inexistente, lista vazia;
  - chave de empresa com `plataforma` → 403 com a flag ligada.
- **Telefone mascarado:** para o funcionário em todas as rotas que levam telefone, incluindo `conversation_id` → apelido; busca restrita.
- **Supervisor no item:** disponibilidade passa; preço → 403.
- **Cancelar pedido confirmado:** funcionário 403, supervisor ok com motivo.
- **Toda rota admin tem regra na tabela** (o teste lê `app.routes`).
- **Legado ligado:** a suíte atual passa sem mudança (admin = gestor, membro = funcionário com `*`).

## 9. Ordem e tamanho (junto com o CHAVES1)

| Parte | O que entra | Tamanho |
|---|---|---|
| **PAPEL1-a** | CHAVES1 inteiro (chave de plataforma, `X-Role` exato, rotas globais só da plataforma, scripts de operação com a chave nova) + os 4 papéis + legado + tabela de permissões por rota com falha fechada + `modelos-disponiveis` + `ESCOPO_EMPRESA_OBRIGATORIO` ligado | G |
| **PAPEL1-b** | `X-Unidades`, unidade de cada recurso, filtro nas listas, 404 fora da unidade, regras de campo (supervisor no item, cancelamento) | G |
| **PAPEL1-c** | Telefone mascarado para o funcionário (inclui `conversation_id` → apelido e eventos), busca restrita | M |

- **Ordem:** a → b → c.
  - O **a** fecha o risco de hoje (rotas de escrita sem trava e papel autodeclarado), e convém fazer antes do LOTE1 (o lote tem exportação e documentos de terceiros).
  - O **b** e o **c** podem vir junto com o painel publicar os papéis novos.
- **Painel:** precisa mandar a chave de plataforma só na sessão de plataforma, o `X-Role` com os nomes novos e o `X-Unidades` montado de `usuario_unidades`.
  - Na fase A do CHAVES1, e com o legado ligado, nada quebra se ele demorar.

## 10. Decisões (aprovadas em 28/10/2026; a 8 é o ajuste do mesmo dia)

| # | Decisão | Proposta |
|---|---|---|
| 1 | Conversa sem unidade (cliente ainda não escolheu a loja) | Visível a todos os papéis da empresa (fila de entrada). Quando a conversa ganha unidade, passa a valer o filtro |
| 2 | Recurso de outra unidade: 404 ou 403 | 404, como para outra empresa (não confirma que existe) |
| 3 | Supervisor cria funcionário? | O gateway não guarda usuários (o painel guarda); nada a fazer aqui |
| 4 | Solicitações à equipe e avisos internos: quem vê | Funcionário vê (é operação), filtrado por unidade; atualizar a solicitação é supervisor |
| 5 | Contatos (lista e ficha) | Supervisor ou acima, filtrado pelas unidades das conversas; exportar e anonimizar: gestor |
| 6 | `conversation_id` com telefone para o funcionário | Apelido estável `cv_<hash>` (seção 5), aceito de volta nas rotas |
| 7 | PAPEL1-a antes do LOTE1 | Sim |
| 8 | Histórico de conversas (ajuste de 28/10) | Visível a todos os papéis de empresa (funcionário, supervisor, gestor), filtrado pelas unidades de cada um; para o funcionário, apelido `cv_` e telefone mascarado (PAPEL1-c). Rotas: lista de conversas, mensagens, atendimentos, follow-ups e eventos |

**Ponto de parada:** só especificação.
