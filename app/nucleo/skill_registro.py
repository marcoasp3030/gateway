"""Registro de todas as chamadas de Skill (PED2, docs/ESPEC_PED2.md).

Inclui as consultas e as recusadas na entrada (campo inválido, conversa inexistente, chave sem o escopo): nome da Skill,
nomes dos campos recebidos, status, motivo e tempo. Nunca os valores. Serve para responder "o agente chamou a Skill?".
O registro curto de `skill_chamadas` (o que o turno refeito já fez) continua como está."""
import json
import logging
import time
from urllib.parse import parse_qsl

log=logging.getLogger('gateway.skills')
MAX_LIDO=65536
# Skills do núcleo; as dos módulos vêm das declarações deles (modulos.REGISTRO[...].skills).
SKILLS={('GET','/api/v1/unidades'):'info_unidades'}
from modulos import REGISTRO as _MODULOS
for _m in _MODULOS.values(): SKILLS.update(_m.skills)


def _json(bruto):
    try: return json.loads(bytes(bruto).decode('utf-8'))
    except (ValueError,UnicodeDecodeError): return None


def motivo_da_resposta(status,corpo):
    """'ok', o motivo da regra (`erro.detalhes.motivo`), o campo recusado na entrada (`campo:<nome>`) ou o código do erro."""
    if status<400: return 'ok'
    erro=(_json(corpo) or {}).get('erro') if isinstance(_json(corpo),dict) else None
    if not isinstance(erro,dict): return f'http_{status}'
    detalhes=erro.get('detalhes') if isinstance(erro.get('detalhes'),dict) else {}
    if detalhes.get('motivo'): return str(detalhes['motivo'])[:60]
    if detalhes.get('campo'): return ('campo:'+str(detalhes['campo']))[:60]
    return str(erro.get('codigo') or f'http_{status}')[:60]


class RegistroDeSkills:
    """Middleware ASGI: observa as rotas das Skills sem mudar nada do que entra ou sai."""
    def __init__(self,app): self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not scope.get('path','').startswith('/api/v1/'): return await self.app(scope,receive,send)
        scope.setdefault('state',{})
        inicio=time.monotonic();corpo=bytearray();resposta=bytearray();status=[500]
        async def receber():
            evento=await receive()
            if evento['type']=='http.request' and len(corpo)<MAX_LIDO: corpo.extend(evento.get('body',b'')[:MAX_LIDO])
            return evento
        async def enviar(evento):
            if evento['type']=='http.response.start': status[0]=evento['status']
            elif evento['type']=='http.response.body' and status[0]>=400 and len(resposta)<MAX_LIDO: resposta.extend(evento.get('body',b''))
            await send(evento)
        try: await self.app(scope,receber,enviar)
        finally:
            try: await self._registrar(scope,status[0],corpo,resposta,int((time.monotonic()-inicio)*1000))
            except Exception: log.warning('Chamada de Skill não registrada')

    async def _registrar(self,scope,status,corpo,resposta,duracao_ms):
        skill=SKILLS.get((scope.get('method'),getattr(scope.get('route'),'path',None)))
        if not skill: return
        motivo=motivo_da_resposta(status,resposta)
        tenant=scope['state'].get('skill_tenant')
        if not tenant:
            # Chave ausente ou inválida: não há empresa a quem atribuir. Fica só no log, sem dado da chamada.
            log.warning('Skill %s recusada antes de identificar a empresa (%s)',skill,status);return
        consulta=dict(parse_qsl(scope.get('query_string',b'').decode('latin-1'),keep_blank_values=True))
        dados=_json(corpo) if corpo else None;dados=dados if isinstance(dados,dict) else {}
        conversa=(scope.get('path_params') or {}).get('conversation_id') or consulta.get('conversation_id') or dados.get('conversation_id')
        campos=sorted({str(k)[:40] for k in list(consulta)+list(dados) if k!='conversation_id'})[:30]
        # Valores que não são dado de cliente, numa lista fechada por Skill (quem monta é a rota, em request.state.skill_ref).
        ref=scope['state'].get('skill_ref');ref={k:v for k,v in ref.items() if v is not None} if isinstance(ref,dict) else None
        await scope['app'].state.db.execute('''INSERT INTO skill_registro(tenant_id,conversation_id,skill,metodo,campos,status,motivo,duracao_ms,ref)
            VALUES ($1,$2,$3,$4,$5::text[],$6,$7,$8,$9::jsonb)''',tenant,str(conversa)[:300] if isinstance(conversa,str) and conversa else None,
            skill,scope['method'],campos,status,motivo,duracao_ms,json.dumps(ref,ensure_ascii=False,default=str) if ref else None)
