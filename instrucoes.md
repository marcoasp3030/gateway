# Instruções — gateway By Koji

## Implantação cardápio/taxas (02/11/2026)

- `sh scripts/implantar.sh cardapio-publico-20261102` → `gateway:8f5208a`, migrações até 061.
- Correção inclusa: busca com `public.unaccent`.
- `.env`: `PAINEL_PUBLIC_URL` para link do cardápio. Avaliar WORKERS/concorrência conforme `docs/CAPACIDADE.md`.

## Push completo para o GitHub (01/11/2026)

- Repositório: https://github.com/marcoasp3030/gateway.git
- Enviado o histórico local completo de `main` (estava ~205 commits à frente do remoto).
- HEAD da última feature antes do registro: `0cc55d5` (TEL1 — validação de telefone BR).
- Worklog de desenvolvimento: `docs/worklog.md`.
