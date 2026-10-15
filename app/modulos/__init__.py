"""Registro explícito de capacidades. Cada módulo possui contrato próprio."""
from dataclasses import dataclass
from fastapi import HTTPException
from nucleo.banco import escopo

@dataclass(frozen=True)
class Modulo:
    nome: str
    migracoes: tuple
    rotas: tuple
    escopos: tuple
    contexto: object
    # MULTI1-a: o que o módulo declara além do contexto e das Skills.
    # Motivos de transferência e tipos de solicitação próprios ({"id","rotulo"}), que somam aos do núcleo e do modelo.
    motivos_transferencia: tuple = ()
    tipos_solicitacao: tuple = ()
    # Comportamentos que só existem para agente (ou empresa) com o módulo.
    comportamentos: tuple = ()
    # Telas do painel que dependem do módulo (identificadores para o menu).
    telas: tuple = ()

async def vazio(repo, tenant, conversa):
    escopo(tenant)
    return {}

async def catalogo_contexto(repo, tenant, conversa):
    escopo(tenant)
    n=await repo.db.fetchval('SELECT count(*) FROM catalogo_itens WHERE tenant_id=$1 AND ativo',tenant)
    return {'itens_ativos':n}

async def pedidos_contexto(repo, tenant, conversa):
    escopo(tenant)
    from modulos.pedidos.servico import contexto
    return await contexto(repo,tenant,conversa)

REGISTRO={
 'catalogo':Modulo('catalogo',('006_catalogo.sql','021_catalogo_pedidos.sql'),('/api/v1/catalogo/busca',),('ferramentas:catalogo',),catalogo_contexto,
                   telas=('cardapio',)),
 'pedidos':Modulo('pedidos',('021_catalogo_pedidos.sql','022_pedidos_dados.sql'),('/api/v1/carrinho/{conversation_id}','/api/v1/pedidos'),('ferramentas:pedidos',),pedidos_contexto,
                  motivos_transferencia=({'id':'pedido_para_confirmar','rotulo':'Pedido para confirmar'},),
                  comportamentos=('carrinho_expira_h','acoes_recentes_min'),telas=('pedidos','avisos_de_pedido')),
}

# Camada do núcleo: valem para todo agente, qualquer que seja o modelo e os módulos.
MOTIVOS_NUCLEO=({'id':'pediu_humano','rotulo':'Cliente pediu atendente'},{'id':'reclamacao','rotulo':'Reclamação'})
TIPOS_NUCLEO=({'id':'outro','rotulo':'Outro'},)
# Ordem em que os motivos de hoje aparecem no perfil (mantém o pacote do By Koji igual). Os novos vêm depois, na ordem declarada.
ORDEM_MOTIVOS=('pediu_humano','reclamacao','pedido_para_confirmar','pagamento','restricao_alimentar','evento_ou_encomenda','catering','fora_do_escopo','assunto_proibido')
ORDEM_TIPOS=('evento','catering','encomenda','orcamento','parceria','outro')
COMPORTAMENTOS_DE_MODULO={c:nome for nome,m in REGISTRO.items() for c in m.comportamentos}


def _em_ordem(itens,ordem):
    """Sem repetição (o primeiro vale) e na ordem de referência; o que não está nela vem depois, na ordem em que veio."""
    vistos={}
    for item in itens:
        if isinstance(item,dict) and item.get('id') and item['id'] not in vistos: vistos[item['id']]=dict(item)
    return sorted(vistos.values(),key=lambda i:(ordem.index(i['id']) if i['id'] in ordem else len(ordem)+list(vistos).index(i['id'])))


def modulos_efetivos(modelo,ligados_no_agente,chaves_da_empresa):
    """Módulos que valem para um agente: permitido no modelo E ligado no agente (nulo = padrão do modelo) E ligado na empresa
    (sem registro = ligado). Na ordem do registro."""
    permitidos=modelo.get('modulos_permitidos')
    if permitidos is None: permitidos=modelo.get('modulos') or []
    padrao=modelo.get('modulos_padrao')
    if padrao is None: padrao=permitidos
    ligados=padrao if ligados_no_agente is None else ligados_no_agente
    return [m for m in REGISTRO if m in permitidos and m in ligados and chaves_da_empresa.get(m,True)]


def motivos_efetivos(modelo,modulos):
    """Motivos de transferência de um agente: núcleo + modelo + módulos ligados, com rótulo."""
    itens=list(MOTIVOS_NUCLEO)+list(modelo.get('motivos_transferencia') or [])
    for m in modulos: itens+=list(REGISTRO[m].motivos_transferencia)
    return _em_ordem(itens,ORDEM_MOTIVOS)


def tipos_efetivos(modelo,modulos):
    """Tipos de solicitação à equipe de um agente: núcleo + modelo + módulos ligados, com rótulo."""
    itens=list(modelo.get('tipos_solicitacao') or [])+list(TIPOS_NUCLEO)
    for m in modulos: itens+=list(REGISTRO[m].tipos_solicitacao)
    return [{'id':i['id'],'rotulo':i['rotulo']} for i in _em_ordem(itens,ORDEM_TIPOS)]


async def chaves_da_empresa(db,tenant):
    """{módulo: ligado} da empresa; módulo sem registro não aparece (vale como ligado)."""
    return {r['modulo']:r['ativo'] for r in await db.fetch('SELECT modulo,ativo FROM modulos_tenant WHERE tenant_id=$1',escopo(tenant))}


async def habilitado(repo, tenant, nome):
    escopo(tenant)
    # Chave da empresa: sem registro = ligado (MULTI1-a); só a plataforma desliga.
    if nome not in REGISTRO or (await chaves_da_empresa(repo.db,tenant)).get(nome,True) is False:
        raise HTTPException(404,'NOT_FOUND')
    return REGISTRO[nome]

async def contextos(repo,tenant,conversa,cfg):
    """contexto_modulos do pacote: um bloco por módulo que vale para o agente."""
    result={}
    for nome in cfg.get('modulos') or []:
        result[nome]=await REGISTRO[nome].contexto(repo,tenant,conversa)
    return result
