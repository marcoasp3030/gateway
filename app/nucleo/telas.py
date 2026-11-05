"""TELAS1: telas do painel que a plataforma libera ou bloqueia por empresa (tenants.telas_bloqueadas; vazio = tudo liberado).

O painel esconde a tela no menu e na rota. Aqui o gateway recusa as rotas que só servem a uma tela, para quem não é a plataforma.
Rota usada por mais de uma tela (agentes, canais, unidades, conversas, chaves de formulário...) não entra no mapa: bloqueá-la
quebraria a tela que continua liberada."""
import re
import time

TELAS=('conversas','pedidos','operacao','cardapio','unidades','avisos_de_pedido','aparencia','agentes','comportamento',
       'documentos','simular','canais','chaves_formulario','webhooks','historico','usuarios')

_CACHE:dict[str,tuple[float,frozenset]]={}
VALIDADE_S=10.0


def tela_da_rota(rota:str)->str|None:
    """Tela dona da rota (caminho como o FastAPI registra, sem /admin/v1). None = rota de várias telas ou de nenhuma."""
    m=re.match(r'^/tenants/\{[^}]+\}/(.+)$',rota or '')
    if not m: return None
    partes=m.group(1).split('/')
    p0,p1=partes[0],(partes[1] if len(partes)>1 else None)
    if p0=='pedidos': return 'avisos_de_pedido' if 'avisos' in partes else 'pedidos'
    if p0=='catalogo': return 'cardapio'
    if p0=='cardapio':
        # Prévia e link da loja também saem da conversa (enviar o cardápio ao cliente).
        if p1 in ('previa','loja'): return None
        return 'aparencia' if p1 in ('aparencia','redes') else 'cardapio'
    if p0=='webhooks-saida': return 'webhooks'
    if p0=='operacao': return 'operacao'
    if p0=='respostas-rapidas': return 'conversas'
    return None


def normalizar(lista)->list[str]:
    """Só telas conhecidas, sem repetição, na ordem do catálogo."""
    pedidas=set(lista or [])
    return [t for t in TELAS if t in pedidas]


async def bloqueadas(db,tenant_id:str)->frozenset:
    agora=time.monotonic()
    guardado=_CACHE.get(tenant_id)
    if guardado and agora-guardado[0]<VALIDADE_S: return guardado[1]
    valor=frozenset(await db.fetchval('SELECT telas_bloqueadas FROM tenants WHERE id=$1',tenant_id) or ())
    _CACHE[tenant_id]=(agora,valor)
    return valor


def invalidar(tenant_id:str|None=None):
    if tenant_id is None: _CACHE.clear()
    else: _CACHE.pop(tenant_id,None)
