"""Normalização herdada; confirmar com fixtures reais na Fase 1."""
import httpx
import seguranca

def normalizar(payload: dict) -> dict | None:
    """Converte o webhook do uazapi num formato interno. None = ignorar."""
    evento = payload.get("EventType") or payload.get("event")
    if evento and evento != "messages":
        return None

    msg = payload.get("message") or {}
    chatid = msg.get("chatid") or msg.get("chatId") or ""
    if chatid.endswith("@g.us") or msg.get("isGroup"):
        return None  # ignora grupos

    telefone = chatid.split("@")[0] or str(msg.get("sender", "")).split("@")[0]
    if not telefone:
        return None

    texto = msg.get("text")
    if not texto and isinstance(msg.get("content"), str):
        texto = msg["content"]

    return {
        "telefone": telefone,
        "nome": msg.get("senderName"),
        "texto": (texto or "").strip(),
        "tipo": msg.get("messageType") or msg.get("type"),
        "message_id": msg.get("messageid") or msg.get("id"),
        "from_me": bool(msg.get("fromMe")),
        # True quando a mensagem foi enviada pela API (pelo próprio bot)
        "via_api": bool(msg.get("wasSentByApi")),
    }


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
