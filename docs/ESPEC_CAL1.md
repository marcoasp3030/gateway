# CAL1 — Calendário no pacote

**Situação:** especificação para aprovação. **Nada implementado.**
**Data:** 13/10/2026. Produção: `gateway:b9b6ff1`, schema 38.

## 1. Objetivo

O agente nunca calcula datas. Todo pacote leva, na **raiz**, o campo `calendario` com hoje e os 7 dias seguintes.

## 2. Contrato (gateway → agente)

```json
"calendario": {
  "fuso": "America/Sao_Paulo",
  "dias": [
    {"data": "2026-10-12", "dia_semana": "segunda-feira", "rotulo": "hoje",
     "feriado": "Nossa Senhora Aparecida", "tipo_feriado": "nacional", "observacao": null},
    {"data": "2026-10-13", "dia_semana": "terça-feira", "rotulo": "amanhã",
     "feriado": null, "tipo_feriado": null, "observacao": "Morumbi fechado (inventário)"},
    {"data": "2026-10-14", "dia_semana": "quarta-feira", "rotulo": null,
     "feriado": null, "tipo_feriado": null, "observacao": null}
  ]
}
```

| Campo | Regra |
|---|---|
| `calendario` | Sempre presente, em todo turno (cliente e avisos de sistema), com ou sem módulo de pedidos |
| `fuso` | Fuso da empresa. Todas as datas são calculadas nele: às 00:05 "hoje" já é o dia novo |
| `dias` | Sempre 8 itens, em ordem: hoje + 7 |
| `data` | `AAAA-MM-DD` |
| `dia_semana` | `segunda-feira`, `terça-feira`, `quarta-feira`, `quinta-feira`, `sexta-feira`, `sábado`, `domingo` |
| `rotulo` | `"hoje"` no primeiro, `"amanhã"` no segundo, `null` nos demais |
| `feriado` | Nome do feriado, ou `null` |
| `tipo_feriado` | `nacional`, `estadual`, `municipal`, `empresa` ou `null` (sempre nulo junto com `feriado` nulo) |
| `observacao` | Texto curto sobre o que muda nas unidades naquele dia, ou `null` |

## 3. Fontes

1. **Nacionais e estaduais:** biblioteca `holidays` (país `BR`, subdivisão = UF da unidade; By Koji = `SP`), só a categoria de feriado oficial. Versão fixada em `app/requirements.txt`. Hoje a biblioteca **não está** na imagem: entra neste bloco.
2. **Municipais:** lista no próprio gateway, por cidade, em `app/nucleo/calendario.py`. Começa com São Paulo capital: aniversário da cidade (25/01) e Corpus Christi (Páscoa + 60 dias, calculado). Outras cidades entram acrescentando uma linha.
3. **Exceções do painel** (`unidade_excecoes`, as mesmas do horário): viram `observacao`.

**Cidade da unidade:** hoje a unidade só tem o endereço em texto; não há campo de cidade. Proposta: colunas novas `cidade` e `uf` na unidade (migração aditiva), preenchidas com `São Paulo` / `SP` nas 4 unidades do By Koji, aceitas no cadastro de unidade da API do painel. Unidade sem cidade: só nacionais.

## 4. Regras

- **Mais de um feriado no dia:** vale um só, na ordem nacional → estadual → municipal → empresa.
- **Unidades em cidades ou estados diferentes:** o feriado estadual ou municipal aparece se valer para alguma unidade ativa; quando não vale para todas, a `observacao` diz onde ("feriado só em São Paulo").
- **`observacao`**, uma frase por unidade afetada, juntas por "; ", até 160 caracteres:
  - fechada o dia todo: `"Morumbi fechado (inventário)"` — o motivo entre parênteses é o cadastrado; sem motivo, só `"Morumbi fechado"`;
  - horário diferente: `"Einstein abre 12h–16h"`;
  - todas as unidades com a mesma exceção: `"Todas as unidades fechadas (motivo)"`;
  - exceção que só repete o feriado (unidade fechada no feriado, sem motivo próprio ou com o nome do feriado como motivo): não gera observação.
- **`tipo_feriado: "empresa"`:** dia sem feriado oficial em que **todas** as unidades ativas estão fechadas por exceção com o mesmo motivo. `feriado` = o motivo (ex.: "Recesso de fim de ano") e `observacao` fica nula.
- **Fora do calendário:** ponto facultativo (Carnaval, Quarta-feira de Cinzas), pausa manual de unidade e a grade semanal normal. Esses já estão em `unidades` (`aberta_agora`, `proxima_abertura`, `horarios`).
- **Falha ao montar** (biblioteca, banco): o pacote sai com `calendario` contendo os 8 dias sem feriado nem observação, e fica um aviso no log. O turno nunca falha por causa do calendário.
- Sem configuração nova, sem mudança de comportamento do gateway: é só informação para o agente.

## 5. Onde ficam hoje os outros campos

Todos na **raiz** do pacote, nenhum dentro de `contexto_modulos`:

| Campo | Onde | Sempre presente |
|---|---|---|
| `recursos` | raiz | sim |
| `fora_do_horario` | raiz | sim |
| `proxima_abertura` | raiz | sim (nulo com a empresa aberta) |
| `equipe_disponivel` | raiz | sim |
| `retorno_equipe` | raiz | sim (nulo com a equipe disponível) |
| `aviso_do_gateway` | raiz | sim (nulo quase sempre) |

`contexto_modulos` só tem `catalogo`, `pedidos` e, com o recurso ligado, `solicitacoes`.

## 6. Banco, testes e implantação

- Migração 039 (aditiva): `tenant_unidades.cidade`, `tenant_unidades.uf`; By Koji preenchido.
- Testes (relógio fixo): feriado nacional (12/10); estadual (09/07, SP); municipal (25/01); Corpus Christi em dois anos (data móvel); virada às 00:05 (rótulos e `data` no fuso da empresa, não em UTC); exceção de uma unidade (fechada e horário especial); exceção igual ao feriado não repete; todas as unidades fechadas → `empresa`; unidade sem cidade → só nacionais; empresa de outro estado não recebe o estadual de SP; 8 itens sempre; falha da biblioteca não derruba o turno; teste do contrato do pacote com o campo novo.
- Implantação por `scripts/implantar.sh cal1`, com tag de volta e backup. O build passa a baixar a `holidays`.

## 7. Decisões que preciso de você

1. **Cidade por unidade** (colunas `cidade` e `uf`, By Koji = São Paulo/SP), como proposto, ou uma cidade só por empresa?
2. **`tipo_feriado: "empresa"`** só quando todas as unidades fecham pelo mesmo motivo (proposta). Serve?
3. **Ponto facultativo** (Carnaval, Cinzas) fora do calendário (proposta), ou entra como `observacao` ("ponto facultativo: Carnaval")?
4. **Exceção com horário especial:** texto `"Einstein abre 12h–16h"`; com dois turnos, `"Einstein abre 12h–15h e 19h–23h"`. Serve?
5. **Aviso de sistema** (turno `tarefa: notificar`) também leva `calendario` (proposta: sim, o pacote é o mesmo)?
