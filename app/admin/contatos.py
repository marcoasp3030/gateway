"""LGPD: escopo explícito, anonimização transacional e exclusão mútua com envio."""
import hashlib,json,re,uuid
from fastapi import APIRouter,Request,Depends,HTTPException,Query
from pydantic import Field
from .modelos import Estrito
from .servico import auditar
from .capacidades import empresa
from nucleo.autenticacao import painel
from nucleo.banco import registro
from seguranca import segredo_confere
router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])
class Confirmacao(Estrito):
    confirmacao:str=Field(min_length=1,max_length=255)

async def contato(c,t,cid):
    row=await c.fetchrow('SELECT * FROM contatos WHERE tenant_id=$1 AND id=$2',t,cid)
    if not row: raise HTTPException(404,'NOT_FOUND')
    return registro(row)

def contem(texto,identificadores):
    # Também cobre telefones formatados em texto de provedores antigos.
    grupos=[re.sub(r'\D','',m.group()) for m in re.finditer(r'\+?\d[\d ()-]{6,}\d',texto)]
    return any(i in texto or (i.isdigit() and any(i in g for g in grupos)) for i in identificadores if i)

@router.get('/tenants/{tenant_id}/contatos')
async def lista(request:Request,tenant_id:str,busca:str=Query('',max_length=200)):
    await empresa(request.app.state.db,tenant_id)
    rows=await request.app.state.db.fetch("SELECT id,tenant_id,telefone,email,nome,anonimizado_em FROM contatos WHERE tenant_id=$1 AND (coalesce(telefone,'') ILIKE $2 OR coalesce(email,'') ILIKE $2 OR coalesce(nome,'') ILIKE $2) ORDER BY id LIMIT 100",tenant_id,'%'+busca+'%')
    return {'itens':[dict(r) for r in rows]}

@router.get('/tenants/{tenant_id}/contatos/{cid}/exportar')
async def exportar(request:Request,tenant_id:str,cid:str,actor=Depends(painel)):
    if not actor.strip(): raise HTTPException(400,'X-Actor obrigatório')
    async with request.app.state.db.acquire() as c,c.transaction():
        ct=await contato(c,tenant_id,cid)
        convs=await c.fetch('SELECT * FROM conversas WHERE tenant_id=$1 AND contato_id=$2',tenant_id,cid)
        ids=[r['conversation_id'] for r in convs]
        msgs=await c.fetch('SELECT * FROM mensagens WHERE tenant_id=$1 AND conversation_id=ANY($2::text[]) ORDER BY id',tenant_id,ids)
        turnos=await c.fetch('SELECT * FROM turnos WHERE tenant_id=$1 AND conversation_id=ANY($2::text[]) ORDER BY id',tenant_id,ids)
        identificadores=ids+[v for v in (ct['telefone'],ct['email']) if v]
        bruto=[r for r in await c.fetch('SELECT id,fonte,payload,recebido_em,contato_id FROM webhook_raw WHERE tenant_id=$1',tenant_id) if r['contato_id']==cid or contem(str(r['payload']),identificadores)]
        entregas=[dict(r) for r in await c.fetch('SELECT id,evento,corpo,status,tentativas,criado_em FROM webhook_entregas WHERE tenant_id=$1',tenant_id) if contem(r['corpo'],identificadores)]
        auditoria=await c.fetch('SELECT * FROM audit_log WHERE tenant_id=$1 AND (alvo=$2 OR alvo=ANY($3::text[])) ORDER BY id',tenant_id,cid,ids)
        await auditar(c,tenant_id,actor,'contato.exportado',cid)
    return {'contato':ct,'conversas':[dict(r) for r in convs],'mensagens':[registro(r) for r in msgs],'turnos':[registro(r) for r in turnos],'webhook_raw':[registro(r) for r in bruto],'entregas':entregas,'auditoria':[registro(r) for r in auditoria],'pedidos':[]}

@router.delete('/tenants/{tenant_id}/contatos/{cid}')
async def anonimizar(request:Request,tenant_id:str,cid:str,body:Confirmacao,actor=Depends(painel)):
    t=tenant_id;locks=[];p=request.app.state.pipeline;r=request.app.state.redis
    try:
        async with request.app.state.db.acquire() as c,c.transaction():
            await c.fetchrow('SELECT id FROM contatos WHERE tenant_id=$1 AND id=$2 FOR UPDATE',t,cid)
            ct=await contato(c,t,cid)
            if ct['anonimizado_em']: raise HTTPException(409,'Contato já anonimizado')
            if not any(segredo_confere(body.confirmacao,v) for v in (ct['telefone'],ct['email']) if v): raise HTTPException(400,'Confirmação inválida')
            convs=await c.fetch('SELECT * FROM conversas WHERE tenant_id=$1 AND contato_id=$2',t,cid)
            await c.execute('SET CONSTRAINTS ALL DEFERRED')
            ident=[x for x in (ct['telefone'],ct['email']) if x]
            for conv in convs:
                old=conv['conversation_id'];key=p.prefixo+':lock:'+hashlib.sha256(p.chave(t,old).encode()).hexdigest();token=uuid.uuid4().hex
                if not await r.set(key,token,nx=True,ex=600): raise HTTPException(409,'Conversa em processamento; tente novamente')
                locks.append((key,token))
                new=f'{t}:{conv["canal_id"]}:anon_{uuid.uuid4().hex}'
                ident.append(old)
                await c.execute("UPDATE mensagens SET conversation_id=$3,conteudo='[anonimizado]',message_id=NULL,evento=NULL,processada_em=now() WHERE tenant_id=$1 AND conversation_id=$2",t,old,new)
                await c.execute('UPDATE turnos SET conversation_id=$3,resposta=NULL,erro=NULL WHERE tenant_id=$1 AND conversation_id=$2',t,old,new)
                await c.execute('UPDATE conversas SET conversation_id=$3,telefone=NULL,nome=NULL,unidade_preferida=NULL,bot_pausado_ate=NULL WHERE tenant_id=$1 AND conversation_id=$2',t,old,new)
                await c.execute("UPDATE audit_log SET alvo=$3,dados='{}'::jsonb WHERE tenant_id=$1 AND alvo=$2",t,old,new)
            # Brutos sem vínculo legados: só anonimiza registros que contêm identificador.
            for raw in await c.fetch('SELECT id,payload,contato_id FROM webhook_raw WHERE tenant_id=$1',t):
                if raw['contato_id']==cid or contem(str(raw['payload']),ident):
                    await c.execute("UPDATE webhook_raw SET payload='{}'::jsonb,contato_id=$3 WHERE tenant_id=$1 AND id=$2",t,raw['id'],cid)
            for entrega in await c.fetch('SELECT id,corpo FROM webhook_entregas WHERE tenant_id=$1 FOR UPDATE',t):
                if contem(entrega['corpo'],ident):
                    await c.execute("UPDATE webhook_entregas SET corpo='{}',status=CASE WHEN status='pendente' THEN 'cancelada' ELSE status END WHERE tenant_id=$1 AND id=$2",t,entrega['id'])
            for a in await c.fetch('SELECT id,actor,alvo,dados FROM audit_log WHERE tenant_id=$1',t):
                if contem(str(dict(a)),ident):
                    await c.execute("UPDATE audit_log SET actor='anonimizado',alvo=$3,dados='{}'::jsonb WHERE tenant_id=$1 AND id=$2",t,a['id'],cid)
            await c.execute("UPDATE contatos SET telefone=NULL,email=NULL,nome=NULL,perfil='{}'::jsonb,resumo=NULL,anonimizado_em=now(),atualizado_em=now() WHERE tenant_id=$1 AND id=$2",t,cid)
            await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
            await auditar(c,t,'anonimizado' if contem(actor,ident) else actor,'contato.anonimizado',cid)
        for conv in convs:
            chave=p.chave(t,conv['conversation_id'])
            await r.zrem(p.prefixo+':due',chave)
            await r.delete(p.prefixo+':first:'+chave)
        return {'ok':True,'anonimizado':True}
    finally:
        for key,token in locks:
            await r.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",1,key,token)
