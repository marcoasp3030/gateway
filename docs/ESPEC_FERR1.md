# FERR1 — passe de ferramenta por turno (Skills para várias empresas)

**Situação: proposta para revisão do Marco, 23/10/2026. Nada implementado.**

## 1. Problema

- **Hoje:** cada Skill do Skyone manda no `X-API-Key` uma chave fixa de **uma** empresa.
  - O mesmo fluxo não serve a outra empresa: no teste real de 23/10 a Skill `ler_documento` do Teste Documentos foi com a chave do By Koji e recebeu 403.
  - Uma chave fixa vale para qualquer conversa da empresa. Se vazar, lê e mexe em todas as conversas dela até ser revogada.
- **Objetivo:** um fluxo único do Skyone atende qualquer empresa. Cada turno leva no pacote um **passe** curto, assinado e preso a empresa + agente + conversa + turno; as Skills usam esse passe no lugar da chave.

## 2. O passe

- **Formato:** `ft1.<dados>.<assinatura>`.
  - **Dados:** em base64url, sem preenchimento, um JSON `{"t": empresa, "a": agente, "c": conversation_id, "r": request_id do turno, "e": [escopos], "x": expira (unix), "n": aleatório}`.
  - **Assinatura:** HMAC-SHA256 dos dados com `FERRAMENTA_PASSE_CHAVE`.
- **Chave de assinatura:** `FERRAMENTA_PASSE_CHAVE` (+ `_NEXT` para rotação) no `.env`, gerada pelo Marco e nunca mostrada. O gateway não sobe se ela for fraca ou igual a outra chave.
- **Sem estado:** não é gravado no banco, porque a assinatura basta. A revogação em massa é trocar a chave.
- **Validade:** 15 min a partir da montagem do pacote, mais do que o `timeout_s` máximo e os refazer do turno. Cada pacote (inclusive o de um turno refeito) leva um passe novo.
- **Escopos:** os do agente naquele turno.
  - `ferramentas:catalogo` (também usado por `info_unidades`) e `ferramentas:pedidos`: com o módulo;
  - `ferramentas:documentos`: com a capacidade ativa, incluindo o interruptor da empresa.
  - Nada além do que o agente já pode.
  - Hoje `info_unidades` exige `ferramentas:catalogo`, então agente sem catálogo não consulta unidades. Proposta: com o passe, `info_unidades` vale para todo agente (decisão 6).

## 3. No pacote

- **Campos novos na raiz:** `ferramenta_passe` (o passe) e `ferramenta_passe_expira_em` (ISO).
- **Por agente:** só vão para agente com o comportamento `ferramenta_passe = on` (padrão `off`, só a plataforma muda). O By Koji não muda até alguém ligar, e o retrato continua igual.

## 4. Nas rotas das Skills

- **Rotas que aceitam o passe:** todas as Skills (`/api/v1/catalogo/busca`, `/carrinho/…`, `/pedidos…`, `/unidades`, `/documentos/{id}/conteudo`).
  - O passe vai no cabeçalho `X-Ferramenta-Passe`, no lugar do `X-API-Key`.
  - Com os dois cabeçalhos: 400 `duas_credenciais`.
- **Conferências, nesta ordem:**
  1. assinatura (atual ou próxima) e validade;
  2. empresa ativa e agente ativo, ainda o mesmo da conversa;
  3. escopo da rota dentro de `e`;
  4. **a `conversation_id` da chamada** (caminho, query ou corpo) **igual a `c`**. Isso impede ler ou mexer em outra conversa ou outra empresa;
  5. Skills com efeito (escritas no carrinho, `fechar_pedido`, `cancelar_pedido`) só enquanto o turno `r` está em andamento (`etapa = processando`). Leitura vale até expirar.
- **Erros:**

| Status | `motivo` |
|---|---|
| 401 | `passe_invalido`, `passe_expirado` |
| 403 | `escopo`, `conversa_do_passe`, `agente_do_passe` |
| 409 | `turno_encerrado` (escrita depois do turno) |

  - Sempre no formato de erro de hoje, sem dizer o que estava errado no passe além do motivo.
- **Por dentro:** a chamada com passe vira o mesmo contexto da chave (`tenant_id`, `agente_id`, actor `passe:<request_id>`), então as rotas não mudam.

## 5. Auditoria e operação

- **`skill_registro`:** ganha `credencial` (`chave:<id>` ou `passe`) e `turno_id` pelo `r` do passe, não mais pela conversa.
- **Recusas do passe** (assinatura, expirado, outra conversa): registradas com o motivo e sem o passe.
  - `conversa_do_passe` gera alerta `passe_outra_conversa`: é sinal de injeção no prompt ou de erro do fluxo.
- **Nunca em log:** o filtro de segredos passa a cobrir `ft1.\S+`.
- **Painel:** em cada chamada de Skill da conversa, mostra se veio por chave ou por passe.

## 6. Risco: o passe ser visto pelo modelo de IA

O pacote inteiro passa pelo Normalizar. Se o passe chegar ao contexto do modelo, uma instrução maliciosa (num documento, por exemplo) pode fazer o modelo escrever o passe na resposta ou chamar uma Skill com outros parâmetros. Mitigações, em camadas:

1. **Normalizar:** tira `ferramenta_passe` antes de montar as mensagens do modelo e guarda numa variável do fluxo. O modelo nunca vê o passe.
2. **Skills no Skyone:** o cabeçalho `X-Ferramenta-Passe: {{ferramenta_passe}}` e o `conversation_id` vêm das variáveis do fluxo, **não** de argumentos que o modelo preenche. O modelo só escolhe página, busca, item…
3. **Gateway, no retorno:** se a resposta contém `ft1.` ou o próprio passe, o turno é tratado como "resposta contém credencial", como já é feito com a chave de hoje. A resposta não sai e vai o texto de falha.
4. **Alcance limitado:** mesmo vazado, o passe só serve àquela conversa e àquele agente por até 15 min, e escrita só durante o turno. Hoje uma chave vazada vale para a empresa toda, sem prazo.

## 7. Transição (sem quebrar o By Koji)

1. **Implantar:** o gateway aceita as duas formas. As chaves atuais continuam valendo, e `ferramenta_passe` fica `off` em todos os agentes.
2. **Fluxo novo no Skyone** (Assistente de documentos): Normalizar e Skills como na seção 8; liga o `ferramenta_passe` no Teste Documentos e testa.
3. **By Koji, quando o Marco decidir:** liga o passe no agente e troca as Skills do fluxo dele. Por alguns dias as duas formas convivem; o painel mostra quantas chamadas ainda vêm por chave.
4. **Depois:** a chave fixa de ferramentas do By Koji é revogada. Também pode ser restringida antes, por IP do Skyone.

## 8. O que muda no Skyone

- **Normalizar:** lê `ferramenta_passe` e `conversation_id` do pacote, guarda em variáveis do fluxo e **remove** `ferramenta_passe` do que vai ao modelo.
- **Cada Skill:**
  - o cabeçalho `X-API-Key: <chave fixa>` passa a ser `X-Ferramenta-Passe: {{ferramenta_passe}}`;
  - `conversation_id` vem da variável, não do modelo;
  - 401 `passe_expirado` → a Skill responde ao modelo "não consegui consultar agora" (sem tentar outra credencial).
- **Interpretar e Retorno:** nada muda.

## 9. Testes

- Assinatura inválida, adulterada ou com a chave antiga depois da rotação; passe expirado.
- Escopo fora; agente trocado ou pausado; empresa suspensa.
- `conversation_id` diferente (mesma empresa, outra empresa); com os dois cabeçalhos.
- Escrita depois do turno → 409; leitura depois do turno, até expirar → ok.
- Turno refeito leva passe novo, e o antigo continua válido só para leitura.
- Retorno com o passe ou com `ft1.` → resposta segurada.
- Passe nunca em log nem no banco.
- By Koji com `ferramenta_passe = off`: pacote e retrato idênticos; chave fixa continua funcionando.

## 10. Decisões em aberto

| # | Decisão | Proposta |
|---|---|---|
| 1 | Validade do passe | 15 min |
| 2 | Cabeçalho | `X-Ferramenta-Passe` (separado do `X-API-Key`, para não confundir nos logs e nas regras de chave) |
| 3 | Escrita só durante o turno | Sim (seção 4, conferência 5) |
| 4 | Ligar por agente (`ferramenta_passe`) ou para todos | Por agente, padrão `off` |
| 5 | Revogar a chave fixa do By Koji depois da troca | Sim, após uma semana sem chamadas por chave |
| 6 | `info_unidades` para agente sem catálogo | Sim, com o passe (horário e endereço servem a qualquer segmento) |

**Ponto de parada:** só especificação.
