"""Outbox assinada; três tentativas, sem redirects e sem registrar respostas."""
import asyncio,hashlib,hmac,json,time
from seguranca import validar_url_saida
from nucleo.banco import escopo

async def emitir(db,tenant,evento,dados):
    escopo(tenant)
    corpo=json.dumps({'evento':evento,'tenant_id':tenant,'dados':dados},ensure_ascii=False,separators=(',',':'))
    await db.execute("INSERT INTO webhook_entregas(tenant_id,webhook_id,evento,corpo) SELECT tenant_id,id,$2,$3 FROM tenant_webhooks_saida WHERE tenant_id=$1 AND ativo AND $2=ANY(eventos)",tenant,evento,corpo)

async def despachar(db,http,cofre):
    # Enumera identidades; cada entrega é relida e travada com seu tenant explícito.
    candidatos=await db.fetch("SELECT tenant_id,id FROM webhook_entregas WHERE status='pendente' AND proxima_em<=now() ORDER BY id LIMIT 10")
    for item in candidatos:
        t=item['tenant_id']
        async with db.acquire() as c,c.transaction():
            e=await c.fetchrow("SELECT * FROM webhook_entregas WHERE tenant_id=$1 AND id=$2 AND status='pendente' AND proxima_em<=now() FOR UPDATE SKIP LOCKED",t,item['id'])
            if not e: continue
            w=await c.fetchrow('SELECT * FROM tenant_webhooks_saida WHERE tenant_id=$1 AND id=$2',t,e['webhook_id'])
            if not w['ativo']:
                await c.execute("UPDATE webhook_entregas SET status='cancelada' WHERE tenant_id=$1 AND id=$2",t,e['id']);continue
            codigo=None;ok=False
            try:
                segredo=cofre.abrir(t,w['segredo_enc'])
                corpo=e['corpo'].encode();ts=str(int(time.time()))
                sig=hmac.new(segredo.encode(),ts.encode()+b'.'+corpo,hashlib.sha256).hexdigest()
                async def enviar():
                    await validar_url_saida(w['url'])
                    async with http.stream('POST',w['url'],content=corpo,headers={'Content-Type':'application/json','X-Gateway-Event':e['evento'],'X-Gateway-Timestamp':ts,'X-Gateway-Signature':'sha256='+sig,'X-Gateway-Delivery':str(e['id'])},follow_redirects=False,timeout=10) as response:
                        return response.status_code
                codigo=await asyncio.wait_for(enviar(),timeout=12);ok=200<=codigo<300
            except Exception:
                pass  # Nenhum texto/URL/credencial de terceiros é logado.
            n=e['tentativas']+1
            await c.execute("UPDATE webhook_entregas SET tentativas=$3,status=$4,http_status=$5,proxima_em=now()+make_interval(secs=>$6) WHERE tenant_id=$1 AND id=$2",t,e['id'],n,'entregue' if ok else ('erro' if n>=3 else 'pendente'),codigo,2**n)
