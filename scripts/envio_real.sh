#!/bin/sh
# Envio real ao cliente (send_mode=live), por agente. Freio de emergência incluído.
#   sh scripts/envio_real.sh estado                       quem está com envio real, piloto e canais
#   sh scripts/envio_real.sh desligar                     TODOS os agentes com envio real voltam a envio de teste
#   sh scripts/envio_real.sh desligar <tenant> <agente>   só esse agente volta a envio de teste
# Ligar o envio real não é feito por este script: é decisão por agente, pela API administrativa.
# A troca vale na hora na recepção e em até 30 s nos workers (cache de configuração).
set -eu
cd "$(dirname "$0")/.."
docker compose exec -T gateway python - "$@" <<'PY'
import asyncio,json,os,sys,urllib.request
import asyncpg
def ch(metodo,caminho,dados=None):
    r=urllib.request.Request('http://127.0.0.1:8000/admin/v1'+caminho,method=metodo,data=json.dumps(dados).encode() if dados else None)
    r.add_header('Authorization','Bearer '+os.environ['PANEL_SERVICE_KEY']);r.add_header('X-Actor','envio_real.sh');r.add_header('Content-Type','application/json')
    return json.load(urllib.request.urlopen(r,timeout=10))
async def agentes():
    c=await asyncpg.connect(os.environ['DATABASE_URL'])
    try: return [(r['tenant_id'],r['id']) for r in await c.fetch('SELECT tenant_id,id FROM agentes ORDER BY 1,2')]
    finally: await c.close()
def estado(todos):
    reais=0
    for t,a in todos:
        d=ch('GET',f'/tenants/{t}/agentes/{a}');c=d['configuracao_efetiva']
        if c['send_mode']!='live': continue
        reais+=1
        canais=[(k['id'],k['tipo'],'ativo' if k['ativo'] else 'inativo') for k in ch('GET',f'/tenants/{t}/canais?limite=99')['itens'] if k['agente_id']==a]
        print(f"ENVIO REAL: {t}/{a} ({d.get('nome')}) | status {c['status']} | piloto {c['modo_piloto']} | contatos {c['contatos_permitidos']} | canais {canais}")
    print(f'agentes com envio real: {reais} de {len(todos)}')
acao=sys.argv[1] if len(sys.argv)>1 else ''
todos=asyncio.run(agentes())
if acao=='estado': estado(todos)
elif acao=='desligar':
    alvo=[(sys.argv[2],sys.argv[3])] if len(sys.argv)==4 else todos
    for t,a in alvo:
        if ch('GET',f'/tenants/{t}/agentes/{a}')['configuracao_efetiva']['send_mode']=='live':
            ch('PATCH',f'/tenants/{t}/agentes/{a}',{'send_mode':'dry'});print(f'{t}/{a}: envio real DESLIGADO (envio de teste)')
    estado(todos)
else: raise SystemExit('Uso: sh scripts/envio_real.sh estado | desligar [<tenant> <agente>]')
PY
