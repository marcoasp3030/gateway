"""LOTE1-a (docs/ESPEC_NFSE1_LOTE1.md): notas em lote para um agente com a capacidade de documentos.

O painel cria o lote e manda os arquivos um a um (PDF, XML ou ZIP com PDFs/XMLs). Cada nota vira um anexo do ARQ1 (cifrado, mesma
retenção, origem 'lote'), mas a leitura NÃO é imediata: o agendador lê uma por vez e cede a vez sempre que há documento de atendimento
esperando (o turno dos agentes nunca espera o lote). Depois de lido, o arquivo ganha uma chave de junção (hash da chave de acesso, ou
do número + CNPJ/CPF do emitente) para juntar o PDF e o XML da mesma nota. Fechar o lote = não aceita mais arquivo; quando o último
for lido, o lote fica `concluido`. A conciliação com a planilha de pagamentos é o LOTE1-b."""
import asyncio
import hashlib
import io
import logging
import stat
import time
import uuid
import zipfile
from nucleo import documentos as D
from admin.servico import auditar

log=logging.getLogger('gateway.lotes')

FORMATOS=('pdf','xml')
MAX_NOTAS=200
MAX_ARQUIVOS=2*MAX_NOTAS
MAX_BYTES_LOTE=200*1024*1024
ZIP_ENTRADAS=200
ZIP_DESCOMPACTADO=100*1024*1024
ZIP_TAXA=50
SEGUNDOS_POR_ARQUIVO=1.5
LIMITES={'notas':MAX_NOTAS,'arquivos':MAX_ARQUIVOS,'bytes_total':MAX_BYTES_LOTE,'zip_entradas':ZIP_ENTRADAS,'zip_descompactado':ZIP_DESCOMPACTADO,
         'formatos':list(FORMATOS)}
# Estado do anexo → estado do arquivo no lote.
DO_ANEXO={'pronto':'lido','sem_texto':'sem_texto','rejeitado':'recusado','falhou':'falhou','expirado':'falhou','apagado':'falhou'}
CONTADORES=('na_fila','lido','sem_texto','recusado','falhou','duplicado')


class Recusa(Exception):
    """Recusa do envio com status HTTP, campo e motivo seguro (mesmo formato de erro dos documentos)."""
    def __init__(self,status,campo=None,motivo=None,**extra): self.status,self.campo,self.motivo,self.extra=status,campo,motivo,extra


def novo_id(prefixo): return f'{prefixo}_'+uuid.uuid4().hex[:24]


def _digitos(v): return ''.join(c for c in str(v or '') if c.isdigit())


def juncao(campos):
    """Hash que junta PDF e XML da mesma nota: a chave de acesso (44 dígitos NF-e, 50 NFS-e nacional); sem ela, número + documento do
    emitente/prestador. None quando não há como juntar (a nota fica sozinha)."""
    def valor(k): return (campos.get(k) or {}).get('valor')
    chave=_digitos(valor('chave_acesso'))
    if len(chave) in (44,50): base='ch:'+chave
    else:
        numero=_digitos(valor('numero')).lstrip('0')
        doc=_digitos(valor('emitente_documento') or valor('prestador_documento'))
        if not numero or len(doc) not in (11,14): return None
        base=f'nd:{numero}:{doc}'
    return hashlib.sha256(base.encode()).hexdigest()[:32]


# ---------------- envio ----------------

def _entradas_do_zip(dados,max_bytes):
    """[(nome, bytes ou None, motivo)] das entradas de um ZIP, com as defesas contra "zip bomb" e caminho perigoso.
    Recusa o ZIP inteiro (Recusa) quando ele é inválido, protegido ou grande demais; entrada ruim só é listada como recusada."""
    try: z=zipfile.ZipFile(io.BytesIO(dados))
    except (zipfile.BadZipFile,ValueError): raise Recusa(400,'arquivo','zip_invalido') from None
    with z:
        infos=[i for i in z.infolist() if not i.is_dir()]
        if not infos: raise Recusa(400,'arquivo','zip_vazio')
        if len(infos)>ZIP_ENTRADAS: raise Recusa(400,'arquivo','zip_entradas_demais',maximo=ZIP_ENTRADAS)
        if sum(i.file_size for i in infos)>ZIP_DESCOMPACTADO: raise Recusa(400,'arquivo','zip_grande_demais',maximo=ZIP_DESCOMPACTADO)
        if any(i.flag_bits & 0x1 for i in infos): raise Recusa(400,'arquivo','zip_protegido')
        saida=[];total=0
        for i in infos:
            nome=i.filename.replace('\\','/')
            base=nome.rsplit('/',1)[-1] or 'documento'
            if nome.startswith('/') or '..' in nome.split('/') or ':' in nome.split('/')[0]: saida.append((base,None,'zip_caminho_invalido'));continue
            if stat.S_ISLNK(i.external_attr>>16): saida.append((base,None,'zip_link'));continue
            if i.file_size>ZIP_TAXA*max(i.compress_size,1): saida.append((base,None,'zip_taxa_de_compressao'));continue
            if i.file_size>max_bytes: saida.append((base,None,'tamanho'));continue
            try:
                with z.open(i) as f: conteudo=f.read(max_bytes+1)
            except Exception: saida.append((base,None,'zip_entrada_ilegivel'));continue
            total+=len(conteudo)
            # O tamanho declarado pode mentir: vale o lido (com teto).
            if len(conteudo)>max_bytes: saida.append((base,None,'tamanho'));continue
            if total>ZIP_DESCOMPACTADO: raise Recusa(400,'arquivo','zip_grande_demais',maximo=ZIP_DESCOMPACTADO)
            saida.append((base,conteudo,None))
        return saida


def _formato(dados,aceitos):
    """(formato, motivo de recusa)."""
    f=D.formato_real(dados)
    if f is None and 'xml' in aceitos and D.parece_xml(dados): return None,'xml_nao_e_nfe'
    if f is None or f not in aceitos: return None,'tipo_nao_aceito'
    return f,None


async def _capacidade(db,tenant,agente_id):
    ag=await db.fetchrow('SELECT a.status,t.status AS empresa FROM agentes a JOIN tenants t ON t.id=a.tenant_id WHERE a.tenant_id=$1 AND a.id=$2',tenant,agente_id)
    if ag is None: raise Recusa(404,'agente_id')
    if ag['empresa']!='ativo': raise Recusa(409,'tenant','empresa_inativa')
    if ag['status']!='ativo': raise Recusa(409,'agente_id','agente_inativo')
    cap=await D.capacidade(db,tenant,agente_id)
    if not cap or not cap['ativo']: raise Recusa(409,'agente_id','capacidade_desligada')
    aceitos=[t for t in FORMATOS if t in cap['tipos']]
    if not aceitos: raise Recusa(409,'agente_id','capacidade_sem_pdf_nem_xml')
    return cap,aceitos


async def criar(db,tenant,agente_id,actor,nome=None,origem='painel'):
    cap,_=await _capacidade(db,tenant,agente_id)
    if not D.disponivel(): raise Recusa(503,motivo='armazenamento_indisponivel')
    lid=novo_id('lot')
    async with db.acquire() as conn,conn.transaction():
        row=await conn.fetchrow("""INSERT INTO lotes(id,tenant_id,agente_id,nome,origem,criado_por,expira_em) VALUES ($1,$2,$3,$4,$5,$6,now()+make_interval(days=>$7))
            RETURNING *""",lid,tenant,agente_id,nome,origem,actor,int(cap['retencao_dias']))
        await auditar(conn,tenant,actor,'lote.criado',lid)
    return row


async def receber(db,tenant,lote_id,dados,nome,actor):
    """Um arquivo do lote (PDF, XML ou ZIP). Devolve os itens criados ({arquivo_id, nome, zip, estado, motivo}).
    Arquivo solto inválido: Recusa (nada gravado). ZIP: cada entrada vira um item, as recusadas listadas com o motivo."""
    lote=await db.fetchrow('SELECT * FROM lotes WHERE tenant_id=$1 AND id=$2',tenant,lote_id)
    if lote is None or lote['estado']=='apagado': raise Recusa(404,'lote_id')
    if lote['estado']!='aberto': raise Recusa(409,'lote_id','lote_fechado')
    cap,aceitos=await _capacidade(db,tenant,lote['agente_id'])
    if not D.disponivel(): raise Recusa(503,motivo='armazenamento_indisponivel')
    if not dados: raise Recusa(400,'arquivo','vazio')
    max_bytes=cap['max_bytes']
    if len(dados)>max_bytes: raise Recusa(413,'arquivo','tamanho',max_bytes=max_bytes)
    nome_zip=None
    if dados[:4]==b'PK\x03\x04':
        nome_zip=D.nome_seguro(nome,'zip')
        entradas=_entradas_do_zip(dados,max_bytes)
    else:
        f,motivo=_formato(dados,aceitos)
        if motivo: raise Recusa(400,'arquivo',motivo,tipos=aceitos)
        entradas=[(nome,dados,None)]
    itens=[]
    async with db.acquire() as conn,conn.transaction():
        # Trava o lote: dois envios ao mesmo tempo não passam juntos do limite.
        lote=await conn.fetchrow('SELECT * FROM lotes WHERE tenant_id=$1 AND id=$2 FOR UPDATE',tenant,lote_id)
        if lote['estado']!='aberto': raise Recusa(409,'lote_id','lote_fechado')
        validos=await conn.fetchval("SELECT count(*) FROM lote_arquivos WHERE lote_id=$1 AND estado NOT IN ('recusado','duplicado')",lote_id)
        vistos={r['sha256'] for r in await conn.fetch("SELECT sha256 FROM lote_arquivos WHERE lote_id=$1 AND sha256 IS NOT NULL AND estado<>'duplicado'",lote_id)}
        bytes_total=lote['bytes_total']
        novos=[e for e in entradas if e[1] is not None]
        if validos+len(novos)>MAX_ARQUIVOS: raise Recusa(409,'arquivo','limite_do_lote',maximo=MAX_ARQUIVOS,restam=max(MAX_ARQUIVOS-validos,0))
        if bytes_total+sum(len(e[1]) for e in novos)>MAX_BYTES_LOTE:
            raise Recusa(409,'arquivo','limite_do_lote',maximo_bytes=MAX_BYTES_LOTE,restam_bytes=max(MAX_BYTES_LOTE-bytes_total,0))
        for nome_e,conteudo,motivo in entradas:
            formato=None
            if conteudo is not None and not motivo:
                formato,motivo=_formato(conteudo,aceitos)
            arquivo_id=novo_id('lar')
            if motivo:
                ext=''.join(c for c in (nome_e.rsplit('.',1)[-1].lower() if '.' in nome_e else '') if c.isalnum())[:5] or 'bin'
                nome_mostrado=D.nome_seguro(nome_e,ext)
                await conn.execute("""INSERT INTO lote_arquivos(id,tenant_id,lote_id,nome,zip,estado,motivo) VALUES ($1,$2,$3,$4,$5,'recusado',$6)""",
                                   arquivo_id,tenant,lote_id,nome_mostrado,nome_zip,motivo)
                itens.append({'arquivo_id':arquivo_id,'nome':nome_mostrado,'zip':nome_zip,'estado':'recusado','motivo':motivo});continue
            sha=hashlib.sha256(conteudo).hexdigest();nome_ok=D.nome_seguro(nome_e,formato)
            if sha in vistos:
                await conn.execute("""INSERT INTO lote_arquivos(id,tenant_id,lote_id,nome,zip,formato,tamanho_bytes,sha256,estado,motivo)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'duplicado','arquivo_repetido')""",arquivo_id,tenant,lote_id,nome_ok,nome_zip,formato,len(conteudo),sha)
                itens.append({'arquivo_id':arquivo_id,'nome':nome_ok,'zip':nome_zip,'estado':'duplicado','motivo':'arquivo_repetido'});continue
            vistos.add(sha)
            a,_=await D.criar(conn,tenant,lote['agente_id'],conteudo,nome_ok,'extrair','lote',referencia=lote_id,retencao_dias=cap['retencao_dias'],formato=formato)
            await conn.execute("""INSERT INTO lote_arquivos(id,tenant_id,lote_id,anexo_id,nome,zip,formato,tamanho_bytes,sha256,estado)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'na_fila')""",arquivo_id,tenant,lote_id,a['id'],nome_ok,nome_zip,formato,len(conteudo),sha)
            bytes_total+=len(conteudo)
            itens.append({'arquivo_id':arquivo_id,'nome':nome_ok,'zip':nome_zip,'estado':'na_fila','motivo':None})
        await conn.execute('UPDATE lotes SET bytes_total=$2 WHERE id=$1',lote_id,bytes_total)
        recebidos=sum(1 for i in itens if i['estado']=='na_fila')
        if recebidos: await auditar(conn,tenant,actor,'lote.arquivos_recebidos',lote_id,{'arquivos':recebidos})
    return itens


# ---------------- leitura em segundo plano ----------------

async def _apos_leitura(db,arquivo_id,anexo):
    """Copia o estado do anexo para o arquivo do lote e calcula a chave de junção."""
    estado=DO_ANEXO.get(anexo['estado'])
    if estado is None: return
    chave=None
    if estado=='lido':
        try: chave=juncao(D.campos(D.resultado(anexo) or {}))
        except Exception: chave=None
    await db.execute("""UPDATE lote_arquivos SET estado=$2,motivo=$3,juncao=$4,lido_em=now() WHERE id=$1 AND estado='na_fila'""",
                     arquivo_id,estado,anexo['motivo'],chave)


async def _concluir(db):
    """Lote fechado sem arquivo na fila → concluido. Lote vencido pela retenção → expirado (os anexos o ARQ1 já apaga)."""
    for r in await db.fetch("""SELECT l.id,l.tenant_id FROM lotes l WHERE l.estado='lendo'
            AND NOT EXISTS(SELECT 1 FROM lote_arquivos f WHERE f.lote_id=l.id AND f.estado='na_fila') LIMIT 50"""):
        async with db.acquire() as conn,conn.transaction():
            if await conn.fetchval("UPDATE lotes SET estado='concluido',concluido_em=now() WHERE id=$1 AND estado='lendo' RETURNING id",r['id']):
                await auditar(conn,r['tenant_id'],'sistema','lote.concluido',r['id'])
    for r in await db.fetch("SELECT id,tenant_id FROM lotes WHERE estado IN ('aberto','lendo','concluido') AND expira_em<now() LIMIT 50"):
        async with db.acquire() as conn,conn.transaction():
            await conn.execute("UPDATE lote_arquivos SET estado='falhou',motivo='expirado' WHERE lote_id=$1 AND estado='na_fila'",r['id'])
            await conn.execute("UPDATE lotes SET estado='expirado' WHERE id=$1",r['id'])
            await auditar(conn,r['tenant_id'],'sistema','lote.expirado',r['id'])


async def ciclo(state,prazo_s=20):
    """Agendador: lê arquivos de lote, um por vez, por até `prazo_s`. Para assim que houver documento de atendimento esperando."""
    db=state.db;fim=time.monotonic()+prazo_s;lidos=0
    # Anexo já resolvido (queda entre a leitura e a cópia do estado): acerta antes de pegar o próximo.
    for r in await db.fetch("""SELECT f.id,a.* FROM lote_arquivos f JOIN anexos a ON a.id=f.anexo_id
            WHERE f.estado='na_fila' AND a.estado<>'processando' LIMIT 50"""):
        await _apos_leitura(db,r['id'],r)
    while time.monotonic()<fim:
        if await db.fetchval("SELECT EXISTS(SELECT 1 FROM anexos WHERE estado='processando' AND origem<>'lote')"): break
        r=await db.fetchrow("""SELECT f.id,f.anexo_id FROM lote_arquivos f JOIN anexos a ON a.id=f.anexo_id
            WHERE f.estado='na_fila' AND a.estado='processando' AND (a.processando_ate IS NULL OR a.processando_ate<now())
            ORDER BY f.criado_em,f.id LIMIT 1""")
        if r is None: break
        try: anexo=await D.processar(state,r['anexo_id'])
        except Exception:
            log.warning('Arquivo de lote não lido neste ciclo');break
        if anexo is None: break   # falha passageira (o ARQ1 marca nova tentativa) ou outro leitor pegou: próximo ciclo
        await _apos_leitura(db,r['id'],anexo);lidos+=1
        await asyncio.sleep(0)
    await _concluir(db)
    return lidos


# ---------------- consulta ----------------

async def resumo(db,lote):
    n={r['estado']:r['n'] for r in await db.fetch('SELECT estado,count(*) AS n FROM lote_arquivos WHERE lote_id=$1 GROUP BY 1',lote['id'])}
    arquivos={k:n.get(k,0) for k in CONTADORES}
    notas=await db.fetchval("""SELECT count(DISTINCT coalesce(juncao,id)) FROM lote_arquivos WHERE lote_id=$1 AND estado='lido'""",lote['id'])
    return {'lote_id':lote['id'],'agente_id':lote['agente_id'],'nome':lote['nome'],'estado':lote['estado'],'origem':lote['origem'],
            'criado_por':lote['criado_por'],'criado_em':lote['criado_em'],'fechado_em':lote['fechado_em'],'concluido_em':lote['concluido_em'],
            'expira_em':lote['expira_em'],'bytes_total':lote['bytes_total'],'arquivos':{'total':sum(arquivos.values()),**arquivos},'notas':notas,
            'estimativa_s':round(arquivos['na_fila']*SEGUNDOS_POR_ARQUIVO) if arquivos['na_fila'] else 0,'limites':LIMITES}


CAMPOS_DA_NOTA=('chave_acesso','modelo','serie','numero','emissao','emitente_nome','emitente_documento','prestador_documento','tomador_documento',
                'destinatario_nome','destinatario_documento','valor_total','valor','situacao')


async def notas(db,tenant,lote_id):
    """Notas do lote depois de juntar PDF e XML: o XML é a fonte; divergência de valor/número entre os dois vira aviso."""
    rows=await db.fetch("""SELECT f.id AS arquivo_id,f.nome,f.formato,f.juncao,f.zip,a.* FROM lote_arquivos f JOIN anexos a ON a.id=f.anexo_id
        WHERE f.tenant_id=$1 AND f.lote_id=$2 AND f.estado='lido' ORDER BY f.criado_em,f.id""",tenant,lote_id)
    grupos={}
    for r in rows: grupos.setdefault(r['juncao'] or r['arquivo_id'],[]).append(r)
    saida=[]
    for chave,arqs in grupos.items():
        lidos=[]
        for r in arqs:
            try: c=D.mascarar_cpf(D.campos(D.resultado(r) or {}))
            except Exception: c={}
            lidos.append((r,{k:v for k,v in c.items() if k in CAMPOS_DA_NOTA}))
        xml=next((x for x in lidos if x[0]['formato']=='xml'),None);pdf=next((x for x in lidos if x[0]['formato']=='pdf'),None)
        base=(xml or pdf)[1];campos={**(pdf[1] if pdf and xml else {}),**base}
        avisos=[]
        if arqs[0]['juncao'] is None: avisos.append('sem_chave_de_juncao')
        if sum(1 for x in lidos if x[0]['formato']=='xml')>1 or sum(1 for x in lidos if x[0]['formato']=='pdf')>1: avisos.append('nota_duplicada')
        if xml and pdf:
            for k in ('valor_total','valor','numero'):
                a,b=(xml[1].get(k) or {}).get('valor'),(pdf[1].get(k) or {}).get('valor')
                if a is not None and b is not None and str(a)!=str(b): avisos.append('pdf_diverge_do_xml');break
        tipo=next((r['tipo_documento'] for r,_ in ([xml] if xml else [])+lidos if r['tipo_documento']),None)
        saida.append({'tipo_documento':tipo,'fonte':'xml' if xml else 'pdf','campos':campos,'avisos':avisos,
                      'arquivos':[{'arquivo_id':r['arquivo_id'],'anexo_id':r['id'],'nome':r['nome'],'formato':r['formato'],'zip':r['zip']} for r,_ in lidos]})
    return saida


async def apagar(db,tenant,lote,actor):
    """LGPD: apaga os arquivos (originais e leituras) e marca o lote como apagado. Auditado."""
    async with db.acquire() as conn,conn.transaction():
        for a in await conn.fetch("""SELECT a.* FROM anexos a JOIN lote_arquivos f ON f.anexo_id=a.id WHERE f.lote_id=$1 AND a.apagado_em IS NULL""",lote['id']):
            await D.apagar(conn,a)
        await conn.execute("UPDATE lote_arquivos SET estado='falhou',motivo='lote_apagado' WHERE lote_id=$1 AND estado='na_fila'",lote['id'])
        await conn.execute("UPDATE lotes SET estado='apagado',apagado_em=now(),nome=NULL WHERE id=$1",lote['id'])
        await auditar(conn,tenant,actor,'lote.apagado',lote['id'])
