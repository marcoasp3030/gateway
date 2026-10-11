"""Entrega e pagamento da unidade (Gestão G4, docs/ESPEC_GESTAO.md seção 11): validação das linhas de taxa
(bairro e faixa de CEP) e das formas de pagamento. Funções puras, sem banco: o painel e, no G4-b, o carrinho
usam as mesmas regras. Cada validação devolve todos os problemas de uma vez."""
import re
import unicodedata
from nucleo.atendimento import analisar_texto

MAX_LINHAS=500
MAX_APELIDOS=10
MAX_PAGAMENTOS=12
TAXA_MAX=999.99
# Códigos sugeridos com o rótulo padrão (decisão 6 do Marco). Código livre também vale, com rótulo obrigatório.
SUGESTOES={'pix':'Pix','cartao_entrega':'Cartão na entrega','dinheiro':'Dinheiro','vale_refeicao':'Vale-refeição','pagamento_online':'Pagamento online'}
CODIGO=re.compile(r'[a-z0-9_]{2,30}')
CAMPOS_BAIRRO={'tipo','bairro','apelidos','atendido','taxa','ativo'}
CAMPOS_CEP={'tipo','cep_inicio','cep_fim','taxa','ativo'}
CAMPOS_PAGAMENTO={'codigo','rotulo','aceita_troco','instrucoes','ativo'}


def nome_busca(texto):
    """Forma de comparação de bairro: sem acento, sem maiúsculas, sem pontuação, espaços reduzidos a um.
    "Jd. Morumbi" e "jd morumbi" ficam iguais."""
    sem_acento=''.join(c for c in unicodedata.normalize('NFKD',texto) if not unicodedata.combining(c)).casefold()
    return ' '.join(re.sub(r'[^\w\s]|_',' ',sem_acento).split())


def cep_limpo(valor):
    """CEP com 8 dígitos, aceito com ou sem hífen; None se não for um CEP."""
    if not isinstance(valor,str): return None
    v=valor.strip()
    return v.replace('-','') if re.fullmatch(r'\d{5}-?\d{3}',v) else None


def _taxa(valor):
    if isinstance(valor,bool) or not isinstance(valor,(int,float)): return None,'tipo'
    if not 0<=valor<=TAXA_MAX: return None,'taxa_invalida'
    return round(float(valor),2),None


def _texto_simples(valor,minimo,maximo):
    if not isinstance(valor,str): return None,'tipo'
    v=' '.join(valor.split())
    if any(ord(c)<32 or ord(c)==127 for c in valor.replace('\n',' ').replace('\t',' ')): return None,'caractere'
    if not minimo<=len(v)<=maximo: return None,'tamanho'
    return v,None


def validar_linha_taxa(dados,base=None):
    """(linha limpa, erros [{campo, motivo}]) de uma linha de taxa. base = linha existente (PATCH): os campos
    enviados se juntam a ela e o tipo não muda."""
    erros=[]
    if not isinstance(dados,dict): return None,[{'campo':'linha','motivo':'tipo'}]
    tipo=(base or {}).get('tipo') or dados.get('tipo')
    if base and 'tipo' in dados and dados['tipo']!=base['tipo']: return None,[{'campo':'tipo','motivo':'tipo_invalido'}]
    if tipo not in ('bairro','cep'): return None,[{'campo':'tipo','motivo':'tipo_invalido'}]
    permitidos=CAMPOS_BAIRRO if tipo=='bairro' else CAMPOS_CEP
    erros+=[{'campo':str(c)[:40],'motivo':'campo_desconhecido'} for c in dados if c not in permitidos and c!='id']
    d={**(base or {}),**{k:v for k,v in dados.items() if k in permitidos}}
    linha={'tipo':tipo}
    ativo=d.get('ativo',True)
    if type(ativo) is not bool: erros.append({'campo':'ativo','motivo':'tipo'})
    linha['ativo']=ativo is not False
    if tipo=='bairro':
        bairro,e=_texto_simples(d.get('bairro'),2,80)
        if e: erros.append({'campo':'bairro','motivo':e})
        linha['bairro']=bairro
        apelidos=d.get('apelidos') or []
        if not isinstance(apelidos,list): erros.append({'campo':'apelidos','motivo':'tipo'});apelidos=[]
        elif len(apelidos)>MAX_APELIDOS: erros.append({'campo':'apelidos','motivo':'quantidade'})
        limpos=[]
        for a in apelidos[:MAX_APELIDOS]:
            v,e=_texto_simples(a,2,80)
            if e: erros.append({'campo':'apelidos','motivo':e});break
            limpos.append(v)
        linha['apelidos']=limpos
        atendido=d.get('atendido',True)
        if type(atendido) is not bool: erros.append({'campo':'atendido','motivo':'tipo'});atendido=True
        linha['atendido']=atendido
    else:
        ini,fim=cep_limpo(d.get('cep_inicio')),cep_limpo(d.get('cep_fim'))
        if not ini: erros.append({'campo':'cep_inicio','motivo':'cep_invalido'})
        if not fim: erros.append({'campo':'cep_fim','motivo':'cep_invalido'})
        if ini and fim and ini>fim: erros.append({'campo':'cep_fim','motivo':'cep_invalido'})
        linha['cep_inicio'],linha['cep_fim'],linha['atendido']=ini,fim,True
    taxa=d.get('taxa')
    if linha['atendido']:
        if taxa is None: erros.append({'campo':'taxa','motivo':'taxa_obrigatoria'});linha['taxa']=None
        else:
            linha['taxa'],e=_taxa(taxa)
            if e: erros.append({'campo':'taxa','motivo':e})
    else:
        # Bairro marcado como não atendido não tem taxa: recusa com certeza (fora_area no G4-b).
        if taxa is not None: erros.append({'campo':'taxa','motivo':'taxa_em_nao_atendido'})
        linha['taxa']=None
    return linha,erros


def _lado(linha,ref):
    """Como a linha aparece num conflito: posição no corpo enviado ou id da linha já gravada."""
    campos={k:linha[k] for k in (('bairro',) if linha['tipo']=='bairro' else ('cep_inicio','cep_fim'))}
    return {**ref,**campos}


def conflitos_taxas(linhas):
    """Conflitos entre linhas ativas: faixas de CEP sobrepostas e bairro/apelido repetido (comparação sem
    acento, maiúscula, pontuação e espaço extra). linhas: [(ref, linha)], ref = {"linha": n} ou {"id": x}.
    Faixas vizinhas (…999 e …000) não se sobrepõem. Devolve [{motivo, ...}]."""
    saida=[]
    faixas=[(r,l) for r,l in linhas if l['tipo']=='cep' and l['ativo'] and l.get('cep_inicio') and l.get('cep_fim')]
    for i,(ra,a) in enumerate(faixas):
        for rb,b in faixas[i+1:]:
            if a['cep_inicio']<=b['cep_fim'] and b['cep_inicio']<=a['cep_fim']:
                saida.append({'motivo':'faixas_sobrepostas','a':_lado(a,ra),'b':_lado(b,rb)})
    vistos={}
    for r,l in linhas:
        if l['tipo']!='bairro' or not l['ativo'] or not l.get('bairro'): continue
        for nome in [l['bairro']]+list(l.get('apelidos') or []):
            chave=nome_busca(nome)
            if chave in vistos:
                ra,la=vistos[chave]
                saida.append({'motivo':'bairro_duplicado','nome':chave,'a':_lado(la,ra),'b':_lado(l,r)})
            else: vistos[chave]=(r,l)
    return saida


def validar_pagamentos(itens):
    """(lista limpa na ordem do corpo, erros [{linha, campo, motivo}], bloqueios [{linha, campo, motivo, trecho}])."""
    if not isinstance(itens,list): return [],[{'linha':None,'campo':'itens','motivo':'tipo'}],[]
    erros=[];bloqueios=[];limpos=[];codigos={}
    if len(itens)>MAX_PAGAMENTOS: erros.append({'linha':None,'campo':'itens','motivo':'limite_de_linhas'})
    for n,item in enumerate(itens[:MAX_PAGAMENTOS]):
        if not isinstance(item,dict): erros.append({'linha':n,'campo':'linha','motivo':'tipo'});continue
        erros+=[{'linha':n,'campo':str(c)[:40],'motivo':'campo_desconhecido'} for c in item if c not in CAMPOS_PAGAMENTO]
        codigo=item.get('codigo')
        if not isinstance(codigo,str) or not CODIGO.fullmatch(codigo): erros.append({'linha':n,'campo':'codigo','motivo':'codigo_invalido'});codigo=None
        elif codigo in codigos: erros.append({'linha':n,'campo':'codigo','motivo':'codigo_duplicado','repete_linha':codigos[codigo]})
        else: codigos[codigo]=n
        rotulo=item.get('rotulo')
        if rotulo is None and codigo in SUGESTOES: rotulo=SUGESTOES[codigo]
        rotulo,e=_texto_simples(rotulo,2,40) if rotulo is not None else (None,'rotulo_obrigatorio')
        if e: erros.append({'linha':n,'campo':'rotulo','motivo':e})
        troco=item.get('aceita_troco',False)
        if type(troco) is not bool: erros.append({'linha':n,'campo':'aceita_troco','motivo':'tipo'});troco=False
        elif troco and codigo!='dinheiro': erros.append({'linha':n,'campo':'aceita_troco','motivo':'troco_so_em_dinheiro'})
        ativo=item.get('ativo',True)
        if type(ativo) is not bool: erros.append({'linha':n,'campo':'ativo','motivo':'tipo'});ativo=True
        instrucoes=item.get('instrucoes')
        if isinstance(instrucoes,str) and not instrucoes.strip(): instrucoes=None
        if instrucoes is not None:
            if not isinstance(instrucoes,str): erros.append({'linha':n,'campo':'instrucoes','motivo':'tipo'});instrucoes=None
            elif any(ord(c)<32 and c not in '\n\t' or ord(c)==127 for c in instrucoes): erros.append({'linha':n,'campo':'instrucoes','motivo':'caractere'})
            else:
                instrucoes='\n'.join(l.rstrip() for l in instrucoes.replace('\t',' ').strip().split('\n'))
                if len(instrucoes)>300: erros.append({'linha':n,'campo':'instrucoes','motivo':'tamanho'})
                # Vai ao agente: a mesma análise das regras da casa (CFG1). Instrução de sistema bloqueia.
                bloqueios+=[{'linha':n,'campo':'instrucoes','motivo':b['motivo'],'trecho':b['trecho']} for b in analisar_texto('instrucoes',instrucoes)[0]]
        limpos.append({'codigo':codigo,'rotulo':rotulo,'aceita_troco':troco,'instrucoes':instrucoes,'ativo':ativo})
    return limpos,erros,bloqueios
