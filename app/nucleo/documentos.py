"""ARQ1-a1: documentos recebidos por API (docs/ESPEC_ARQ1.md, seções 13 e 14).

Capacidade transversal do núcleo, declarada na versão do modelo de agente (capacidades.documentos; ausente = desligada) e só
restringida pelo agente. Bytes cifrados (AES-256-GCM, chave de dados por documento embrulhada pela chave mestra
ARQUIVOS_CHAVE_MESTRA) num volume privado, isolados por empresa; resultado da leitura cifrado no banco. A leitura é do contêiner
`leitor` (sem rede para fora e sem credenciais): aqui só se decifra, manda os bytes e grava o que voltou. Retenção pelo prazo da
capacidade; apagar remove arquivo e resultado. Foto e PDF escaneado: `sem_texto` até o OCR (ARQ1-a2); campos por regra no ARQ1-a3."""
import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import datetime,timezone,timedelta
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from admin.servico import auditar

log=logging.getLogger('gateway.documentos')

FORMATOS=('pdf','xml','jpg','png','webp')
IMAGENS=('jpg','png','webp')
LIMITE_MAXIMO=15*1024*1024
PADRAO={'ativo':False,'tipos':list(FORMATOS),'max_bytes':10*1024*1024,'max_paginas':50,'retencao_dias':30}
TETO={'max_bytes':LIMITE_MAXIMO,'max_paginas':50,'retencao_dias':90}
ESPERA_PADRAO_S=8
ESPERA_MAXIMA_S=20
LEASE_S=150
MAX_TENTATIVAS=3
VERSAO_CHAVE=1


class Indisponivel(Exception):
    """Armazenamento sem chave mestra (ou volume) configurado: falha fechada."""


# ---------------- capacidade ----------------

def validar_capacidade(d,restringir_de=None):
    """capacidades.documentos validado. No agente (`restringir_de` = o do modelo) só pode restringir. ValueError com o campo."""
    if d is None: return None
    if not isinstance(d,dict) or set(d)-set(PADRAO): raise ValueError('capacidades.documentos')
    saida={}
    if 'ativo' in d:
        if not isinstance(d['ativo'],bool): raise ValueError('capacidades.documentos.ativo')
        saida['ativo']=d['ativo']
    if 'tipos' in d:
        if not isinstance(d['tipos'],list) or not d['tipos'] or any(t not in FORMATOS for t in d['tipos']): raise ValueError('capacidades.documentos.tipos')
        saida['tipos']=[t for t in FORMATOS if t in d['tipos']]
    for campo,minimo in (('max_bytes',1024),('max_paginas',1),('retencao_dias',1)):
        if campo in d:
            v=d[campo]
            if isinstance(v,bool) or not isinstance(v,int) or not minimo<=v<=TETO[campo]: raise ValueError(f'capacidades.documentos.{campo}')
            saida[campo]=v
    if restringir_de is not None:
        base={**PADRAO,**restringir_de}
        if saida.get('ativo') and not base['ativo']: raise ValueError('capacidades.documentos.ativo')
        if 'tipos' in saida and set(saida['tipos'])-set(base['tipos']): raise ValueError('capacidades.documentos.tipos')
        for campo in ('max_bytes','max_paginas','retencao_dias'):
            if campo in saida and saida[campo]>base[campo]: raise ValueError(f'capacidades.documentos.{campo}')
    return saida


def _ler_json(v):
    if v is None: return {}
    return json.loads(v) if isinstance(v,str) else dict(v)


async def capacidade(db,tenant,agente_id):
    """Capacidade efetiva de documentos do agente: a do modelo (versão em que o agente está), restringida pelo agente.
    Devolve o dicionário completo (com `ativo`) e a origem de cada parte."""
    row=await db.fetchrow("""SELECT a.capacidades AS do_agente,m.capacidades AS do_modelo FROM agentes a
        LEFT JOIN modelos_agente m ON m.id=a.modelo_id AND m.versao=a.modelo_versao WHERE a.tenant_id=$1 AND a.id=$2""",tenant,agente_id)
    if row is None: return None
    modelo=_ler_json(row['do_modelo']).get('documentos') or {}
    agente=_ler_json(row['do_agente']).get('documentos') or {}
    efetiva={**PADRAO,**modelo}
    efetiva['ativo']=bool(modelo.get('ativo')) and agente.get('ativo',True)
    if 'tipos' in agente: efetiva['tipos']=[t for t in efetiva['tipos'] if t in agente['tipos']]
    for campo in ('max_bytes','max_paginas','retencao_dias'):
        if campo in agente: efetiva[campo]=min(efetiva[campo],agente[campo])
    return {**efetiva,'do_modelo':modelo,'do_agente':agente}


# ---------------- formato e nome ----------------

def formato_real(dados):
    """Formato pela assinatura dos bytes (nunca pelo nome ou pelo MIME informado). None = não aceito."""
    inicio=dados[:1024]
    if b'%PDF-' in inicio: return 'pdf'
    if dados[:3]==b'\xff\xd8\xff': return 'jpg'
    if dados[:8]==b'\x89PNG\r\n\x1a\n': return 'png'
    if dados[:4]==b'RIFF' and dados[8:12]==b'WEBP': return 'webp'
    cabeca=dados[:4096].lstrip(b'\xef\xbb\xbf \t\r\n')
    if cabeca[:1]==b'<' and b'portalfiscal.inf.br/nfe' in cabeca: return 'xml'
    return None


def nome_seguro(nome,formato):
    """Nome para mostrar: sem caminho nem caractere de controle, até 120 caracteres, com a extensão do formato real."""
    base=os.path.basename(str(nome or '').replace('\\','/'))
    base=''.join(c for c in base if c.isprintable() and c not in '<>:"|?*').strip(' .') or 'documento'
    raiz,_,ext=base.rpartition('.')
    raiz=raiz if raiz and ext else base
    return (raiz[:110] or 'documento')+'.'+formato


# ---------------- cifra e volume ----------------

def _chave_mestra():
    bruto=os.getenv('ARQUIVOS_CHAVE_MESTRA','').strip()
    if not bruto: raise Indisponivel()
    try: chave=base64.urlsafe_b64decode(bruto+'='*(-len(bruto)%4))
    except ValueError: raise Indisponivel() from None
    if len(chave)!=32: raise Indisponivel()
    return chave


def _pasta():
    return os.getenv('ARQUIVOS_DIR','/dados/arquivos')


def disponivel():
    try: _chave_mestra()
    except Indisponivel: return False
    return os.path.isdir(_pasta()) and os.access(_pasta(),os.W_OK)


def _aad(tenant,anexo_id,parte): return f'{tenant}:{anexo_id}:{parte}'.encode()


def cifrar(tenant,anexo_id,dados):
    """(chave de dados embrulhada, bytes cifrados). Nonce aleatório de 96 bits por cifra; o par empresa+anexo autenticado."""
    mestra=_chave_mestra()
    dk=AESGCM.generate_key(bit_length=256)
    n1=secrets.token_bytes(12);embrulhada=n1+AESGCM(mestra).encrypt(n1,dk,_aad(tenant,anexo_id,'chave'))
    n2=secrets.token_bytes(12);blob=n2+AESGCM(dk).encrypt(n2,dados,_aad(tenant,anexo_id,'original'))
    return embrulhada,blob


def _dk(tenant,anexo_id,embrulhada):
    return AESGCM(_chave_mestra()).decrypt(embrulhada[:12],embrulhada[12:],_aad(tenant,anexo_id,'chave'))


def decifrar(tenant,anexo_id,embrulhada,blob,parte='original'):
    return AESGCM(_dk(tenant,anexo_id,embrulhada)).decrypt(blob[:12],blob[12:],_aad(tenant,anexo_id,parte))


def cifrar_com(tenant,anexo_id,embrulhada,dados,parte):
    n=secrets.token_bytes(12)
    return n+AESGCM(_dk(tenant,anexo_id,embrulhada)).encrypt(n,dados,_aad(tenant,anexo_id,parte))


def caminho_de(tenant,anexo_id):
    # Pasta pela empresa em hash: nem o id da empresa nem o nome do arquivo aparecem no caminho.
    return os.path.join(hashlib.sha256(tenant.encode()).hexdigest()[:16],anexo_id)


def gravar_arquivo(relativo,blob):
    final=os.path.join(_pasta(),relativo);os.makedirs(os.path.dirname(final),mode=0o700,exist_ok=True)
    temporario=final+'.parcial'
    with open(temporario,'wb') as f: f.write(blob);f.flush();os.fsync(f.fileno())
    os.chmod(temporario,0o600);os.replace(temporario,final)


def ler_arquivo(relativo):
    with open(os.path.join(_pasta(),relativo),'rb') as f: return f.read()


def apagar_arquivo(relativo):
    if not relativo: return
    try: os.remove(os.path.join(_pasta(),relativo))
    except FileNotFoundError: pass


# ---------------- registro ----------------

def novo_id(): return 'anx_'+uuid.uuid4().hex[:24]


async def criar(db,tenant,agente_id,dados,nome,modo,origem,chave_id=None,referencia=None,idempotencia=None,retencao_dias=30,formato=None):
    """Grava o original cifrado e o registro em `processando`. Devolve a linha (ou a já existente, com a mesma idempotência)."""
    if idempotencia:
        ja=await db.fetchrow('SELECT * FROM anexos WHERE tenant_id=$1 AND chave_id IS NOT DISTINCT FROM $2 AND idempotencia=$3',tenant,chave_id,idempotencia)
        if ja: return ja,False
    anexo_id=novo_id();relativo=caminho_de(tenant,anexo_id)
    embrulhada,blob=cifrar(tenant,anexo_id,dados)
    await asyncio.to_thread(gravar_arquivo,relativo,blob)
    try:
        row=await db.fetchrow('''INSERT INTO anexos(id,tenant_id,agente_id,origem,chave_id,modo,nome,formato,tamanho_bytes,sha256,referencia,idempotencia,
            chave_versao,chave_cifrada,caminho,expira_em) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,now()+make_interval(days=>$16)) RETURNING *''',
            anexo_id,tenant,agente_id,origem,chave_id,modo,nome,formato,len(dados),hashlib.sha256(dados).hexdigest(),referencia,idempotencia,
            VERSAO_CHAVE,embrulhada,relativo,int(retencao_dias))
    except Exception:
        await asyncio.to_thread(apagar_arquivo,relativo);raise
    return row,True


def resultado(anexo):
    """Resultado da leitura decifrado (dicionário) ou None."""
    if not anexo['resultado'] or not anexo['chave_cifrada']: return None
    return json.loads(decifrar(anexo['tenant_id'],anexo['id'],anexo['chave_cifrada'],anexo['resultado'],'resultado'))


# ---------------- leitura ----------------

async def ler_no_leitor(http,formato,dados):
    """Manda os bytes ao contêiner `leitor` (rede interna). Devolve o JSON dele. Exceção = leitor fora do ar (tenta de novo depois)."""
    url=os.getenv('LEITOR_URL','http://leitor:8090').rstrip('/')+'/ler'
    r=await http.post(url,content=dados,headers={'X-Leitor-Token':os.getenv('LEITOR_TOKEN',''),'X-Formato':formato,'Content-Type':'application/octet-stream'},
                      timeout=90)
    if r.status_code!=200: raise RuntimeError('leitor respondeu %s'%r.status_code)
    return r.json()


def _tipo_documento(lido):
    nfe=lido.get('nfe') or {}
    return {'55':'nfe','65':'nfce'}.get(nfe.get('modelo')) if nfe else None


async def processar(state,anexo_id):
    """Lê o documento (uma vez por vez por documento, com prazo de posse). Idempotente: quem não pegar a posse não faz nada."""
    db=state.db
    a=await db.fetchrow("""UPDATE anexos SET processando_ate=now()+make_interval(secs=>$2),tentativas=tentativas+1 WHERE id=$1 AND estado='processando'
        AND (processando_ate IS NULL OR processando_ate<now()) RETURNING *""",anexo_id,LEASE_S)
    if a is None: return None
    estado=motivo=None;lido=None
    try:
        if a['formato'] in IMAGENS:
            # OCR é o ARQ1-a2: a imagem fica guardada e marcada; será lida quando o OCR entrar.
            estado,motivo='sem_texto','imagem_sem_ocr'
        else:
            dados=decifrar(a['tenant_id'],a['id'],a['chave_cifrada'],await asyncio.to_thread(ler_arquivo,a['caminho']))
            r=await ler_no_leitor(state.http,a['formato'],dados)
            if not r.get('ok'): estado,motivo='rejeitado',str(r.get('motivo') or 'falha_na_leitura')[:60]
            else:
                lido={k:r[k] for k in ('formato','paginas','nfe') if k in r}
                cap=await capacidade(db,a['tenant_id'],a['agente_id']) if a['agente_id'] else PADRAO
                if len(lido['paginas'])>((cap or PADRAO)['max_paginas']): estado,motivo,lido='rejeitado','paginas_demais',None
                elif any(p['tem_texto'] for p in lido['paginas']): estado='pronto'
                else: estado,motivo='sem_texto','ocr_necessario'
    except Exception:
        log.warning('Leitura de documento falhou (tentativa %s)',a['tentativas'])
        if a['tentativas']<MAX_TENTATIVAS:
            await db.execute('UPDATE anexos SET processando_ate=now()+make_interval(secs=>$2) WHERE id=$1',anexo_id,30*a['tentativas'])
            return None
        estado,motivo='falhou','leitor_indisponivel'
    blob=cifrar_com(a['tenant_id'],a['id'],a['chave_cifrada'],json.dumps(lido,ensure_ascii=False).encode(),'resultado') if lido else None
    async with db.acquire() as conn,conn.transaction():
        row=await conn.fetchrow("""UPDATE anexos SET estado=$2,motivo=$3,resultado=$4,paginas=$5,tipo_documento=$6,processado_em=now(),processando_ate=NULL
            WHERE id=$1 AND estado='processando' RETURNING *""",anexo_id,estado,motivo,blob,len(lido['paginas']) if lido else None,_tipo_documento(lido or {}))
        if row is None: return None
        await auditar(conn,row['tenant_id'],'sistema','documento.'+estado,row['id'])
        from nucleo.webhooks import emitir
        await emitir(conn,row['tenant_id'],'documento.pronto' if estado in ('pronto','sem_texto') else 'documento.falhou',evento(row))
    return row


def evento(a):
    """Webhook de retorno: só ids e estado, sem conteúdo (o conteúdo é lido pela rota de consulta)."""
    return {'anexo_id':a['id'],'estado':a['estado'],'motivo':a['motivo'],'modo':a['modo'],'referencia':a['referencia'],'agente_id':a['agente_id'],
            'conversation_id':a['conversation_id'],'formato':a['formato'],'paginas':a['paginas'],'tipo_documento':a['tipo_documento']}


async def aguardar(db,ids,segundos):
    """Espera os documentos saírem de `processando`, até `segundos`. Devolve as linhas atuais."""
    fim=asyncio.get_running_loop().time()+max(0,segundos)
    while True:
        rows=await db.fetch('SELECT * FROM anexos WHERE id=ANY($1::text[])',list(ids))
        if all(r['estado']!='processando' for r in rows) or asyncio.get_running_loop().time()>=fim: return rows
        await asyncio.sleep(0.25)


async def apagar(conn,a,estado='apagado'):
    """Remove arquivo e resultado (LGPD/retenção); o registro fica só com metadados mínimos."""
    await asyncio.to_thread(apagar_arquivo,a['caminho'])
    await conn.execute("""UPDATE anexos SET estado=$2,resultado=NULL,chave_cifrada=NULL,caminho=NULL,nome='documento.'||formato,referencia=NULL,
        apagado_em=now() WHERE id=$1""",a['id'],estado)


async def varrer(state,limite=5):
    """Agendador: documentos parados (posse vencida) voltam a ser lidos; vencidos pela retenção são apagados."""
    for r in await state.db.fetch("""SELECT id FROM anexos WHERE estado='processando' AND (processando_ate IS NULL OR processando_ate<now())
            ORDER BY criado_em LIMIT $1""",limite):
        try: await processar(state,r['id'])
        except Exception: log.warning('Documento não processado nesta varredura')
    for a in await state.db.fetch('SELECT * FROM anexos WHERE apagado_em IS NULL AND expira_em<now() ORDER BY expira_em LIMIT 100'):
        async with state.db.acquire() as conn,conn.transaction():
            await apagar(conn,a,'expirado')
            await auditar(conn,a['tenant_id'],'sistema','documento.expirado',a['id'])


async def apagar_da_conversa(conn,tenant,conversa):
    """LGPD: anonimização do contato apaga todos os documentos da conversa."""
    for a in await conn.fetch('SELECT * FROM anexos WHERE tenant_id=$1 AND conversation_id=$2 AND apagado_em IS NULL',tenant,conversa):
        await apagar(conn,a)


# ---------------- formatos de saída ----------------

def documento(a,lido,completo=True,limite=256*1024):
    """JSON do documento para quem enviou (modo extrair e consulta). Grande demais → só a estrutura, `parcial: true` (texto por página na consulta)."""
    base={'anexo_id':a['id'],'estado':a['estado'],'motivo':a['motivo'],'modo':a['modo'],'referencia':a['referencia'],'conversation_id':a['conversation_id'],
          'nome':a['nome'],'formato':a['formato'],'tamanho_bytes':a['tamanho_bytes'],'paginas':a['paginas'],'tipo_documento':a['tipo_documento'],
          'recebido_em':a['criado_em'],'expira_em':a['expira_em'],'verificacao_pagamento':None}
    if not lido or not completo: return {**base,'conteudo':None}
    paginas=[{'pagina':p['pagina'],'origem':p['origem'],'texto':p['texto'],'tabelas':[{'id':f'tab_{p["pagina"]}_{n}','linhas':t['linhas']} for n,t in enumerate(p['tabelas'],1)]}
             for p in lido['paginas']]
    conteudo={'paginas':paginas,'campos':campos(lido),'avisos':[]}
    if len(json.dumps(conteudo,ensure_ascii=False,default=str))>limite:
        conteudo={'paginas':[{'pagina':p['pagina'],'origem':p['origem'],'caracteres':len(p['texto']),'tabelas':len(p['tabelas'])} for p in paginas],
                  'campos':conteudo['campos'],'avisos':[],'parcial':True}
    return {**base,'conteudo':conteudo}


def _c(valor,confianca='lido_do_texto',fonte='xml'):
    return {'valor':valor,'confianca':confianca,'fonte':fonte} if valor not in (None,'') else None


def campos(lido):
    """Campos por tipo (ARQ1-a1: só os do XML da NF-e, `lido_do_texto`; a validação por dígito verificador é do ARQ1-a3)."""
    nfe=lido.get('nfe')
    if not nfe: return {}
    c={'chave_acesso':_c(nfe.get('chave')),'modelo':_c(nfe.get('modelo')),'serie':_c(nfe.get('serie')),'numero':_c(nfe.get('numero')),
       'emissao':_c(nfe.get('emissao')),'emitente_nome':_c((nfe.get('emitente') or {}).get('nome')),'emitente_documento':_c((nfe.get('emitente') or {}).get('documento')),
       'destinatario_nome':_c((nfe.get('destinatario') or {}).get('nome')),'destinatario_documento':_c((nfe.get('destinatario') or {}).get('documento')),
       'valor_total':_c(nfe.get('valor_total')),'protocolo':_c((nfe.get('protocolo') or {}).get('numero')),'situacao':_c((nfe.get('protocolo') or {}).get('motivo'))}
    return {k:v for k,v in c.items() if v}


def mascarar_cpf(campos_):
    """Pacote do agente: CPF de pessoa física mascarado (decisão 31); CNPJ inteiro."""
    saida={}
    for k,v in campos_.items():
        valor=v['valor']
        if k.endswith('_documento') and isinstance(valor,str) and len(''.join(ch for ch in valor if ch.isdigit()))==11:
            d=''.join(ch for ch in valor if ch.isdigit());valor=f'***.{d[3:6]}.{d[6:9]}-**'
        saida[k]={**v,'valor':valor}
    return saida


def para_o_pacote(a,lido,fuso):
    """Resumo compacto do anexo para o agente: nunca o texto inteiro. Campos só `validado` e `lido_do_texto`, com a marca."""
    texto=' '.join(' '.join(p['texto'].split()) for p in (lido or {}).get('paginas',[]) if p.get('texto'))
    todos=campos(lido or {})
    bons={k:{'valor':v['valor'],'confianca':v['confianca']} for k,v in mascarar_cpf(todos).items() if v['confianca'] in ('validado','lido_do_texto')}
    return {'anexo_id':a['id'],'estado':a['estado'],**({'motivo':a['motivo']} if a['motivo'] else {}),'nome':a['nome'],'formato':a['formato'],
            'paginas':a['paginas'],'origem':_origem(lido),'tipo_documento':a['tipo_documento'] or ('outro' if a['estado']=='pronto' else None),
            'recebido_em':a['criado_em'].astimezone(fuso).isoformat(timespec='seconds'),'campos':bons,'outros_campos_a_conferir':len(todos)-len(bons),
            'tabelas':sum(len(p['tabelas']) for p in (lido or {}).get('paginas',[])),'avisos':[],'verificacao_pagamento':None,
            'trecho_inicial':texto[:300]+('…' if len(texto)>300 else '') if texto else None}


def _origem(lido):
    if not lido: return None
    origens={p['origem'] for p in lido['paginas']}
    return 'xml' if 'xml' in origens else ('texto' if origens=={'texto'} else ('misto' if 'texto' in origens else 'sem_texto'))


MAX_ANEXOS_PACOTE=5
MAX_BLOCO_PACOTE=3*1024


async def do_pacote(db,tenant,conversa,atendimento_id,fuso):
    """Bloco `anexos`: os do atendimento atual, do mais recente para o mais antigo, até 5 e 3 KiB (os mais antigos saem primeiro)."""
    rows=await db.fetch("""SELECT * FROM anexos WHERE tenant_id=$1 AND conversation_id=$2 AND apagado_em IS NULL
        AND ($3::text IS NULL OR atendimento_id=$3) ORDER BY criado_em DESC LIMIT $4""",tenant,conversa,atendimento_id,MAX_ANEXOS_PACOTE)
    saida=[]
    for a in rows:
        try: lido=resultado(a)
        except Exception: lido=None
        item=para_o_pacote(a,lido,fuso)
        if len(json.dumps(saida+[item],ensure_ascii=False,default=str))>MAX_BLOCO_PACOTE:
            item={**item,'trecho_inicial':None}
            if len(json.dumps(saida+[item],ensure_ascii=False,default=str))>MAX_BLOCO_PACOTE: break
        saida.append(item)
    return saida
