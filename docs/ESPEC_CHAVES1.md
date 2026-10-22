# CHAVES1 — chave de plataforma separada, papel exato e rotas globais só da plataforma

**Situação: proposta para revisão do Marco, 22/10/2026. Nada implementado.**
- **Base:** as decisões 2 e 3 do Marco de 22/10 e o resultado de `tests/test_escopo_obrigatorio.py`.
- **Motivo:**
  - Hoje há uma chave só do painel (`PANEL_SERVICE_KEY`, e a próxima na rotação), e o papel vem do cabeçalho `X-Role`.
  - Quem tem a chave e manda `X-Role: plataforma` vê e altera todas as empresas.
  - A exigência de `X-Tenant-Scope` não protege contra isso: erro do painel ao montar o papel, cabeçalho injetado ou SSRF no servidor do painel.

## 1. Chaves

| Variável no `.env` | Quem usa | Papéis aceitos |
|---|---|---|
| `PANEL_SERVICE_KEY` (+ `PANEL_SERVICE_KEY_NEXT`) | painel, sessões de empresa | `admin`, `membro` |
| `PANEL_PLATFORM_KEY` (+ `PANEL_PLATFORM_KEY_NEXT`) | painel, sessão do admin da plataforma; scripts de operação | `plataforma` (e também `admin`/`membro`, para o super admin agir dentro de uma empresa) |

- **Origem das chaves:** geradas pelo Marco direto no `.env` da VPS. Nunca aparecem no chat, em log, em commit nem na linha de comando.
- **Validação na partida:**
  - As mesmas regras de hoje: tamanho mínimo e nenhum valor de exemplo.
  - A chave de plataforma precisa ser diferente das duas chaves de empresa. Se for igual, o gateway não sobe.
- **Rotação:** como a de hoje. A `_NEXT` é aceita junto com a atual até a troca.
- O log registra só qual chave foi usada (`chave_painel=empresa|plataforma`, `atual|proxima`), nunca o valor.

## 2. Papel (`X-Role`)

- **Comparação exata:** `plataforma`, `admin` ou `membro`, em minúsculas e sem espaço.
- **Valor fora da lista:** 403.
  - Hoje o gateway tira espaços e passa para minúsculas, e um papel desconhecido vira `membro`.
  - Ex.: `PLATAFORMA `, `Admin`, `superadmin` e `plataforma-x` passam a dar 403.
- **Cabeçalho ausente:** 403. Hoje vira `membro`.
- **`plataforma` só com a chave de plataforma:**
  - Com a chave de empresa, a resposta é 403 (`papel_sem_credencial`).
  - Gera alerta `painel_papel_plataforma` (aviso, no máximo um por minuto por rota) e registro na auditoria.
  - Nada da rota é executado.

## 3. Rotas globais: só plataforma

Leitura e escrita só com `X-Role: plataforma` (portanto, com a chave de plataforma). Sessão de empresa recebe 403, mesmo com `X-Tenant-Scope`:

| Rota | Hoje, sessão de empresa com o cabeçalho |
|---|---|
| `GET/POST /tenants` | GET devolve só a própria empresa; POST dá 403 |
| `GET /modelos-agente`, `GET /modelos-agente/{id}` | modelos globais + os da empresa |
| `POST/PATCH /modelos-agente…` | 403 |
| `GET /fila` | 403 |
| `GET /auditoria/plataforma` | 403 |
| `GET /segmentos`, `GET /segmentos/{id}` | modelos de atendimento globais |
| `GET /modulos` | registro de módulos |

O painel já chama todas essas rotas só pela sessão de plataforma (`sessao.plataforma()`, que exige `superAdmin`). Nada muda para quem usa o painel de empresa.

## 4. Transição

- **Fase A (a implantação):** `CHAVE_PLATAFORMA_OBRIGATORIA=off`.
  - `X-Role: plataforma` com a chave antiga continua aceito e fica registrado em log (`gateway.auth`, um por minuto por rota, sem dados) e na contagem do alerta `painel_papel_plataforma`.
  - A comparação exata do papel e as rotas globais (seção 3) passam a valer já: o painel atual já cumpre as duas.
  - Os scripts de operação passam a usar a chave de plataforma (seção 6).
- **Fase B, por ordem do Marco:** `CHAVE_PLATAFORMA_OBRIGATORIA=on`. A partir daí, `plataforma` com a chave de empresa recebe 403.
- **Para voltar atrás:** desligar a flag, pelo `implantar.sh`.

## 5. Comando para o [PAINEL]

> [PAINEL] Separação de chaves do gateway (CHAVES1):
> 1. Nova variável de ambiente no servidor do painel: `GATEWAY_PLATFORM_KEY`, com o mesmo valor de `PANEL_PLATFORM_KEY` do gateway. O Marco copia pelo gerenciador de senhas; nunca em código, log ou commit.
> 2. Em `src/lib/gateway/cliente.server.ts`:
>    - toda requisição com `papel === "plataforma"` usa `Bearer ${GATEWAY_PLATFORM_KEY}`;
>    - as de `admin` e `membro` continuam com `GATEWAY_PANEL_KEY`;
>    - sem a chave de plataforma configurada, a chamada de plataforma falha (não cai para a chave de empresa);
>    - o servidor nunca expõe nenhuma das duas ao navegador.
> 3. `X-Role` sempre exato (`plataforma`, `admin`, `membro`).
> 4. Mantenha `X-Tenant-Scope` em todas as chamadas de admin e membro, como hoje.
> 5. Testes:
>    - chamada de plataforma usa a chave de plataforma;
>    - admin e membro usam a de empresa;
>    - sem a chave de plataforma, a sessão de plataforma dá erro claro e nenhuma chamada sai com a chave errada.
> 6. Publique e avise o [GATEWAY]. A exigência (fase B) só é ligada depois.

## 6. Scripts de operação do gateway

Os 6 scripts que chamam a API admin mandam hoje só `Authorization` e `X-Actor`, sem `X-Role` nem `X-Tenant-Scope`: `envio_real.sh` (freio de emergência), `canal_credencial.py`, `fila_duravel.sh`, `importar_catalogo.py`, `bench.py` e `carga_fila.py`.
- Com `ESCOPO_EMPRESA_OBRIGATORIO=on`, todos passam a receber 403.
- **Proposta:** todos passam a mandar `X-Role: plataforma` com a chave lida de `PANEL_PLATFORM_KEY` (na fase A, de `PANEL_PLATFORM_KEY` se existir, senão de `PANEL_SERVICE_KEY`).
- **Teste:** cada script contra o app de teste, com as duas exigências ligadas.

## 7. Testes

- **Chave e papel:**
  - `plataforma` só com a chave de plataforma (atual e próxima), com a flag ligada;
  - com a flag desligada: aceito, com log e alerta;
  - chave de empresa + `plataforma` dá 403 e alerta.
- **Papel exato:** valores com maiúsculas, espaços, desconhecidos ou ausentes dão 403.
- **Rotas globais:** as da seção 3 dão 403 para `admin` e `membro`, com e sem `X-Tenant-Scope`, e funcionam para plataforma.
- **Partida:** chave de plataforma igual à de empresa impede a subida; valores de exemplo também.
- **Scripts:** os 6 da seção 6 funcionam com `ESCOPO_EMPRESA_OBRIGATORIO=on`.
- **`tests/test_escopo_obrigatorio.py`:** atualizado para o novo comportamento.

## 8. Decisões em aberto

| # | Decisão | Proposta |
|---|---|---|
| 1 | O super admin pode usar a chave de plataforma com `X-Role: admin`/`membro` dentro de uma empresa? | Sim: o painel decide o papel da sessão; a chave de plataforma vale para os três |
| 2 | Nome da flag da fase B | `CHAVE_PLATAFORMA_OBRIGATORIA` |
| 3 | Scripts de operação (seção 6) | Corrigir já, antes de ligar o `ESCOPO_EMPRESA_OBRIGATORIO` (ver relatório de 22/10) |

**Ponto de parada:** só especificação.
