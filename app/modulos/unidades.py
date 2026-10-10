"""Skill info_unidades: detalhe das unidades para o agente, sob demanda.
Mesma função de situação do painel e do pacote (nucleo/horarios). Nulo = não informado: o agente
não afirma nada e diz que a unidade confirma. O WhatsApp de pedidos é interno e nunca sai daqui."""
import json
from datetime import datetime,timedelta,timezone
from fastapi import APIRouter,Depends,Request,HTTPException,Query
from nucleo.autenticacao import contexto_da_chave
from nucleo.banco import Repositorio
from nucleo import horarios as H
router=APIRouter()
DIAS_DE_AGENDA=7


def _numero(v): return float(v) if v is not None else None


async def unidade_para_o_agente(db,tenant,r,fuso_empresa,agora=None):
    horarios=json.loads(r['horarios']) if isinstance(r['horarios'],str) else r['horarios']
    fuso=r['fuso'] or fuso_empresa;hoje=(agora or datetime.now(timezone.utc)).date()
    exc=await H.excecoes_da_unidade(db,tenant,r['id'],hoje-timedelta(days=2),hoje+timedelta(days=DIAS_DE_AGENDA+2))
    s=await H.situacao_da_unidade(db,tenant,r,fuso_empresa,agora)
    return {'codigo':r['codigo'],'nome':r['nome'],'ativa':r['ativo'],'endereco':r['endereco'],'telefone':r['telefone'],'fuso':fuso,
            'situacao':{**s,'pausa_motivo':r['pausa_motivo'] if s['motivo']=='pausa' else None},
            'agenda':H.agenda(horarios,fuso,exc,DIAS_DE_AGENDA,agora),
            # Ainda não há cadastro de "faz delivery" (bloco G4): nulo = a unidade confirma.
            'delivery':None,'retirada':r['aceita_retirada'],'area_entrega':r['area_entrega'],
            'taxa':{'modo':'fixa','valor':float(r['taxa_entrega'])} if r['taxa_entrega'] is not None else None,
            'pedido_minimo':_numero(r['pedido_minimo']),'formas_pagamento':r['formas_pagamento'],'regras':r['regras']}


@router.get('/api/v1/unidades')
async def info_unidades(request:Request,conversation_id:str,unidade:str|None=Query(None,max_length=40),
                        ctx=Depends(contexto_da_chave('ferramentas:catalogo'))):
    """Sem filtro: as unidades ativas da empresa. Com unidade=<codigo>: só ela, mesmo desativada (ativa=false)."""
    db=request.app.state.db;repo=Repositorio(db);t=ctx['tenant_id']
    conv=await repo.obter(t,'conversas',conversation_id)
    if not conv or (ctx['agente_id'] and conv['agente_id']!=ctx['agente_id']): raise HTTPException(404,'NOT_FOUND')
    if unidade is None: rows=await db.fetch('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND ativo ORDER BY nome,id',t)
    else:
        rows=await db.fetch('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2',t,unidade)
        if not rows: raise HTTPException(404,'NOT_FOUND')
    fuso=await db.fetchval('SELECT timezone FROM tenants WHERE id=$1',t)
    return {'unidades':[await unidade_para_o_agente(db,t,r,fuso) for r in rows]}
