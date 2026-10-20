# Regras obrigatórias (resumo; a versão completa está em AGENTS.md)

1. **Nunca rodar `docker compose up`, `build`, `restart`, `run` ou `down` em produção à mão.** A única forma de implantar é `sh scripts/implantar.sh <rotulo>`, com tudo num commit e a suíte passando na trava. Imagem que não sai do `implantar.sh` dispara alerta do vigia.
2. **Nunca escrever nome, telefone ou dado de cliente em docs, PROGRESS.md, testes, fixtures ou commits.** Use dados fictícios.
3. Nenhum segredo no repositório, no chat ou na linha de comando. Nada de prune sem a confirmação do Marco. Testes só nos bancos `_test`.
4. Outras sessões escrevem aqui: confira `git status` e `git log` antes de escrever e não faça commit de arquivos alheios.

Leia `AGENTS.md`, `PLANO_CURSOR.md` e `GUIA_CURSOR.md` antes de alterar qualquer coisa.
