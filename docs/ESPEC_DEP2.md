# DEP2 — atualização de FastAPI/Starlette no gateway e de pdfplumber/pdfminer/Pillow no leitor

**Situação: proposta para revisão do Marco, 26/10/2026. Nada implementado.**
- **Base:** a verificação de segurança de 26/10, com pip-audit nas imagens em produção.
- **Já feito:** as atualizações pequenas (python-multipart 0.0.31, cryptography 46.0.7, pip 26.2.2, `cap_drop: ALL`, `.dockerignore`) entraram no bloco de 26/10.

## 1. O que falta e por quê

| Imagem | Pacote | Hoje | Alvo | Advisories abertos | Exposição hoje |
|---|---|---|---|---|---|
| gateway | starlette (via fastapi) | 0.41.3 | ≥ 1.3.1 | 7 (negação de serviço com formulário grande; limites de formulário só no multipart; URL montada pelo Host; outros que não se aplicam) | Média: a rota pública `POST /api/v1/documentos` lê formulário |
| gateway | fastapi | 0.115.6 | a versão atual que aceita starlette ≥ 1.3.1 | — (é o que trava a starlette em < 0.42) | — |
| leitor | pdfminer.six (via pdfplumber) | 20231228 | ≥ 20251230 | 2 (pickle carregado por um PDF; escalada) | Média-baixa: precisa de um arquivo malicioso já no disco, e o leitor é só leitura, sem rede e sem segredo |
| leitor | pdfplumber | 0.11.4 | a versão que aceita pdfminer.six ≥ 20251230 | — (é o que trava o pdfminer) | — |
| leitor | Pillow (via pdfplumber) | 11.0.0 | 12.3.x ou 13.x | 50 (decodificadores de imagem) | Baixa hoje: o leitor não decodifica imagem nem renderiza página. Sobe com o OCR (ARQ1-a2) |

## 2. Gateway: FastAPI e Starlette

### 2.1 Riscos de quebrar

- **Starlette 1.x:** saíram APIs antigas.
  - Hoje usamos `@app.middleware('http')`, middleware ASGI próprio (`RegistroDeSkills`, `LimiteCorpo`), `request.form(max_files, max_fields)`, `JSONResponse` e `Response`, `exception_handler`, `TestClient` e `ASGITransport` nos testes, e `request.state` dentro do `scope`.
  - O que mudou na 1.x precisa ser conferido um a um: ordem e assinatura dos middlewares, `on_startup`/`lifespan` (já usamos `lifespan`), parâmetros de `request.form`, e o comportamento de `request.url` com o Host validado.
- **FastAPI recente:**
  - mudanças na resolução de dependências com `Depends` em `dependencies=[...]` (usamos muito);
  - mensagens de validação: o `RequestValidationError` é convertido em 400 com `campo_validacao`, então o formato de `errors()` precisa continuar igual;
  - `response_model` e serialização de `datetime`.
- **Pydantic:** o FastAPI novo pode exigir pydantic mais novo que o 2.10.4. Fixar a versão compatível; os modelos `Estrito` (`strict=True`, `extra='forbid'`) precisam continuar recusando o mesmo.
- **httpx `ASGITransport`:** a suíte depende dele; conferir a versão.

### 2.2 Como fazer

1. Branch: subir fastapi para a versão atual que aceita starlette ≥ 1.3.1, com as versões compatíveis de pydantic e uvicorn fixadas.
2. Suíte completa na imagem nova. Ela precisa rodar numa **imagem reconstruída**: hoje a trava usa a imagem em uso mais o `requirements` por cima, o que não vale para pacote compilado nem para trocar o framework. Proposta: o `test_suite.sh` passa a construir `gateway:teste` a partir de `app/` e rodar a suíte nela, como já faz com o leitor.
3. **Retrato do By Koji idêntico:** se nada mudou no contrato, o `test_multi1b_retrato` passa sem regravar.
4. **Contratos de erro:** os testes que comparam `erro.codigo` e `detalhes.campo` (400, 404, 409, 413, 422→400) continuam iguais.
5. **Carga:** `scripts/bench.py` no `bench_passo5` antes e depois (latência p50 e p90 da recepção e de `/admin`), sem regressão maior que 10%.
6. Implantar com a imagem de volta pronta. Fica 24 h em observação (erros 5xx, tempo de turno, avisos do vigia) antes de apagar a volta.

## 3. Leitor: pdfplumber, pdfminer.six e Pillow

### 3.1 Riscos de quebrar

- **pdfminer.six:** pode mudar a extração de texto (ordem das linhas, espaços, hifenização, ligaduras) e de caracteres de fontes com CMap. Isso afeta as regras do ARQ1-a3, que procuram rótulos no começo da linha ("Beneficiário", "Pagador", "Vencimento").
- **pdfplumber:** pode mudar a detecção de tabelas (bordas e células) e o `extract_tables`, o que afeta itens de NF e tabelas na Skill.
- **Pillow 12 ou 13:** sem uso direto hoje. Pode mudar quando o pdfplumber renderiza algo, e para o OCR futuro.
- **Tamanho da imagem e memória:** o leitor tem limite de 512 MB e de memória por documento (`RLIMIT_AS`); conferir que o pico não sobe.

### 3.2 Como medir que a leitura não piorou

1. **Corpus fixo:** `backups/gateway/corpus-arq1/` (fora do git), mais os PDFs sintéticos dos testes (boleto, concessionária, NF-e, NFS-e, comprovante PIX, BR Code, PDF com tabela, PDF com conteúdo ativo, protegido, escaneado).
2. **Antes:** rodar `scripts/medir_documentos.py` na imagem atual do leitor e guardar o resultado por documento: tipo, cada campo e a confiança, avisos, número de páginas, caracteres por página, tabelas (linhas e colunas), tempo e memória.
3. **Depois:** o mesmo na imagem nova.
4. **Comparação automática:**
   - `tipo_documento` igual em 100% dos documentos;
   - campos `validado` iguais em 100% (valor e confiança). Linha digitável, código de barras, chave da NF-e, CNPJ, valor e vencimento não podem mudar;
   - campos `lido_do_texto` e `a_conferir`: listar as diferenças para revisão humana (aceitável quando só muda espaço ou quebra de linha);
   - tabelas: mesmo número de linhas e colunas; células diferentes vão para revisão;
   - texto por página: diferença normalizada (sem espaço extra) menor que 1% dos caracteres;
   - tempo médio e pico de memória: no máximo 20% acima.
5. **Recusas iguais:** PDF com conteúdo ativo, protegido, inválido, com páginas demais e XML que não é NF-e continuam recusados com o mesmo `motivo`.
6. **Testes:** `leitor/tests` e `tests/test_arq1a*.py` passam.
7. **Teste real:** repetir os cenários A a F de 23/10 no Teste Documentos (bench_passo5), com o fluxo do Skyone.

## 4. Ordem proposta

1. **Leitor primeiro:** é isolado, a mudança é menor e dá para medir bem com o corpus.
2. **Gateway depois:** a mudança de framework é maior. Inclui a mudança no `test_suite.sh` para testar na imagem reconstruída.

## 5. Decisões em aberto

| # | Decisão | Proposta |
|---|---|---|
| 1 | Pillow 12.3 (menos mudança) ou 13 (fecha todos os advisories) | 13, porque o OCR vai usar Pillow de verdade |
| 2 | A suíte passa a rodar numa imagem reconstruída (`gateway:teste`) | Sim: evita testar uma imagem diferente da que vai para produção |
| 3 | Tolerância na comparação do corpus | A da seção 3.2; revisão humana do que sair dela |

**Ponto de parada:** só especificação.
