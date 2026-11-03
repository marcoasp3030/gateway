"""Registro explícito de capacidades. Cada módulo possui contrato próprio."""
import importlib
from dataclasses import dataclass,field
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
    # Comportamentos que só existem para agente (ou empresa) com o módulo:
    # {campo: (grupo, tipo, mínimo, máximo, opções, disponível, padrão)} — mesmo formato de nucleo.atendimento.COMPORTAMENTOS + o padrão.
    comportamentos: dict = field(default_factory=dict)
    # Telas do painel que dependem do módulo (identificadores para o menu).
    telas: tuple = ()
    # MULTI1-b: o núcleo lê daqui o que antes era fixo nele.
    # Skills: {(método, rota): nome} para o registro de chamadas; as que têm efeito para fora não deixam refazer o turno.
    skills: dict = field(default_factory=dict)
    skills_com_efeito: tuple = ()
    # Ações que o agente pode devolver além das do núcleo; as com efeito para fora não deixam refazer; as que aceitam botões.
    acoes: tuple = ()
    acoes_com_efeito: tuple = ()
    acoes_com_botao: tuple = ()
    # Motivos de transferência que só valem depois de uma Skill ter recusado algo no turno (guarda "transferência sem recusa").
    motivos_com_recusa: tuple = ()
    # Campos da unidade que vão ao agente no pacote (só para agente com o módulo).
    campos_da_unidade: tuple = ()
    # Módulo com as funções-gancho (importado só quando usado): antes_do_turno, numero_interno, mensagem_do_cliente, em_andamento, acoes_do_turno,
    # acoes_recentes, guarda_da_resposta, resumo_para_alerta, resumo_das_conversas, anonimizar, metricas, enviar_aviso, unidades_no_pacote;
    # ENC1-a: encerra_no_turno, sem_retorno, resumo_do_atendimento, resumo_dos_atendimentos; FUP1-a: pendencia_valida, unidade_da_conversa.
    ganchos: str | None = None

    def gancho(self,nome):
        """A função-gancho `nome` do módulo, ou None se ele não declara."""
        if not self.ganchos: return None
        return getattr(importlib.import_module(self.ganchos),nome,None)

async def vazio(repo, tenant, conversa):
    escopo(tenant)
    return {}

async def catalogo_contexto(repo, tenant, conversa):
    escopo(tenant)
    n=await repo.db.fetchval('SELECT count(*) FROM catalogo_itens WHERE tenant_id=$1 AND ativo AND arquivado_em IS NULL',tenant)
    return {'itens_ativos':n}

async def pedidos_contexto(repo, tenant, conversa):
    escopo(tenant)
    from modulos.pedidos.servico import contexto
    return await contexto(repo,tenant,conversa)

REGISTRO={
 'catalogo':Modulo('catalogo',('006_catalogo.sql','021_catalogo_pedidos.sql'),('/api/v1/catalogo/busca',),('ferramentas:catalogo',),catalogo_contexto,
                   telas=('cardapio',),skills={('GET','/api/v1/catalogo/busca'):'buscar_catalogo'},ganchos='modulos.catalogo.ganchos'),
 'pedidos':Modulo('pedidos',('021_catalogo_pedidos.sql','022_pedidos_dados.sql'),('/api/v1/carrinho/{conversation_id}','/api/v1/pedidos','/api/v1/entrega'),('ferramentas:pedidos',),pedidos_contexto,
                  motivos_transferencia=({'id':'pedido_para_confirmar','rotulo':'Pedido para confirmar'},),
                  comportamentos={
                      # UX3.1: acoes_recentes do pacote = chamadas de Skill da conversa nos últimos N minutos (0 = lista vazia).
                      'acoes_recentes_min':('ritmo','inteiro',0,1440,None,True,15),
                      # UX3: carrinho sem alteração há mais de N horas é arquivado na próxima mensagem do cliente.
                      'carrinho_expira_h':('pedidos','inteiro',1,720,None,True,6)},
                  telas=('pedidos','avisos_de_pedido'),
                  skills={('GET','/api/v1/carrinho/{conversation_id}'):'ver_carrinho',
                          ('POST','/api/v1/carrinho/{conversation_id}/itens'):'adicionar_item',
                          ('PATCH','/api/v1/carrinho/{conversation_id}/itens/{linha_id}'):'alterar_item',
                          ('DELETE','/api/v1/carrinho/{conversation_id}/itens/{linha_id}'):'remover_item',
                          ('DELETE','/api/v1/carrinho/{conversation_id}'):'esvaziar_carrinho',
                          ('PUT','/api/v1/carrinho/{conversation_id}/entrega'):'definir_entrega',
                          ('GET','/api/v1/entrega'):'consultar_entrega',
                          ('POST','/api/v1/pedidos'):'fechar_pedido',
                          ('GET','/api/v1/pedidos'):'consultar_pedido',
                          ('GET','/api/v1/pedidos/{numero}'):'consultar_pedido',
                          ('POST','/api/v1/pedidos/{numero}/cancelar'):'cancelar_pedido'},
                  skills_com_efeito=('fechar_pedido','cancelar_pedido'),
                  acoes=('pedido_fechado',),acoes_com_efeito=('pedido_fechado',),acoes_com_botao=('pedido_fechado',),
                  motivos_com_recusa=('pedido_para_confirmar',),
                  campos_da_unidade=('formas_pagamento','taxa_entrega','pedido_minimo','area_entrega','aceita_retirada'),
                  ganchos='modulos.pedidos.ganchos'),
}

# Camada do núcleo: valem para todo agente, qualquer que seja o modelo e os módulos.
MOTIVOS_NUCLEO=({'id':'pediu_humano','rotulo':'Cliente pediu atendente'},{'id':'reclamacao','rotulo':'Reclamação'})
TIPOS_NUCLEO=({'id':'outro','rotulo':'Outro'},)
COMPORTAMENTOS_DE_MODULO={c:nome for nome,m in REGISTRO.items() for c in m.comportamentos}


def declarados(atributo,modulos=None):
    """Tudo o que os módulos declaram num atributo de tupla, sem repetir, na ordem do registro. `modulos` = só estes."""
    saida=[]
    for nome,m in REGISTRO.items():
        if modulos is not None and nome not in modulos: continue
        for x in getattr(m,atributo):
            if x not in saida: saida.append(x)
    return tuple(saida)


def ganchos(nome,modulos=None):
    """[(módulo, função)] dos módulos que declaram o gancho `nome`. Sem `modulos`: todos os do registro
    (ganchos que só olham dados do próprio módulo: sem dado, não há efeito)."""
    return [(m,f) for m,mod in REGISTRO.items() if (modulos is None or m in modulos) for f in [mod.gancho(nome)] if f]


def _em_ordem(itens):
    """Sem repetição (o primeiro vale), na ordem em que vieram."""
    vistos={}
    for item in itens:
        if isinstance(item,dict) and item.get('id') and item['id'] not in vistos: vistos[item['id']]=dict(item)
    return list(vistos.values())


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
    """Motivos de transferência de um agente, com rótulo: os do núcleo, depois os dos módulos ligados, depois os do modelo."""
    itens=list(MOTIVOS_NUCLEO)
    for m in modulos: itens+=list(REGISTRO[m].motivos_transferencia)
    return _em_ordem(itens+list(modelo.get('motivos_transferencia') or []))


def tipos_efetivos(modelo,modulos):
    """Tipos de solicitação à equipe de um agente, com rótulo: os do modelo, depois os dos módulos ligados, e "outro" por último."""
    itens=list(modelo.get('tipos_solicitacao') or [])
    for m in modulos: itens+=list(REGISTRO[m].tipos_solicitacao)
    return [{'id':i['id'],'rotulo':i['rotulo']} for i in _em_ordem(itens+list(TIPOS_NUCLEO))]


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
