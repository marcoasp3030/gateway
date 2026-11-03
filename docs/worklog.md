## [2026-11-03 00:09] — Link da loja (migração 062) na VPS
- **Autor:** Auto
- **Objetivo:** Publicar `ff865b6` (link da loja + finalização WhatsApp).
- **Arquivos alterados:** pull `ff865b6`; migração `062_cardapio_loja.sql` aplicada.
- **Resumo das mudanças:** `implantar.sh link-loja-20261103`: suíte 423 OK, backup, migração 062, `gateway:ff865b6` saudável. Volta: `gateway:8f5208a`.
- **Comandos executados:** implantar → OK; health 200; `max(versao)=62`.
- **Pendências / TODO:** —
- **Próximos passos:** validar card Link da loja no painel.

## [2026-11-02 22:50] — Implantação cardápio/taxas/isolamento na VPS
- **Autor:** Auto
- **Objetivo:** Atualizar o gateway da VPS com o `main` do GitHub de forma segura.
- **Arquivos alterados:**
 - `app/admin/operacao.py` — `public.unaccent` (commit `8f5208a`)
 - `.env` — `PAINEL_PUBLIC_URL=https://painel.luxbrain.com.br` (local)
- **Resumo das mudanças:** `implantar.sh cardapio-publico-20261102`: suíte 415 passed, backup, migrações 056–061, serviços em `gateway:8f5208a` saudáveis. Volta: `gateway:0cc55d5`.
- **Decisões e justificativas:** A suíte travou 1 teste de busca (503) porque `unaccent` não estava no search_path do schema de teste.
- **Comandos executados:** implantar → OK; health `/gateway/health` → 200; `max(versao)=61`.
- **Erros e resoluções:** `unaccent` → `public.unaccent`.
- **Pendências / TODO:** avaliar `WORKERS=3` e `CONCORRENCIA_POR_WORKER=100` no `.env` da VPS.
- **Próximos passos:** acompanhar vigia 24 h.

## [2026-11-02] — Taxa de entrega por bairro e CEP (G4-b)

- Migração 061: bairro e CEP no carrinho e no pedido, `taxa_origem` e `frete_gratis`.
- `modulos/entrega.py`: `calcular_taxa`, função pura com o contrato da ESPEC_GESTAO 11.4 e 11.11. Carrinho, fechamento, cardápio público e `consultar_entrega` usam a mesma regra.
- Recusas `fora_area`, `bairro_nao_reconhecido`, `falta_cep` e `cep_invalido`. Área conferida em `definir_entrega` e de novo no fechamento.
- O bairro e o CEP são procurados no texto do endereço quando não vêm separados. Unidade sem modo por área segue igual: o retrato do By Koji mudou só em quatro campos novos.
- Skill nova `consultar_entrega`; rota pública `/api/v1/publico/cardapio/taxa`.
- **Atenção local:** salvar o código antes de aplicar a migração faz o gateway recusar o schema e parar. Na implantação, `implantar.sh` migra antes da troca.

## [2026-11-02] — Carga com 55 empresas e novos padrões

- Teste na pilha isolada: nenhuma perda ou duplicidade; a empresa grande não atrasou as outras. Resultados em `docs/CAPACIDADE.md`.
- Novos padrões: recepção com 1 CPU e 256 MB, `WORKERS=3`, `CONCORRENCIA_POR_WORKER=100`.
- `scripts/carga_fila.py`: até 100 empresas e 5.000 conversas, modelo próprio e opção `--dominante`.
- **Na publicação:** o `.env` da VPS define `WORKERS` e `CONCORRENCIA_POR_WORKER`; ajustar lá depois de conferir a folga da máquina.

## [2026-11-02] — Limites separados por empresa

- `limites.cotas`: webhook por IP e canal (30/s) e API por IP e chave (10/s), com teto por IP (300/s e 200/s). Provedor e Skyone falam por um IP com várias empresas, e o limite só por IP deixava uma recusar a outra. Sem chave, segue por IP (10/s), agora com o IP do cliente informado pelo painel.
- `tests/test_isolamento_empresas.py`: sessão e chave de uma empresa atacam todas as rotas com ids reais de outra; nada pode vazar nem mudar.

# Worklog — gateway

## [2026-11-01] — Cardápio interativo e aparência por empresa
- **Resumo das mudanças:** Migrações 059 (link do cardápio) e 060 (aparência). Rotas públicas do cardápio, busca de CEP e entrega de imagem; rotas do painel para link, prévia e aparência.
- **Decisões:** Imagem fica no banco, com tipo vindo dos bytes e sem SVG. O gateway não decodifica imagem: quem reduz e converte é o navegador de quem envia. As rotas públicas devolvem o motivo por `LinkRecusado`, porque o envelope padrão o descarta. Cache longo da imagem fica na rota do painel; o gateway mantém o no-store geral.
- **Publicar:** `PAINEL_PUBLIC_URL` no `.env` e migrações antes do painel.

## [2026-11-01] — Próximos passos na lista de pedidos
- **Autor:** Claude Code
- **Arquivos alterados:** `app/modulos/pedidos/rotas.py`
- **Resumo das mudanças:** `resumo_painel` passa a trazer `proximos_status`, para o cartão do quadro avançar o pedido sem abrir o detalhe.
- **Pendências / TODO:** implantar antes do painel.

## [2026-11-01] — Etiquetas, busca na conversa, operação e envio de arquivo
- **Autor:** Claude Code
- **Arquivos alterados:** `app/admin/etiquetas.py`, `app/admin/operacao.py`, `app/admin/gestao.py`, `app/admin/contatos.py`, `app/canais/uazapi.py`, `app/canais/rotas.py`, `app/limites.py`, `app/nucleo/permissoes.py`, migração 058, testes
- **Resumo das mudanças:** Etiquetas por empresa; busca em mensagens e notas; `mensagens.autor` e `conversa_esperas` para os tempos; `GET /operacao`; `POST /conversas/{conv}/midia` com `uazapi.enviar_midia` (`/send/media`, formato da documentação). Arquivo conferido pelos bytes e não guardado. Retrato do By Koji ganhou só `etiquetas`.
- **Pendências / TODO:** validar `/send/media` com instância real antes de produção.

## [2026-11-01] — Respostas rápidas, responsável da conversa e notas internas
- **Autor:** Claude Code
- **Arquivos alterados:** `app/admin/respostas_rapidas.py`, `app/admin/notas.py`, `app/admin/gestao.py`, `app/admin/contatos.py`, `app/nucleo/atendimentos.py`, `app/nucleo/permissoes.py`, migrações 056 e 057, testes
- **Resumo das mudanças:** Respostas rápidas por empresa (equipe) e por pessoa. Responsável da conversa com conflito dentro da trava; transferir a partir de supervisor. Notas em tabela própria, fora de `mensagens`, na exportação e apagadas na anonimização. Retrato do By Koji ganhou só os 3 campos novos da lista de conversas.
- **Pendências / TODO:** implantar com `sh scripts/implantar.sh` antes do painel.

## [2026-11-01 01:18] — Push completo do gateway para o GitHub
- **Autor:** Auto
- **Objetivo:** Subir o código completo e atualizado do gateway (última atualização) para o GitHub.
- **Arquivos alterados:**
 - `docs/worklog.md` — criado — registro desta publicação
 - `instrucoes.md` — criado/atualizado — fase push GitHub
- **Resumo das mudanças:** Working tree limpa; `main` local 205 commits à frente de `origin/main` (HEAD `0cc55d5` TEL1). Envio de todos os commits pendentes para `marcoasp3030/gateway`.
- **Decisões e justificativas:** Sem rebase/force; push fast-forward do histórico local já validado na VPS.
- **Comandos executados:** `git push … HEAD:main` → OK (`e6f9517..a1ea6b7`)
- **Erros e resoluções:** —
- **Pendências / TODO:** —
- **Próximos passos:** Na máquina local: `git pull origin main` no clone do gateway.
