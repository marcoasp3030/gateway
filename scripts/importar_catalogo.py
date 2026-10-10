"""Importa o catálogo de uma empresa a partir de um CSV, pela API administrativa.

Por padrão só SIMULA: mostra o que seria inserido, atualizado e desativado e os erros por
linha, sem gravar nada. Com --aplicar grava; havendo erro em qualquer linha, nada é gravado.
O arquivo é o catálogo inteiro: item que não está nele fica inativo (não é apagado).

Uso (o arquivo é lido pela entrada padrão; a chave do painel vem do ambiente do container):
  docker compose run --rm --no-deps -T -v "$PWD/scripts:/workspace/scripts:ro" gateway \
    python /workspace/scripts/importar_catalogo.py --tenant bykoji [--aplicar] < .local/bykoji_catalogo.csv
"""
import argparse,asyncio,json,os,sys
from urllib.parse import quote

import httpx


def resumo(r):
    linhas=[('SIMULAÇÃO (nada gravado)' if r['simulacao'] else ('GRAVADO' if r['aplicado'] else 'NÃO GRAVADO: há erros')),
            f"linhas {r['linhas']} | válidas {r['validas']} | inseridos {r['inseridos']} | atualizados {r['atualizados']} | "
            f"inalterados {r['inalterados']} | desativados {r['desativados']} | erros {len(r['erros'])}"]
    for unidade,u in sorted(r['por_unidade'].items()):
        tipos=', '.join(f'{t} {n}' for t,n in sorted(u['por_tipo'].items()))
        linhas.append(f"  {unidade}: {u['total']} itens ({tipos}) | ativos {u['ativos']} | com preço {u['com_preco']} | vendáveis {u['vendaveis']}")
    for e in r['erros'][:50]: linhas.append(f"  ERRO linha {e['linha']} ({e['codigo']}): {e['erro']}")
    return '\n'.join(linhas)


async def enviar(c,tenant,conteudo,aplicar):
    r=await c.post(f'/admin/v1/tenants/{quote(tenant,safe="")}/catalogo/importar',params={'simulacao':'false' if aplicar else 'true'},
                   content=conteudo,headers={'Content-Type':'text/csv; charset=utf-8'})
    if r.status_code!=200:
        try: detalhe=json.dumps(r.json(),ensure_ascii=False)[:300]
        except ValueError: detalhe=''
        raise ValueError(f'O gateway recusou (HTTP {r.status_code}) {detalhe}')
    return r.json()


async def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--tenant',required=True)
    p.add_argument('--aplicar',action='store_true',help='grava de verdade (sem isto, só simula)')
    p.add_argument('--arquivo',help='caminho do CSV (padrão: entrada padrão)')
    p.add_argument('--url',default='http://gateway-gateway-1:8000')
    p.add_argument('--json',action='store_true',help='imprime o relatório completo em JSON')
    a=p.parse_args()
    chave=os.environ.get('PANEL_SERVICE_KEY')
    if not chave: raise ValueError('PANEL_SERVICE_KEY ausente no ambiente')
    conteudo=open(a.arquivo,'rb').read() if a.arquivo else sys.stdin.buffer.read()
    if not conteudo.strip(): raise ValueError('Arquivo vazio')
    async with httpx.AsyncClient(base_url=a.url,timeout=120,follow_redirects=False,
            headers={'Authorization':'Bearer '+chave,'X-Actor':'importar_catalogo.py'}) as c:
        r=await enviar(c,a.tenant,conteudo,a.aplicar)
    print(json.dumps(r,ensure_ascii=False,indent=1) if a.json else resumo(r))
    return 0 if not r['erros'] else 1


if __name__=='__main__':
    try: raise SystemExit(asyncio.run(main()))
    except (ValueError,OSError,httpx.HTTPError) as erro:
        raise SystemExit('Importação não feita: '+(str(erro) if isinstance(erro,(ValueError,OSError)) else type(erro).__name__)) from None
