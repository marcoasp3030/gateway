"""Botões de resposta (docs/ESPEC_BOTOES.md, bloco B2).

O agente devolve só {"opcoes": [{"id","titulo","descricao"?}], "titulo_lista"?}; aqui se decide a forma
(botão, lista — bloco B3 — ou texto numerado) e por quê. Nada aqui monta payload de canal, e nenhum caso vira erro: sempre há plano B em texto."""
import re
import unicodedata

MAX_BOTOES=3
MAX_OPCOES=10
MAX_TITULO=20
# Lista (B3): 4 a 10 opções, ou qualquer quantidade quando alguma opção tem descrição. Limites da Meta.
MAX_TITULO_LISTA=24
MAX_DESCRICAO=72
MAX_BOTAO_DA_LISTA=20
BOTAO_DA_LISTA='Ver opções'
ID=re.compile(r'[a-z0-9_]{1,40}')
MOTIVOS=('canal_sem_botao','acima_do_limite','titulo_longo','descricao_longa','invalido','provedor_recusou','provedor_incerto','desligado')
# Só com estas ações faz sentido oferecer escolha.
# MULTI1-b: as do núcleo e as que os módulos declaram (pedido_fechado, do módulo de pedidos).
from modulos import declarados as _declarados
ACOES_COM_BOTAO=('nenhuma','notificar_equipe')+_declarados('acoes_com_botao')


def _chave(titulo):
    """Comparação de títulos sem maiúsculas e sem acento."""
    return ''.join(c for c in unicodedata.normalize('NFKD',titulo.casefold()) if not unicodedata.combining(c))


def do_retorno(valor,acao,textos):
    """Campo "botoes" do Retorno do agente, só com o que é do contrato. None = sem botões.

    Ausente, nulo, sem balão para prender ou ação que não comporta escolha → None (sem erro).
    Opção com id vazio é descartada. Formato errado ou mais de MAX_OPCOES → {'opcoes': [], 'invalido': True}."""
    if valor is None or not textos or acao not in ACOES_COM_BOTAO: return None
    opcoes=valor.get('opcoes') if isinstance(valor,dict) else None
    if not isinstance(opcoes,list): return {'opcoes':[],'invalido':True}
    lidas=[];ruim=False
    for o in opcoes:
        if not isinstance(o,dict) or not isinstance(o.get('titulo',''),str) or not isinstance(o.get('id',''),(str,type(None))):
            ruim=True;continue
        ident=(o.get('id') or '').strip()
        if not ident: continue
        lida={'id':ident[:80],'titulo':o.get('titulo','').strip()[:200]}
        descricao=o.get('descricao')
        if isinstance(descricao,str) and descricao.strip(): lida['descricao']=' '.join(descricao.split())[:200]
        elif descricao is not None and not isinstance(descricao,str): ruim=True
        lidas.append(lida)
    if len(lidas)>MAX_OPCOES: return {'opcoes':[],'invalido':True}
    if not lidas and not ruim: return None
    lido={'opcoes':lidas,'invalido':True} if ruim else {'opcoes':lidas}
    titulo_lista=valor.get('titulo_lista')
    if isinstance(titulo_lista,str) and titulo_lista.strip(): lido['titulo_lista']=' '.join(titulo_lista.split())[:100]
    return lido


def candidata_a_lista(botoes):
    """As opções iriam como lista (mais de 3, ou alguma com descrição)? Usado antes de planejar."""
    opcoes=[o for o in (botoes or {}).get('opcoes') or [] if isinstance(o,dict)]
    return len(opcoes)>MAX_BOTOES or any(o.get('descricao') for o in opcoes)


def com_descricoes(botoes,descricoes):
    """Cópia das opções com a descrição trocada pela de `descricoes` (id → texto) onde houver.
    É como o gateway põe a situação real da unidade na lista; o que o agente escreveu para essas opções é substituído."""
    if not botoes or not descricoes: return botoes
    return {**botoes,'opcoes':[{**o,'descricao':descricoes[o['id']]} if isinstance(o,dict) and o.get('id') in descricoes else o for o in botoes.get('opcoes') or []]}


def titulo_da_lista(botoes):
    """Texto do botão que abre a lista: o do agente, se couber; senão o padrão."""
    titulo=(botoes or {}).get('titulo_lista') or ''
    return titulo if 0<len(titulo)<=MAX_BOTAO_DA_LISTA and '|' not in titulo else BOTAO_DA_LISTA


def planejar(botoes,canal_tipo,ligado):
    """(forma, motivo, opcoes): forma 'botao' | 'lista' | 'texto' | None (só o balão); opcoes = as que serão mostradas."""
    if not botoes: return None,None,[]
    opcoes=[o for o in botoes.get('opcoes') or [] if isinstance(o,dict)]
    aproveitaveis=[];titulos=set();ids=set();invalido=bool(botoes.get('invalido'))
    for o in opcoes:
        titulo=' '.join(str(o.get('titulo') or '').split());ident=str(o.get('id') or '')
        if '\n' in str(o.get('titulo') or '') or '|' in titulo or not ID.fullmatch(ident) or ident in ids: invalido=True
        ids.add(ident)
        if not titulo or _chave(titulo) in titulos:
            invalido=True;continue
        titulos.add(_chave(titulo));boa={'id':ident,'titulo':titulo.replace('|','/')}
        descricao=' '.join(str(o.get('descricao') or '').split()).replace('|','/')
        if descricao: boa['descricao']=descricao
        aproveitaveis.append(boa)
    if invalido: return ('texto' if aproveitaveis else None),'invalido',aproveitaveis
    if not aproveitaveis: return None,None,[]
    if len(aproveitaveis)>MAX_BOTOES or any(o.get('descricao') for o in aproveitaveis):
        # Lista (B3). Mesmos planos B dos botões, mais a descrição comprida demais.
        if any(len(o['titulo'])>MAX_TITULO_LISTA for o in aproveitaveis): return 'texto','titulo_longo',aproveitaveis
        if any(len(o.get('descricao') or '')>MAX_DESCRICAO for o in aproveitaveis): return 'texto','descricao_longa',aproveitaveis
        if not ligado: return 'texto','desligado',aproveitaveis
        if canal_tipo!='uazapi': return 'texto','canal_sem_botao',aproveitaveis
        return 'lista',None,aproveitaveis
    if any(len(o['titulo'])>MAX_TITULO for o in aproveitaveis): return 'texto','titulo_longo',aproveitaveis
    if not ligado: return 'texto','desligado',aproveitaveis
    if canal_tipo!='uazapi': return 'texto','canal_sem_botao',aproveitaveis
    return 'botao',None,aproveitaveis


def numerado(texto,opcoes):
    """Plano B e histórico: o balão, uma linha em branco e as opções numeradas."""
    if not opcoes: return texto
    return texto+'\n\n'+'\n'.join(f'{i}. {o["titulo"]}'+(f' — {o["descricao"]}' if o.get('descricao') else '') for i,o in enumerate(opcoes,start=1))


def registro(forma,motivo,opcoes):
    """O que fica em mensagens.meta.botoes."""
    return {'opcoes':opcoes,'forma':forma,'motivo':motivo}
