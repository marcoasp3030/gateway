# Regras para assistentes de código neste repositório (Claude, Cursor, Gemini, Codex e outros)

Leia antes de qualquer alteração. Estas regras valem para todas as sessões e ferramentas.

## Implantação

- **Nunca rodar `docker compose up`, `build`, `restart`, `run` ou `down` em produção à mão.**
- **A única forma de implantar é `sh scripts/implantar.sh <rotulo>`.** Ele exige a árvore limpa, com tudo num commit, e roda a suíte inteira pela trava. Também guarda a imagem de volta com a tag do commit, faz o backup, migra com a imagem antiga no ar e confere a saúde.
- O vigia (`scripts/vigia.sh`, a cada 2 min) dispara um alerta quando um contêiner do gateway roda uma imagem que não saiu do `implantar.sh`.
- Nada de `docker image prune`, `builder prune` nem `system prune` sem a confirmação do Marco.
- Os testes rodam só nos bancos `_test`, por `sh scripts/test_suite.sh` ou `sh scripts/suite_travada.sh`. Nunca no banco de produção (`gateway`).

## Dados pessoais e segredos

- **Nunca escrever nome, telefone ou qualquer dado de cliente em docs, PROGRESS.md, testes, fixtures ou mensagens de commit.** Use "cliente real do By Koji em 16/10" e números fictícios (`55119000000NN`).
- Cópias integrais com dados reais, quando forem indispensáveis, ficam fora do git, em `/home/twadmin/backups/gateway/docs-fora-do-git/` (permissão 600).
- Nenhum segredo (token, chave, senha, URL de webhook com `?s=`) no repositório, no chat ou na linha de comando. Segredos ficam só no `.env` da VPS. Credenciais de canal entram só por `scripts/canal_credencial.py`.

## Trabalho

- Mais de uma sessão mexe neste repositório. Confira `git status` e `git log` antes de escrever e não faça commit de arquivos de outra sessão.
- Migrações são aditivas e numeradas a partir da última em `app/migrations`.
- Planos e regras completas: `PLANO_CURSOR.md` e `GUIA_CURSOR.md`. Andamento: `PROGRESS.md`.
