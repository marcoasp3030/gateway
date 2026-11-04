# CANAL-EXC — excluir canal

**Situação: decisões do Marco em 04/11/2026; implementado no gateway; botão no painel pendente ([PAINEL]).**

## Decisões

1. **Histórico:** excluir o canal apaga todo o histórico das conversas dele. Isso inclui mensagens, turnos, atendimentos, notas, etiquetas, esperas, follow-ups, solicitações, avisos internos, registros de Skills, documentos, links do cardápio e carrinhos.
2. **Pedidos:** ficam, sem dados pessoais. Somem endereço, bairro, CEP, observações, nome e telefone do cliente e os motivos dos eventos; ficam itens, valores, unidade, pagamento e status.
3. **Quem pode:** só a plataforma.
4. **Condições:**
   - o canal precisa estar **desativado**;
   - é preciso digitar o **nome do canal** para confirmar;
   - a auditoria registra só quantidades, nunca conteúdo.

## Rota

`DELETE /admin/v1/tenants/{t}/canais/{canal}` com o corpo `{"confirmacao": "<nome do canal>"}`. Só plataforma.

| Situação | Resposta |
|---|---|
| Excluído | `200 {"ok":true,"conversas_apagadas","conversas_com_pedido","mensagens_apagadas","pedidos_mantidos","contatos_apagados","canal":"apagado"\|"marcado"}` |
| Canal ativo | `409 CONFLICT`, `campo: ativo` (desative antes) |
| Confirmação diferente do nome | `400`, `campo: confirmacao` |
| Canal inexistente ou já excluído | `404` |

## Como fica

- **Conversa sem pedido:** é apagada.
- **Conversa com pedido:** vira uma conversa vazia e anônima, com identificador `…:excluida_<aleatório>`, sem telefone, nome nem contato. Ela existe só para o pedido continuar apontando para uma conversa e não aparece na lista de Conversas.
- **Contato:** se ficar sem nenhuma conversa, é apagado junto com os brutos de webhook e o opt-out de follow-up dele.
- **Canal:** se não sobrar nenhuma conversa, a linha é apagada. Se sobrar, ela fica marcada com `excluido_em`, sem credenciais e sem segredo do webhook, e:
  - some da lista de canais e da ficha do agente;
  - devolve 404 em consulta, alteração e webhook;
  - não pode ser reativada.
- **Auditoria:** o `alvo` das entradas antigas que apontavam para as conversas apagadas vira `conversa_excluida`, e os dados delas saem. Entra a entrada `canal.excluido`, com as quantidades.
- **Backups:** os backups diários locais continuam tendo os dados até vencerem.
