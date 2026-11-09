# CARD-LINK — ligar ou desligar o envio do link do cardápio digital

**Situação: aprovada pelo Marco em 09/11/2026 e implementada no gateway; painel pendente ([PAINEL]).**

## 1. O que muda

- **Novo comportamento do agente:** `enviar_link_cardapio`, com os valores `on` e `off`. Nulo herda; o padrão é `on`.
  - **on (padrão, como era):** o pacote ao agente traz `contexto_modulos.pedidos.link_cardapio`, e o agente manda o link quando o cliente pede o cardápio.
  - **off:** o pacote traz `link_cardapio: null` e `cardapio_digital: "desligado"`. Nenhum link é gerado. O agente apresenta as opções na conversa, como fazia antes do cardápio digital.
- **Onde fica:** é **por agente**, junto dos outros comportamentos (decisão 1 do Marco). Se a empresa tiver mais de um agente, cada um segue o seu.
- **Quem muda:** **gestor e acima** (decisão 2), pelo rascunho e publicação do atendimento ou pela ficha do agente. A mudança fica registrada como os outros comportamentos.
- **O que não muda:** o Link da loja, que a empresa divulga por conta própria, a prévia e a tela do cardápio no painel.

## 2. Contrato com o agente (Skyone)

- `contexto_modulos.pedidos.cardapio_digital` é campo novo, com os valores `"ligado"` ou `"desligado"`.
- **Instrução para o fluxo:**
  - com `"desligado"`, nunca mencionar nem prometer link de cardápio;
  - apresentar as opções pela busca no catálogo, como antes.
- `link_cardapio` continua nulo também quando a empresa não tem catálogo ativo.

## 3. Dados

Migração `071_cardapio_link_ligado.sql`: coluna `enviar_link_cardapio` em `tenant_config` e `agentes`, com `CHECK` em `on` e `off`. Aditiva.

## 4. Testes

`tests/test_card_link.py`:
- o padrão é ligado e traz o link;
- desligado pelo gestor, não traz nem gera o link;
- supervisor não altera;
- valor inválido é recusado;
- religado, o link volta.
