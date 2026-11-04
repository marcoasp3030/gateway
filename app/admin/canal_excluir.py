"""CANAL-EXC (docs/ESPEC_CANAL_EXC.md): a plataforma exclui um canal desativado e todo o histórico das conversas dele.

Os pedidos ficam, sem dados pessoais, presos a uma conversa vazia e anônima (o pedido exige conversa). Se sobrar alguma, o canal
fica marcado como excluído; senão, a linha some. Irreversível: só plataforma, canal desativado e o nome digitado como confirmação."""
import uuid
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import Field
from admin.modelos import Estrito
from admin.servico import auditar
from nucleo.autenticacao import painel

router=APIRouter(prefix='/admin/v1',dependencies=[Depends(painel)])

# Tudo o que guarda conteúdo ou rastro da conversa e não é pedido: sai inteiro.
APAGAR=('mensagens','turnos','agente_callbacks','avisos_internos','conversa_esperas',
        'conversa_etiquetas','conversa_notas','follow_up_tarefas','skill_chamadas','skill_registro','solicitacoes')


class Confirmacao(Estrito):
    confirmacao:str=Field(min_length=1,max_length=255)


@router.delete('/tenants/{tenant}/canais/{canal}')
async def excluir(request:Request,tenant:str,canal:str,body:Confirmacao,actor=Depends(painel)):
    async with request.app.state.db.acquire() as c,c.transaction():
        k=await c.fetchrow('SELECT * FROM tenant_canais WHERE tenant_id=$1 AND id=$2 FOR UPDATE',tenant,canal)
        if not k or k['excluido_em']: raise HTTPException(404,'NOT_FOUND')
        if k['ativo']: raise HTTPException(409,{'campo':'ativo'})
        if body.confirmacao.strip()!=k['nome'].strip(): raise HTTPException(400,{'campo':'confirmacao'})
        await c.execute('SET CONSTRAINTS ALL DEFERRED')
        convs=await c.fetch('SELECT conversation_id,contato_id FROM conversas WHERE tenant_id=$1 AND canal_id=$2',tenant,canal)
        ids=[r['conversation_id'] for r in convs]
        # Conversas que um módulo precisa manter (pedidos: o pedido exige conversa), com quantos registros cada uma segura.
        from modulos import ganchos
        presas={}
        for _,ver in ganchos('conversas_presas'):
            for cid,n in (await ver(c,tenant,ids)).items(): presas[cid]=presas.get(cid,0)+n
        com_pedido=set(presas)
        n_mensagens=await c.fetchval('SELECT count(*) FROM mensagens WHERE tenant_id=$1 AND conversation_id=ANY($2::text[])',tenant,ids)
        # Documentos: arquivo e leitura saem do disco; depois o registro.
        from nucleo.documentos import apagar_da_conversa
        for cid in ids: await apagar_da_conversa(c,tenant,cid)
        await c.execute('DELETE FROM anexos WHERE tenant_id=$1 AND conversation_id=ANY($2::text[])',tenant,ids)
        for tabela in APAGAR:
            await c.execute(f'DELETE FROM {tabela} WHERE tenant_id=$1 AND conversation_id=ANY($2::text[])',tenant,ids)
        await c.execute('DELETE FROM turnos WHERE tenant_id=$1 AND canal_id=$2',tenant,canal)
        # Cada módulo apaga o que guarda das conversas (pedidos: o carrinho sai, o pedido fica).
        for _,limpar in ganchos('excluir_conversas'): await limpar(c,tenant,ids)
        await c.execute('DELETE FROM atendimentos WHERE tenant_id=$1 AND conversation_id=ANY($2::text[])',tenant,ids)
        # O identificador da conversa leva o telefone: a auditoria antiga perde o alvo e os dados.
        await c.execute("UPDATE audit_log SET alvo='conversa_excluida',dados='{}'::jsonb WHERE tenant_id=$1 AND alvo=ANY($2::text[])",tenant,ids)
        await c.execute('DELETE FROM conversas WHERE tenant_id=$1 AND conversation_id=ANY($2::text[])',tenant,[i for i in ids if i not in com_pedido])
        for antiga in com_pedido:
            nova=f'{tenant}:{canal}:excluida_{uuid.uuid4().hex}'
            await c.execute('''UPDATE conversas SET conversation_id=$3,telefone=NULL,nome=NULL,contato_id=NULL,unidade_preferida=NULL,bot_pausado_ate=NULL,
                atribuida_a=NULL,atribuida_em=NULL,aguardando_atendente_desde=NULL,aguardando_motivo=NULL,atendimento_id=NULL
                WHERE tenant_id=$1 AND conversation_id=$2''',tenant,antiga,nova)
            # Pedidos (ON UPDATE CASCADE) já seguem o identificador novo; os módulos tiram os dados pessoais.
            for _,limpar in ganchos('anonimizar'): await limpar(c,tenant,antiga,nova)
        # Contato que ficou sem nenhuma conversa sai, com o que só existe por causa dele.
        contatos=[cid for cid in {r['contato_id'] for r in convs if r['contato_id']}
                  if not await c.fetchval('SELECT EXISTS(SELECT 1 FROM conversas WHERE tenant_id=$1 AND contato_id=$2)',tenant,cid)]
        for tabela in ('webhook_raw','follow_up_opt_out','follow_up_tarefas','solicitacoes'):
            await c.execute(f'DELETE FROM {tabela} WHERE tenant_id=$1 AND contato_id=ANY($2::text[])',tenant,contatos)
        await c.execute('DELETE FROM contatos WHERE tenant_id=$1 AND id=ANY($2::text[])',tenant,contatos)
        if com_pedido:
            await c.execute("""UPDATE tenant_canais SET excluido_em=now(),ativo=false,credenciais_enc='{}'::jsonb,webhook_secret=NULL,numero_instancia=NULL
                WHERE tenant_id=$1 AND id=$2""",tenant,canal)
        else:
            await c.execute('DELETE FROM tenant_canais WHERE tenant_id=$1 AND id=$2',tenant,canal)
        resumo={'conversas_apagadas':len(ids)-len(com_pedido),'conversas_com_pedido':len(com_pedido),'mensagens_apagadas':n_mensagens,
                'pedidos_mantidos':sum(presas.values()),
                'contatos_apagados':len(contatos),'canal':'marcado' if com_pedido else 'apagado'}
        await auditar(c,tenant,actor,'canal.excluido',canal,resumo)
    request.app.state.configuracoes.invalidar(tenant)
    return {'ok':True,**resumo}
