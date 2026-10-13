# HOR2 — Próxima abertura com cardápio, horário reconferido na troca de unidade e transferência sem tentativa

**Situação:** aprovado pelo Marco em 13/10/2026 (itens 6, 7 e 8 da análise das conversas do dia) e implementado.
**Banco:** migração 041, aditiva (`turnos.transferencia_sem_recusa`).

## 1. O que as conversas de 13/10 mostraram

- 11:26 — o agente disse "a próxima abertura é da Einstein às 11:30" e ofereceu agendar lá. O Einstein não tem nenhum item com preço. O campo `proxima_abertura` da raiz do pacote apontava a primeira unidade a abrir, com ou sem cardápio.
- 11:30 — o carrinho tinha "hoje às 11:30" (marcado para o Einstein); ao trocar a unidade para o Pátio Guedala, que abre às 12h, o `definir_entrega` aceitou sem dizer nada. O horário só seria barrado no `fechar_pedido`.
- 11:30 e 11:33 — o agente transferiu duas vezes com `motivo: pedido_para_confirmar` sem chamar nenhuma Skill. A conversa pausou e precisou ser devolvida ao bot pelo painel.

## 2. `proxima_abertura` (raiz do pacote) só com unidades que têm cardápio

- Quando a empresa está fora do horário (`fora_do_horario: true`), `proxima_abertura` passa a ser a primeira abertura **entre as unidades com `tem_cardapio: true`**. Formato inalterado: `{"unidade","codigo","dia_semana","data","hora"}`.
- Nenhuma unidade com cardápio tem abertura conhecida → `null`.
- Empresa sem nenhum item que possa ser pedido (sem catálogo): continua valendo a primeira abertura de qualquer unidade, como antes.
- **Não muda:** `fora_do_horario` (todas as unidades fechadas), o texto do modo "só avisar", `equipe_disponivel` / `retorno_equipe` e a `proxima_abertura` de cada unidade dentro de `unidades[]` (cada uma continua com a sua, junto do `tem_cardapio`).
- De quebra: a situação de cada unidade em `unidades[]` passou a ser calculada no mesmo instante do `agora` do pacote (antes era lida alguns milissegundos depois).

## 3. Horário reconferido quando a unidade muda

O carrinho ganha `horario_aviso`, **sempre presente**: `null`, ou o motivo pelo qual o horário guardado não vale para a unidade atual, no mesmo formato das recusas de horário (PED2):

```json
"horario_aviso": {"motivo": "horario_fora_funcionamento",
                  "agendado_para": "2026-10-13T12:15:00-03:00", "agora": "2026-10-13T11:26:00-03:00",
                  "aberta_no_dia": [["12:30", "15:00"]],
                  "proxima_abertura": {"unidade": "Morumbi", "codigo": "morumbi", "dia_semana": "terça-feira", "data": "2026-10-13", "hora": "12:30"}}
```

- `motivo`: `horario_fora_funcionamento` ou `horario_passado` (este com `aberta_agora` e `proxima_abertura`, como na recusa).
- Com `horario_aviso` preenchido: `pode_fechar: false` e `faltando` traz `"horario"`. `horario_vencido` continua existindo (é o caso `horario_passado`).
- **`definir_entrega`**: o sucesso traz `aviso_horario` na **raiz** da resposta (o mesmo objeto, ou `null`). A troca de unidade é **aceita**; o aviso diz na hora que o horário guardado não serve para a unidade nova. Vale para qualquer chamada (trocar só a modalidade também devolve o aviso, enquanto ele existir).
- Está no carrinho de toda resposta de Skill e em `contexto_modulos.pedidos.carrinho` do pacote.
- O `fechar_pedido` continua recusando com `horario_passado` / `horario_fora_funcionamento`, como antes.
- Sem horário marcado ("o quanto antes"): nunca há aviso.

## 4. Transferência por `pedido_para_confirmar` sem recusa de Skill

`pedido_para_confirmar` existe para o que o gateway recusou (item sem preço etc.). Sem recusa no turno, o agente não tentou.

- **Quando atua:** turno de cliente, resposta `acao: transferir_humano` com `motivo: pedido_para_confirmar`, agente com o módulo de pedidos, e **nenhuma chamada de Skill recusada** (status HTTP 400 ou mais, qualquer Skill e qualquer motivo) desde o início do turno (do primeiro, se foi refeito).
- **1ª vez:** a resposta é descartada (`turnos.transferencia_sem_recusa = "refeito"`), nada é enviado, a conversa não pausa, e o turno é refeito com `aviso_do_gateway`:
  > Você pediu transferência por pedido_para_confirmar, mas nenhuma Skill recusou nada neste turno. Chame a Skill necessária (buscar_catalogo, adicionar_item) antes; só transfira se ela recusar, ou explique o motivo real.
- **Repetiu:** a transferência **segue** normalmente (cliente avisado, conversa pausada e aguardando atendente). O turno fica com `transferencia_sem_recusa = "mantida"`, entra na auditoria (`transbordo.sem_recusa`) e sai alerta no Telegram e no log (empresa, agente, turno e os 4 últimos dígitos da conversa).
- **Não atua:** outros motivos de transferência; turno em que alguma Skill recusou algo; avisos de sistema.
- Uma guarda por turno: se o turno já foi refeito por anúncio falso de pedido (PED2), a transferência do turno refeito não é refeita de novo (segue, com o alerta).
- Só na fila durável, como a do anúncio falso.
- **Métricas** (`GET /admin/v1/tenants/{id}/metricas`): `transferencias_sem_recusa_refeitas` e `transferencias_sem_recusa_mantidas`.

## 5. Conferências pedidas (só leitura do código)

- **Id da opção tocada:** em `mensagens_do_lote[].interacao` o campo é **`id`**. O objeto é `{"tipo": "botao"|"lista", "id": "<id da opção>", "atual": true|false}`, mais `"substituiu": [ids]` quando houve toques contraditórios na mesma mensagem. Na raiz, `ultima_interacao` é `{"tipo", "id", "titulo", "atual"}`. Não existem `opcao_id` nem `button_id`.
- **Tamanho e formato do id** (botão e linha de lista, no envio e no retorno do toque): de 1 a **40** caracteres, só **letras minúsculas, números e `_`**. `item_<item_id>` e `qtd_<N>_<item_id>` cabem com folga (`qtd_10_775` tem 10). Id fora do formato (maiúscula, hífen, acento, espaço, mais de 40) ou repetido faz **todas** as opções daquela resposta saírem como texto numerado em vez de botões.

## 6. Testes

`tests/test_hor2.py`: próxima abertura ignorando unidade sem cardápio; empresa sem cardápio nenhum; campo nulo com unidade aberta; função de base inalterada para quem não pede o filtro; troca de unidade com horário marcado (aviso na raiz, no carrinho e no pacote; `pode_fechar`; fechamento recusando); aviso some com horário novo; `horario_passado`; sem horário não há aviso; transferência refeita e resolvida; transferência repetida (segue, alerta, auditoria); recusa de Skill no turno (`sem_preco`, `item_nao_buscado`) deixa passar; outro motivo não é tocado; métricas.
