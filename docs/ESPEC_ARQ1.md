# ARQ1 — Arquivos no núcleo dos agentes

**Situação: proposta para decisão do Marco, 15/10/2026. Nada implementado.** Entra na fila **depois do MULTI1-b**, sem ativar capacidade em modelos existentes. Arquivos são capacidade transversal do núcleo, não módulo de delivery. Delivery permanece desligado; o By Koji preserva pacote e comportamento anteriores.

Todos os limites, nomes de campos e rotas abaixo são **propostos**, não contratos disponíveis. As decisões em aberto estão na seção 12, cada uma com recomendação.

## 1. Base atual e fronteiras

Fontes locais: `docs/ESPEC_MULTI1.md` (especialmente contrato publicado da seção 10), `docs/SKYONE.md`, `app/canais/uazapi.py`, `app/agentes/skyone.py` e `app/nucleo/pipeline.py`.

- MULTI1-a já define modelo versionado, papéis e `X-Tenant-Scope`; MULTI1-b separa regras de negócio do núcleo. Usar essa arquitetura, sem importar catálogo/pedidos para processar arquivos.
- Hoje `_midia` identifica documentos/imagens, nome/MIME/tamanho informado e produz aviso de leitura indisponível. Há limite indicativo de15MiB; isso **não é um armazenamento nem um leitor de anexos**.
- O envio durável atual é por parte, com estado `enviando/enviada/incerta`; partes incertas não são reenviadas automaticamente. ARQ1 amplia esse mecanismo.
- O adaptador Skyone limita resposta HTTP a2MiB; o contrato atual exige `tipo:"text"`. Manter envelope e limite, acrescentando `arquivos` opcional, sem Base64.
- O adaptador de canal consultado tem texto, menus e reações, mas não download/envio de documento ou imagem. **Não presumir endpoint, autenticação de mídia, limite ou confirmação da uazapi.** Antes da implementação, conferir documentação da versão instalada e fixtures sanitizadas de download, envio de documento/imagem e legenda. Se faltar suporte, registrar impedimento do canal; não publicar rota inventada.
- Fora de ARQ1 inicial: áudio/vídeo, transcrição, execução de código, macros, edição de arquivos recebidos, OCR automático e interpretação visual de fotografias. JPG/PNG podem ser recebidos, visualizados e encaminhados dentro do fluxo permitido; o agente não pode dizer que leu seu conteúdo sem um recurso de visão/OCR contratado.

## 2. Receber, armazenar e apagar

### 2.1 Fluxo e estados

WhatsApp: autenticar webhook → resolver tenant/canal/conversa/agente → conferir capacidade → persistir referência ao anexo → tarefa de aquisição. Responder ao webhook após persistência durável, sem esperar antivírus/extração. Deduplicar por `(tenant, canal, mensagem_provedor, indice_anexo)`. Não confiar no tamanho/MIME/nome informado.

Simulador: navegador → servidor autenticado do painel → upload admin interno do gateway. Destino sempre derivado da conversa/agente autorizado; não aceitar tenant/actor arbitrário, URL para download ou caminho local vindo do navegador. Primeiro recebe arquivo pendente, depois associa à mensagem em transação. Uploads não associados expiram em1h.

Estados públicos: `recebendo`, `processando`, `pronto`, `sem_texto`, `rejeitado`, `falhou`, `expirado`, `apagado`. Internamente separar aquisição, quarentena, verificação, extração e geração. Registrar código de motivo seguro; stack trace, endereço de mídia e credenciais não entram na API.

O lote espera anexos até30s após seu fechamento por debounce, dentro do timeout de preparação; teto configurado da plataforma. Se ainda processando, responder de modo explícito, sem fingir leitura. Quando terminar, não iniciar nova fala espontaneamente: disponibilizar para próxima mensagem/consulta e atualizar painel por evento. Proposta evita dois turnos concorrentes e resposta tardia desconectada. Anexo sozinho produz entrada textual controlada com seus IDs, não conteúdo vazio nem encerramento de conversa vazia.

### 2.2 Armazenamento recomendado

**Primeira versão: volume de disco privado, fora do Git e do diretório público**, montado somente nos serviços necessários, com interface de armazenamento que permita migrar para objeto privado compatível com S3 depois. Metadados/estado no Postgres; bytes nunca em coluna de mensagem nem Redis. Propor estrutura por tenant + UUID opaco, sem usar nome original ou telefone no caminho. IDs não substituem autorização.

Cifra autenticada por objeto, chave de dados aleatória e chave mestra separada das credenciais de agentes; envelope com versão da chave, nonce e vínculo autenticado a tenant/arquivo. Biblioteca mantida e formato a escolher na implementação, sem criptografia caseira. Nunca reutilizar nonce; hashes calculados do conteúdo e guardados privadamente, sem deduplicação entre empresas. Cifrar também texto/tabelas extraídos, miniaturas e derivados. Chave mestra fora do volume, rotação com recifragem de envelopes e procedimento de restauração testado.

Temporários descriptografados só em área privada efêmera com quota, preferencialmente memória, permissões restritas; apagar ao terminar e varrer órfãos após falha. Isso reduz exposição, não promete apagamento físico verificável em SSD. Worker de análise sem credenciais do banco principal, canal ou Skyone: recebe trabalho mínimo e devolve resultado por interface controlada. Credenciais do armazenamento não ficam dentro do parser.

### 2.3 Limites iniciais propostos

| Recurso | Limite inicial |
|---|---|
| Arquivo recebido |10MiB; plataforma pode elevar até teto15MiB após teste do canal|
| Mensagem / lote |até3 anexos por mensagem;30MiB e5 anexos por lote; excesso recusado explicitamente|
| Cota por empresa |1GiB somando originais, extrações e gerados; uploads em andamento reservam espaço atomicamente|
| PDF |100 páginas;10MiB de texto extraído|
| Planilha |10 abas;20.000 linhas por aba;100 colunas;200.000 células totais|
| DOCX / TXT |10MiB de texto extraído; DOCX sem promessa de paginação igual ao Word|
| Imagem |20 megapixels; lado até10.000px; imagem final sanitizada com metadados removidos|
| Expansão de contêiner |100MiB descompactados,2.000 entradas, sem arquivos aninhados arbitrários|
| Extração |30s por arquivo,512MiB de RAM por processo, concorrência inicial1 por worker dedicado|
| Arquivo gerado |até5MiB,100 páginas PDF,5.000 linhas/50 colunas/100.000 células em tabela|
| Gráfico |máximo100 rótulos e5 séries; pizza até12 fatias e1 série; PNG até2000×1200|

Nenhum truncamento silencioso: recusar limites estruturais antes de concluir, ou declarar `parcial:true` quando uma consulta paginada explicitamente retornar parte. Teto de bytes durante leitura em streaming mesmo sem Content-Length; interromper ao ultrapassar. Cota impede novo upload/geração, não apaga material recente automaticamente. Limites ficam abaixo dos suportados pelo canal, após confirmação; o menor vence.

### 2.4 Retenção e LGPD

Proposta: **7 dias** para recebidos, gerados e derivados, máximo30; configuração por empresa só pode encurtar o limite do modelo/plataforma. Prazo a partir da aquisição/geração, sem renovar por download. Quarentena rejeitada e temporários: apagar em até1h; metadados mínimos sem nome/conteúdo podem permanecer30dias para auditoria operacional.

Expiração revoga acesso imediatamente pelo relógio do servidor; remoção física assíncrona em até24h, com fila reconciliável e alerta de atraso. Exclusão LGPD invalida uploads, tarefas, caches, tokens e acessos; remove original/extração/miniatura/gerado relacionado e trechos do histórico/pacote persistidos. Vínculos de proveniência ligam arquivo gerado aos anexos/fontes e contato, para evitar deixar cópias derivadas. Bloquear geração/envio concorrente após exclusão. Arquivo compartilhado com outra conversa não será recurso inicial, para simplificar propriedade e apagamento.

Backups cifrados têm retenção separada proposta de7dias; restauração reaplica lista de exclusões antes de liberar tráfego. Documentar diferença entre indisponibilidade imediata e expurgo do backup. Cópia já enviada ao WhatsApp/Skyone ou baixada não pode ser recolhida pelo gateway: incluir essa limitação no contrato e política de operação. Não afirmar conformidade jurídica automática; retenção por segmento depende da decisão do controlador.

## 3. Arquivo malicioso e isolamento

- Allowlist de extensão **e** assinatura/MIME detectados; divergência rejeitada. Nome sanitizado, até120 caracteres, extensão definida pelo formato real, sem caminhos/caracteres de controle. Nenhum arquivo executável ou ZIP genérico. XLSX/DOCX são contêineres permitidos sob limites próprios.
- Antivírus e validação estrutural antes de disponibilizar bytes ou parser. Serviço de verificação indisponível → permanece em quarentena, nunca liberado por timeout. Antivírus não é garantia de arquivo seguro.
- Parser em processo/container sem rede, sem root, filesystem de sistema somente leitura, limites de CPU/RAM/tempo/PIDs e diretório efêmero. Não iniciar Office, shell ou código fornecido pelo documento.
- Rejeitar macro/VBA, planilha protegida/cifrada, documentos com senha, objetos embutidos, referências externas e conteúdo ativo não suportado. Fórmulas não são executadas: só valor cacheado identificado; se ausente, célula `nao_calculada`, nunca zero inventado. XLS legado exige parser específico isolado e inspeção de macros; se não der para classificar com segurança, rejeitar com motivo.
- PDF: não executar JavaScript/ações/links; sem carregar fontes/imagens externas. Não servir PDF original inline no painel na primeira versão; prévia é texto/imagem derivada sanitizada. Download `attachment`, MIME correto e `nosniff`.
- JPG/PNG: decodificar sob limite de pixels e recodificar, removendo EXIF/localização e conteúdo extra; não disponibilizar SVG/HTML. Miniatura só após aprovação.
- Downloads do provedor: SSRF/DNS verificados em cada conexão, host permitido para a integração, sem redirects livres, sem IP privado/metadados/credencial em log. URL vem exclusivamente do canal autenticado/lookup por mensagem, não do texto do cliente. Se o provedor requer decriptação, usar fluxo oficial validado; não implementar protocolo de mídia por tentativa.
- Texto extraído é **dado não confiável**, nunca instrução de sistema. Aviso no Normalizar/Skill; prompt injection não pode mudar tenant, escopos, URLs, destinatário, regras ou ferramentas disponíveis. Não executar SQL/expressões de arquivo.
- Autorização em toda operação: tenant + conversa + agente vinculado + capacidade efetiva + estado/retencão. ID de outra empresa/conversa →404. Sem listagem global pela Skill. Modelos privados seguem isolamento MULTI1.

## 4. Ler: texto, tabelas e imagens

TXT: UTF-8/UTF-8 BOM inicial; encoding não reconhecido → pedir conversão, sem adivinhar e alterar valores. CSV: UTF-8/BOM, delimitador vírgula/ponto-e-vírgula detectado ou selecionado; ambiguidade/colunas inconsistentes devolvem erro com linha. Cabeçalho ausente recebe IDs de coluna estáveis; nomes repetidos não substituem coluna.

PDF textual: preservar número físico da página (base1), extrair em ordem aproximada, avisar sobre colunas/layout; não prometer reconstrução de tabela de PDF. DOCX: parágrafos/seções e tabelas com identificadores, sem número de página fictício. XLSX/XLS: abas, linhas base1 da fonte, colunas com IDs, tipo inferido e valor original; datas preservam tipo e indicação de sistema de datas quando necessário. Não transformar documentos em verdade contábil: fórmulas cacheadas podem estar antigas.

Número decimal: conservar representação textual original e converter com regra explícita (decimal `,` ou `.`, separador de milhar), escolhida no perfil da tabela e exposta ao agente. Valores ambíguos não entram em soma automaticamente. Soma usa decimal exato, informa válidos/excluídos/vazios, unidade/moeda se conhecida; não mistura moedas nem arredonda implicitamente.

**PDF escaneado:** `sem_texto`, motivo `ocr_necessario`, quantidade de páginas. Em PDF misto, indicar por página quais não têm texto; jamais afirmar documento inteiro lido. Proposta inicial: pedir versão textual/CSV/XLSX. JPG/PNG: metadados e prévia humana, estado de leitura `imagem_sem_ocr`; nenhum OCR/visão automático nesta fase. OCR futuro é bloco adicional, opt-in por modelo, com custo/privacidade, idioma/confiança e referências de página; texto OCR nunca substitui original sem rótulo.

### 4.1 Pacote compacto

Somente quando capacidade efetiva ligada, acrescentar `anexos` e `recursos.arquivos`. Desligado: manter comportamento anterior, sem baixar e sem alterar pacote legado. `anexos` inclui até5 referências recentes relevantes da **mesma conversa**, priorizando lote atual; consulta por ID para anteriores ainda retidos. Nome e trecho são dados não confiáveis.

```json
{
  "recursos":{"arquivos":{"receber":["pdf","csv"],"gerar":["csv","png"],"consultar":true}},
  "anexos":[{
    "id":"arq_UUID","nome":"vendas.csv","tipo":"csv","tamanho_bytes":18420,
    "estado":"pronto","paginas":null,
    "tabelas":[{"id":"tab_1","nome":"vendas","linhas":120,"colunas":3}],
    "trecho_inicial":"data;produto;valor\n2026-10-01;Serviço A;36,50",
    "trecho_truncado":true,"expira_em":"2026-10-22T15:00:00Z"
  }]
}
```

Até500 caracteres de trecho por arquivo e4KiB no bloco total; até10 descritores de tabela, demais por Skill. Resumo inicial é **metadado + trecho determinístico**, não chamada extra ao modelo. Se nomes/tabelas excederem o orçamento, indicar truncamento e conservar IDs; nunca cortar JSON inválido. Não incluir URL de download, Base64, chave, arquivo inteiro ou miniatura binária. As extrações só seguem ao Skyone mediante trechos/consultas explícitas; informar essa transferência de conteúdo ao configurar o agente.

### 4.2 Skill proposta `consultar_arquivo`

`POST /api/v1/arquivos/{arquivo_id}/consultar`, chave de integração no cofre Skyone, escopo **novo** `ferramentas:arquivos`, vinculada ao agente; `conversation_id` obrigatório. Empresa vem da chave. Corpo discriminado por operação, sem campos extras:

| `operacao` | Campos propostos | Limites |
|---|---|---|
| `estrutura` | conversation_id | páginas/seções/tabelas e esquema de colunas, paginado |
| `texto` | pagina (PDF) ou secao (TXT/DOCX), cursor opcional |uma página/seção por vez, máximo8.000 caracteres; cursor quando exceder|
| `linhas` | tabela_id, colunas (IDs), filtros, limite, cursor |até100 linhas,20 colunas, resposta até32KiB|
| `somar` | tabela_id, coluna_id, filtros |até200.000 células do arquivo já admitido; sem código/SQL|

Filtros: até5, combinação AND, operadores fechados `eq`, `contains`, `gte`, `lte`, `is_null`; tipo coerente com coluna, texto até200 caracteres. Sem regex arbitrária, join entre arquivos, fórmula ou ordenação não limitada. Cursores opacos ligados ao arquivo/versão/operação/filtro; não aceitar cursor de outra consulta. Consulta não altera arquivo. Limite10 chamadas/turno e orçamento32.000 caracteres devolvidos ao modelo por turno, além de limite por chave; ao exceder, erro legível, sem loop automático.

```json
{"conversation_id":"...","operacao":"somar","tabela_id":"tab_1","coluna_id":"c3",
 "filtros":[{"coluna_id":"c1","op":"gte","valor":"2026-10-01"}]}
```

Resposta proposta: `{ok,arquivo_id,versao_extracao,operacao,dados,referencias,parcial,proximo_cursor,avisos}`. Soma em `dados`: `valor_decimal:"4380.00"`, `linhas_consideradas:120`, `invalidas:0`, `vazias:0`, `unidade:null`; nunca devolver resultado de varredura parcial como soma completa. Referências identificam página/tabela/linhas usadas. `processando` →409 `CONFLICT`; expirado/apagado →404 `NOT_FOUND` com mensagem segura; consulta excedida →429 com Retry-After; arquivo sem texto/operacao não aplicável →400 `VALIDATION_ERROR` com campo/motivo. Auditoria de operação e contagens, sem texto/células em log.

## 5. Gerar: modelo declara, gateway renderiza

Retorno Skyone mantém correlação/status/tipo/acao existentes e ganha lista opcional **`arquivos`**, até3 itens. O modelo não fornece binário, Base64, caminho, URL externa, HTML, CSS, JavaScript, fórmula de planilha, código de gráfico ou destinatário. O gateway escolhe renderer e template fixos.

```json
{
  "request_id":"mesmo-UUID","conversation_id":"mesma-conversa",
  "status":"success","tipo":"text","acao":"nenhuma",
  "resposta":["Segue o resumo solicitado."],
  "arquivos":[
    {"id_local":"resumo","tipo":"xlsx","nome":"resumo.xlsx","legenda":"Vendas por mês",
     "conteudo":{"tabela":{"colunas":[{"id":"mes","titulo":"Mês","tipo":"texto"},{"id":"total","titulo":"Total","tipo":"decimal"}],
       "linhas":[["Outubro","4380.00"],["Novembro","5120.00"]]}}},
    {"id_local":"grafico","tipo":"png","nome":"vendas.png","legenda":"Comparação por mês",
     "conteudo":{"grafico":{"tipo":"barra","titulo":"Vendas","rotulos":["Outubro","Novembro"],
       "series":[{"nome":"Total","valores":[4380,5120]}]}}}
  ]
}
```

União estrita: CSV/XLSX recebem `tabela`; PDF recebe `texto` simples OU `tabela` OU `grafico`; PNG recebe `grafico`. Texto PDF até20.000 caracteres; legenda até500; nome até120; títulos até120; célula textual até1.000; rótulo de gráfico até80. Números finitos, sem NaN/Infinity, séries do mesmo comprimento dos rótulos. Pizza só valores não negativos e soma positiva; barra/linha aceitam negativos. Decimal da tabela como string validada, até18 dígitos e6 casas, sem notação ambígua; exportar com formato definido, sem arredondar silenciosamente. Resultado além do teto físico é falha, não arquivo truncado.

CSV UTF-8 BOM e delimitador `;` por padrão pt-BR, aspas/escapes corretos. Neutralizar fórmulas em texto CSV começando por `=`, `+`, `-`, `@` (inclusive após controles/espaços relevantes); valores numéricos tipados continuam números. XLSX grava texto como texto, nunca fórmula, sem hyperlinks automáticos/macros. PDF com template interno, quebras e fontes locais; sem rede. Gráfico PNG renderizado no servidor, legenda legível, paleta fixa acessível, sem recursos externos. Testar limites/legibilidade, não só sucesso do encoder.

Limite adicional:512KiB para JSON de `arquivos`, dentro do limite HTTP2MiB já existente. Geração deve validar **todos** os descritores e produzir todos os artefatos antes da primeira saída do turno; guardar hash da especificação canônica, versão do renderer e blob pronto. Falha de um não deve deixar mensagem “segue anexo” sem anexo: suprimir partes da resposta e enviar uma mensagem técnica curta única de falha, preservando pedidos/efeitos já existentes; registrar motivo para revisão. Sem refazer automaticamente chamada Skyone que possa repetir Skills com efeito.

Arquivo gerado é saída do modelo, não garantia de cálculo verdadeiro. Sempre que puder, usar soma/linhas da Skill e referências explícitas às fontes. Evolução futura: tabela derivada por consulta validada no gateway, evitando copiar milhares de linhas pelo modelo; **não incluir linguagem de consulta arbitrária no ARQ1**. Resultado grande deve ser reduzido pelo agente ou recusado com orientação.

`acao=silencio` com arquivos é conflito; rejeitar contrato, não enviar silenciosamente. `resposta:[]` com arquivos válidos pode ser resposta legítima (ajustar INV04 para conteúdo efetivo). Transferir/encerrar com arquivos: somente após partes concluídas; em falha conservar estado pendente/handoff sem declarar entrega. Refeito antes de envio reutiliza artefatos por especificação quando compatível; não enviar arquivo de tentativa descartada.

## 6. Enviar e idempotência

Ordem proposta: balões de `resposta`, depois arquivos na ordem da lista; cada arquivo com legenda própria. Sem mistura de menus interativos com anexo na mesma parte. Parte0 continua reservada ao aviso de espera. O plano completo de partes é persistido antes de qualquer envio.

```json
{"turno_request_id":"UUID","parte":2,"tipo":"arquivo","arquivo_id":"arq_UUID",
 "modo":"documento","legenda":"Vendas por mês","sha256":"hash-do-artefato",
 "entrega":"pendente"}
```

Tipos CSV/XLSX/PDF → documento; PNG → imagem. Rota/payload exatos uazapi pendentes de validação da integração. Preferir upload autenticado dos bytes pelo gateway. Se provedor só aceitar URL: acesso por capacidade curta (ex.:60s), arquivo/uso único ou número de leituras limitado validado para retries do provedor, revogável, sem credenciais em query/logs. Essa rota de mídia seria exceção pública **específica**, nunca tornar `/admin/v1` público. Decisão requer confirmar comportamento real de fetch/retry antes de escolher consumo único.

Unicidade `(tenant,request_id,parte)`, artefato imutável, destino derivado da conversa. Se canal suporta chave de idempotência, enviá-la; não presumir deduplicação do provedor. Persistir `enviando` antes da rede; guardar ID do provedor ao sucesso. Timeout/quebra após possível aceitação →`incerta`, revisão humana, **sem retry automático**. Erro comprovadamente anterior a qualquer aceitação pode ter até3 tentativas com backoff; HTTP4xx definitivo não repete. Falha parcial conserva partes entregues e não as reenvia. Revalida pausa, tenant, canal, agente, política e expiração antes de cada parte.

Simulador (`dry`): gera arquivo real privado e acrescenta parte de anexo no histórico, sem chamar canal; estado distinguível `simulada`. O histórico recebe IDs/metadados de arquivo, não link permanente. Retry do navegador reaproveita chave de idempotência da mensagem. Em simulação mock, fixture determinística; IA real só no modo explicitamente escolhido, seguindo regra existente.

## 7. Configuração de capacidade via MULTI1

Proposta de objeto novo **`capacidades.arquivos` na versão do modelo**:

```json
{"ativo":false,"receber_tipos":["txt","csv","pdf","xlsx","xls","docx","jpg","png"],
 "gerar_tipos":["csv","xlsx","pdf","png"],"max_recebido_bytes":10485760,
 "max_gerado_bytes":5242880,"retencao_dias":7}
```

Criar/alterar modelo continua só plataforma e gera versão nova; agente fica fixado na versão. Capacidade **ausente = desligada**, inclusive versões antigas. Delivery: explicitamente false; sem ativar By Koji nem atualizar sua versão. Demais segmentos também não são ativados automaticamente: plataforma escolhe modelo/versão e testa.

Efetivo = modelo permite **e** política da empresa/plataforma permite; override de empresa/agente pode restringir/desligar, nunca ampliar tipo/tamanho/retencão. Proposta primeira versão: plataforma administra política da empresa; admin pode desligar/restringir no agente; membro só usa funções da conversa autorizada. Expor origem e limites efetivos no GET do agente, seguindo padrão de `configuracao_efetiva`, e capacidades de canal para ocultar ações impossíveis. Não representar arquivos como módulo de pedidos nem como segmento fixo no código.

Desligar imediatamente impede novas aquisições/consultas pelo agente/gerações/envios ainda pendentes; downloads humanos já autorizados continuam até retenção, salvo exclusão explícita. Não apagar dados silenciosamente ao desligar. Tarefa enfileirada revalida versão/política antes de executar; auditar cancelamento.

## 8. Skyone: Normalizar e Interpretar

**Normalizar Entrada:** manter campos/histórico existentes; para capacidade ligada, incluir metadados compactos de `anexos` como dados delimitados e informar IDs/estados. Não baixar URLs, não embutir binários, não expandir todo texto. Anexo processando/sem_texto não é lido: explicar limitação e usar Skill quando pronto. Instruções de comportamento ficam no prompt do fluxo, respeitando decisão MULTI1 de não transportar textos de instruções de módulo no pacote.

**Skill:** cadastrar `consultar_arquivo` com chave no cofre e escopo restrito. Usar o mesmo conversation_id. Conteúdo retornado não altera instruções/identidade nem autoriza transmissão a destinatário diferente. Respeitar paginação, orçamento e declarar fontes/limitações ao cliente.

**Interpretar Resposta:** aceitar e preservar `arquivos` estruturado; validar formato básico sem converter dados em código; não transformar JSON do arquivo em balão. Manter `request_id`, `conversation_id`, `status`, `tipo:"text"` e ações existentes. O gateway faz a validação final e aplica política. Campo ausente equivale a lista vazia. Campo presente com capacidade desligada → contrato recusado de forma sanitizada, sem execução; não ignorar arquivo e mandar uma promessa falsa de anexo.

Propor marcador de contrato `arquivos_versao:1` dentro de `recursos.arquivos`; rollback desliga capacidade antes de voltar imagem sem ARQ1, sem mudar fluxo delivery. Fixtures dos fluxos ligados e snapshot By Koji garantem que capacidade desligada não acrescenta campos nem modifica respostas anteriores.

## 9. Contrato proposto para o painel

Base interna `/admin/v1/tenants/{t}`; browser nunca recebe PANEL_SERVICE_KEY. Servidor do painel deriva X-Actor, X-Role e X-Tenant-Scope da sessão. Downloads também passam por esse controle, sem bearer de serviço no link do navegador. Não acessar banco/disco do gateway diretamente.

| Rota proposta | Uso / resposta |
|---|---|
| `POST /agentes/{a}/simulacoes` |cria contexto de conversa de teste sem chamar agente; canal API e identificador explícitos; retorno conversation_id/idempotência|
| `POST /conversas/{c}/arquivos` |multipart, um arquivo por requisição, upload com chave de idempotência;202 com id/estado/limites; conversa já autorizada|
| `GET /conversas/{c}/arquivos?cursor=` |lista paginada de metadados, limite padrão20/máximo100|
| `GET /conversas/{c}/arquivos/{id}` |estado, limites, extração, expira_em e capacidades de prévia/download|
| `GET /conversas/{c}/arquivos/{id}/previa?pagina=` |texto paginado ou miniatura sanitizada; sem inline de original ativo|
| `GET /conversas/{c}/arquivos/{id}/conteudo` |stream autenticado, attachment, nome seguro; só aprovado/não expirado|
| `DELETE /conversas/{c}/arquivos/{id}` |202, revoga acesso imediatamente e agenda expurgo; admin/plataforma; auditoria|

Ampliar `POST …/agentes/{a}/simular` com `arquivo_ids` (até3) e idempotência; texto opcional se houver anexos. IDs precisam ser uploads pendentes da mesma conversa, tenant e agente; associação atômica e única, sem poder anexar arquivo de conversa alheia. Nenhum upload inicia chamada Skyone sozinho. Uma rota de criação de contexto evita contornar isso mandando mensagem vazia ao agente.

Papéis: membro autorizado à conversa pode anexar, consultar e baixar; não muda modelo/política nem apaga histórico geral. Admin/plataforma podem excluir; upload órfão próprio pode ser cancelado pelo autor antes da associação. Tudo servidor a servidor. Escopo restrito de unidade/conversa, se existir no painel, deve ser verificado também para download; não basta conhecer tenant.

Simulador: botão Anexar, seleção/arrastar com tipos e limite visíveis, progresso e Cancelar; lista de pendentes com nome/tamanho/estado. Enviar aceita arquivo processando, informa que o agente só lerá quando pronto; não deixa expectativa de leitura completa. Prévia PDF/DOCX por texto/página/seção, tabelas com primeiras linhas e paginação, imagem miniatura. Download expira segundo política; mensagem antiga permanece com “Arquivo expirado/apagado”. Erros `erro.detalhes.campo` junto ao upload; rejeição de tipo/tamanho/antivírus diferenciada sem expor scanner.

Reaproveitar polling/eventos: `arquivo.recebido`, `arquivo.pronto`, `arquivo.rejeitado`, `arquivo.falhou`, `arquivo.expirado`, `arquivo.apagado`; payload com IDs e estado, sem conteúdo. Histórico de mensagens ganha lista de anexos tipados e partes de saída. URLs de conteúdo são construídas pelo servidor do painel, não persistidas como link público. Cache HTTP privado/no-store para documentos e prévias sensíveis; limitar streaming/range/conexões para não esgotar servidor.

Upload tem limite próprio no proxy/painel/gateway, sem aumentar globalmente o limite de webhooks/JSON. Erros no mesmo envelope:400 campos,404 vínculo/expiração,409 estado,413 tamanho (`VALIDATION_ERROR`,campo arquivo),429 cota/taxa com Retry-After quando transitório; cota de armazenamento esgotada409 `CONFLICT` com motivo `cota_arquivos`. Preservar estado local para nova tentativa sem upload duplicado.

## 10. Dados, operação e testes

Entidades propostas: `arquivos` (tenant/conversa/agente/origem/estado/blob/chave/hash/tamanho/tipo/retencão); `arquivo_derivados` (versão extração/tabela/página/miniatura); `arquivo_tarefas` (aquisição/verificação/extração/geração/expurgo, idempotência/lease); vínculos de mensagens/partes e proveniência. FKs compostas por tenant; nenhum nome real em chave de armazenamento. Notas/resumos do agente não substituem estado persistido do arquivo.

Fila de arquivos separada das vagas de resposta para não travar todos os agentes com PDFs. Justiça por empresa, no máximo2 tarefas simultâneas por tenant quando houver mais de um worker; quotas reservadas e liberadas transacionalmente. Limpeza de órfãos cruza banco/armazenamento sem apagar trabalho ativo. Métricas: bytes/tenant, arquivos por estado, latência de aquisição/extração/geração, falhas, expurgos atrasados, envio incerto e consumo de Skill. Logs somente IDs, tipos, contagens, motivos; nunca texto, senha, URL assinada ou bytes.

Testes obrigatórios: tenant/conversa/agente alheios; membro sem conversa; capacidade desligada; nomes com traversal; MIME falso; limite streaming sem header; ZIP bomb/arquivo macro/senha; falta de scanner; PDF misto/sem texto; Unicode, CSV com aspas/quebras e fórmulas; XLS sem valor cacheado; precisão decimal e inválidos na soma; paginação sem perda; limite de memória/tempo; prompt injection como conteúdo; upload repetido; lease/queda/Redis; entrada/pausa/exclusão concorrente; falha parcial de envio e entrega incerta sem duplicação; links expirados; LGPD com todos derivados; restauração com tombstones; modo dry sem rede de envio; snapshot delivery idêntico. Arquivos maliciosos de teste são fixtures controladas e inertes/isoladas, sem usar documentos reais de cliente.

## 11. Ordem e tamanho

| Bloco | Escopo | Tamanho / critério de saída |
|---|---|---|
| Pré-requisito |MULTI1-b concluído + confirmar mídia uazapi e capacidade da VPS|nenhuma implantação ARQ1 antes; contratos de canal validados|
| **ARQ1-a** |política/capacidade, schema, armazenamento/cifra/quotas/LGPD, ingestão WhatsApp + API simulador, worker de verificação/extração, Skill de consulta|**Grande**, recomendado em subentregas a.1 infraestrutura/recepção e a.2 leitores/Skill; testes de isolamento e apagamento verdes; OCR fora|
| **ARQ1-b** |retorno estruturado, renderers CSV/XLSX/PDF/PNG e gráficos, plano durável de partes, uazapi e histórico dry|**Grande**, b.1 geração e b.2 envio/falhas; nenhum binário do modelo, entrega incerta testada|
| **ARQ1-c** |painel: configuração do modelo/política, anexar, progresso, prévia, baixar, histórico, expiração|**Médio/grande**, após APIs a/b fechadas; desktop/celular e papéis testados|

ARQ1-a pode ser exercitado por cliente de testes antes da tela; API do simulador nasce em a, não depende do painel pronto. ARQ1-b inclui gráficos, como solicitado. Nenhum prazo em dias prometido antes de validar uazapi, parsers e recursos de isolamento. Rollout opt-in em modelo geral de teste; depois segmentos aprovados. Delivery continua desligado. Publicação futura exigirá backup de banco **e** estratégia de bytes/chaves, imagem de volta e teste de restauração; este documento não autoriza executar esses passos.

## 12. Decisões em aberto — recomendação para cada uma

| # | Decisão | Proposta |
|---|---|---|
|1|Núcleo ou módulo?|Capacidade do núcleo, declarada no modelo; sem dependência de segmento/pedidos|
|2|Disco ou objeto?|Volume privado cifrado inicialmente, interface substituível por objeto privado; rever após crescimento/alta disponibilidade|
|3|Tamanho/cota?|10MiB recebido,5MiB gerado,1GiB/empresa; plataforma pode ajustar até limites testados; não elevar limite global HTTP|
|4|Retenção?|7dias, máximo30; uploads órfãos/rejeitados1h; revogar imediato, expurgar até24h; backups7dias com reaplicação de exclusões|
|5|Cifra e chaves?|Cifra autenticada por objeto com chave de dados e envelope por chave mestra versionada, inclusive extrações/derivados; escolher biblioteca na implementação|
|6|Formatos legados XLS e fórmulas?|Receber XLS com parser isolado e inspeção de macros; nunca executar fórmula; valor cacheado identificado ou célula não calculada; rejeitar senha/macros|
|7|PDF escaneado e imagem?|Receber e pré-visualizar, mas sem OCR/visão inicial; declarar sem_texto/imagem_sem_ocr e pedir versão textual; OCR separado depois|
|8|Conteúdo no pacote?|Metadados/trecho determinístico até500 caracteres por arquivo e4KiB total; Skill para conteúdo, sem resumo LLM extra|
|9|Skill única ou várias?|Uma `consultar_arquivo` discriminada em estrutura/texto/linhas/somar; sem SQL, código, joins ou regex livres|
|10|Extração atrasada?|Esperar até30s após debounce; informar pendência e não gerar fala espontânea ao terminar; painel recebe evento|
|11|Gráficos?|Barra/linha/pizza em PNG ou PDF; limites da seção5, dados finitos e template local; sem HTML/JS do agente|
|12|Falha de geração?|Validar/gerar tudo antes do envio; falha suprime promessa de anexo e gera aviso curto, sem repetir Skills de efeito|
|13|Mídia uazapi?|Confirmar endpoints/limites/recibos em documentação da instância e fixtures; upload de bytes preferido; URL curta só se exigido e validado|
|14|Quem configura?|Plataforma permite no modelo versionado e política da empresa; admin só restringe/desliga no agente; membro usa conversa autorizada|
|15|Delivery?|Desligado em todas as versões atuais; sem migrar By Koji para versão nova ou modificar pacote legado|
|16|Reuso entre conversas?|Não no ARQ1; arquivo ligado à conversa e ao agente, sem compartilhamento transversal ou deduplicação entre tenants|
|17|Formato do retorno?|`arquivos` opcional, até3 descritores estritos, no envelope `tipo:text` atual; sem binário/URL/código|
|18|Ordenação/duplicidade?|Primeiro textos, depois arquivos, plano persistido, única parte por request/índice; entrega incerta exige revisão|
|19|Custo e capacidade do worker?|Processamento isolado com concorrência inicial1 e512MiB por tarefa; testar VPS antes de reservar recurso; fila separada|
|20|Plano de entrega?|Depois MULTI1-b: ARQ1-a receber/ler, ARQ1-b gerar/enviar com gráficos, ARQ1-c painel; subdividir a/b em commits testáveis|

**Ponto de parada: aguardando decisões do Marco. Só especificação; nenhuma rota, migração, capacidade, upload, chamada externa ou serviço foi implementado/ativado.**
