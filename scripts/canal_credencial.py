"""Grava as credenciais de um canal uazapi (endereço base e token) pela API administrativa.

Os dois valores são pedidos pelo terminal com getpass: não aparecem na tela, não entram
em argumento de linha de comando, variável de ambiente, arquivo nem log. O gateway guarda
cifrado (credenciais_enc) e nunca devolve. O endereço base passa pelas regras de SSRF
(https, porta 443, sem usuário/senha, DNS resolvendo só para IP público).

Uso (pede terminal interativo; a chave do painel vem do ambiente do container):
  docker compose run --rm --no-deps -v "$PWD/scripts:/workspace/scripts:ro" gateway \
    python /workspace/scripts/canal_credencial.py --tenant bykoji --canal can_...
"""
import argparse,asyncio,getpass,os,sys
from pathlib import Path
from urllib.parse import quote,urlsplit

import httpx

# No repositório o código fica em ../app; no container (script montado), em /app.
for pasta in (Path(__file__).resolve().parents[1]/'app',Path('/app')):
    if (pasta/'seguranca.py').exists(): sys.path.insert(0,str(pasta));break
from seguranca import UrlRecusada,validar_url_saida


async def preparar(base_url,token,validar=validar_url_saida):
    """Confere e normaliza o que foi digitado. Erros não repetem o valor."""
    base_url=(base_url or '').strip().rstrip('/');token=(token or '').strip()
    if not base_url or not token: raise ValueError('Endereço base e token são obrigatórios')
    if any(ord(c)<33 or ord(c)==127 for c in base_url+token): raise ValueError('Espaço ou caractere de controle não é permitido')
    partes=urlsplit(base_url)
    if partes.query or partes.fragment or partes.path not in ('','/'):
        raise ValueError('Informe só o endereço base (https://dominio), sem caminho nem parâmetros')
    if len(token)>500 or len(base_url)>300: raise ValueError('Valor grande demais')
    try: await validar(base_url+'/send/text')
    except UrlRecusada as erro:
        # Motivo sem o endereço: mensagens de DNS podem repetir o host.
        raise ValueError('Endereço base recusado: '+str(erro).split(':')[0].split('(')[0].strip()) from None
    return {'base_url':base_url,'token':token}


async def gravar(c,tenant,canal,cred):
    caminho=f'/admin/v1/tenants/{quote(tenant,safe="")}/canais/{quote(canal,safe="")}'
    r=await c.get(caminho)
    if r.status_code!=200: raise ValueError(f'Canal não encontrado (HTTP {r.status_code})')
    if r.json().get('tipo')!='uazapi': raise ValueError('O canal não é do tipo uazapi')
    r=await c.patch(caminho,json={'credenciais':cred})
    if r.status_code!=200: raise ValueError(f'O gateway recusou a gravação (HTTP {r.status_code})')
    return bool(r.json().get('credenciais_configurado'))


async def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--tenant',required=True)
    p.add_argument('--canal',required=True)
    p.add_argument('--url',default='http://gateway-gateway-1:8000',help='endereço interno do gateway')
    a=p.parse_args()
    if not sys.stdin.isatty(): raise ValueError('Rode em terminal interativo (sem -T), para a digitação não aparecer')
    chave=os.environ.get('PANEL_SERVICE_KEY')
    if not chave: raise ValueError('PANEL_SERVICE_KEY ausente no ambiente')
    cred=await preparar(getpass.getpass('Endereço base da instância uazapi (não aparece): '),
                        getpass.getpass('Token da instância (não aparece): '))
    async with httpx.AsyncClient(base_url=a.url,timeout=15,follow_redirects=False,
            headers={'Authorization':'Bearer '+chave,'X-Actor':'canal_credencial.py'}) as c:
        ok=await gravar(c,a.tenant,a.canal,cred)
    print(f'Canal {a.canal}: credenciais_configurado = {str(ok).lower()}')
    return 0 if ok else 1


if __name__=='__main__':
    try: raise SystemExit(asyncio.run(main()))
    except (ValueError,httpx.HTTPError) as erro:
        # Sem detalhes do cliente HTTP: podem conter endereço.
        raise SystemExit('Não gravado: '+(str(erro) if isinstance(erro,ValueError) else type(erro).__name__)) from None
