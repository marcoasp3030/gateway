# ATUALIZACAO_CURSOR.md — Atualização de documentos (08/10/2026)

> **Para o agente do Cursor.** Este pacote contém **somente documentação**. Nenhum arquivo de código, `.env`, `docker-compose*.yml` ou do painel deve ser substituído por ele. O código na VPS (inclusive `app/limites.py`, ajustes de `app/main.py`, `scripts/skyone_probe.py`, `docs/SKYONE.md`, `docs/PUBLICACAO.md`, `docs/Caddyfile.painel.example` e `PROGRESS.md`) é a fonte da verdade e deve ser preservado.

## Arquivos deste pacote

| Arquivo | Ação | O que mudou |
|---|---|---|
| `PLANO_CURSOR.md` | **Substituir** | Nova seção 0 (estado atual: Fase 0 ✅, publicação via Caddy ✅, Fase 0.3 parcial); regras 2/3 e SEG02/05/07/08 trocadas de Nginx para Caddy; Fase 0.3 reescrita com o que já foi respondido; SEG13 (gateway → Skyone autenticado); contrato do Apêndice A alinhado ao fluxo validado (`message`, `session_id`, sem `token` no corpo); pendências de código que você registrou no `PROGRESS.md` viraram tarefas nas Fases 0.5, 0.6, 1 e 3 |
| `GUIA_CURSOR.md` | **Substituir** | Fase 0.3 e Fase 1 atualizadas; referências a Nginx trocadas por Caddy |
| `.cursor/rules/gateway.mdc` | **Substituir** | Regras de proxy (Caddy) e leitura obrigatória da seção 0 + `PROGRESS.md` |
| `docs/API_ADMIN.md` | **Substituir** | Referência ao proxy público ajustada |
| `docs/SKYONE_RESULTADOS.md` | **Adicionar** (novo) | Resultados dos testes do Marco no Skyone + roteiro dos testes pendentes. **Não substitui** o `docs/SKYONE.md` existente |

## Passos

1. Antes de copiar, faça um commit do estado atual (`git add -A && git commit -m "antes da atualização de docs"`), se houver alterações pendentes.
2. Copie os arquivos acima para a raiz do repositório na VPS (`/home/twadmin/gateway`), respeitando as pastas.
3. Rode `git diff --stat` e confira que **só** esses 5 arquivos mudaram.
4. Leia a seção 0 e a Fase 0.3 do `PLANO_CURSOR.md` e o `docs/SKYONE_RESULTADOS.md`.
5. Verifique se algum item do `PROGRESS.md` (seção "Análise para as próximas fases") ficou sem tarefa correspondente no plano; se ficou, aponte ao Marco antes de seguir.
6. Commit: `docs: plano atualizado com estado da VPS e resultados do Skyone`.

## Contexto rápido do que o Marco descobriu no Skyone

- Fluxo de teste "gateway": `Webhook → JavaScript "Normalizar Entrada" → AI Agent Call → Retorno`, URL `https://luxtia.api.integrasky.cloud/k3iynwDoOU`, **sem autenticação por enquanto**.
- Corpo aceito: `{"conversation_id","session_id","request_id","message","instrucoes_agente"}`.
- Resposta: `{"resposta": "<texto>", "request_id", "conversation_id", "status": "success", "tipo": "text", "acao": "nenhuma"}`.
- Instruções no payload funcionam; base/Skills/modelo são fixos no agente; URL é por fluxo.
- Latência 9–14 s, ~99% no AI Agent Call, causada pelo prompt do sistema do agente (~15 mil tokens). Não é problema do gateway.
