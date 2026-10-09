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
    detalhes={'campo':campo or 'corpo'} if codigo=='VALIDATION_ERROR' else {}
    mensagem='O corpo da requisição excede o tamanho permitido.' if status==413 else MENSAGENS[codigo]
    return JSONResponse({'erro':{'codigo':codigo,'mensagem':mensagem,'detalhes':detalhes}},status_code=status,
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',**(headers or {})})


def campo_validacao(errors):
    # Somente nomes conhecidos: nunca ecoar chaves arbitrárias ou valores inválidos.
    from admin.modelos import Tenant,Agente,Canal,Chave,Comportamento
    campos=set().union(*(m.model_fields for m in (Tenant,Agente,Canal,Chave,Comportamento)))
    campos.update(('texto','message','identificador','canal_id','message_id','enviada_em','minutos','limite','antes',
                   'cursor','desde','status','agente','canal','busca','tenant','tenant_id','conv','conversation_id',
                   'evento','dados','instrucao','confirmacao','url','eventos','horarios','telefone_whatsapp'))
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
