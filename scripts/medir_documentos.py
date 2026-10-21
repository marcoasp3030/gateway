"""Medição de acerto por campo do ARQ1 (docs/ESPEC_ARQ1.md, seção 15.4), nas amostras reais (corpus fora do git).

Roda dentro da imagem do leitor, sem rede (as amostras nunca saem da VPS):
  docker run --rm --network none --read-only --tmpfs /tmp --user 10002 \
    -v /home/twadmin/backups/gateway/corpus-arq1:/corpus:ro -v /home/twadmin/gateway:/workspace:ro \
    gateway-leitor:<commit> python -I /workspace/scripts/medir_documentos.py /corpus

O corpus é uma pasta com os arquivos e um `gabarito.json`:
  {"boleto-01.pdf": {"tipo_documento": "boleto", "valor": "152.30", "vencimento": "2026-10-25", "beneficiario_documento": "11.222.333/0001-81"}, …}
(valores no mesmo formato da saída: dinheiro com ponto e 2 casas, datas AAAA-MM-DD; campo ausente no gabarito não é medido).

Saída: por tipo e por campo, acertos/total; e a lista de campos `validado` ERRADOS (a meta é zero) — só nomes de arquivo e de campo,
nunca os valores (o relatório pode ir para o chat)."""
import json
import os
import sys
from datetime import datetime,timezone

sys.path[:0]=['/workspace/leitor','/workspace/app']
import leitor
from nucleo import documentos_regras as R

FORMATOS={'.pdf':'pdf','.xml':'xml'}


def comparar(esperado,obtido):
    if isinstance(esperado,str) and isinstance(obtido,str):
        return ' '.join(esperado.split()).lower()==' '.join(obtido.split()).lower()
    return esperado==obtido


def main(pasta):
    gabarito=json.load(open(os.path.join(pasta,'gabarito.json'),encoding='utf-8'))
    por_campo={};validados_errados=[];sem_leitura=[]
    for arquivo,esperado in sorted(gabarito.items()):
        formato=FORMATOS.get(os.path.splitext(arquivo)[1].lower())
        if not formato: sem_leitura.append((arquivo,'formato_fora_do_a3'));continue
        r=leitor.ler_isolado(formato,open(os.path.join(pasta,arquivo),'rb').read())
        if not r.get('ok'): sem_leitura.append((arquivo,r.get('motivo')));continue
        a=R.analisar(r,datetime.now(timezone.utc))
        tipo=esperado.get('tipo_documento') or a['tipo_documento'] or '?'
        for campo,valor in esperado.items():
            if campo=='tipo_documento': obtido,confianca=a['tipo_documento'],None
            else:
                c=a['campos'].get(campo) or {};obtido,confianca=c.get('valor'),c.get('confianca')
            ok=comparar(valor,obtido)
            k=(tipo,campo);t=por_campo.setdefault(k,{'acertos':0,'total':0,'por_confianca':{}})
            t['total']+=1;t['acertos']+=int(ok)
            pc=t['por_confianca'].setdefault(confianca or 'ausente',[0,0]);pc[1]+=1;pc[0]+=int(ok)
            if confianca=='validado' and not ok: validados_errados.append((arquivo,campo))
    print(f'Amostras: {len(gabarito)} · sem leitura: {len(sem_leitura)}')
    for (tipo,campo),t in sorted(por_campo.items()):
        detalhe=', '.join(f'{c} {a}/{n}' for c,(a,n) in sorted(t['por_confianca'].items()))
        print(f'{tipo:22} {campo:26} {t["acertos"]:3}/{t["total"]:<3} ({100*t["acertos"]/t["total"]:5.1f}%)  [{detalhe}]')
    print(f'Campos "validado" errados: {len(validados_errados)} (meta: 0)')
    for arquivo,campo in validados_errados: print(f'  {arquivo}: {campo}')
    for arquivo,motivo in sem_leitura: print(f'  sem leitura: {arquivo} ({motivo})')
    return 1 if validados_errados else 0


if __name__=='__main__':
    raise SystemExit(main(sys.argv[1] if len(sys.argv)>1 else '/corpus'))
