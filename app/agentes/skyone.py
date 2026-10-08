"""Contrato Skyone e credenciais isolados do pipeline."""
import asyncio
import json
import uuid
from zoneinfo import ZoneInfo
import httpx
import seguranca
from nucleo.banco import Repositorio
from nucleo.credenciais import CredencialInvalida


class FalhaAgente(Exception):
    """Mensagem fixa: não propaga corpo, URL ou credencial de terceiros."""


async def pacote(repo,tenant_id,conversa_id,antes_id,message,cfg,request_id=None):
    conv = await repo.obter(tenant_id,'conversas',conversa_id)
    if not conv or conv['agente_id']!=cfg['id'] or cfg['tenant_id']!=tenant_id:
        raise LookupError('Conversa não encontrada')
    tenant = await repo.tenant(tenant_id)
    if not tenant: raise LookupError('Tenant não encontrado')
    fuso = ZoneInfo(tenant['timezone'])
    historico = await repo.historico(tenant_id,conversa_id,antes_id,cfg['history_turns'])
    rid = request_id or str(uuid.uuid4())
    from modulos import contextos
    return {
        'contexto_modulos':await contextos(repo,tenant_id,conversa_id,cfg),
        'conversation_id':conversa_id,'request_id':rid,'session_id':rid,'message':message,
        'instrucoes_agente':cfg['instrucoes_agente'],
        'historico':[{'role':'user' if h['direcao']=='entrada' else 'assistant',
                     'content':h['conteudo'],'enviada_em':h['enviada_em'].astimezone(fuso).isoformat()}
                    for h in historico],
        'tenant_id':tenant_id,'agente_id':cfg['id'],'modelo_id':cfg['modelo_id'],
    }


async def chamar(client,repo,cofre,tenant_id,cfg,payload):
    if cfg['tenant_id']!=tenant_id or payload['tenant_id']!=tenant_id or payload['agente_id']!=cfg['id']:
        raise FalhaAgente('Contexto inválido')
    if cfg['agente_mode']=='mock':
        return {'resposta':['Resposta simulada.'],'acao':'nenhuma'}
    if cfg['provedor']!='skyone' or cfg['modo_resposta']!='sync':
        raise FalhaAgente('Provedor/modo indisponível')
    tenant = await repo.tenant(tenant_id)
    agente = await repo.obter(tenant_id,'agentes',cfg['id'])
    if not tenant or not agente: raise FalhaAgente('Agente indisponível')
    try:
        headers = {}
        tipo = agente['skyone_auth_tipo']
        if tipo=='nenhuma':
            if not tenant['tenant_teste']: raise FalhaAgente('Autenticação obrigatória')
        else:
            segredo = cofre.abrir(tenant_id,agente['skyone_auth_enc'])
            if not isinstance(segredo,str) or not segredo or '\r' in segredo or '\n' in segredo:
                raise FalhaAgente('Credencial inválida')
            if tipo=='bearer': headers['Authorization']='Bearer '+segredo
            elif tipo=='cabecalho':
                nome=agente['skyone_auth_cabecalho']
                import re
                if not nome or not re.fullmatch(r'[A-Za-z0-9-]+',nome) or nome.lower() in ('host','content-length','transfer-encoding','connection'):
                    raise FalhaAgente('Cabeçalho inválido')
                headers[nome]=segredo
            else: raise FalhaAgente('Autenticação desconhecida')
        async def requisitar():
            await seguranca.validar_url_saida(cfg['skyone_webhook_url'],cfg['skyone_dominios_permitidos'])
            async with client.stream('POST',cfg['skyone_webhook_url'],json=payload,headers=headers,
                                     timeout=cfg['timeout_s'],follow_redirects=False) as response:
                if not 200<=response.status_code<300: raise FalhaAgente('HTTP do agente recusado')
                partes=[]; tamanho=0
                async for parte in response.aiter_bytes():
                    tamanho+=len(parte)
                    if tamanho>2097152: raise FalhaAgente('Resposta excede limite')
                    partes.append(parte)
                return json.loads(b''.join(partes))
        corpo=await asyncio.wait_for(requisitar(),timeout=cfg['timeout_s'])
        if not isinstance(corpo,dict) or corpo.get('request_id')!=payload['request_id'] or corpo.get('conversation_id')!=payload['conversation_id'] or corpo.get('status')!='success' or corpo.get('tipo')!='text':
            raise FalhaAgente('Contrato inválido')
        resposta=corpo.get('resposta')
        if isinstance(resposta,dict): resposta=resposta.get('body')
        acao=corpo.get('acao')
        if acao not in ('nenhuma','silencio','transferir_humano','encerrar','pedido_fechado'):
            raise FalhaAgente('Ação inválida')
        if acao=='silencio':
            if not corpo.get('motivo'): raise FalhaAgente('Silêncio sem motivo')
            textos=[]
        else:
            if not isinstance(resposta,str) or not resposta.strip(): raise FalhaAgente('Resposta vazia/inválida')
            textos=[resposta]
        # Guardar somente campos do contrato, sem eco arbitrário do provedor.
        result={'resposta':textos,'acao':acao}
        for campo in ('motivo','desfecho'):
            if isinstance(corpo.get(campo),str): result[campo]=corpo[campo]
        # Não permitir eco da credencial mesmo dentro de campos do contrato.
        if headers and segredo in json.dumps(result,ensure_ascii=False):
            raise FalhaAgente('Resposta contém credencial')
        return result
    except (httpx.HTTPError,TimeoutError,ValueError,TypeError,CredencialInvalida):
        raise FalhaAgente('Falha na comunicação com o agente') from None
