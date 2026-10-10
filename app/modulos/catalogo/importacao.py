"""Importação do catálogo por CSV (Fase 4). O arquivo é o catálogo inteiro da empresa:
cada linha é criada ou atualizada pelo `codigo`; item que sumiu do arquivo fica inativo
(nunca é apagado). Qualquer erro de linha impede a gravação: ou entra tudo, ou nada."""
import csv,io,re
from decimal import Decimal,InvalidOperation

COLUNAS=['codigo','unidade','categoria','nome','descricao','preco','tipo','alcoolico','delivery','ativo','observacao']
TIPOS=('item','adicional','opcao_executivo','regra')
DELIVERY=('sim','nao','a_confirmar')
MAX_BYTES=2*1024*1024
MAX_LINHAS=20000
MAX_ERROS=200
COMPARADOS=('unidade','categoria','nome','descricao','preco','tipo','alcoolico','delivery','ativo','observacao')


class ArquivoInvalido(ValueError):
    pass


def _preco(valor):
    """Vazio = sem preço (None). Aceita 124, 124.50 e 124,50. Zero ou negativo é erro, nunca vira 0."""
    v=valor.strip().replace('R$','').strip()
    if not v: return None
    if not re.fullmatch(r'\d{1,7}(?:[.,]\d{1,2})?',v): raise ValueError('preço inválido')
    try: d=Decimal(v.replace(',','.'))
    except InvalidOperation: raise ValueError('preço inválido') from None
    if d<=0: raise ValueError('preço zero não é aceito; deixe vazio para item sem preço')
    return d.quantize(Decimal('0.01'))


def _sim_nao(valor,campo):
    v=valor.strip().lower()
    if v not in ('sim','nao','não'): raise ValueError(f'{campo} deve ser sim ou nao')
    return v=='sim'


def ler(conteudo):
    """bytes ou texto do CSV → (itens válidos, erros por linha). Levanta ArquivoInvalido se o arquivo não serve."""
    if isinstance(conteudo,bytes):
        if len(conteudo)>MAX_BYTES: raise ArquivoInvalido('arquivo maior que 2 MB')
        try: conteudo=conteudo.decode('utf-8-sig')
        except UnicodeDecodeError: raise ArquivoInvalido('o arquivo precisa estar em UTF-8') from None
    conteudo=conteudo.lstrip('﻿')
    leitor=csv.reader(io.StringIO(conteudo,newline=''))
    try: cabecalho=[c.strip().lower() for c in next(leitor)]
    except StopIteration: raise ArquivoInvalido('arquivo vazio') from None
    if cabecalho!=COLUNAS: raise ArquivoInvalido('cabeçalho esperado: '+','.join(COLUNAS))
    itens=[];erros=[];vistos={}
    for campos in leitor:
        n=leitor.line_num
        if not any(c.strip() for c in campos): continue
        if len(itens)+len(erros)>=MAX_LINHAS: raise ArquivoInvalido(f'mais de {MAX_LINHAS} linhas')
        codigo=campos[0].strip() if campos else ''
        try:
            if len(campos)!=len(COLUNAS): raise ValueError(f'{len(campos)} colunas; esperado {len(COLUNAS)}')
            l={k:v.strip() for k,v in zip(COLUNAS,campos)}
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,39}',l['codigo']): raise ValueError('codigo inválido')
            if l['codigo'] in vistos: raise ValueError(f'codigo repetido (linha {vistos[l["codigo"]]})')
            if not re.fullmatch(r'[a-z0-9][a-z0-9_]{0,39}',l['unidade']): raise ValueError('unidade inválida')
            if not l['categoria'] or not l['nome']: raise ValueError('categoria e nome são obrigatórios')
            if len(l['nome'])>200 or len(l['categoria'])>200 or len(l['descricao'])>1000 or len(l['observacao'])>1000: raise ValueError('texto longo demais')
            if l['tipo'] not in TIPOS: raise ValueError('tipo deve ser '+' | '.join(TIPOS))
            if l['delivery'] not in DELIVERY: raise ValueError('delivery deve ser '+' | '.join(DELIVERY))
            item={'codigo':l['codigo'],'unidade':l['unidade'],'categoria':l['categoria'],'nome':l['nome'],
                  'descricao':l['descricao'] or None,'preco':_preco(l['preco']),'tipo':l['tipo'],
                  'alcoolico':_sim_nao(l['alcoolico'],'alcoolico'),'delivery':l['delivery'],
                  'ativo':_sim_nao(l['ativo'],'ativo'),'observacao':l['observacao'] or None}
            vistos[l['codigo']]=n;itens.append(item)
        except ValueError as erro:
            if len(erros)<MAX_ERROS: erros.append({'linha':n,'codigo':codigo[:40] or None,'erro':str(erro)})
    if not itens and not erros: raise ArquivoInvalido('nenhuma linha de item')
    return itens,erros


def _contagem(itens):
    por={}
    for i in itens:
        u=por.setdefault(i['unidade'],{'total':0,'ativos':0,'com_preco':0,'vendaveis':0,'por_tipo':{}})
        u['total']+=1;u['ativos']+=int(i['ativo']);u['com_preco']+=int(i['preco'] is not None)
        # Vendável = pode entrar em carrinho: ativo, com preço e delivery diferente de "nao".
        u['vendaveis']+=int(i['ativo'] and i['preco'] is not None and i['delivery']!='nao')
        u['por_tipo'][i['tipo']]=u['por_tipo'].get(i['tipo'],0)+1
    return por


async def importar(conn,tenant,conteudo,simulacao=True):
    """Compara o arquivo com o catálogo da empresa e, se não for simulação nem houver erro, grava.
    Chamar dentro de transação (a simulação não escreve nada)."""
    itens,erros=ler(conteudo)
    atuais={r['codigo']:r for r in await conn.fetch('SELECT * FROM catalogo_itens WHERE tenant_id=$1 AND codigo IS NOT NULL',tenant)}
    novos=[i for i in itens if i['codigo'] not in atuais]
    mudados=[i for i in itens if i['codigo'] in atuais and any(atuais[i['codigo']][c]!=i[c] for c in COMPARADOS)]
    no_arquivo={i['codigo'] for i in itens}
    sumidos=[c for c,r in atuais.items() if c not in no_arquivo and r['ativo']]
    relatorio={'simulacao':simulacao,'aplicado':False,'linhas':len(itens)+len(erros),'validas':len(itens),
        'inseridos':len(novos),'atualizados':len(mudados),'inalterados':len(itens)-len(novos)-len(mudados),
        'desativados':len(sumidos),'erros':erros,'por_unidade':_contagem(itens)}
    if simulacao or erros: return relatorio
    for i in novos:
        await conn.execute('''INSERT INTO catalogo_itens(tenant_id,codigo,unidade,categoria,nome,descricao,preco,tipo,alcoolico,delivery,ativo,observacao)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)''',tenant,*(i[c] for c in ['codigo']+list(COMPARADOS)))
    for i in mudados:
        await conn.execute('''UPDATE catalogo_itens SET unidade=$3,categoria=$4,nome=$5,descricao=$6,preco=$7,tipo=$8,alcoolico=$9,
            delivery=$10,ativo=$11,observacao=$12,atualizado_em=now() WHERE tenant_id=$1 AND codigo=$2''',tenant,*(i[c] for c in ['codigo']+list(COMPARADOS)))
    if sumidos:
        await conn.execute('UPDATE catalogo_itens SET ativo=false,atualizado_em=now() WHERE tenant_id=$1 AND codigo=ANY($2::text[])',tenant,sumidos)
    relatorio['aplicado']=True
    return relatorio
