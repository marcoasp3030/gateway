# NFSE1 (leitura da NFS-e nacional) e LOTE1 (lote e conciliação de pagamentos)

**Situação: proposta para revisão do Marco, 26/10/2026. Nada implementado.**
- **Para quem:** o agente "Conciliação de Notas Fiscais de Frete" (empresa luxtia, `ag_bb770f5dda22c7a4b53b1587`, modelo `assistente_documentos` v1).
- **Volume:** até 200 notas por lote, em PDF, XML ou os dois.
- **Ordem:** NFSE1 antes do LOTE1. A conciliação só pode dar "APROVADO" com dado da nota que o gateway consegue verificar.

## Fontes oficiais conferidas (26/10/2026)

| Fonte | Versão | O que confirma |
|---|---|---|
| Esquemas XSD da NFS-e nacional (gov.br/nfse, "Documentação atual") | v1.01, 09/02/2026 | namespace, nomes dos campos, formato da chave e do Id |
| ANEXO_I-SEFIN_ADN-DPS_NFSe (leiaute e regras de negócio) | v1.01, 09/02/2026 | composição do identificador; "verificar DV" da chave, do CNPJ e do CPF |
| Guia do Emissor Público Nacional Web | v12 | campos da chave na consulta pública |

**Namespace:** `http://www.sped.fazenda.gov.br/nfse`.

**Identificador da NFS-e** (`infNFSe/@Id`, tipo `TSIdNFSe`): 53 posições, `NFS[0-9]{50}`. A chave são os 50 dígitos depois de "NFS" (tipo `TSChaveNFSe`, `[0-9]{50}`). Texto oficial do XSD:

> "NFS" + Cód.Mun.(7) + Amb.Ger.(1) + Tipo de Inscrição Federal(1) + Inscrição Federal(14) + No.NFS-e(13) + AnoMes Emis.(4) + Cód.Num.(9) + DV(1)

| Parte | Posições | Conferência possível |
|---|---|---|
| Código do município emissor (IBGE) | 1–7 | existe no ANEXO_A (municípios IBGE) e é igual a `cLocEmi` do XML |
| Ambiente gerador | 8 | 1 ou 2 |
| Tipo de inscrição federal | 9 | 1 = CPF, 2 = CNPJ |
| Inscrição federal (CPF com 000 à esquerda) | 10–23 | DV do CNPJ/CPF e igual ao emitente do XML |
| Número da NFS-e | 24–36 | igual a `nNFSe` |
| Ano/mês de emissão (AAMM) | 37–40 | igual ao mês de emissão no XML |
| Código numérico aleatório | 41–49 | — |
| DV | 50 | **regra não publicada** (ver abaixo) |

**O DV da chave não tem algoritmo publicado nos documentos oficiais.**
- O Anexo I só manda "verificar DV da chave de NFS-e".
- O Guia do Emissor diz "Dígito verificador do código numérico", sem a conta.
- Fontes não oficiais citam o módulo 11, como na NF-e, mas isso não foi confirmado em documento oficial.
- **Proposta:** até a regra ser confirmada, a chave é `validado` por **consistência**: as conferências das partes acima batem com o XML e o CNPJ/CPF tem DV válido. O DV em si fica como aviso informativo (`dv_da_chave_nao_conferido`).
- **Confirmação:** testar a hipótese do módulo 11 contra NFS-e reais da luxtia (o Marco envia uma amostra, sem dado pessoal no repositório). Se 100% baterem, o DV passa a valer.

**CNPJ alfanumérico:** no XSD v1.01 o CNPJ ainda é `[0-9]{14}`. A nota técnica da RTC anuncia a mudança para alfanumérico a partir de 07/2026. O gateway já valida CNPJ alfanumérico (ARQ1-a3), então a regra fica pronta para as duas formas.

## NFSE1 — leitura da NFS-e

### 1. XML da NFS-e nacional

- **Aceitar:**
  - o reconhecimento do formato (`formato_real`) passa a aceitar também o namespace `sped.fazenda.gov.br/nfse`;
  - o leitor ganha `ler_xml_nfse`, com o mesmo `defusedxml` (sem entidade externa, sem DTD).
- **Recusar:** XML só da DPS, sem a NFS-e emitida (raiz `DPS`): 400 `xml_dps_nao_e_nota`. A DPS é a declaração do prestador, não a nota.
- **Campos** (caminhos do XSD v1.01; `infDPS` fica dentro de `infNFSe/DPS`):

| Campo no resultado | Origem no XML | Confiança |
|---|---|---|
| `chave_acesso` | `infNFSe/@Id` sem "NFS" | `validado` se as conferências da chave baterem (DV: ver acima); senão `a_conferir` + aviso |
| `numero` | `infNFSe/nNFSe` | `validado` se for igual à posição 24–36 da chave |
| `emissao` | `infDPS/dhEmi` (e `dhProc`, processamento) | `validado` se o AAMM bater com a chave; senão `lido_do_texto` |
| `competencia` | `infDPS/dCompet` | `lido_do_texto` |
| `prestador_documento` | `emit/CNPJ` ou `emit/CPF` | `validado` com o DV certo **e** igual ao da chave |
| `prestador_nome` | `emit/xNome` | `lido_do_texto` |
| `tomador_documento`, `tomador_nome` | `infDPS/toma/CNPJ`/`CPF`, `xNome` | documento `validado` (DV); nome `lido_do_texto` |
| `municipio_emissor`, `municipio_prestacao`, `municipio_incidencia` | `cLocEmi`/`xLocEmi`, `cLocPrestacao`, `cLocIncid`/`xLocIncid` | código `validado` se existir no ANEXO_A; nome `lido_do_texto` |
| `servico_codigo` | `cServ/cTribNac`, `cTribMun`, `cNBS`; descrição `xDescServ` | `cTribNac` `validado` se estiver na lista nacional (ANEXO_B); demais `lido_do_texto` |
| `valor_servico` | `valores/vServPrest/vServ` | ver regra de valores |
| `valor_liquido` | `infNFSe/valores/vLiq` | ver regra de valores |
| `retencoes` | ISS: `vISSQN` com `tpRetISSQN` (1 não retido, 2 pelo tomador, 3 pelo intermediário); IR: `vRetIRRF`; INSS: `vRetCP`; PIS e COFINS: `vPis` e `vCofins` com `tpRetPisCofins` (códigos 0–9 do XSD); CSLL: `vRetCSLL`; total: `vTotalRet` | ver regra de valores |

- **Regra de valores:** `valor_servico`, `valor_liquido` e cada retenção ficam `validado` quando **todas** estas conferências passam:
  1. as contas fecham: `vLiq` = `vServ` − descontos incondicionados − `vTotalRet` (tolerância de R$ 0,01 só por arredondamento);
  2. `vTotalRet` = a soma das retenções marcadas como retidas pelos códigos `tp*`;
  3. a chave está consistente.

  Se qualquer conferência falhar, todos os valores ficam `a_conferir`, com o aviso que diz qual falhou.
- **Limite honesto:** sem conferir a assinatura digital do XML, um XML editado com contas coerentes passaria. Ver a decisão 1.

### 2. PDF da NFS-e (DANFSe)

- **Novo:** extrair o código de verificação e a chave de acesso (50 dígitos, com ou sem espaços) quando aparecerem.
  - Chave encontrada e consistente com o número e o CNPJ do prestador lidos no PDF: `chave_acesso` vira `validado` (pela mesma regra de consistência).
  - Código de verificação: `lido_do_texto`.
- **O resto continua `a_conferir`:** número, valores, retenções e datas. É o PDF que vale a pena juntar com o XML (LOTE1).

### 3. Tamanho e testes

- **Tamanho:** M.
- **Partes:**
  - reconhecer o formato e ler o XML no leitor (com testes no próprio leitor);
  - regras e confiança em `documentos_regras`;
  - código de verificação e chave no PDF;
  - a lista de municípios (ANEXO_A) e a de serviços (ANEXO_B), embutidas e versionadas.
- **Testes:**
  - XML sintético conforme o XSD v1.01 (válido, contas que não fecham, CNPJ com DV errado, chave inconsistente, DPS sem nota, entidade externa);
  - PDF com e sem chave;
  - medição no corpus (`medir_documentos.py`).
- **Com a decisão 1 (assinatura):** soma M. O leitor ganha `lxml` + `xmlsec`, bibliotecas compiladas, e a imagem cresce cerca de 15 MB.

## LOTE1 — lote e conciliação

### 1. Fluxo

1. O painel (ou o n8n, pela API) **cria o lote** no agente.
2. Envia os **arquivos um a um**: PDF, XML, ZIP com PDFs/XMLs, e a **planilha de pagamentos** (CSV ou XLSX). Cada envio passa pelos mesmos limites e pelo mesmo leitor do ARQ1 e entra na fila do lote.
3. Na primeira vez, o painel mostra as colunas da planilha e o usuário **mapeia**. O mapeamento fica salvo no agente.
4. **Fechar o lote:** o gateway termina de ler, junta PDF e XML de cada nota, **concilia em código** e emite `lote.concluido`.
5. **Um turno do agente** recebe o resumo (totais e divergências) e responde. O agente consulta uma nota específica pela Skill.

### 2. Dados (migração nova)

- **`lotes`:** `id` (`lot_…`), empresa, agente, estado (`aberto`, `lendo`, `conciliando`, `concluido`, `falhou`, `expirado`, `apagado`), contadores, mapeamento usado, `criado_por`, `criado_em`, `fechado_em`, `concluido_em`, `expira_em`.
- **`lote_arquivos`:** um por arquivo recebido, com `anexo_id` (o arquivo cifrado, como no ARQ1, `origem = lote`), o papel (`nota` ou `planilha`), o estado (`na_fila`, `lido`, `recusado`) e o motivo. O arquivo vindo de um ZIP guarda o ZIP de origem.
- **`lote_notas`:** uma por nota após juntar PDF e XML, com `chave` (ou número + CNPJ), `xml_anexo_id`, `pdf_anexo_id`, os campos com a confiança, `resultado`, `motivo`, `linha_planilha`, `diferenca` e `observacoes`.
- **`agente_mapeamentos_planilha`:** colunas mapeadas por agente e o valor de status que conta como pago (padrão "Efetuado").
- **A planilha:** fica cifrada como anexo, porque tem nome e CPF/CNPJ de favorecidos. As linhas lidas ficam só na memória da conciliação e no resultado, mascaradas.

### 3. Rotas (painel; a API com chave da empresa espelha em `/api/v1/lotes` para o n8n)

| Rota | Faz | Papel |
|---|---|---|
| `POST /admin/v1/tenants/{t}/agentes/{a}/lotes` | Cria o lote → 201 `{"lote_id","estado":"aberto","limites"}` | admin, membro |
| `POST …/lotes/{l}/arquivos` (multipart, 1 arquivo) | PDF, XML, ZIP, CSV ou XLSX → 202 `{"arquivos":[{"arquivo_id","nome","papel","estado":"na_fila"}]}`. O ZIP devolve um item por arquivo de dentro. Recusa com o motivo (`tipo_nao_aceito`, `xml_nao_e_nfe`, `zip_*`, `limite_do_lote`) | admin, membro |
| `GET …/lotes/{l}` | Progresso: estado, `arquivos{total,lidos,recusados,na_fila}`, `notas`, `estimativa_s`, `resumo` (quando concluído) | admin, membro |
| `GET …/lotes/{l}/arquivos?estado=&cursor=` | Arquivos com o estado e o motivo | admin, membro |
| `POST …/lotes/{l}/planilha/previa` | Cabeçalhos detectados, 5 linhas de exemplo (mascaradas) e a sugestão de mapeamento | admin, membro |
| `PUT /admin/v1/tenants/{t}/agentes/{a}/mapeamento-planilha` | `{"favorecido","documento","valor","status","data","referencia","status_pago":"Efetuado"}` (nomes das colunas) | admin |
| `POST …/lotes/{l}/fechar` | 202 → `lendo`/`conciliando`. 409 se não houver nota, planilha ou mapeamento | admin, membro |
| `GET …/lotes/{l}/notas?resultado=&cursor=` | Resultado por nota | admin, membro |
| `GET …/lotes/{l}/resultado.xlsx` | Exportação (CPF mascarado). Auditado | admin, plataforma |
| `DELETE …/lotes/{l}` | Apaga arquivos e resultados (LGPD). Auditado | admin |
| Skill `GET /api/v1/lotes/{l}/notas/{ref}?conversation_id=` | O agente consulta uma nota do lote (`ref` = chave ou número) | chave `ferramentas:documentos` (ou o passe do FERR1) |

**Webhook:** `lote.concluido` com `{"lote_id","agente_id","totais":{"aprovado","reprovado","conferir"},"concluido_em"}`, sem nomes nem valores.

### 4. Limites

| Limite | Proposta | Por quê |
|---|---|---|
| Arquivo | o do ARQ1: 10 MiB por padrão, teto de 15 MiB; um por requisição | O limite do proxy não muda. O envio passa pelo servidor do painel, um arquivo por vez |
| Notas por lote | 200 (arquivos de nota; PDF e XML da mesma nota contam como uma depois de juntar) | O pedido |
| Arquivos por lote | 400 (até PDF + XML de cada uma) + 1 planilha | — |
| Tamanho total do lote | **200 MiB** | 400 arquivos de 0,5 MB médio, com folga; disco cifrado no volume `arquivos` |
| ZIP | até 10 MiB compactado (mesmo limite de um arquivo), até 200 entradas, até 100 MiB descompactado e taxa de compressão até 50×. Sem pastas aninhadas, sem `..`, caminho absoluto ou link simbólico, só `.pdf`/`.xml`. Entrada fora disso: recusada e listada | Protege contra "zip bomb" e contra escrita fora do lugar. O ZIP é aberto no leitor (isolado), não no gateway |
| Planilha | CSV (UTF-8 ou Latin-1, `;` ou `,`) ou XLSX, até 5.000 linhas e 5 MiB | Os XLSX são lidos sem macro (só os valores) |
| Retenção | a da capacidade de documentos do agente (30 dias); depois o lote expira e tudo é apagado | Igual ao ARQ1 |

### 5. Fila e prioridade

- **Sem leitura em paralelo:** hoje cada documento dispara uma leitura na hora. Um lote de 200 de uma vez seria 200 leituras ao mesmo tempo num leitor de 512 MB. No lote, a leitura é **sequencial**.
- **Um leitor de lote por vez:** trava no Redis (`gw05:lote:leitor`) e retomada por lease, como os anexos (`processando_ate`), de modo que a queda do worker não perde nada.
- **Prioridade menor:** o laço do lote roda no agendador, e antes de cada arquivo confere se há documento de **atendimento** em leitura ou esperando. Se houver, espera. O turno dos agentes nunca espera o lote.
- **Estimativa:** média móvel do tempo dos últimos arquivos × arquivos que faltam, mais a conciliação (menos de 5 s para 200 notas).

**Medição de 26/10:**
- **Ambiente:** imagem do leitor em produção, contêiner descartável com 512 MB e 1 CPU, sem rede; 200 NFS-e fictícias em PDF, de 1 página com tabela.
  - Total: **74 s**, cerca de 0,36 s por arquivo (p90 0,41 s; máximo 0,56 s).
  - Pico de memória da leitura: **34 MB**.
- **PDF real** (logo, QR code, mais texto): estimativa de 1 a 2 s por arquivo, o que dá **4 a 7 min** para 200 PDFs. XML: menos de 0,1 s por arquivo.
- **Medição de aceite do LOTE1:** repetir com 200 NFS-e reais (ou o corpus) antes de ligar para a luxtia.
- **Alerta da VPS:** na medição havia só cerca de 530 MB disponíveis e 2,2 GB de swap em uso. A leitura em si é leve, mas a VPS já está sem folga, e a conversa sobre aumentar a VPS (a mesma do OCR) vale aqui também.

### 6. Juntar PDF e XML

- **Chave de junção:** a chave de acesso. Sem chave no PDF, junta por número + CNPJ do prestador (+ município emissor quando houver).
- **O XML é a fonte.** O PDF complementa (por exemplo, o código de verificação). Divergência entre os dois (valor, número, CNPJ) vira aviso `pdf_diverge_do_xml`, e vale o XML.
- **Só PDF:** a nota segue com os campos `a_conferir`, e o resultado só pode ser CONFERIR ou REPROVADO.
- **Duplicatas:** a mesma chave enviada duas vezes vira aviso `nota_duplicada` e é conciliada uma vez só.

### 7. Planilha e mapeamento

- **Primeira vez no agente:** `planilha/previa` mostra os cabeçalhos e sugere o mapeamento por nome ("favorecido", "beneficiário", "cpf/cnpj", "valor", "situação"/"status", "data", "referência"/"histórico"). O usuário confirma, e fica salvo no agente.
- **Nos lotes seguintes:** o mapeamento salvo é usado. Se faltar alguma coluna mapeada: 409 `mapeamento_nao_confere`, e a tela pede de novo.
- **Leitura das colunas:**
  - **valor:** lido em centavos ("1.234,56", "1234.56", "R$ 1.234,56");
  - **data:** dd/mm/aaaa ou aaaa-mm-dd;
  - **documento:** CNPJ com DV válido conta como "comparável"; CPF ou documento mascarado ("***.456.789-**") **não** são comparados.

### 8. Conciliação (determinística, em código)

Regras do prompt v3.3, como o Marco descreveu. O texto exato do v3.3 não está no gateway; peço o trecho para conferir os nomes dos status e as exceções.

Para cada nota, as **linhas candidatas** são as da planilha com:
1. o status igual ao "pago" mapeado ("Efetuado");
2. o **valor idêntico ao líquido** da nota, em centavos, com tolerância zero;
3. a **identidade do favorecido**:
   - **CNPJ:** se a linha tem CNPJ com DV válido, ele precisa ser igual ao do prestador;
   - **sem documento comparável** (CPF, mascarado ou vazio): o nome sem acento, em minúsculas, sem "ltda/me/eireli/sa/epp" e pontuação, comparado por tokens; os tokens do nome mais curto precisam estar todos no outro;
   - **sobrenome:** o fallback por sobrenome só vale quando existe documento comparável que não contradiz.

**Preferência e descarte:**
- linha cuja referência cita o número desta NF: preferida;
- linha cuja referência cita **outra** NF: descartada para esta.

**Uma linha nunca aprova duas notas.**
- A atribuição vai das notas com menos candidatas para as com mais.
- Empate de uma linha entre notas: decide a referência. Sem referência que desempate, as notas em disputa vão para CONFERIR.

**Resultado:**

| Resultado | Quando |
|---|---|
| **APROVADO** | Prestador, número e valor líquido da nota `validado`, e exatamente uma linha atribuída |
| **REPROVADO COM DIVERGÊNCIA** | Nenhuma linha compatível. Motivos: `sem_pagamento`, `valor_diferente` (com a diferença), `status_nao_pago`, `favorecido_diferente` |
| **CONFERIR** | Dado da nota necessário não `validado` (ex.: só PDF), linha compatível com mais de uma nota, ou documento da planilha não comparável e nome ambíguo |

**Observações** (não mudam o resultado):
- `valor_igual_ao_bruto`: a planilha tem o valor do serviço, não o líquido, e a nota tem retenção. Nesse caso o resultado é REPROVADO, com essa observação explicando;
- `pagamento_antes_da_emissao`;
- `mais_de_uma_linha_compativel` (indica qual foi usada e por quê);
- avisos da leitura.

**Saída por nota:** resultado, motivo, a linha da planilha usada (número da linha e os campos, com CPF mascarado), a diferença em centavos, as observações e os avisos.

**Exportação XLSX:** uma aba de resultado e uma de resumo.
- Gerada com `openpyxl` (Python puro, cerca de 4 MB) na imagem do gateway; ver a decisão 4.
- Sem fórmula: os campos de texto que começam com `=`, `+`, `-` ou `@` recebem `'` na frente, contra injeção de fórmula.

### 9. Turno do agente e Skill

- **Ao concluir:** uma mensagem de sistema na conversa do lote, com `tarefa: "notificar"` e o `evento` `lote.concluido` (totais e até 20 divergências: número da nota, prestador, motivo e diferença; CPF mascarado). Isso gera **um** turno do agente, que escreve o resumo.
  - O Normalizar v11 precisa ler `tarefa` e `evento`, que hoje não lê.
- **Skill `consultar_nota_do_lote`:** o agente pede uma nota por chave ou número e recebe o resultado completo dela. Usa o escopo `ferramentas:documentos` (ou o passe do FERR1).

### 10. LGPD

- Arquivos e planilha cifrados (ARQ1). Retenção de 30 dias; depois tudo é apagado.
- `DELETE` do lote apaga na hora; a anonimização do contato não se aplica (o lote não é de um contato).
- **CPF sempre mascarado** no resultado, na exportação, no evento e no turno. CNPJ aparece inteiro (é de empresa).
- **Exportação e download** só para admin e plataforma, com registro na auditoria. O membro vê o resultado na tela, mascarado.
- Os webhooks não levam nome nem valor.

### 11. Tamanho e ordem

| Bloco | Tamanho | Depende de |
|---|---|---|
| NFSE1 (XML + PDF) | M | — |
| NFSE1 + assinatura do XML (decisão 1) | +M | NFSE1 |
| LOTE1-a: lote, arquivos, ZIP, fila sequencial com prioridade, progresso, junção PDF+XML | G | NFSE1 |
| LOTE1-b: planilha, mapeamento, conciliação, resultado, XLSX, evento, turno e Skill | G | LOTE1-a |

## O que o [PAINEL] precisa

1. **Clipe com envio múltiplo** no Simular conversa e na tela do lote:
   - selecionar vários arquivos (ou um ZIP);
   - enviar **um por requisição, em sequência**, com barra por arquivo;
   - mostrar recusa com o motivo;
   - não ultrapassar o limite por arquivo.
2. **Tela do lote:** progresso (lidos, na fila, recusados), estimativa de tempo e o botão "Fechar e conciliar" (com a planilha já enviada).
3. **Mapeamento de colunas na primeira vez:** prévia da planilha (5 linhas mascaradas), sugestão automática e confirmação; depois, "Editar mapeamento" na ficha do agente.
4. **Resultado:**
   - tabela por nota (resultado, motivo, prestador, número, líquido, linha usada, diferença, observações);
   - filtros por resultado;
   - totais;
   - "Baixar XLSX" (admin);
   - "Apagar lote" (admin).
5. **Na conversa:** cartão do lote com link para o resultado; o resumo do agente aparece como resposta normal.

## Decisões em aberto

| # | Decisão | Proposta |
|---|---|---|
| 1 | Conferir a assinatura digital do XML (XMLDSig da Sefin Nacional) para os valores serem `validado` de verdade | **Sim**, junto com o NFSE1. Sem ela, os valores ficam `validado` só por consistência, e um XML editado com contas coerentes passaria |
| 2 | DV da chave enquanto a regra oficial não aparece | Consistência + teste da hipótese do módulo 11 com uma amostra real da luxtia |
| 3 | Tamanho total do lote | 200 MiB |
| 4 | `openpyxl` na imagem do gateway para gerar o XLSX | Sim (Python puro). Alternativa: CSV no lugar do XLSX |
| 5 | Onde roda a fila do lote | No agendador, um arquivo por vez, cedendo a vez a documento de atendimento |
| 6 | Texto exato das regras do prompt v3.3 | O Marco envia o trecho para conferir status, exceções e prioridade |
| 7 | API para o n8n além do painel | Sim, espelhada em `/api/v1/lotes` (chave `documentos:enviar`) |

**Ponto de parada:** só especificação.
