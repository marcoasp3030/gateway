"""Busca no catálogo: sem acento, sem diferença de maiúsculas e tolerante a erro de digitação."""
import difflib,re,unicodedata

MAX_RESULTADOS=20
# Semelhança mínima entre uma palavra buscada e uma palavra do item para valer como "quase igual".
SEMELHANCA=0.8


def normalizar(texto):
    sem=''.join(c for c in unicodedata.normalize('NFKD',str(texto or '')) if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+',' ',sem.lower()).strip()


def _palavra(termo,palavras):
    """3 = palavra igual, 2 = começo ou trecho, 1 = parecida (erro de digitação), 0 = não achou."""
    if termo in palavras: return 3
    if any(termo in p for p in palavras) if len(termo)>=3 else any(p.startswith(termo) for p in palavras): return 2
    if len(termo)>=4 and any(abs(len(p)-len(termo))<=2 and difflib.SequenceMatcher(None,termo,p).ratio()>=SEMELHANCA for p in palavras): return 1
    return 0


def pontuar(q,item):
    """Pontuação do item para a busca (0 = não entra). Todas as palavras buscadas precisam aparecer
    no nome, na descrição ou na categoria; achar no nome vale mais."""
    termos=normalizar(q).split()
    if not termos: return 1
    nome=normalizar(item['nome']).split();resto=normalizar((item.get('descricao') or '')+' '+(item.get('categoria') or '')).split()
    total=0
    for t in termos:
        no_nome=_palavra(t,nome);no_resto=_palavra(t,resto)
        if not no_nome and not no_resto: return 0
        total+=max(no_nome*10,no_resto)
    # Empate: nome mais curto primeiro (o mais específico para o que foi buscado).
    return total*100-min(len(nome),50)


def buscar(itens,q,limite=MAX_RESULTADOS):
    pontuados=[(pontuar(q,i),i) for i in itens]
    achados=sorted((p for p in pontuados if p[0]>0),key=lambda p:(-p[0],p[1]['nome'],p[1]['id']))
    return [i for _,i in achados[:limite]]
