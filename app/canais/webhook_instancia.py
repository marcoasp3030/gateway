"""Cadastro do webhook na instância uazapi pelo gateway (MULTI1-a, docs/ESPEC_MULTI1.md seção 7)."""
from urllib.parse import quote
from admin.servico import auditar
from nucleo.banco import Repositorio
from canais import uazapi


def endereco(state,tipo,canal,segredo):
    base=(state.infra.public_url or '').rstrip('/')
    return f'{base}/webhooks/{tipo}/{quote(canal,safe="")}?s={segredo}' if base else None


async def registrar(state,tenant,canal_id,actor,segredo=None):
    """Cadastra o webhook do canal na instância e registra na auditoria. `segredo` = o recém-gerado (senão, o guardado)."""
    linha=await Repositorio(state.db).obter(tenant,'tenant_canais',canal_id)
    if not linha or linha['tipo']!='uazapi': return None
    if segredo is None:
        try: segredo=state.cofre.abrir(tenant,linha['webhook_secret'])
        except Exception: segredo=None
    url=endereco(state,'uazapi',canal_id,segredo) if segredo else None
    if url is None: resultado={'registrado':False,'motivo':'endereco_publico_ausente'}
    else: resultado=await uazapi.registrar_webhook(state.http,state.cofre,tenant,linha,url)
    async with state.db.acquire() as conn,conn.transaction():
        await auditar(conn,tenant,actor,'canal.webhook_registrado' if resultado['registrado'] else 'canal.webhook_falhou',canal_id)
    return resultado
