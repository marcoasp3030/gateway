"""Canal uazapi: leitura do webhook (formato real conferido na Fase 1) e envio."""
from datetime import datetime,timezone,timedelta
import re
import httpx
import seguranca

# Acima disto o anexo nem é baixado (INV11).
LIMITE_ANEXO_BYTES=15*1024*1024
# messageType do provedor → (tipo interno, nome para o agente).
MIDIAS={'AudioMessage':('audio','áudio'),'ImageMessage':('imagem','imagem'),'VideoMessage':('video','vídeo'),
    'DocumentMessage':('documento','documento'),'StickerMessage':('figurinha','figurinha'),
    'ContactMessage':('contato','contato'),'ContactsArrayMessage':('contato','contatos')}
TEXTOS={'Conversation','ExtendedTextMessage'}
# Clique em botão de resposta (payload real de 10/10/2026: tests/fixtures/uazapi/clique_botao.json).
# Clique em botão de resposta e escolha em lista (B3): viram texto com o título da opção.
CLIQUES_DE_BOTAO={'TemplateButtonReplyMessage':'botao','ListResponseMessage':'lista'}
ID_DE_BOTAO=re.compile(r'[a-z0-9_]{1,40}')
LOCALIZACOES={'LocationMessage','LiveLocationMessage'}


def _so_digitos(valor):
    return ''.join(c for c in str(valor or '').split('@')[0] if c.isdigit())


def _telefone(payload,msg):
    """Telefone do cliente. O chatid pode vir como @lid (sem número); aí vale sender_pn ou o chat."""
    chatid=str(msg.get('chatid') or msg.get('chatId') or '')
    if chatid.endswith('@s.whatsapp.net') and _so_digitos(chatid): return _so_digitos(chatid)
    chat=payload.get('chat') if isinstance(payload.get('chat'),dict) else {}
    candidatos=[chat.get('wa_chatid'),chat.get('phone')]
    if not msg.get('fromMe'): candidatos.insert(0,msg.get('sender_pn'))
    for c in candidatos:
        if c and '@lid' not in str(c) and 8<=len(_so_digitos(c))<=16: return _so_digitos(c)
    return None


def _enviada_em(msg,agora=None):
    """Data/hora informada pelo provedor (ms ou s). None se ausente ou implausível."""
    try: t=float(msg.get('messageTimestamp') or 0)
    except (TypeError,ValueError): return None
    if t>1e11: t/=1000
    if t<=0: return None
    try: quando=datetime.fromtimestamp(t,timezone.utc)
    except (OverflowError,OSError,ValueError): return None
    agora=agora or datetime.now(timezone.utc)
    return quando if agora-timedelta(days=7)<=quando<=agora+timedelta(minutes=5) else None


def _tamanho(n):
    return f'{n/1048576:.1f} MB'.replace('.',',') if n>=1048576 else f'{max(1,round(n/1024))} KB'


def _midia(tipo_provedor,conteudo,legenda):
    """Anexo que o gateway ainda não lê: texto para o agente pedir outro formato (INV11)."""
    tipo,nome=MIDIAS.get(tipo_provedor,('desconhecido',None))
    meta={'tipo_provedor':tipo_provedor}
    for origem,destino in (('mimetype','mimetype'),('fileLength','tamanho_bytes'),('seconds','segundos'),('fileName','arquivo'),('pageCount','paginas')):
        if conteudo.get(origem) not in (None,''): meta[destino]=conteudo[origem]
    if tipo=='desconhecido':
        rotulo,motivo=str(tipo_provedor or 'anexo')[:60],'tipo de mensagem não suportado'
    else:
        rotulo=nome
        if tipo=='documento' and meta.get('arquivo'): rotulo+=f' "{str(meta["arquivo"])[:120]}"'
        if tipo=='audio' and meta.get('segundos'): rotulo+=f' de {meta["segundos"]} s'
        tamanho=meta.get('tamanho_bytes') if isinstance(meta.get('tamanho_bytes'),(int,float)) else 0
        motivo=(f'arquivo grande demais ({_tamanho(tamanho)})' if tamanho>LIMITE_ANEXO_BYTES
            else 'leitura deste tipo de anexo ainda não está disponível')
    aviso=f'[não foi possível ler {rotulo}: {motivo}]'
    return tipo,(legenda+'\n'+aviso if legenda else aviso),meta


def _titulo_do_botao(msg,conteudo):
    """Título do botão clicado, como o cliente viu (uma linha, até 200 caracteres)."""
    for valor in (conteudo.get('selectedDisplayText'),msg.get('vote'),msg.get('text'),conteudo.get('title')):
        if isinstance(valor,str) and valor.strip(): return ' '.join(valor.split())[:200]
    return None


def normalizar(payload: dict, agora=None) -> dict | None:
    """Converte o webhook do uazapi num formato interno. None = ignorar."""
    evento = payload.get("EventType") or payload.get("event")
    if evento and evento != "messages":
        return None
    msg = payload.get("message") or {}
    if not isinstance(msg,dict): return None
    chatid = str(msg.get("chatid") or msg.get("chatId") or "")
    if chatid.endswith("@g.us") or msg.get("isGroup") or chatid.endswith('@newsletter') or chatid.endswith('@broadcast'):
        return None  # grupos, canais e listas de transmissão
    telefone = _telefone(payload,msg)
    message_id = msg.get("messageid") or msg.get("id")
    if not telefone or not message_id:
        return None
    conteudo = msg.get("content")
    texto = msg.get("text")
    if not texto and isinstance(conteudo, str):
        texto = conteudo
    texto = str(texto or "").strip()
    conteudo = conteudo if isinstance(conteudo,dict) else {}
    tipo_provedor = str(msg.get("messageType") or msg.get("type") or "")
    meta = {}
    if tipo_provedor == 'ReactionMessage' or msg.get('type') == 'reaction':
        # Reação entra no histórico, mas não abre turno (o agente não responde a 👍).
        tipo = 'reacao'; meta = {'reacao_a': msg.get('reaction') or None, 'emoji': texto}
        texto = f'[reação: {texto}]' if texto else '[reação removida]'
    elif tipo_provedor in LOCALIZACOES or 'degreesLatitude' in conteudo:
        tipo = 'localizacao'
        lat, lon = conteudo.get('degreesLatitude'), conteudo.get('degreesLongitude')
        if isinstance(lat,(int,float)) and isinstance(lon,(int,float)) and -90<=lat<=90 and -180<=lon<=180:
            lugar = ' — '.join(str(conteudo[k]).strip()[:200] for k in ('name','address') if conteudo.get(k))
            meta = {'latitude': lat, 'longitude': lon, 'ao_vivo': tipo_provedor == 'LiveLocationMessage'}
            if lugar: meta['lugar'] = lugar
            marcador = f'[localização: {lat}, {lon}' + (f' — {lugar}' if lugar else '') + ']'
            texto = texto + '\n' + marcador if texto else marcador
        else:
            texto = '[não foi possível ler localização: coordenadas ausentes]'
    elif tipo_provedor in CLIQUES_DE_BOTAO and _titulo_do_botao(msg,conteudo):
        # O clique é uma mensagem de texto do cliente: o texto é o título do botão.
        tipo = 'texto'; texto = _titulo_do_botao(msg,conteudo)
        # Toque feito no aparelho da própria instância (não é o cliente escolhendo, nem atendente escrevendo): ignora.
        if msg.get('fromMe'): return None
        escolha = conteudo.get('singleSelectReply') if isinstance(conteudo.get('singleSelectReply'),dict) else {}
        ident = str(conteudo.get('selectedID') or msg.get('buttonOrListid') or escolha.get('selectedRowID') or '')
        origem = str(msg.get('quoted') or (conteudo.get('contextInfo') or {}).get('stanzaID') or '')[:200]
        # Sem id válido ou sem a mensagem de origem, fica só o texto.
        if ID_DE_BOTAO.fullmatch(ident) and origem: meta = {'interacao': {'tipo': CLIQUES_DE_BOTAO[tipo_provedor], 'id': ident, 'mensagem': origem}}
    elif tipo_provedor in TEXTOS or (not tipo_provedor and texto):
        tipo = 'texto'
        if not texto: return None
    else:
        tipo, texto, meta = _midia(tipo_provedor, conteudo, texto)
    if msg.get('quoted'): meta['resposta_a'] = str(msg['quoted'])[:255]
    return {
        "telefone": telefone,
        "nome": None if msg.get("fromMe") else (msg.get("senderName") or None),
        "texto": texto[:20000],
        "tipo": tipo,
        "meta": meta,
        "message_id": str(message_id)[:200],
        "enviada_em": _enviada_em(msg, agora),
        "from_me": bool(msg.get("fromMe")),
        # True quando a mensagem foi enviada pela API (pelo próprio bot)
        "via_api": bool(msg.get("wasSentByApi")),
    }


# Campos do payload que não devem ficar em webhook_raw: endereço da instância, chaves
# de decifrar mídia e miniaturas (conteúdo do cliente em base64).
_REMOVER={'baseurl','jpegthumbnail','waveform'}
_MASCARAR=('mediakey',)


def limpar_bruto(valor):
    """Complementa o sanitizar() genérico (tokens/segredos) com o que é próprio do uazapi."""
    if isinstance(valor,dict):
        return {k:('***' if any(s in k.lower() for s in _MASCARAR) else limpar_bruto(v))
                for k,v in valor.items() if k.lower() not in _REMOVER}
    if isinstance(valor,list): return [limpar_bruto(v) for v in valor]
    return valor


def _credencial(cofre,tenant_id,canal):
    cred=cofre.abrir(tenant_id,(canal['credenciais_enc'] or {}).get('fernet'))
    if not isinstance(cred,dict) or not cred.get('token') or not cred.get('base_url'):
        raise ValueError('Credenciais de canal indisponíveis')
    return cred['base_url'].rstrip('/'),cred['token']


class EnvioRecusado(ValueError):
    """Falha no /send/text. `certo` = o provedor recusou (4xx): a mensagem não saiu."""
    def __init__(self,mensagem,certo=False):
        super().__init__(mensagem);self.certo=certo


class MenuRecusado(ValueError):
    """O provedor recusou o menu com certeza (4xx): nada foi enviado, dá para mandar em texto."""


async def _chamar(client,cofre,tenant_id,canal,metodo,caminho,corpo=None,timeout=15):
    """Uma chamada à instância: URL conferida (SSRF), sem redirecionamento, erro sem detalhes."""
    base,token=_credencial(cofre,tenant_id,canal)
    url=base+caminho
    await seguranca.validar_url_saida(url)
    response=await client.request(metodo,url,headers={'token':token},json=corpo,timeout=timeout,follow_redirects=False)
    if not 200<=response.status_code<300:
        raise ValueError('Provedor recusou a chamada (HTTP %s)'%response.status_code)
    return response


def id_do_envio(corpo):
    """messageid devolvido pelo envio (o mesmo que aparece no webhook e na busca). None se não vier."""
    if not isinstance(corpo,dict): return None
    for origem in (corpo,corpo.get('message'),corpo.get('response')):
        if not isinstance(origem,dict): continue
        for campo in ('messageid','messageId','id'):
            valor=origem.get(campo)
            if isinstance(valor,str) and valor.strip():
                # "id" vem como "<dono>:<messageid>".
                valor=valor.strip().split(':')[-1] if campo=='id' else valor.strip()
                if 8<=len(valor)<=200 and all(c.isalnum() or c in '-_.' for c in valor): return valor
    return None


async def enviar(client,cofre,tenant_id,canal,telefone,texto):
    """POST /send/text {number,text} (documentação uazapi). Erro HTTP sobe para o pipeline.
    Devolve o messageid do provedor (ou None), para a varredura reconhecer o que o bot enviou."""
    try: response=await _chamar(client,cofre,tenant_id,canal,'POST','/send/text',{'number':telefone,'text':texto})
    except ValueError as erro:
        achado=re.search(r'HTTP (\d{3})',str(erro))
        # 4xx (menos 408) é recusa certa: nada saiu. O resto pode ter saído.
        raise EnvioRecusado('Envio recusado' if achado else str(erro),bool(achado and 400<=int(achado.group(1))<500 and achado.group(1)!='408')) from None
    try: return id_do_envio(response.json())
    except ValueError: return None


async def _menu(client,cofre,tenant_id,canal,corpo):
    """POST /send/menu. Recusa certa do provedor (4xx, menos 408) → MenuRecusado: nada saiu, quem chama envia em texto.
    Qualquer outra falha sobe como entrega incerta (não reenviar). Devolve o messageid do provedor."""
    try: response=await _chamar(client,cofre,tenant_id,canal,'POST','/send/menu',corpo)
    except ValueError as erro:
        achado=re.search(r'HTTP (\d{3})',str(erro))
        if achado and 400<=int(achado.group(1))<500 and achado.group(1)!='408': raise MenuRecusado('Menu recusado') from None
        raise ValueError('Envio recusado' if achado else str(erro)) from None
    try: return id_do_envio(response.json())
    except ValueError: return None


async def enviar_menu(client,cofre,tenant_id,canal,telefone,texto,opcoes):
    """Botões de resposta: type "button", choices "título|id"."""
    return await _menu(client,cofre,tenant_id,canal,{'number':telefone,'type':'button','text':texto,'choices':[f'{o["titulo"]}|{o["id"]}' for o in opcoes]})


async def enviar_lista(client,cofre,tenant_id,canal,telefone,texto,opcoes,botao,secao='Opções'):
    """Lista (B3): type "list", listButton = texto do botão que abre a lista, choices "[Seção]" e "título|id|descrição"."""
    return await _menu(client,cofre,tenant_id,canal,{'number':telefone,'type':'list','text':texto,'listButton':botao,
        'choices':[f'[{secao}]']+[f'{o["titulo"]}|{o["id"]}'+(f'|{o["descricao"]}' if o.get('descricao') else '') for o in opcoes]})


def marca_do_envio(canal_id,provedor_id):
    """(message_id, meta) da mensagem enviada ao vivo: com o id do provedor, ou marcada como sem id."""
    if provedor_id: return f'{canal_id}:{provedor_id}',None
    return None,'{"envio_sem_id": true}'


async def digitando(client,cofre,tenant_id,canal,telefone,duracao_ms=10000,presenca='composing'):
    """POST /message/presence {number,presence,delay}: "digitando" por duracao_ms (máx. 5 min).
    A instância cancela sozinha ao enviar mensagem para o mesmo chat; 'paused' cancela antes."""
    if presenca not in ('composing','recording','paused'): raise ValueError('Presença inválida')
    corpo={'number':telefone,'presence':presenca}
    if presenca!='paused': corpo['delay']=max(1000,min(int(duracao_ms),300000))
    await _chamar(client,cofre,tenant_id,canal,'POST','/message/presence',corpo,timeout=10)


ESTADOS={'connected':'conectado','connecting':'conectando','disconnected':'desconectado'}


def _estado(valor):
    if isinstance(valor,bool): return 'conectado' if valor else 'desconectado'
    return ESTADOS.get(str(valor or '').strip().lower())


async def estado(client,cofre,tenant_id,canal):
    """GET /instance/status → 'conectado' | 'conectando' | 'desconectado'."""
    d=(await _chamar(client,cofre,tenant_id,canal,'GET','/instance/status',timeout=10)).json()
    instancia=d.get('instance') if isinstance(d.get('instance'),dict) else {}
    situacao=d.get('status') if isinstance(d.get('status'),dict) else {}
    return _estado(instancia.get('status')) or _estado(situacao.get('connected')) or 'desconectado'


def estado_do_evento(payload):
    """Evento "connection" do webhook. O formato não está documentado: lê os campos prováveis
    e devolve None se não reconhecer (a varredura confere o estado pela API de qualquer forma)."""
    for origem in (payload.get('instance'),payload.get('status'),payload.get('data'),payload):
        if isinstance(origem,dict):
            for campo in ('status','state','connection'):
                achado=_estado(origem.get(campo)) if not isinstance(origem.get(campo),(dict,bool)) else None
                if achado: return achado
            if isinstance(origem.get('connected'),bool): return _estado(origem['connected'])
        elif isinstance(origem,str) and _estado(origem): return _estado(origem)
    return None


async def recentes(client,cofre,tenant_id,canal,desde_ms,pagina=100,max_paginas=20):
    """POST /message/find sem chatid: mensagens de todos os chats, da mais recente para trás,
    até passar de desde_ms. A API não filtra por data. Devolve (mensagens, completo)."""
    achadas=[];offset=0
    for _ in range(max_paginas):
        d=(await _chamar(client,cofre,tenant_id,canal,'POST','/message/find',{'limit':pagina,'offset':offset},timeout=30)).json()
        lote=d.get('messages') if isinstance(d,dict) else None
        if not isinstance(lote,list) or not lote: return achadas,True
        for msg in lote:
            if not isinstance(msg,dict): continue
            try: quando=float(msg.get('messageTimestamp') or 0)
            except (TypeError,ValueError): continue
            if quando<1e11: quando*=1000
            if quando<desde_ms: return achadas,True
            achadas.append(msg)
        if len(lote)<pagina or d.get('hasMore') is False: return achadas,True
        offset=d.get('nextOffset') if isinstance(d.get('nextOffset'),int) and d['nextOffset']>offset else offset+len(lote)
    return achadas,False
