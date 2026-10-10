"""Botões de resposta (docs/ESPEC_BOTOES.md, bloco B2).

O agente devolve só {"opcoes": [{"id","titulo"}]}; aqui se decide a forma (botão ou texto numerado)
e por quê. Nada aqui monta payload de canal, e nenhum caso vira erro: sempre há plano B em texto."""
import re
import unicodedata

MAX_BOTOES=3
MAX_OPCOES=10
MAX_TITULO=20
ID=re.compile(r'[a-z0-9_]{1,40}')
MOTIVOS=('canal_sem_botao','acima_do_limite','titulo_longo','invalido','provedor_recusou','provedor_incerto','desligado')
# Só com estas ações faz sentido oferecer escolha.
ACOES_COM_BOTAO=('nenhuma','pedido_fechado')


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
        lidas.append({'id':ident[:80],'titulo':o.get('titulo','').strip()[:200]})
    if len(lidas)>MAX_OPCOES: return {'opcoes':[],'invalido':True}
    if not lidas and not ruim: return None
    return {'opcoes':lidas,'invalido':True} if ruim else {'opcoes':lidas}


def planejar(botoes,canal_tipo,ligado):
    """(forma, motivo, opcoes): forma 'botao' | 'texto' | None (só o balão); opcoes = as que serão mostradas."""
    if not botoes: return None,None,[]
    opcoes=[o for o in botoes.get('opcoes') or [] if isinstance(o,dict)]
    aproveitaveis=[];titulos=set();ids=set();invalido=bool(botoes.get('invalido'))
    for o in opcoes:
        titulo=' '.join(str(o.get('titulo') or '').split());ident=str(o.get('id') or '')
        if '\n' in str(o.get('titulo') or '') or '|' in titulo or not ID.fullmatch(ident) or ident in ids: invalido=True
        ids.add(ident)
        if not titulo or _chave(titulo) in titulos:
            invalido=True;continue
        titulos.add(_chave(titulo));aproveitaveis.append({'id':ident,'titulo':titulo.replace('|','/')})
    if invalido: return ('texto' if aproveitaveis else None),'invalido',aproveitaveis
    if not aproveitaveis: return None,None,[]
    if len(aproveitaveis)>MAX_BOTOES: return 'texto','acima_do_limite',aproveitaveis
    if any(len(o['titulo'])>MAX_TITULO for o in aproveitaveis): return 'texto','titulo_longo',aproveitaveis
    if not ligado: return 'texto','desligado',aproveitaveis
    if canal_tipo!='uazapi': return 'texto','canal_sem_botao',aproveitaveis
    return 'botao',None,aproveitaveis


def numerado(texto,opcoes):
    """Plano B e histórico: o balão, uma linha em branco e as opções numeradas."""
    if not opcoes: return texto
    return texto+'\n\n'+'\n'.join(f'{i}. {o["titulo"]}' for i,o in enumerate(opcoes,start=1))


def registro(forma,motivo,opcoes):
    """O que fica em mensagens.meta.botoes."""
    return {'opcoes':opcoes,'forma':forma,'motivo':motivo}
