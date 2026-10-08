# Descobertas no Skyone — Fase 0.3

> Atualização de 08/10/2026: o probe foi adaptado ao contrato validado. O roteiro abaixo é histórico e ainda aguarda consolidação após os testes do Marco; não usar suas orientações de token no corpo ou API de clonagem. Para a execução atual, consultar `docs/SKYONE_RESULTADOS.md`, seção "Probe executado na VPS". Sessão e autenticação continuam sem decisão consolidada.

Status em 2026-10-08: ferramenta preparada e testada; validação real pendente.
O gateway continua em `mock/dry`. Nenhum fluxo real foi chamado.

## Preparar o fluxo de teste

1. Criar um fluxo separado do atendimento: Webhook → JavaScript → AI Agent Call → Retorno.
2. Configurar validação do `token` recebido. O token esperado deve corresponder ao `SKYONE_PROBE_TOKEN` da VPS.
3. No Logger, identificar onde o corpo do webhook aparece. Usar o mapeamento real da interface; este repositório não presume nomes de variáveis do Skyone.
4. Verificar se `instrucoes_agente` pode ser conectado às instruções do AI Agent Call. Registrar também se a seleção da base de conhecimento e das Skills aceita parâmetros.
5. Usar agente sem memória/cache/deduplicação no diagnóstico de idiomas. Conforme o roteiro, os dois payloads são iguais, exceto pela instrução. Os identificadores são gerados de novo a cada execução do probe.
6. Devolver `{"resposta": ["texto do agente"], "acao": "nenhuma", "motivo": null}`.
7. Na VPS, editar `/home/twadmin/gateway/.env` e preencher `SKYONE_PROBE_URL` e `SKYONE_PROBE_TOKEN`. Não colocar credenciais em comandos, commits ou capturas de tela.

## Executar o diagnóstico

Prévia sem rede e com token mascarado:

```bash
cd /home/twadmin/gateway
docker compose run --rm --no-deps \
  -v /home/twadmin/gateway:/workspace:ro gateway \
  python /workspace/scripts/skyone_probe.py --dry-run
```

Executar os dois casos (inglês e português):

```bash
docker compose run --rm --no-deps \
  -v /home/twadmin/gateway:/workspace:ro gateway \
  python /workspace/scripts/skyone_probe.py --caso idiomas
```

O comando lê o `.env` por meio do Compose e usa as dependências da imagem do gateway. Não exige instalar Python/pip no host nem alterar o modo de atendimento. Cada URL é validada antes da chamada; só HTTPS público na porta 443 é permitido, e redirecionamentos são recusados. O relatório mostra status HTTP, duração e resposta sanitizada. HTTP 2xx, sozinho, não comprova nenhuma das capacidades em investigação.

Para guardar um relatório local, usar `umask 077`, criar `.local/` e redirecionar a saída para `.local/skyone-probe.json`; a pasta é ignorada pelo Git. Revisar o conteúdo antes de compartilhá-lo.

## Tempo e callback

1. No fluxo de diagnóstico, configurar um Delay de 60 segundos. Pode ser fixo na interface, ou ligado ao campo **de teste** `probe_delay_s` se o Skyone permitir esse mapeamento. Esse campo é uma convenção deste probe, não uma API confirmada do Skyone.
2. Executar o comando abaixo com `--espera-s 60`, depois repetir com 120 e 300, ajustando também o Delay do fluxo.

```bash
docker compose run --rm --no-deps \
  -v /home/twadmin/gateway:/workspace:ro gateway \
  python /workspace/scripts/skyone_probe.py --caso tempo --espera-s 60 --timeout 360
```

3. Registrar duração, status e se a conexão devolve o resultado final ou só uma confirmação de recebimento. Timeout de leitura é registrado como `ReadTimeout`, não como sucesso.
4. Para validar callback, configurar um bloco REST ao fim do fluxo chamando um receptor de diagnóstico sob seu controle, com payload sintético. Registrar método, autenticação, status recebido e tempo até a entrega. **O endpoint de callback do gateway ainda não existe**; entra na Fase 0.6. Não usar `/gateway/health` como prova de entrega de callback.
5. Conferir na documentação ou no suporte do Skyone se há API de criação/clonagem de fluxos/agentes. Registrar a referência real; nenhuma API de gestão foi presumida.

## Registro de decisões

| Pergunta | Evidência necessária | Resultado |
|---|---|---|
| Instruções pelo payload mudam a resposta? | Dois retornos e mapeamento do AI Agent Call | Pendente |
| Base e Skills podem ser parametrizadas? | Campos/mapeamentos reais | Pendente |
| Existe API para criar/clonar fluxos e agentes? | Link da documentação ou resposta do suporte | Pendente |
| Limite síncrono em 60/120/300 s? | Status e durações do probe | Pendente |
| Bloco REST entrega callback externo? | Registro no receptor de teste | Pendente |
| Estratégia `por_agente` ou `por_modelo`? | Resultado das duas primeiras perguntas | A decidir |
| Resposta `sync` ou `async`? | Resultado de tempo e callback | A decidir |

O checkpoint da Fase 0.3 em `PLANO_CURSOR.md` pede esses resultados antes da Fase 0.5. A publicação do domínio foi antecipada por solicitação do Marco; ela não conclui a Fase 1, que também exige fixtures reais do uazapi.
