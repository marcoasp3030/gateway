import json
from datetime import datetime,timezone
from fastapi import APIRouter,Request,HTTPException,Depends
from pydantic import BaseModel,Field,AwareDatetime,ConfigDict
from seguranca import segredo_confere
from nucleo.autenticacao import contexto_da_chave,painel
from nucleo.banco import Repositorio
from nucleo.pipeline import sanitizar
from admin.servico import auditar
from canais import uazapi

router=APIRouter()


class Entrada(BaseModel):
    model_config=ConfigDict(extra='forbid')
    identificador:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_@+.-]+$')
    texto:str=Field(min_length=1,max_length=20000)
    message_id:str|None=Field(None,max_length=255)
    enviada_em:AwareDatetime|None=None


class EntradaAPI(Entrada):
    canal_id:str


AMOSTRA_DE_PRESENCA=20


@router.post('/webhooks/{tipo}/{canal_id}')
async def webhook(request:Request,tipo:str,canal_id:str,s:str=''):
    # Só resolve identidade antes da autenticação. Todo acesso posterior usa tenant explícito.
    canal=await request.app.state.db.fetchrow('SELECT tenant_id,webhook_secret,tipo FROM tenant_canais WHERE id=$1',canal_id)
    if not canal or canal['tipo']!=tipo: raise HTTPException(404,'NOT_FOUND')
    esperado=request.app.state.cofre.abrir(canal['tenant_id'],canal['webhook_secret']) if canal['webhook_secret'] else None
    if not segredo_confere(s,esperado): raise HTTPException(401,'UNAUTHORIZED')
    if tipo not in ('api','uazapi'): raise HTTPException(501,'Provedor ainda não implementado')
    tenant=canal['tenant_id']
    canal_reg,empresa,cfg=await request.app.state.pipeline.contexto(tenant,canal_id)
    if not canal_reg['ativo'] or empresa['status']!='ativo' or cfg['status']!='ativo': return {'ok':True,'ignorado':True}
    try: bruto=await request.json()
    except ValueError: raise HTTPException(400,'JSON inválido') from None
    if not isinstance(bruto,dict): raise HTTPException(400,'Objeto JSON obrigatório')
    if tipo=='uazapi' and (bruto.get('EventType') or bruto.get('event'))=='presence':
        # Presença ("digitando") não é processada: guarda só uma amostra para estudo e descarta o resto na entrada.
        if await request.app.state.db.fetchval("SELECT count(*) FROM webhook_raw WHERE tenant_id=$1 AND fonte='uazapi' AND payload->>'EventType'='presence'",tenant)<AMOSTRA_DE_PRESENCA:
            await request.app.state.db.execute('INSERT INTO webhook_raw(tenant_id,fonte,payload) VALUES ($1,$2,$3::jsonb)',tenant,tipo,json.dumps(uazapi.limpar_bruto(sanitizar(bruto))))
        return {'ok':True,'ignorado':True}
    raw_id=await request.app.state.db.fetchval('INSERT INTO webhook_raw(tenant_id,fonte,payload) VALUES ($1,$2,$3::jsonb) RETURNING id',
        tenant,tipo,json.dumps(uazapi.limpar_bruto(sanitizar(bruto)) if tipo=='uazapi' else sanitizar(bruto)))
    # Sinal de vida do canal para o painel: qualquer evento autenticado conta.
    await request.app.state.db.execute('UPDATE tenant_canais SET ultimo_evento_em=now() WHERE tenant_id=$1 AND id=$2',tenant,canal_id)
    if tipo=='uazapi':
        # T1: o provedor informa em cada evento o número da própria instância; guardado para não servir de destino de alerta.
        proprio=''.join(c for c in str(bruto.get('owner') or '') if c.isdigit())
        if 10<=len(proprio)<=15:
            await request.app.state.db.execute('UPDATE tenant_canais SET numero_instancia=$3 WHERE tenant_id=$1 AND id=$2 AND numero_instancia IS DISTINCT FROM $3',tenant,canal_id,proprio)
    if tipo=='api':
        m=Entrada.model_validate(bruto).model_dump()
    else:
        if (bruto.get('EventType') or bruto.get('event'))=='connection':
            from canais.varredura import registrar_estado
            estado=uazapi.estado_do_evento(bruto)
            if estado: await registrar_estado(request.app.state.db,tenant,canal_id,estado)
            return {'ok':True,'ignorado':True}
        try: norm=uazapi.normalizar(bruto)
        except (TypeError,AttributeError,ValueError): raise HTTPException(400,'Payload inválido') from None
        if norm is None: return {'ok':True,'ignorado':True}
        try: m=Entrada.model_validate({'identificador':norm['telefone'],'texto':norm['texto'],
            'message_id':norm['message_id'],'enviada_em':norm['enviada_em']}).model_dump()
        except ValueError: raise HTTPException(400,'Payload inválido') from None
        m.update(humano=norm['from_me'],via_api=norm['via_api'],nome=norm['nome'],tipo=norm['tipo'],meta=norm['meta'],origem_recebimento='webhook')
    result=await request.app.state.pipeline.receber(tenant,canal_id,m)
    await request.app.state.db.execute('UPDATE webhook_raw SET contato_id=(SELECT contato_id FROM conversas WHERE tenant_id=$1 AND conversation_id=$3) WHERE tenant_id=$1 AND id=$2',tenant,raw_id,f'{tenant}:{canal_id}:{m["identificador"]}')
    return result


@router.post('/api/v1/conversas/mensagens')
async def mensagem_api(request:Request,dados:EntradaAPI,ctx=Depends(contexto_da_chave('conversar'))):
    canal=await Repositorio(request.app.state.db).obter(ctx['tenant_id'],'tenant_canais',dados.canal_id)
    if not canal or canal['tipo']!='api' or (ctx['agente_id'] and canal['agente_id']!=ctx['agente_id']):
        raise HTTPException(404,'NOT_FOUND')
    return await request.app.state.pipeline.receber(ctx['tenant_id'],dados.canal_id,dados.model_dump(exclude={'canal_id'}))


@router.post('/admin/v1/tenants/{tenant_id}/agentes/{agente_id}/simular')
async def simular(request:Request,tenant_id:str,agente_id:str,dados:EntradaAPI,actor=Depends(painel)):
    repo=Repositorio(request.app.state.db)
    canal=await repo.obter(tenant_id,'tenant_canais',dados.canal_id)
    if not canal or canal['agente_id']!=agente_id or canal['tipo']!='api': raise HTTPException(404,'NOT_FOUND')
    cfg=await request.app.state.configuracoes.efetiva(tenant_id,agente_id)
    # Simulação aceita IA real (agente live) porque nada sai para fora com envio em teste (dry).
    if cfg['send_mode']!='dry': raise HTTPException(400,{'campo':'send_mode'})
    result=await request.app.state.pipeline.receber(tenant_id,dados.canal_id,{**dados.model_dump(exclude={'canal_id'}),'simulacao':True})
    async with request.app.state.db.acquire() as conn,conn.transaction():
        await auditar(conn,tenant_id,actor,'agente.simulado',agente_id)
    return result


@router.get('/admin/v1/tenants/{tenant_id}/conversas/{conversa_id}/mensagens',dependencies=[Depends(painel)])
async def mensagens_admin(request:Request,tenant_id:str,conversa_id:str,antes:int|None=None,limite:int=100):
    if not 1<=limite<=100: raise HTTPException(400,{"campo":"limite"})
    if antes is not None and not 1<=antes<=9223372036854775807: raise HTTPException(400,{"campo":"antes"})
    if not await Repositorio(request.app.state.db).obter(tenant_id,'conversas',conversa_id): raise HTTPException(404,'NOT_FOUND')
    rows=await request.app.state.db.fetch('SELECT id,conteudo,direcao,origem,enviada_em FROM mensagens WHERE tenant_id=$1 AND conversation_id=$2 AND ($3::bigint IS NULL OR id<$3) ORDER BY id DESC LIMIT $4',tenant_id,conversa_id,antes,limite+1)
    return {'itens':[dict(r) for r in reversed(rows[:limite])],'tem_mais':len(rows)>limite}


@router.get('/admin/v1/tenants/{tenant_id}/metricas',dependencies=[Depends(painel)])
async def metricas(request:Request,tenant_id:str,agente:str|None=None):
    repo=Repositorio(request.app.state.db)
    if not await repo.tenant(tenant_id): raise HTTPException(404,'NOT_FOUND')
    if agente and not await repo.obter(tenant_id,'agentes',agente): raise HTTPException(404,'NOT_FOUND')
    from admin.fila import fila_da_empresa
    linha=await request.app.state.db.fetchrow('''SELECT count(*) AS turnos,
        count(*) FILTER (WHERE status='erro') AS erros,
        percentile_cont(0.5) WITHIN GROUP(ORDER BY total_ms) AS total_p50_ms,
        percentile_cont(0.95) WITHIN GROUP(ORDER BY total_ms) AS total_p95_ms
        FROM turnos WHERE tenant_id=$1 AND etapa='concluido' AND ($2::text IS NULL OR agente_id=$2)''',tenant_id,agente)
    # Indicadores da fila durável da empresa (zerados enquanto ela estiver desligada).
    try: fila=await fila_da_empresa(request,tenant_id)
    except HTTPException: raise
    except Exception: fila={'ligada':request.app.state.infra.fila_duravel,'indisponivel':True}
    # Botões (docs/ESPEC_BOTOES.md, seção 6): tudo sai de mensagens.meta.
    bot=await request.app.state.db.fetch("""SELECT m.meta->'botoes'->>'forma' AS forma, m.meta->'botoes'->>'motivo' AS motivo, count(*) AS n
        FROM mensagens m JOIN conversas c ON c.tenant_id=m.tenant_id AND c.conversation_id=m.conversation_id
        WHERE m.tenant_id=$1 AND m.direcao='saida' AND m.meta ? 'botoes' AND ($2::text IS NULL OR c.agente_id=$2) GROUP BY 1,2""",tenant_id,agente)
    cliques=await request.app.state.db.fetchrow("""SELECT count(*) AS total,
        count(*) FILTER (WHERE o.id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM mensagens d WHERE d.tenant_id=m.tenant_id
            AND d.conversation_id=m.conversation_id AND d.direcao='saida' AND coalesce(d.tipo,'')<>'reacao' AND d.id>o.id AND d.id<m.id)) AS atuais
        FROM mensagens m JOIN conversas c ON c.tenant_id=m.tenant_id AND c.conversation_id=m.conversation_id
        LEFT JOIN mensagens o ON o.tenant_id=m.tenant_id AND o.conversation_id=m.conversation_id AND o.direcao='saida' AND o.origem='bot'
            AND o.message_id=c.canal_id||':'||(m.meta->'interacao'->>'mensagem')
        WHERE m.tenant_id=$1 AND m.direcao='entrada' AND m.meta->'interacao'->>'tipo' IN ('botao','lista') AND ($2::text IS NULL OR c.agente_id=$2)""",tenant_id,agente)
    # Com motivo = não saiu como botão (plano B em texto, ou entrega incerta).
    texto={}
    for r in bot:
        if r['motivo']: texto[r['motivo']]=texto.get(r['motivo'],0)+r['n']
    botoes={'enviados':{f:sum(r['n'] for r in bot if r['forma']==f and not r['motivo']) for f in ('botao','lista')},
            'em_texto':{'total':sum(texto.values()),'por_motivo':texto},
            'clicados':{'atual':cliques['atuais'],'antigo':cliques['total']-cliques['atuais']}}
    # Mensagens picotadas (docs/ESPEC_UX2.md): turnos de cliente, sem os ignorados.
    pic=await request.app.state.db.fetchrow("""SELECT count(*) FILTER (WHERE status='refeito') AS refeitos,
        count(*) FILTER (WHERE status<>'refeito') AS respondidos,
        round(avg(n_mensagens) FILTER (WHERE status<>'refeito'),2) AS media, count(*) FILTER (WHERE status<>'refeito' AND n_mensagens=1) AS um,
        count(*) FILTER (WHERE status<>'refeito' AND n_mensagens=2) AS dois, count(*) FILTER (WHERE status<>'refeito' AND n_mensagens>=3) AS tres_ou_mais,
        round(avg(espera_ms) FILTER (WHERE status<>'refeito')) AS espera_media_ms,
        percentile_cont(0.95) WITHIN GROUP(ORDER BY espera_ms) FILTER (WHERE status<>'refeito') AS espera_p95_ms,
        count(*) FILTER (WHERE status<>'refeito' AND EXISTS(SELECT 1 FROM mensagens m WHERE m.tenant_id=t.tenant_id AND m.conversation_id=t.conversation_id
            AND m.direcao='entrada' AND m.origem='cliente' AND m.criado_em>t.agente_inicio AND m.criado_em<coalesce(t.enviado_em,t.agente_fim)
            AND NOT (m.id=ANY(t.mensagem_ids)))) AS atropelados
        FROM turnos t WHERE tenant_id=$1 AND etapa='concluido' AND origem='cliente' AND status<>'ignorado' AND ($2::text IS NULL OR agente_id=$2)""",tenant_id,agente)
    recusas={r['refazer_recusado']:r['n'] for r in await request.app.state.db.fetch("""SELECT refazer_recusado,count(*) n FROM turnos
        WHERE tenant_id=$1 AND refazer_recusado IS NOT NULL AND ($2::text IS NULL OR agente_id=$2) GROUP BY 1""",tenant_id,agente)}
    picotadas={'turnos_refeitos':pic['refeitos'],'refazer_recusado':recusas,'atropelados':pic['atropelados'],
               'mensagens_por_turno':{'media':float(pic['media']) if pic['media'] is not None else None,'um':pic['um'],'dois':pic['dois'],'tres_ou_mais':pic['tres_ou_mais']},
               'espera_media_ms':int(pic['espera_media_ms']) if pic['espera_media_ms'] is not None else None,
               'espera_p95_ms':int(pic['espera_p95_ms']) if pic['espera_p95_ms'] is not None else None}
    sol=await request.app.state.db.fetchrow("""SELECT count(*) AS criadas,coalesce(sum(atualizacoes),0) AS atualizadas,count(*) FILTER (WHERE origem='convertida') AS convertidas
        FROM solicitacoes WHERE tenant_id=$1 AND ($2::text IS NULL OR agente_id=$2)""",tenant_id,agente)
    alertas=await request.app.state.db.fetchval("SELECT count(*) FROM avisos_internos WHERE tenant_id=$1 AND tipo='solicitacao' AND status='enviado'",tenant_id)
    # SOL1. convertidas = transferências que o gateway trocou por aviso à equipe (acao_original transferir_humano → acao_aplicada notificar_equipe).
    # CFG1-c: reações pedidas pelo agente (o que saiu e o que foi barrado, por motivo), fora do horário e transferências.
    reac=await request.app.state.db.fetch("""SELECT reacao->>'resultado' AS resultado,reacao->>'emoji' AS emoji,reacao->>'motivo' AS motivo,count(*) AS n FROM turnos
        WHERE tenant_id=$1 AND reacao IS NOT NULL AND ($2::text IS NULL OR agente_id=$2) GROUP BY 1,2,3""",tenant_id,agente)
    aplicadas={};bloqueadas={}
    for r in reac:
        if r['resultado'] in ('aplicada','simulada'): aplicadas[r['emoji']]=aplicadas.get(r['emoji'],0)+r['n']
        else: bloqueadas[r['motivo'] or 'falha_provedor']=bloqueadas.get(r['motivo'] or 'falha_provedor',0)+r['n']
    reacoes={'aplicadas':{'total':sum(aplicadas.values()),'por_emoji':aplicadas},'bloqueadas':{'total':sum(bloqueadas.values()),'por_motivo':bloqueadas}}
    extra=await request.app.state.db.fetchrow("""SELECT count(*) FILTER (WHERE resposta->>'fora_do_horario'='avisado') AS avisos,
        count(*) FILTER (WHERE resposta->>'fora_do_horario'='sem_resposta') AS mudas,
        count(*) FILTER (WHERE equipe_fora_do_horario IS FALSE) AS dentro,count(*) FILTER (WHERE equipe_fora_do_horario) AS fora
        FROM turnos WHERE tenant_id=$1 AND status<>'refeito' AND ($2::text IS NULL OR agente_id=$2)""",tenant_id,agente)
    horario={'avisos_enviados':extra['avisos'],'mensagens_sem_resposta':extra['mudas']}
    transbordo={'transferencias':{'dentro_do_horario':extra['dentro'],'fora_do_horario':extra['fora']}}
    solicitacoes={'criadas':sol['criadas'],'atualizadas':int(sol['atualizadas']),'convertidas':sol['convertidas'],'alertas_enviados':alertas}
    return {**dict(linha),'fila':fila,'botoes':botoes,'picotadas':picotadas,'solicitacoes':solicitacoes,
            'reacoes':reacoes,'fora_do_horario':horario,'transbordo':transbordo}
