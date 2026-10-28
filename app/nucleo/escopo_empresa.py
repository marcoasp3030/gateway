"""Empresa da sessão na API admin (MULTI1-a, docs/ESPEC_MULTI1.md seção 6).

O servidor do painel envia `X-Tenant-Scope` com a empresa do usuário logado. Para quem não é `plataforma`:
rota de outra empresa responde 404; escritas e visões de toda a plataforma respondem 403. A plataforma vê tudo.
Sem o cabeçalho, durante a transição (ESCOPO_EMPRESA_OBRIGATORIO desligado), a chamada segue como antes e fica registrada
em log e alerta; com a exigência ligada, responde 403."""
import logging
import os
import re
import time
from urllib.parse import unquote

log=logging.getLogger('gateway.escopo')
EMPRESA_NO_CAMINHO=re.compile(r'^/admin/v1/tenants/([^/]+)')
# Rotas sem empresa no caminho que mostram ou mudam a plataforma inteira.
SO_PLATAFORMA=(('POST','/admin/v1/tenants'),('GET','/admin/v1/fila'),('GET','/admin/v1/auditoria/plataforma'))
ESCRITAS=('POST','PUT','PATCH','DELETE')
_ultimo_log={}
LOG_A_CADA_S=60


def obrigatorio():
    # PAPEL1-a: ligado por padrão (o painel manda a empresa da sessão desde 22/10); desligar só por ordem do Marco.
    return os.getenv('ESCOPO_EMPRESA_OBRIGATORIO','on').strip().lower() in ('on','1','sim','true')


def do_pedido(request):
    """Empresa da sessão enviada pelo painel, ou None."""
    valor=request.headers.get('x-tenant-scope','').strip()
    return valor or None


IDS_NO_CAMINHO=re.compile(r'/(?:[a-z]{2,4}_[0-9a-f]{12,32}|\d+)(?=/|$)')


def rota_sem_ids(caminho):
    """Caminho para log e alerta, sem a empresa e sem ids (agente, canal, contato, número): um aviso por rota, não por registro."""
    return IDS_NO_CAMINHO.sub('/{id}',EMPRESA_NO_CAMINHO.sub('/admin/v1/tenants/{t}',caminho))[:120]


def conferir(request,papel):
    """None = segue. Senão, (status, motivo) para responder sem chamar a rota."""
    caminho=request.url.path
    if not caminho.startswith('/admin/v1/') or papel=='plataforma': return None
    escopo=do_pedido(request)
    if escopo is None:
        if obrigatorio(): return 403,'escopo_ausente'
        return 'sem_escopo'
    m=EMPRESA_NO_CAMINHO.match(caminho)
    if m and unquote(m.group(1))!=escopo: return 404,'outra_empresa'
    alvo=request.query_params.get('tenant')
    if alvo is not None and alvo!=escopo: return 404,'outra_empresa'
    metodo=request.method.upper()
    if not m:
        if (metodo,caminho.rstrip('/')) in SO_PLATAFORMA: return 403,'so_plataforma'
        if metodo in ESCRITAS and caminho.startswith('/admin/v1/modelos-agente'): return 403,'so_plataforma'
    return None


def registrar_sem_escopo(request,papel):
    """Log (no máximo um por minuto por papel e rota) da chamada sem a empresa da sessão."""
    chave=(papel,request.method,rota_sem_ids(request.url.path));agora=time.monotonic()
    if agora-_ultimo_log.get(chave,0)<LOG_A_CADA_S: return False
    _ultimo_log[chave]=agora
    log.warning('Chamada do painel sem X-Tenant-Scope: papel=%s %s %s',papel,chave[1],chave[2])
    return True
