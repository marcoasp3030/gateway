import hashlib
import ipaddress
import logging
from datetime import datetime, timezone
from fastapi import HTTPException, Request
from seguranca import segredo_confere
from limites import ip_cliente

log = logging.getLogger('gateway.auth')


def hash_chave(chave, pepper):
    if not pepper:
        raise ValueError('Pepper obrigatório')
    return hashlib.sha256((pepper+chave).encode()).hexdigest()


def credencial(request: Request):
    """Qual chave do painel veio no Authorization: 'plataforma', 'empresa' ou None (nenhuma confere). CHAVES1: a chave de plataforma
    (PANEL_PLATFORM_KEY e a próxima) é só da sessão de plataforma; a de empresa (PANEL_SERVICE_KEY e a próxima), das outras."""
    infra = request.app.state.infra
    auth = request.headers.get('authorization','')
    chave = auth[7:] if auth.startswith('Bearer ') else ''
    if not chave: return None
    if any(segredo_confere(chave,k) for k in (getattr(infra,'panel_platform_key',''),getattr(infra,'panel_platform_next','')) if k): return 'plataforma'
    if segredo_confere(chave,infra.panel_key) or segredo_confere(chave,infra.panel_next): return 'empresa'
    return None


async def painel(request: Request):
    tipo = credencial(request)
    if tipo is None:
        raise HTTPException(401,'UNAUTHORIZED')
    log.info('chave_painel=%s',tipo)
    actor = request.headers.get('x-actor','')
    if request.method not in ('GET','HEAD','OPTIONS') and (not actor.strip() or len(actor)>200):
        raise HTTPException(400,'X-Actor obrigatório')
    return actor


def contexto_da_chave(escopo):
    async def contexto(request: Request):
        bruto = request.headers.get('x-api-key','')
        if not bruto:
            raise HTTPException(401,'UNAUTHORIZED')
        # Única descoberta global: identidade autenticada pelo hash, antes de obter o tenant.
        chave = await request.app.state.db.fetchrow(
            'SELECT k.*, t.status AS tenant_status FROM tenant_api_keys k '
            'JOIN tenants t ON t.id=k.tenant_id WHERE k.hash=$1',
            hash_chave(bruto,request.app.state.infra.pepper))
        agora = datetime.now(timezone.utc)
        if not chave or chave['revogada_em'] or chave['tenant_status']!='ativo' or (
                chave['expira_em'] and chave['expira_em']<=agora):
            raise HTTPException(401,'UNAUTHORIZED')
        # Empresa identificada pela chave: o registro das chamadas de Skill (PED2) usa isto, mesmo se a chamada for recusada adiante.
        request.state.skill_tenant = chave['tenant_id']
        if escopo not in chave['escopos']:
            raise HTTPException(403,'FORBIDDEN')
        if chave['ips_permitidos']:
            try:
                ip = ipaddress.ip_address(ip_cliente(request.client.host if request.client else None,request.headers))
                valido = any(ip in rede for rede in chave['ips_permitidos'])
            except ValueError:
                valido = False
            if not valido: raise HTTPException(403,'FORBIDDEN')
        await request.app.state.db.execute(
            'UPDATE tenant_api_keys SET ultimo_uso_em=now() WHERE tenant_id=$1 AND id=$2',chave['tenant_id'],chave['id'])
        return {'tenant_id':chave['tenant_id'],'agente_id':chave['agente_id'],'actor':'api-key:'+chave['id']}
    return contexto


def legados_ligados():
    """Transição (PAPEL1-a): admin = gestor e membro = funcionário até o Marco desligar (PAPEIS_LEGADOS=off)."""
    import os
    return os.getenv('PAPEIS_LEGADOS','on').strip().lower() in ('on','1','sim','true')


def papel(request: Request):
    """Papel de quem está no painel (X-Role, preenchido pelo servidor do painel a partir da sessão), por comparação exata:
    plataforma, gestor, supervisor ou funcionario (e, na transição, admin e membro). Qualquer outro valor ou ausente: None
    (as rotas admin respondem 403 antes de chegar aqui)."""
    from nucleo.permissoes import NIVEIS,LEGADOS
    valor=request.headers.get('x-role','')
    if valor in NIVEIS: return valor
    if valor in LEGADOS and legados_ligados(): return LEGADOS[valor]
    return None


def pode(request: Request, minimo: str):
    """O papel da sessão alcança o nível `minimo` (plataforma > gestor > supervisor > funcionario)?"""
    from nucleo.permissoes import atende
    return atende(papel(request),minimo)


def exigir_plataforma(request: Request):
    """Só a equipe da plataforma (ex.: cadastro de modelos de agente e chave de módulo da empresa)."""
    if papel(request)!='plataforma':
        raise HTTPException(403,'FORBIDDEN')


def exigir_admin(request: Request):
    """Gestor ou plataforma (o antigo "admin")."""
    if not pode(request,'gestor'):
        raise HTTPException(403,'FORBIDDEN')
