# Descobertas no Skyone — Fase 0.3

> Atualização de 08/10/2026: o probe foi adaptado ao contrato validado. O roteiro abaixo é histórico e ainda aguarda consolidação após os testes do Marco; não usar suas orientações de token no corpo ou API de clonagem. Para a execução atual, consultar `docs/SKYONE_RESULTADOS.md`, seção "Probe executado na VPS". Sessão e autenticação continuam sem decisão consolidada.

Status em 2026-10-08: ferramenta preparada e testada; validação real pendente.
O gateway continua em `mock/dry`. Nenhum fluxo real foi chamado.

## Preparar o fluxo de teste

1. Criar um fluxo separado do atendimento: Webhook → JavaScript → AI Agent Call → Retorno.
2. Configurar validação do `token` recebido. O token esperado deve corresponder ao `SKYONE_PROBE_TOKEN` da VPS.
3. No Logger, identificar onde o corpo do webhook aparece. Usar o mapeamento real da interface; este repositório não presume nomes de variáveis do Skyone.
4. Verificar se `instrucoes_agente` pode ser conectado às instruções do AI Agent Call. Registrar também se a seleção da base de conhecimento e das Skills aceita parâmetros.
5. Usar agente sem memória/cache/deduplicação no diagnóstico de idiomas. Conforme o roteiro, os dois payloads são iguais, exceto pela instrução. Os identificadores são gerados de novo a cada execução do probe.
6. Devolver `{"resposta": ["texto do agente"], "acao": "nenhuma", "motivo": null}`.
7. Na VPS, editar `/home/twadmin/gateway/.env` e preencher `SKYONE_PROBE_URL` e `SKYONE_PROBE_TOKEN`. Não colocar credenciais em comandos, commits ou capturas de tela.

## Executar o diagnóstico

Prévia sem rede e com token mascarado:

```bash
cd /home/twadmin/gateway
docker compose run --rm --no-deps \
  -v /home/twadmin/gateway:/workspace:ro gateway \
  python /workspace/scripts/skyone_probe.py --dry-run
```

Executar os dois casos (inglês e português):

```bash
docker compose run --rm --no-deps \
  -v /home/twadmin/gateway:/workspace:ro gateway \
  python /workspace/scripts/skyone_probe.py --caso idiomas
```

O comando lê o `.env` por meio do Compose e usa as dependências da imagem do gateway. Não exige instalar Python/pip no host nem alterar o modo de atendimento. Cada URL é validada antes da chamada; só HTTPS público na porta 443 é permitido, e redirecionamentos são recusados. O relatório mostra status HTTP, duração e resposta sanitizada. HTTP 2xx, sozinho, não comprova nenhuma das capacidades em investigação.

Para guardar um relatório local, usar `umask 077`, criar `.local/` e redirecionar a saída para `.local/skyone-probe.json`; a pasta é ignorada pelo Git. Revisar o conteúdo antes de compartilhá-lo.

## Tempo e callback

1. No fluxo de diagnóstico, configurar um Delay de 60 segundos. Pode ser fixo na interface, ou ligado ao campo **de teste** `probe_delay_s` se o Skyone permitir esse mapeamento. Esse campo é uma convenção deste probe, não uma API confirmada do Skyone.
2. Executar o comando abaixo com `--espera-s 60`, depois repetir com 120 e 300, ajustando também o Delay do fluxo.

```bash
docker compose run --rm --no-deps \
  -v /home/twadmin/gateway:/workspace:ro gateway \
  python /workspace/scripts/skyone_probe.py --caso tempo --espera-s 60 --timeout 360
```

3. Registrar duração, status e se a conexão devolve o resultado final ou só uma confirmação de recebimento. Timeout de leitura é registrado como `ReadTimeout`, não como sucesso.
4. Para validar callback, configurar um bloco REST ao fim do fluxo chamando um receptor de diagnóstico sob seu controle, com payload sintético. Registrar método, autenticação, status recebido e tempo até a entrega. **O endpoint de callback do gateway ainda não existe**; entra na Fase 0.6. Não usar `/gateway/health` como prova de entrega de callback.
5. Conferir na documentação ou no suporte do Skyone se há API de criação/clonagem de fluxos/agentes. Registrar a referência real; nenhuma API de gestão foi presumida.

## Registro de decisões

| Pergunta | Evidência necessária | Resultado |
|---|---|---|
| Instruções pelo payload mudam a resposta? | Dois retornos e mapeamento do AI Agent Call | Pendente |
| Base e Skills podem ser parametrizadas? | Campos/mapeamentos reais | Pendente |
| Existe API para criar/clonar fluxos e agentes? | Link da documentação ou resposta do suporte | Pendente |
| Limite síncrono em 60/120/300 s? | Status e durações do probe | Pendente |
| Bloco REST entrega callback externo? | Registro no receptor de teste | Pendente |
| Estratégia `por_agente` ou `por_modelo`? | Resultado das duas primeiras perguntas | A decidir |
| Resposta `sync` ou `async`? | Resultado de tempo e callback | A decidir |

O checkpoint da Fase 0.3 em `PLANO_CURSOR.md` pede esses resultados antes da Fase 0.5. A publicação do domínio foi antecipada por solicitação do Marco; ela não conclui a Fase 1, que também exige fixtures reais do uazapi.

## Adaptador implementado — Fase 0.5, bloco 3

`app/agentes/skyone.py` monta `message`, `conversation_id`, `request_id`, `session_id=request_id`, `instrucoes_agente` e `historico`, além dos IDs de tenant/agente/modelo. Histórico vem de `mensagens`, filtrado por tenant e conversa, anterior ao lote atual, limitado a `history_turns` itens e devolvido cronologicamente com `enviada_em` no fuso do tenant. Migração 004 acrescenta o horário à mensagem; dados existentes recebem `criado_em` como fallback.

Autenticação genérica: `bearer` ou `cabecalho`, com valor Fernet obtido do agente. Chamadas live sem credencial só são permitidas em tenant de teste. Timeout total por agente, DNS/URL validados imediatamente antes de chamar e redirects recusados. Retornos exigem IDs correspondentes, status success e tipo text; texto em `resposta` ou `resposta.body`. Credencial ecoada na resposta é recusada.

Validação com Postgres e transporte HTTP simulado; não houve chamada real ao Skyone neste bloco. O tipo real de autenticação e sua validação 401/403 versus 200 continuam dependentes do Marco para concluir a Fase 0.5.

---

# Fluxo-modelo `atendente_delivery` e criação de agente novo — Fase 2

> **Situação (10/10/2026):** o lado do gateway está implementado e testado (pacote e Retorno abaixo). O conteúdo dos nós do fluxo (JavaScript e prompt) entra aqui **depois que o Marco validar o fluxo no Skyone**; os trechos marcados com 🔲 estão aguardando esse conteúdo. Nada nesta seção foi conferido contra o fluxo real ainda.

## Modelo do agente (decisão do Marco, 10/10/2026)

- **Padrão de atendimento do By Koji: GPT-4.1 mini** (chave própria da OpenAI), sem base de conhecimento e sem Skills no agente.
- **Gemini 3 Flash:** testado como alternativa equivalente (mesmo comportamento e tempos parecidos).
- **GPT OSS 20b: proibido para atendimento.**
- Medições e textos das rodadas em `docs/SKYONE_RESULTADOS.md`. Com 5 clientes simultâneos: GPT-4.1 mini agente_p50 10,7 s / agente_p95 11,3 s / total_p95 15,7 s; Gemini 3 Flash 8,8 s / 10,6 s / 15,8 s.

## Desenho do fluxo

```
Webhook (Basic) → JS "Normalizar Entrada" → AI Agent Call → JS "Interpretar Resposta" → Retorno
```

| Nó | Papel | Conteúdo |
|---|---|---|
| Webhook | Recebe o pacote do gateway. "Solicitar autenticação" ligado (Basic). | 🔲 URL e usuário ficam só no cadastro do agente no painel; nunca neste arquivo. |
| JS "Normalizar Entrada" | Lê o pacote e monta o prompt: `instrucoes_agente`, tempo (`agora`, `tempo_desde_ultima`, `retomada`), `cliente`, `unidades`, `contexto_modulos`, `historico` e as mensagens do lote. | 🔲 código validado |
| AI Agent Call | Chama o modelo (GPT-4.1 mini). Sessão nova por turno (`session_id` = `request_id`): a memória é o `historico` enviado pelo gateway. | 🔲 configuração validada |
| JS "Interpretar Resposta" | Converte a saída do modelo no Retorno do contrato (balões, `acao`, `motivo`, `desfecho`). | 🔲 código validado |
| Retorno | Devolve o JSON do contrato na mesma chamada (modo síncrono). | 🔲 corpo validado |

## Pacote que o gateway envia (ida)

Campos garantidos em toda chamada (teste de contrato: `tests/test_fase2_contrato.py`):

| Campo | Conteúdo |
|---|---|
| `request_id`, `session_id` | UUID do turno; os dois são iguais. |
| `conversation_id`, `tenant_id`, `agente_id`, `modelo_id` | Identificação. |
| `instrucoes_agente` | Prompt complementar do agente (pode ser nulo). |
| `canal` | `{"id", "tipo", "publico"}`. |
| `modo_resposta`, `tarefa` | `"sync"`; `"responder"` (ou `"notificar"`, com `evento`). |
| `agora` | Data e hora atuais, ISO, no fuso da empresa. |
| `tempo_desde_ultima` | Texto: "há 2 minutos", "há 3 dias". **Nulo no primeiro contato.** Conta da última mensagem da conversa antes deste lote até a primeira mensagem do lote. |
| `retomada` | `true` quando esse intervalo passa de 12 h: o agente deve retomar, não continuar a conversa antiga. |
| `message` | Texto consolidado do lote (uma linha por mensagem). |
| `mensagens_do_lote` | Lista `{"enviada_em", "tipo", "texto"}`. `tipo`: `texto`, `audio`, `imagem`, `video`, `documento`, `localizacao`, `figurinha`, `contato`, `desconhecido`. Localização vem como `[localização: lat, long — lugar]`; anexo que o gateway não lê, como `[não foi possível ler <tipo>: <motivo>]`. |
| `historico` | Lista `{"role": "user"|"assistant", "content", "enviada_em"}`, limitada por `history_turns`; não inclui o lote. |
| `ultima_interacao` | Clique em botão ou escolha em lista no lote: `{"tipo": "botao" \| "lista", "id", "titulo", "atual"}`; **nulo quando não houve clique**. O clique chega como mensagem de texto com o título do botão; o item correspondente de `mensagens_do_lote` ganha `interacao: {"tipo", "id", "atual"}`. `atual: false` = botão de mensagem antiga (o bot ou o atendente já falou depois): tratar como texto comum e conferir o contexto. Ver `docs/ESPEC_BOTOES.md`. |
| `acoes_ja_executadas` | Lista; **vazia em turno comum**. Preenchida só quando o gateway refez o turno (chegou mensagem do cliente antes de a resposta sair): `[{"skill", "item", "quantidade", "resultado", "detalhe"?}]` com o que a tentativa descartada já fez nas Skills. O agente **não deve repetir** essas ações; o carrinho em `contexto_modulos` já as reflete. Ver `docs/ESPEC_UX2.md`, seção 8.2. |
| `acoes_recentes` | Lista, **sempre presente** (vazia quando não há nada). Chamadas de Skill de carrinho/pedido desta conversa nos últimos 15 minutos (configurável por agente), da mais antiga para a mais recente, até 20: `[{"skill", "item", "quantidade", "resultado", "detalhe"?, "ha_segundos"}]`. Sem endereço nem observação. O agente usa para **não repetir** o que acabou de fazer (ex.: adicionar de novo o item que entrou no turno anterior). Ver `docs/ESPEC_UX2.md`, seção 12.2. |
| `cliente` | `{"nome", "nome_origem", "unidade_preferida", "resumo_anterior"}` (valores podem ser nulos). `nome` segue a prioridade **cadastro (painel) > informado (`nome_cliente` do agente) > perfil (WhatsApp)** e **nunca é o telefone**; `nome_origem` diz de onde veio (`cadastro`, `informado`, `perfil` ou nulo). Com `nome_origem` nulo ou `perfil`, o agente pode perguntar o nome; com `cadastro` ou `informado`, não precisa. |
| `unidades` | Unidades da empresa com situação de funcionamento (lista vazia se não houver). |
| `contexto_modulos` | Um bloco por módulo habilitado no agente (objeto vazio se não houver). |

A credencial vai no cabeçalho `Authorization` (SEG13), nunca no corpo.

## Retorno que o gateway aceita (volta)

```json
{
  "request_id": "<o mesmo recebido>",
  "conversation_id": "<o mesmo recebido>",
  "status": "success",
  "tipo": "text",
  "resposta": ["balão 1", "balão 2"],
  "acao": "nenhuma",
  "motivo": null,
  "desfecho": null,
  "nome_cliente": null
}
```

- **Obrigatórios:** `request_id` e `conversation_id` iguais aos recebidos, `status: "success"`, `tipo: "text"` e `acao`. Sem eles o gateway trata como erro do agente e envia o fallback.
- **`resposta`:** lista (cada item vira um balão, na ordem) **ou** texto único (parágrafos separados por linha em branco viram balões; quebra de linha simples fica no mesmo balão). Também aceita o formato já validado `{"body": "..."}`. Acima de 10 balões, o excedente segue junto no último.
- **`acao`:**

| Valor | O que o gateway faz |
|---|---|
| `nenhuma` | Envia os balões. |
| `silencio` | Não envia nada. **`motivo` obrigatório**; fica gravado em `turnos.motivo_silencio`. Texto que venha junto é ignorado. |
| `transferir_humano` | Envia os balões (se houver), pausa o bot e marca a conversa como aguardando atendente (`transferido_pelo_agente`). |
| `encerrar` | Envia os balões (se houver) e grava `desfecho`. |
| `pedido_fechado` | Reservado para o módulo de pedidos; hoje se comporta como `nenhuma`. |

- **`desfecho`:** `pedido_feito`, `duvida_resolvida`, `reclamacao`, `sem_interesse` ou nulo (aceita qualquer identificador curto em minúsculas com `_`; fora disso é descartado).
- **Silêncio sem motivo é falha (INV04):** `acao: "silencio"` sem `motivo`, ou `resposta` vazia com `acao: "nenhuma"`. O gateway consulta o agente **mais uma vez** com o mesmo pacote; se persistir, não envia nada ao cliente, pausa o bot e marca a conversa como aguardando atendente (`silencio_sem_motivo`).
- **`botoes`** (opcional): `{"opcoes": [{"id": "confirmar_pedido", "titulo": "Confirmar pedido"}, {"id": "alterar", "titulo": "Alterar"}]}`. Valem para o **último balão** e só com `acao` `nenhuma` ou `pedido_fechado`. Ausente ou `null` = sem botões. Até 3 opções, `titulo` até 20 caracteres, `id` em `[a-z0-9_]` até 40, sem repetidos. Fora disso (ou com o recurso desligado no agente, ou em canal sem botão) as mesmas opções saem numeradas em texto; nunca vira erro. O agente só cria botões de **resposta**; nunca link, telefone ou payload de canal. Ver `docs/ESPEC_BOTOES.md`.
- **Lista (B3):** com **4 a 10 opções**, ou quando alguma opção traz `descricao`, o balão sai como lista: `{"titulo_lista": "Ver unidades", "opcoes": [{"id": "morumbi", "titulo": "Morumbi", "descricao": "…"}, …]}`. `titulo` até 24 caracteres; `descricao` opcional, até 72; `titulo_lista` (texto do botão que abre a lista) opcional, até 20 — padrão "Ver opções". **Quando o `id` é o código de uma unidade da empresa, o gateway escreve a descrição** com a situação real ("Aberta até 22h30", "Fechada · abre amanhã 12h", "Pedidos pausados"); o agente não precisa (e não deve) escrevê-la. Fora dos limites, com o recurso desligado ou se o provedor recusar, as opções saem numeradas em texto, com a descrição: `1. Morumbi — Aberta até 22h30`.
- **`nome_cliente`** (opcional, PED1-c): o nome que o cliente **disse na conversa** ("sou o Marco", "pode anotar Ana Paula"). Mandar só quando o cliente informou o nome — não deduzir do perfil do WhatsApp nem do `cliente.nome` recebido, e não repetir a cada turno (repetir não faz mal). Regra: 2 a 60 caracteres, ao menos duas letras, **sem número de telefone** (8+ dígitos) e **sem emoji**. Fora da regra, o campo é **ignorado sem erro** (a resposta sai normal). O gateway grava como nome "informado" do contato; **nunca sobrescreve o nome de cadastro** feito no painel (o cadastro continua valendo na exibição). Vale em qualquer `acao`. Ver `docs/ESPEC_PED1.md`, seção 14.
- `uso` (`{"tokens_entrada", "tokens_saida"}`) é opcional. Qualquer outro campo é descartado.

## Passo a passo para criar um agente novo

> **Nunca duplicar um fluxo existente:** o fluxo duplicado mantém a URL do original. Criar sempre do zero.

1. 🔲 No Skyone, criar o fluxo do zero com os cinco nós na ordem do desenho acima.
2. 🔲 Webhook: ligar "Solicitar autenticação" (Basic) e definir usuário e senha próprios do agente.
3. 🔲 Colar o JavaScript "Normalizar Entrada".
4. 🔲 Configurar o AI Agent Call (modelo, prompt do sistema).
5. 🔲 Colar o JavaScript "Interpretar Resposta".
6. 🔲 Configurar o Retorno com o corpo do contrato.
7. No painel, no agente: cadastrar a URL do fluxo, o usuário e a senha; manter **envio de teste**. Usar "Testar conexão".
8. Conferir uma conversa pelo Simular e, depois, rodar o benchmark (1, 5 e 10 clientes) antes de ligar o envio real.
