"""Canal uazapi: leitura do webhook (formato real conferido na Fase 1) e envio."""
from datetime import datetime,timezone,timedelta
import httpx
import seguranca

# Acima disto o anexo nem é baixado (INV11).
LIMITE_ANEXO_BYTES=15*1024*1024
# messageType do provedor → (tipo interno, nome para o agente).
MIDIAS={'AudioMessage':('audio','áudio'),'ImageMessage':('imagem','imagem'),'VideoMessage':('video','vídeo'),
    'DocumentMessage':('documento','documento'),'StickerMessage':('figurinha','figurinha'),
    'ContactMessage':('contato','contato'),'ContactsArrayMessage':('contato','contatos')}
TEXTOS={'Conversation','ExtendedTextMessage'}
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


async def enviar(client,cofre,tenant_id,canal,telefone,texto):
    cred=cofre.abrir(tenant_id,canal['credenciais_enc'].get('fernet'))
    if not isinstance(cred,dict) or not cred.get('token') or not cred.get('base_url'):
        raise ValueError('Credenciais de canal indisponíveis')
    url=cred['base_url'].rstrip('/')+'/send/text'
    await seguranca.validar_url_saida(url)
    response=await client.post(url,headers={'token':cred['token']},
        json={'number':telefone,'text':texto},timeout=15,follow_redirects=False)
    if not 200<=response.status_code<300:
        raise ValueError('Envio recusado')
