"""LGPD: escopo explícito, anonimização transacional e exclusão mútua com envio."""
import hashlib,json,re,uuid
from fastapi import APIRouter,Request,Depends,HTTPException,Query
from fastapi.responses import JSONResponse
from pydantic import Field
from .modelos import Estrito
from .servico import auditar
from .capacidades import empresa
from nucleo.autenticacao import painel
from nucleo.banco import registro
from nucleo.contatos import exibicao,validar_nome
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

class NomeDeCadastro(Estrito):
    nome:str|None=Field(max_length=200)


SELECAO_CONTATO="""SELECT ct.id,ct.tenant_id,ct.telefone,ct.email,ct.nome,ct.nome_cadastro,ct.nome_informado,ct.nome_perfil,ct.anonimizado_em,
        coalesce(x.n,0) AS conversas,x.ultima AS ultima_conversa_em,x.canal_tipo
    FROM contatos ct LEFT JOIN LATERAL (SELECT count(*) AS n,max(c.atualizado_em) AS ultima,
        (array_agg(c.canal ORDER BY c.atualizado_em DESC))[1] AS canal_tipo
        FROM conversas c WHERE c.tenant_id=ct.tenant_id AND c.contato_id=ct.id) x ON true"""


def contato_do_painel(r):
    """Campos de sempre + nome de exibição, os três nomes, telefone E.164/formatado e resumo das conversas (PED1-c)."""
    linha=dict(r)
    base={k:linha[k] for k in ('id','tenant_id','telefone','email','nome','anonimizado_em')}
    return {**base,**exibicao(linha,linha['telefone'],linha['canal_tipo']),
            'ultima_conversa_em':linha['ultima_conversa_em'],'conversas':linha['conversas']}


@router.get('/tenants/{tenant_id}/contatos')
async def lista(request:Request,tenant_id:str,busca:str=Query('',max_length=200)):
    await empresa(request.app.state.db,tenant_id)
    rows=await request.app.state.db.fetch(SELECAO_CONTATO+""" WHERE ct.tenant_id=$1 AND concat_ws(' ',ct.telefone,ct.email,ct.nome,
        ct.nome_cadastro,ct.nome_informado,ct.nome_perfil) ILIKE $2 ORDER BY ct.id LIMIT 100""",tenant_id,'%'+busca+'%')
    return {'itens':[contato_do_painel(r) for r in rows]}


@router.patch('/tenants/{tenant_id}/contatos/{cid}')
async def nome_de_cadastro(request:Request,tenant_id:str,cid:str,body:NomeDeCadastro,actor=Depends(painel)):
    """Nome de cadastro digitado no painel (qualquer papel). null apaga. Vence o informado e o de perfil."""
    nome=None
    if body.nome is not None:
        nome,recusa=validar_nome(body.nome)
        if recusa: return JSONResponse({'erro':{'codigo':'VALIDATION_ERROR','mensagem':'Nome inválido.','detalhes':{'campo':'nome','motivo':recusa}}},
                                   status_code=400,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    await empresa(request.app.state.db,tenant_id)
    async with request.app.state.db.acquire() as c,c.transaction():
        ct=await c.fetchrow('SELECT id,anonimizado_em FROM contatos WHERE tenant_id=$1 AND id=$2 FOR UPDATE',tenant_id,cid)
        if not ct: raise HTTPException(404,'NOT_FOUND')
        if ct['anonimizado_em']: raise HTTPException(409,'CONTATO_ANONIMIZADO')
        await c.execute('''UPDATE contatos SET nome_cadastro=$3,nome_cadastro_por=$4,nome_cadastro_em=now(),atualizado_em=now()
            WHERE tenant_id=$1 AND id=$2''',tenant_id,cid,nome,actor[:200])
        await auditar(c,tenant_id,actor,'contato.nome_cadastro' if nome else 'contato.nome_cadastro_removido',cid)
        r=await c.fetchrow(SELECAO_CONTATO+' WHERE ct.tenant_id=$1 AND ct.id=$2',tenant_id,cid)
    return contato_do_painel(r)

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
                await c.execute("UPDATE mensagens SET conversation_id=$3,conteudo='[anonimizado]',message_id=NULL,evento=NULL,meta=NULL,processada_em=now() WHERE tenant_id=$1 AND conversation_id=$2",t,old,new)
                await c.execute('UPDATE turnos SET conversation_id=$3,resposta=NULL,erro=NULL WHERE tenant_id=$1 AND conversation_id=$2',t,old,new)
                await c.execute('UPDATE agente_callbacks SET conversation_id=$3 WHERE tenant_id=$1 AND conversation_id=$2',t,old,new)
                await c.execute('UPDATE conversas SET conversation_id=$3,telefone=NULL,nome=NULL,unidade_preferida=NULL,bot_pausado_ate=NULL WHERE tenant_id=$1 AND conversation_id=$2',t,old,new)
                await c.execute("UPDATE audit_log SET alvo=$3,dados='{}'::jsonb WHERE tenant_id=$1 AND alvo=$2",t,old,new)
                # O que os módulos guardam da conversa (pedidos: carrinho, pedido e eventos) também perde os dados pessoais.
                from modulos import ganchos
                for _,limpar in ganchos('anonimizar'): await limpar(c,t,old,new)
                # LGPD1: o registro das Skills (só nomes de campos e status) passa para o identificador novo.
                await c.execute('UPDATE skill_registro SET conversation_id=$3 WHERE tenant_id=$1 AND conversation_id=$2',t,old,new)
                await c.execute("UPDATE avisos_internos SET conversation_id=$3,texto='[anonimizado]',referencia=md5(referencia) WHERE tenant_id=$1 AND conversation_id=$2",t,old,new)
                await c.execute("UPDATE solicitacoes SET dados='{}'::jsonb,nota=NULL,motivo_informado=NULL WHERE tenant_id=$1 AND conversation_id=$2",t,new)
                # ENC1-a: os atendimentos acompanham a troca do identificador (ON UPDATE CASCADE); o resumo sai.
                await c.execute('UPDATE atendimentos SET resumo=NULL WHERE tenant_id=$1 AND conversation_id=$2',t,new)
                # FUP1-a: nada segue agendado e a pergunta guardada sai (tarefas e turnos).
                from nucleo import follow_up
                await follow_up.anonimizar(c,t,new)
                # ARQ1-a1: documentos da conversa (original, leitura e nome) são apagados.
                from nucleo.documentos import apagar_da_conversa
                await apagar_da_conversa(c,t,new)
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
            await c.execute("""UPDATE contatos SET telefone=NULL,email=NULL,nome=NULL,perfil='{}'::jsonb,resumo=NULL,
                nome_cadastro=NULL,nome_informado=NULL,nome_perfil=NULL,nome_cadastro_por=NULL,nome_cadastro_em=NULL,nome_informado_em=NULL,
                anonimizado_em=now(),atualizado_em=now() WHERE tenant_id=$1 AND id=$2""",t,cid)
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
