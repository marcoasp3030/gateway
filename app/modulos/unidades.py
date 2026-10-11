"""Skill info_unidades: detalhe das unidades para o agente, sob demanda.
Mesma função de situação do painel e do pacote (nucleo/horarios). Nulo = não informado: o agente
não afirma nada e diz que a unidade confirma. O WhatsApp de pedidos é interno e nunca sai daqui.
Lista vazia de pagamentos = nenhuma forma cadastrada (também "a unidade confirma")."""
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
    """Resposta enxuta (é lida pelo modelo): sem id interno, sem WhatsApp de pedidos, sem auditoria."""
    horarios=json.loads(r['horarios']) if isinstance(r['horarios'],str) else r['horarios']
    fuso=r['fuso'] or fuso_empresa;hoje=(agora or datetime.now(timezone.utc)).date()
    exc=await H.excecoes_da_unidade(db,tenant,r['id'],hoje-timedelta(days=2),hoje+timedelta(days=DIAS_DE_AGENDA+2))
    s=await H.situacao_da_unidade(db,tenant,r,fuso_empresa,agora)
    bairros=await db.fetchval("SELECT count(*) FROM unidade_taxas WHERE tenant_id=$1 AND unidade_id=$2 AND ativo AND tipo='bairro' AND atendido",tenant,r['id'])
    pagamentos=await db.fetch('SELECT codigo,rotulo,aceita_troco,instrucoes FROM unidade_pagamentos WHERE tenant_id=$1 AND unidade_id=$2 AND ativo ORDER BY ordem,id',tenant,r['id'])
    modo=r['taxa_modo']
    return {'codigo':r['codigo'],'nome':r['nome'],'ativa':r['ativo'],'endereco':r['endereco'],'telefone':r['telefone'],
            'situacao':{**s,'pausa_motivo':r['pausa_motivo'] if s['motivo']=='pausa' else None},
            'agenda':H.agenda(horarios,fuso,exc,DIAS_DE_AGENDA,agora),
            'aceita_retirada':r['aceita_retirada'],'delivery':r['delivery_ativo'],'taxa_entrega':_numero(r['taxa_entrega']),
            'pedido_minimo':_numero(r['pedido_minimo']),'formas_pagamento':r['formas_pagamento'],
            'area_entrega':r['area_entrega'],'regras':r['regras'],
            # G4-a. A lista de bairros não vai (pode ter centenas): o agente pede o bairro ou o CEP (precisa_de).
            'entrega':{'taxa_modo':modo,'taxa_fixa':_numero(r['taxa_entrega']) if modo in (None,'fixa') else None,
                       'frete_gratis_acima':_numero(r['frete_gratis_acima']),'tempo_min':r['tempo_entrega_min'],
                       'bairros_atendidos':bairros,'precisa_de':modo if modo in ('bairro','cep') else None},
            'retirada':{'aceita':r['aceita_retirada'],'tempo_min':r['tempo_retirada_min']},
            'pagamentos':[dict(p) for p in pagamentos]}


@router.get('/api/v1/unidades')
async def info_unidades(request:Request,conversation_id:str,unidade:str|None=Query(None,max_length=40),
                        ctx=Depends(contexto_da_chave('ferramentas:catalogo'))):
    """Todas as unidades da empresa da chave (a desativada vem com ativa=false), ou só a do código pedido."""
    db=request.app.state.db;repo=Repositorio(db);t=ctx['tenant_id']
    conv=await repo.obter(t,'conversas',conversation_id)
    if not conv or (ctx['agente_id'] and conv['agente_id']!=ctx['agente_id']): raise HTTPException(404,'NOT_FOUND')
    if unidade is None: rows=await db.fetch('SELECT * FROM tenant_unidades WHERE tenant_id=$1 ORDER BY ativo DESC,nome,id',t)
    else:
        rows=await db.fetch('SELECT * FROM tenant_unidades WHERE tenant_id=$1 AND codigo=$2',t,unidade)
        if not rows: raise HTTPException(404,'NOT_FOUND')
    fuso=await db.fetchval('SELECT timezone FROM tenants WHERE id=$1',t)
    return {'unidades':[await unidade_para_o_agente(db,t,r,fuso) for r in rows]}
