"""Envelope público estável; mensagens nunca interpolam entradas/segredos."""
from starlette.responses import JSONResponse

MENSAGENS={
 'VALIDATION_ERROR':'Confira os campos informados.',
 'NOT_FOUND':'Recurso não encontrado.',
 'CONFLICT':'A operação conflita com o estado atual do recurso.',
 'UNAUTHORIZED':'Credencial de acesso ausente ou inválida.',
 'FORBIDDEN':'Você não tem permissão para esta operação.',
 'RATE_LIMITED':'Limite de requisições atingido. Aguarde e tente novamente.',
 'UPSTREAM_ERROR':'Não foi possível concluir a chamada ao provedor.',
 'CREDENTIAL_REJECTED':'Credencial do Skyone recusada.',
 'NOT_IMPLEMENTED':'Esta operação ainda não está disponível.',
 'UNAVAILABLE':'Serviço temporariamente indisponível.'}


def resposta(status,codigo=None,campo=None,headers=None):
    codigo=codigo or {400:'VALIDATION_ERROR',401:'UNAUTHORIZED',403:'FORBIDDEN',404:'NOT_FOUND',
      405:'NOT_IMPLEMENTED',409:'CONFLICT',413:'VALIDATION_ERROR',422:'VALIDATION_ERROR',
      429:'RATE_LIMITED',501:'NOT_IMPLEMENTED',502:'UPSTREAM_ERROR'}.get(status,'UNAVAILABLE')
    if codigo not in MENSAGENS: codigo='UNAVAILABLE'
    if status==429: headers={'Retry-After':'1',**(headers or {})}
    detalhes=({'campo':campo or 'corpo'} if codigo=='VALIDATION_ERROR'
              else {'campo':campo} if codigo=='CONFLICT' and campo else {})
    mensagem='O corpo da requisição excede o tamanho permitido.' if status==413 else MENSAGENS[codigo]
    return JSONResponse({'erro':{'codigo':codigo,'mensagem':mensagem,'detalhes':detalhes}},status_code=status,
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',**(headers or {})})


def campos_conhecidos():
    from admin.modelos import Tenant,Agente,Canal,Chave,Comportamento
    campos=set().union(*(m.model_fields for m in (Tenant,Agente,Canal,Chave,Comportamento)))
    campos.update(('texto','message','identificador','canal_id','message_id','enviada_em','minutos','limite','antes',
                   'cursor','desde','status','agente','canal','busca','tenant','tenant_id','conv','conversation_id',
                   'evento','dados','instrucao','confirmacao','usar_provedor_real','aguardando','url','eventos','horarios','telefone_whatsapp',
                   'escopos','ips_permitidos','expira_em','credenciais','modulos','comportamento_padrao',
                   'desfechos','followups_padrao','skyone_fluxo_modelo','ativo','config','modelo_versao',
                   'item_id','quantidade','observacao','unidade','modalidade','endereco','horario','maioridade_confirmada','motivo','numero',
                   'ate','indefinida','data','fechado','faixas','fuso','dias','entidade'))
    return campos


def campo_validacao(errors):
    # Somente nomes conhecidos: nunca ecoar chaves arbitrárias ou valores inválidos.
    campos=campos_conhecidos()
    for error in errors:
        loc=error.get('loc',())
        if loc and loc[0] in ('body','query','path','header'):loc=loc[1:]
        if loc and str(loc[0]) in campos:return str(loc[0])
    return 'corpo'


CAMPOS_LEGADOS={
 'X-Actor obrigatório':'X-Actor','ID obrigatório':'id','ID imutável':'id',
 'Nome obrigatório':'nome','Modelo obrigatório':'modelo_id','Modelo inválido':'modelo_id',
 'Timezone inválido':'timezone','max_wait_s menor que debounce_s':'max_wait_s',
 'Telefone inválido':'contatos_permitidos','Provedor/URL live inválidos':'skyone_webhook_url',
 'SEG13: credencial obrigatória':'skyone_auth','Cabeçalho inválido':'skyone_auth_cabecalho',
 'Credencial inválida':'skyone_auth','Basic exige usuário e senha':'skyone_auth',
 'Tipo e agente obrigatórios':'agente_id','Tipo imutável':'tipo',
 'Escopos obrigatórios':'escopos','Escopo inválido':'escopos','Data exige fuso':'expira_em',
 'Confirmação inválida':'confirmacao','Limite inválido':'limite','Limite deve ser 1–99':'limite',
 'Simulação exige mock/dry':'agente_mode','Comportamento inválido':'comportamento_padrao',
 'Módulos não habilitados':'modelo_id','Escopo/ID imutáveis':'id'}


# Restrições sem nome de coluna (CHECK compostos) → campo que o cliente precisa corrigir.
RESTRICOES={'agentes_check':'skyone_auth','agentes_check1':'skyone_auth_cabecalho'}


def campo_integridade(exc):
    """Campo de um erro de integridade do Postgres, só entre nomes conhecidos."""
    import re
    campos=campos_conhecidos()
    coluna=getattr(exc,'column_name',None)
    if coluna in campos: return coluna
    restricao=getattr(exc,'constraint_name',None) or ''
    if restricao in RESTRICOES: return RESTRICOES[restricao]
    tabela=getattr(exc,'table_name',None) or ''
    if restricao.endswith('_pkey'): return 'id'
    resto=restricao.removeprefix(tabela+'_') if tabela else restricao
    resto=re.sub(r'_(check\d*|fkey|key)$','',resto)
    for campo in sorted(campos,key=len,reverse=True):
        if resto==campo or resto.startswith(campo+'_'): return campo
    return None


def resposta_integridade(exc):
    import asyncpg
    campo=campo_integridade(exc)
    if isinstance(exc,asyncpg.UniqueViolationError): return resposta(409,campo=campo)
    if isinstance(exc,(asyncpg.NotNullViolationError,asyncpg.CheckViolationError,
                       asyncpg.ForeignKeyViolationError)):
        return resposta(400,campo=campo)
    return resposta(409,campo=campo)
