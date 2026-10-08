# By Koji Gateway — ambiente de teste (Fase 0)

Gateway na VPS que recebe as mensagens do WhatsApp (uazapi), agrupa mensagens picadas (debounce), chama o agente no Skyone Studio, envia a resposta e **mede o tempo de cada etapa**. O Postgres guarda conversas, histórico, métricas e cardápio.

```
uazapi ──► /webhooks/uazapi ──► Postgres (mensagens) + Redis (buffer)
                                      │ debounce (DEBOUNCE_S, teto MAX_WAIT_S)
                                      ▼
                         agente: mock  ou  webhook do fluxo Skyone
                                      ▼
                        resposta ──► uazapi (SEND_MODE=live) + Postgres + métricas
```

## 1. Subir na VPS

```bash
cp .env.example .env && chmod 600 .env   # troque TODAS as senhas/chaves (o gateway não sobe com valor de exemplo)
docker compose up -d --build
curl http://127.0.0.1:8100/health
```

O gateway escuta só em `127.0.0.1:8100`. Exponha pelo Nginx **apenas** webhooks, API de integração e health, usando `docs/nginx-gateway.conf` (o restante de `/gateway/`, incluindo rotas administrativas, responde 404 pelo domínio).

O Postgres fica em `127.0.0.1:5433` (acesso por túnel SSH).

> Rode sempre com **1 worker** (já configurado no Dockerfile): o agendador do debounce roda dentro do processo.

## 2. Roteiro de testes (do mais simples ao real)

| Etapa | `.env` | O que mede |
|---|---|---|
| A. Só a VPS | `SKYONE_MODE=mock`, `SEND_MODE=dry` | Debounce, trava, banco: overhead da VPS |
| B. VPS + Skyone | `SKYONE_MODE=live`, `SEND_MODE=dry` | Tempo real do agente, sem tocar no WhatsApp |
| C. Ponta a ponta | `SKYONE_MODE=live`, `SEND_MODE=live` | Experiência real no celular |

Depois de mudar o `.env`: `docker compose up -d gateway`.

### Benchmark (etapas A e B)

```bash
pip install httpx
python scripts/bench.py --url http://127.0.0.1:8100 --admin-key SUA_ADMIN_KEY --clientes 5
```

Simula clientes mandando 1 a 4 mensagens picadas e mede da **última mensagem** até a resposta. Resultado obtido no teste local em modo mock (debounce 2s, mock 1–2s):

```
Respondidos: 5/5 | p50: 3.7s | 3.2 mensagens por lote | 0 erros
```

### Conversa manual

```bash
curl -X POST http://127.0.0.1:8100/test/mensagem -H "X-Admin-Key: $K" \
     -H "Content-Type: application/json" -d '{"usuario":"marco","texto":"oi, quero pedir"}'
curl http://127.0.0.1:8100/test/conversa/test:marco -H "X-Admin-Key: $K"
```

### Métricas

```bash
curl "http://127.0.0.1:8100/metricas?ultimos=100" -H "X-Admin-Key: $K"
```

| Campo | Significado |
|---|---|
| `espera_ms` | Última msg do cliente → chamada ao agente (≈ debounce) |
| `agente_ms` | Tempo do Skyone (ou mock) |
| `total_ms` | Última msg do cliente → resposta enviada (o que o cliente sente) |
| `msgs_por_lote` | Quantas mensagens picadas viraram uma resposta |
| `avisos_demora` | Vezes que o agente passou de `SLOW_NOTICE_S` |

Detalhe por turno: tabela `turnos`.

## 3. Ligar o uazapi (etapa C)

1. Na instância de teste, configure o webhook para
   `https://SEU-DOMINIO/gateway/webhooks/uazapi?s=<UAZAPI_WEBHOOK_SECRET>`, evento de mensagens.
2. Mande uma mensagem e confira a tabela `webhook_raw`.
3. **Valide o formato real** do payload contra `app/uazapi.py` (`normalizar`) e os caminhos de envio (`/send/text`, `/message/presence`). Ajuste se a sua versão do uazapi usar nomes diferentes.

**Transbordo:** se alguém responder pelo celular (`fromMe` e não enviada pela API), o bot pausa por `PAUSE_MINUTES`. Para retomar: `DELETE /admin/pausa/wa:55119...`.

## 4. Fluxo no Skyone Studio

Estrutura mínima: **Webhook → JavaScript → IF → AI Agent Call → Log → Retorno**.

O gateway envia:

```json
{
  "token": "<SKYONE_TOKEN>",
  "request_id": "uuid",
  "conversation_id": "wa:5511999999999",
  "canal": "wa",
  "mensagem": "oi\nquero pedir\num combinado",
  "historico": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
  "cliente": {"nome": "Ana", "unidade_preferida": null},
  "carrinho": {"itens": [], "total": 0}
}
```

- **JavaScript:** comparar `token` com o valor esperado e montar o texto de entrada do agente (histórico + mensagem). O nome da variável que contém o corpo do webhook depende do mapeamento do seu fluxo; confira na aba Entrada do Logger.
- **IF:** token inválido → Retorno com erro, sem chamar o agente.
- **Retorno:** devolver

```json
{ "resposta": ["Oi, Ana! 😊", "É delivery ou retirada?"], "acao": "nenhuma", "motivo": null }
```

`resposta` pode ser texto único (parágrafos separados por linha em branco viram balões) ou lista. `acao: "transferir_humano"` pausa o bot. O gateway também aceita o corpo dentro de `data` (resposta consolidada).

## 5. Cardápio

```bash
curl -X POST "http://127.0.0.1:8100/admin/cardapio/importar" -H "X-Admin-Key: $K" \
     -H "Content-Type: text/csv" --data-binary @scripts/cardapio_exemplo.csv
```

CSV: `unidade,categoria,nome,descricao,preco,ativo`. Use `TODAS` para itens de todas as unidades. O arquivo de exemplo tem itens e preços **fictícios**.

Rotas para as Skills do Skyone (header `X-API-Key: <SKILLS_API_KEY>`):

- `GET /api/cardapio/busca?q=salmao&unidade=Morumbi&categoria=&limite=10`
- `GET /api/cardapio/categorias?unidade=Morumbi`

## 6. Segurança

Regras SEG01–SEG10 e LGPD01–LGPD05 na seção 2.2 do `PLANO_CURSOR.md`. Já implementado: subida bloqueada com segredo fraco, comparação em tempo constante, rotas administrativas só pela rede interna, `/docs` desligado, segredos mascarados nos logs, limite de corpo, container sem root, Redis com senha e retenção de 30 dias do `webhook_raw`. Testes: `python -m pytest -q tests/test_seguranca.py`.

## 7. Ainda não incluído (próximas fases)

Roteiro de construção: `PLANO_CURSOR.md` (para o agente do Cursor) e `GUIA_CURSOR.md` (para você conduzir o Cursor).

Áudio/imagem (hoje vira `[mídia recebida: ...]`), carrinho e pedido, Meta API, site e e-mail.
