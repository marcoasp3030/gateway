# Progresso do gateway

## Fase 0 — Implantação em modo simulado — 2026-10-08

- Status: concluída.
- Feito:
  - Analisados README, GUIA_CURSOR, PLANO_CURSOR, regras do projeto, contrato administrativo e código existente.
  - Criado `.gitignore`, incluindo segredos, ambiente virtual e caches.
  - Criado `.env` local com permissão 600, senhas/chaves aleatórias e chave no formato Fernet. Valores não versionados.
  - Mantidos `SKYONE_MODE=mock`, `SEND_MODE=dry` e documentação interativa desligada.
  - Criado banco **bykoji_test** no Postgres próprio deste Compose. O benchmark grava apenas nesse ambiente de teste; o banco do painel não foi usado.
  - Subidos gateway, Postgres e Redis com volumes próprios. Gateway em `127.0.0.1:8100`, Postgres em `127.0.0.1:5433`; Redis sem porta publicada.
- Validações (comando → resultado):
  - `docker --version` → 29.1.3; `docker compose version` → 2.40.3.
  - `docker compose up -d --build` → concluído; `docker compose ps` → gateway ativo, Postgres e Redis saudáveis.
  - `curl --fail --silent http://127.0.0.1:8100/health` → `{"ok":true,"agente":"mock","envio":"dry"}`.
  - `tests/test_seguranca.py` em Python 3.12 → **26 passed**.
  - `scripts/bench.py`, 5 clientes, executado dentro do gateway via stdin, com chave lida do ambiente do container → **5/5 respondidos**, **0 erros**.
  - `docker compose exec -T gateway id` → uid 10001, sem root.
  - `/docs` → 404; `/metricas` sem chave → 401; `/metricas` com chave correta e `X-Forwarded-For` → 404.
  - Webhook sem segredo → 401; webhook com segredo e corpo vazio `{}` → 200, ignorado; corpo de 3 MB via curl → 413.
  - `/health` com `X-Forwarded-For` → somente `{"ok":true}`.
  - Busca das chaves locais nos logs do gateway após webhook autenticado → zero ocorrências.
  - `.env` com permissão 600 e ignorado pelo Git.
  - `docker compose exec -T -e ADMIN_KEY=troque-x gateway python -c 'import main'` → recusado com exit 1, como esperado; processo em execução preservado.
- Métricas da primeira execução, 5 turnos:
  - Respostas observadas pelo benchmark: p50 8,0 s, máximo 8,9 s.
  - Servidor: total p50 7.915 ms, p95 8.641,2 ms, máximo 8.659 ms.
  - Agente simulado: p50 3.812 ms, p95 4.505,4 ms; espera p50 4.101 ms.
  - Média de 2,2 mensagens por lote; zero erros e zero avisos de demora.
  - Estes números incluem debounce de 4 s e mock de 2–5 s; não medem a latência real do Skyone.
- Ajustes ao ambiente:
  - O Python 3.10 do host não tem `ensurepip/python3-venv`. Testes executados em venv temporário dentro de container Python 3.12, sem instalar pacotes no host.
  - O proxy existente é **Caddy**, no projeto `painel_koji`, ocupando 80/443. Nginx não está disponível no PATH e `sudo -n` exige senha. A configuração Nginx do roteiro não deve ser aplicada diretamente nesse ambiente.
  - Nenhum serviço do painel foi alterado. Não houve publicação externa, conexão ao Skyone ou envio ao WhatsApp.

### Reproduzir os testes unitários

```bash
cd /home/twadmin/gateway
docker run --rm --user "$(id -u):$(id -g)" \
  -v /home/twadmin/gateway:/workspace:ro -w /workspace python:3.12-slim \
  sh -c 'python -m venv /tmp/test-venv && /tmp/test-venv/bin/pip -q install pytest && /tmp/test-venv/bin/python -m pytest -q -p no:cacheprovider tests/test_seguranca.py'
```

### Reproduzir o benchmark sem expor a chave no comando

```bash
cd /home/twadmin/gateway
python3 - <<'PY'
from pathlib import Path
import subprocess
source = '''import os, sys
from urllib.parse import urlsplit
assert urlsplit(os.environ["DATABASE_URL"]).path.endswith("_test")
sys.argv = ["bench.py", "--url", "http://127.0.0.1:8000",
            "--admin-key", os.environ["ADMIN_KEY"], "--clientes", "5"]
''' + Path('scripts/bench.py').read_text()
subprocess.run(['docker', 'compose', 'exec', '-T', 'gateway', 'python', '-'],
               input=source, text=True, check=True)
PY
```

## Análise para as próximas fases

- O código atual implementa somente a base da Fase 0. `docs/API_ADMIN.md` descreve o contrato futuro: `/admin/v1`, tenants, modelos, agentes, permissões e auditoria ainda não estão implementados.
- **Próxima fase: 0.3, descobertas no Skyone.** Preparar o probe e validar instruções por payload, API de criação/clonagem, timeout síncrono e callback. O checkpoint do PLANO_CURSOR exige resultados reais do Marco antes de escolher a estratégia da Fase 0.5. Nenhuma conclusão sobre essas capacidades foi presumida.
- **Fase 0.5:** já existe Postgres no painel (`painel_koji-banco-1`). Definir a ligação de rede e banco/usuário separados para o gateway conforme o plano, preservando o banco de teste. Revisar o contrato antes de construir as telas.
- **Fase 1:** adaptar a publicação ao Caddy existente, mantendo rotas administrativas internas; definir domínio e capturar payloads reais da instância uazapi de teste.
- Pontos encontrados no código, a tratar antes do uso real nas fases correspondentes:
  - A validação DNS `validar_url_saida` existe, mas ainda não é chamada pelos adaptadores Skyone/uazapi antes das requisições (Fase 0.5).
  - O limite de corpo atual depende de `Content-Length`; falta limitar a leitura de corpos sem esse cabeçalho.
  - O buffer é retirado antes de concluir o processamento; ainda não há recuperação durável dos lotes em execução (Fase 0.6).
  - A pausa humana é consultada antes da chamada ao agente, sem nova checagem antes do envio (INV03, Fase 3).
  - O adaptador uazapi registra HTTP de erro, mas não o propaga ao pipeline; precisa evitar registrar entrega bem-sucedida em envio recusado.
  - Tokens presentes em payloads de terceiros ainda não são sanitizados antes de gravar `webhook_raw`; validar com fixtures reais antes de conectar o provedor.
- A aprovação da Fase 0 confirma a simulação e as verificações descritas acima; as garantias de produção e os demais invariantes continuam pendentes das fases seguintes.
