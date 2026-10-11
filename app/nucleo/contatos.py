"""Nome de exibição, telefone e canal do contato (PED1-c, docs/ESPEC_PED1.md seção 14).

Três origens de nome guardadas separadas no contato; o gateway devolve o resultado pronto e de onde veio.
Prioridade: cadastro (painel) > informado (agente, `nome_cliente`) > perfil (WhatsApp) > telefone."""
import re
import unicodedata

PRIORIDADE=('cadastro','informado','perfil')
ICONES={'uazapi':'whatsapp','meta':'whatsapp','site':'globe','api':'code','email':'mail','teams':'teams','luxbrain':'luxbrain'}
CANAIS_TELEFONE=('uazapi','meta')
ANONIMIZADO='Contato anonimizado'
MIN_NOME,MAX_NOME,MAX_PERFIL=2,60,100
# Oito dígitos ou mais, com ou sem separadores: parece telefone, não nome.
_TELEFONE=re.compile(r'(?:\d[\s().+\-]*){8,}')


def _limpo(texto):
    return ' '.join(str(texto).split()) if isinstance(texto,str) else ''


def validar_nome(valor):
    """Nome de cadastro ou informado. Devolve (nome limpo, None) ou (None, motivo).
    Regra aprovada: 2–60 caracteres, ao menos duas letras, sem número de telefone, sem emoji nem controle."""
    nome=_limpo(valor)
    if not MIN_NOME<=len(nome)<=MAX_NOME: return None,'tamanho'
    if _TELEFONE.search(nome): return None,'telefone'
    for c in nome:
        cat=unicodedata.category(c)
        if cat in ('So','Sk','Cc','Cf','Cs','Co','Cn') or 0xFE00<=ord(c)<=0xFE0F: return None,'caractere'
    if sum(c.isalpha() for c in nome)<2: return None,'letras'
    return nome,None


def nome_de_perfil(valor):
    """senderName do WhatsApp: guardado como veio (limpo, até 100), mesmo que não sirva para exibir."""
    nome=_limpo(valor)
    return nome[:MAX_PERFIL] or None


def _exibivel(nome):
    return bool(nome) and any(c.isalpha() for c in nome)


def e164(identificador,canal_tipo=None):
    """+<dígitos> quando o identificador é telefone: sempre nos canais de WhatsApp; nos demais, se for só número com 10–15 dígitos."""
    bruto=str(identificador or '').strip()
    if canal_tipo not in CANAIS_TELEFONE and not re.fullmatch(r'\+?[\d\s().\-]+',bruto): return None
    d=''.join(c for c in bruto if c.isdigit())
    return '+'+d if 10<=len(d)<=15 else None


def formatado(telefone_e164):
    """+55 11 94728-1513 (Brasil, celular e fixo); outros países ficam no E.164."""
    if not telefone_e164: return None
    d=telefone_e164[1:]
    if d.startswith('55') and len(d)==13: return f'+55 {d[2:4]} {d[4:9]}-{d[9:]}'
    if d.startswith('55') and len(d)==12: return f'+55 {d[2:4]} {d[4:8]}-{d[8:]}'
    return telefone_e164


def nomes(linha):
    """Os três nomes de uma linha com nome_cadastro/nome_informado/nome_perfil (nulos se o contato foi anonimizado).
    `nome` (coluna antiga, gravada pela imagem anterior à 032) vale como perfil quando nome_perfil está vazio."""
    if linha.get('anonimizado_em'): return {o:None for o in PRIORIDADE}
    return {'cadastro':linha.get('nome_cadastro'),'informado':linha.get('nome_informado'),'perfil':linha.get('nome_perfil') or linha.get('nome')}


def exibicao(linha,identificador=None,canal_tipo=None):
    """Campos de nome e telefone prontos para o painel. `linha` traz os três nomes e `anonimizado_em`."""
    if linha.get('anonimizado_em'):
        return {'nome_exibicao':ANONIMIZADO,'nome_origem':'anonimizado','nomes':nomes(linha),'telefone_e164':None,'telefone_formatado':None}
    n=nomes(linha);tel=e164(identificador,canal_tipo)
    origem=next((o for o in PRIORIDADE if _exibivel(n[o])),None)
    if origem: nome=n[origem]
    elif tel: nome,origem=formatado(tel),'telefone'
    elif identificador: nome,origem=str(identificador),'identificador'
    else: nome=None
    return {'nome_exibicao':nome,'nome_origem':origem,'nomes':n,'telefone_e164':tel,'telefone_formatado':formatado(tel)}


def nome_do_cliente(linha):
    """Só nome de verdade (nunca telefone): para o agente, o pedido e o aviso. None se não houver."""
    if not linha or linha.get('anonimizado_em'): return None,None
    n=nomes(linha)
    origem=next((o for o in PRIORIDADE if _exibivel(n[o])),None)
    return (n[origem],origem) if origem else (None,None)


async def nome_da_conversa(conn,tenant,conversa):
    """(nome, origem) do cliente da conversa, pela prioridade; cai no nome antigo da conversa se não houver contato."""
    r=await conn.fetchrow('''SELECT c.nome AS nome_conversa,ct.nome,ct.nome_cadastro,ct.nome_informado,ct.nome_perfil,ct.anonimizado_em,ct.id AS contato
        FROM conversas c LEFT JOIN contatos ct ON ct.tenant_id=c.tenant_id AND ct.id=c.contato_id
        WHERE c.tenant_id=$1 AND c.conversation_id=$2''',tenant,conversa)
    if not r: return None,None
    if r['contato']: return nome_do_cliente(dict(r))
    return (r['nome_conversa'],'perfil') if _exibivel(r['nome_conversa']) else (None,None)


async def gravar_informado(conn,tenant,conversa,nome):
    """nome_cliente devolvido pelo agente (já validado). Não toca no cadastro nem em contato anonimizado."""
    if not nome: return False
    return bool(await conn.fetchval('''UPDATE contatos ct SET nome_informado=$3,nome_informado_em=now(),atualizado_em=now()
        FROM conversas c WHERE c.tenant_id=$1 AND c.conversation_id=$2 AND ct.tenant_id=c.tenant_id AND ct.id=c.contato_id
        AND ct.anonimizado_em IS NULL AND ct.nome_informado IS DISTINCT FROM $3 RETURNING true''',tenant,conversa,nome))


def canal_info(canal_id,tipo,nome,tenant_teste,simulada):
    if not canal_id: return None
    return {'id':canal_id,'tipo':tipo,'nome':nome,'icone':ICONES.get(tipo),'teste':bool(tenant_teste or simulada)}
