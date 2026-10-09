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


async def painel(request: Request):
    infra = request.app.state.infra
    auth = request.headers.get('authorization','')
    chave = auth[7:] if auth.startswith('Bearer ') else ''
    atual = segredo_confere(chave,infra.panel_key)
    proxima = segredo_confere(chave,infra.panel_next)
    if not (atual or proxima):
        raise HTTPException(401,'UNAUTHORIZED')
    log.info('chave_painel=%s','atual' if atual else 'proxima')
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
