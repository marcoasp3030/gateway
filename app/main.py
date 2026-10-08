import asyncio
import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation

import asyncpg
import httpx
import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
import pipeline
import seguranca
import uazapi

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("gateway")

# SEG05: nenhum segredo de query string em log (inclusive o access log do uvicorn)
_filtro = seguranca.FiltroSegredos()
for _nome in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "gateway", "pipeline", "uazapi"):
    logging.getLogger(_nome).addFilter(_filtro)
for _h in logging.getLogger().handlers:
    _h.addFilter(_filtro)

# SEG01: falhar fechado — não sobe com configuração insegura
_problemas = config.problemas_de_seguranca()
if _problemas:
    raise SystemExit("Configuração insegura, gateway não iniciado:\n- " + "\n- ".join(_problemas))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await asyncpg.create_pool(config.DATABASE_URL, min_size=2, max_size=10)
    app.state.redis = aioredis.from_url(config.REDIS_URL, decode_responses=True)
    # follow_redirects=False (padrão do httpx): redirecionamento não desvia chamada de saída (SEG03)
    app.state.http = httpx.AsyncClient(follow_redirects=False)
    tarefa = asyncio.create_task(pipeline.agendador(app))
    limpeza = asyncio.create_task(pipeline.limpeza_lgpd(app))
    log.info("gateway no ar | agente=%s | envio=%s", config.SKYONE_MODE, config.SEND_MODE)
    yield
    tarefa.cancel()
    limpeza.cancel()
    await app.state.http.aclose()
    await app.state.redis.aclose()
    await app.state.db.close()


# SEG08: documentação interativa desligada por padrão
app = FastAPI(
    title="By Koji Gateway", lifespan=lifespan,
    docs_url="/docs" if config.DOCS_ENABLED else None,
    redoc_url=None,
    openapi_url="/openapi.json" if config.DOCS_ENABLED else None,
)


@app.middleware("http")
async def protecoes(request: Request, call_next):
    # SEG02: rotas administrativas só pela rede interna (nunca via Nginx público)
    if seguranca.rota_interna(request.url.path) and not seguranca.chamada_interna(
            request.client.host if request.client else None, request.headers):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    # SEG07: corpo grande demais é recusado antes de ser lido
    tamanho = request.headers.get("content-length")
    if tamanho and tamanho.isdigit() and int(tamanho) > config.MAX_BODY_BYTES:
        return JSONResponse({"detail": "corpo grande demais"}, status_code=413)
    resposta = await call_next(request)
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


# ---------- autenticação (SEG04: tempo constante; falha fechada) ----------
def admin(x_admin_key: str = Header("")):
    if not seguranca.segredo_confere(x_admin_key, config.ADMIN_KEY):
        raise HTTPException(401, "admin key inválida")


def skills(x_api_key: str = Header("")):
    if not seguranca.segredo_confere(x_api_key, config.SKILLS_API_KEY):
        raise HTTPException(401, "api key inválida")


# ---------- helpers ----------
async def garantir_conversa(db, conv_id: str, canal: str, telefone: str | None, nome: str | None):
    await db.execute(
        """INSERT INTO conversas (conversation_id, canal, telefone, nome)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (conversation_id) DO UPDATE
             SET atualizado_em = now(), nome = COALESCE(EXCLUDED.nome, conversas.nome)""",
        conv_id, canal, telefone, nome)


async def salvar_entrada(db, conv_id: str, origem: str, texto: str, message_id: str | None):
    """Retorna o id da mensagem, ou None se for duplicada."""
    return await db.fetchval(
        """INSERT INTO mensagens (conversation_id, direcao, origem, conteudo, message_id)
           VALUES ($1, 'entrada', $2, $3, $4)
           ON CONFLICT (message_id) DO NOTHING RETURNING id""",
        conv_id, origem, texto, message_id)


# ---------- saúde ----------
@app.get("/health")
async def health(request: Request):
    await app.state.db.fetchval("SELECT 1")
    await app.state.redis.ping()
    # público: só "ok"; detalhes de configuração apenas pela rede interna
    if seguranca.chamada_interna(request.client.host if request.client else None, request.headers):
        return {"ok": True, "agente": config.SKYONE_MODE, "envio": config.SEND_MODE}
    return {"ok": True}


# ---------- webhook uazapi ----------
@app.post("/webhooks/uazapi")
async def webhook_uazapi(request: Request, s: str = ""):
    # SEG01/SEG04: sem segredo configurado nada é aceito; comparação em tempo constante
    if not seguranca.segredo_confere(s, config.UAZAPI_WEBHOOK_SECRET):
        raise HTTPException(401)
    payload = await request.json()
    db = app.state.db
    await db.execute("INSERT INTO webhook_raw (fonte, payload) VALUES ('uazapi', $1::jsonb)",
                     json.dumps(payload, ensure_ascii=False))

    m = uazapi.normalizar(payload)
    if not m:
        return {"ok": True, "ignorado": True}

    conv_id = f"wa:{m['telefone']}"
    await garantir_conversa(db, conv_id, "wa", m["telefone"], None if m["from_me"] else m["nome"])

    if m["from_me"]:
        if not m["via_api"] and m["texto"]:
            # atendente respondeu pelo celular -> pausa o bot
            await db.execute(
                "INSERT INTO mensagens (conversation_id, direcao, origem, conteudo, message_id) "
                "VALUES ($1, 'saida', 'humano', $2, $3) ON CONFLICT (message_id) DO NOTHING",
                conv_id, m["texto"], m["message_id"])
            await pipeline.pausar(db, conv_id, config.PAUSE_MINUTES)
        return {"ok": True, "from_me": True}

    texto = m["texto"] or f"[mídia recebida: {m['tipo'] or 'desconhecida'}]"  # áudio/imagem: Fase 3
    msg_id = await salvar_entrada(db, conv_id, "cliente", texto, m["message_id"])
    if msg_id is None:
        return {"ok": True, "duplicada": True}
    await pipeline.enfileirar(app.state.redis, conv_id, msg_id, texto)
    return {"ok": True}


# ---------- teste sem WhatsApp ----------
class MsgTeste(BaseModel):
    usuario: str
    texto: str


@app.post("/test/mensagem", dependencies=[Depends(admin)])
async def test_mensagem(m: MsgTeste):
    conv_id = f"test:{m.usuario}"
    await garantir_conversa(app.state.db, conv_id, "test", None, m.usuario)
    msg_id = await salvar_entrada(app.state.db, conv_id, "cliente", m.texto, None)
    await pipeline.enfileirar(app.state.redis, conv_id, msg_id, m.texto)
    return {"ok": True, "conversation_id": conv_id, "mensagem_id": msg_id}


@app.get("/test/conversa/{conv_id}", dependencies=[Depends(admin)])
async def test_conversa(conv_id: str, limite: int = 50):
    rows = await app.state.db.fetch(
        "SELECT id, direcao, origem, conteudo, criado_em FROM mensagens "
        "WHERE conversation_id = $1 ORDER BY id DESC LIMIT $2", conv_id, limite)
    return [dict(r) for r in reversed(rows)]


# ---------- métricas ----------
@app.get("/metricas", dependencies=[Depends(admin)])
async def metricas(ultimos: int = 100):
    r = await app.state.db.fetchrow(
        """SELECT count(*) AS turnos,
                  count(*) FILTER (WHERE status = 'erro') AS erros,
                  count(*) FILTER (WHERE aviso_demora) AS avisos_demora,
                  round(avg(n_mensagens), 2) AS msgs_por_lote,
                  percentile_cont(0.5)  WITHIN GROUP (ORDER BY espera_ms) AS espera_p50,
                  percentile_cont(0.5)  WITHIN GROUP (ORDER BY agente_ms) AS agente_p50,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY agente_ms) AS agente_p95,
                  percentile_cont(0.5)  WITHIN GROUP (ORDER BY total_ms)  AS total_p50,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms)  AS total_p95,
                  max(total_ms) AS total_max
           FROM (SELECT * FROM turnos ORDER BY id DESC LIMIT $1) t""", ultimos)
    return dict(r)


# ---------- transbordo manual ----------
@app.post("/admin/pausa/{conv_id}", dependencies=[Depends(admin)])
async def pausar(conv_id: str, minutos: int = config.PAUSE_MINUTES):
    await pipeline.pausar(app.state.db, conv_id, minutos)
    return {"ok": True, "pausado_min": minutos}


@app.delete("/admin/pausa/{conv_id}", dependencies=[Depends(admin)])
async def retomar(conv_id: str):
    await app.state.db.execute(
        "UPDATE conversas SET bot_pausado_ate = NULL WHERE conversation_id = $1", conv_id)
    return {"ok": True}


# ---------- cardápio ----------
@app.post("/admin/cardapio/importar", dependencies=[Depends(admin)])
async def importar_cardapio(request: Request, substituir: bool = True):
    """Corpo: CSV (UTF-8) com cabeçalho unidade,categoria,nome,descricao,preco,ativo"""
    texto = (await request.body()).decode("utf-8-sig")
    linhas, erros = [], []
    for n, row in enumerate(csv.DictReader(io.StringIO(texto)), start=2):
        try:
            preco = Decimal(str(row["preco"]).replace(",", "."))
            linhas.append((row["unidade"].strip(), row["categoria"].strip(), row["nome"].strip(),
                           (row.get("descricao") or "").strip() or None, preco,
                           str(row.get("ativo", "true")).strip().lower() in ("1", "true", "sim", "s")))
        except (KeyError, InvalidOperation, AttributeError) as e:
            erros.append(f"linha {n}: {e}")
    if erros:
        raise HTTPException(400, {"erros": erros[:20]})
    async with app.state.db.acquire() as con, con.transaction():
        if substituir:
            await con.execute("DELETE FROM cardapio_itens")
        await con.executemany(
            "INSERT INTO cardapio_itens (unidade, categoria, nome, descricao, preco, ativo) "
            "VALUES ($1,$2,$3,$4,$5,$6)", linhas)
    return {"ok": True, "importados": len(linhas)}


@app.get("/api/cardapio/busca", dependencies=[Depends(skills)])
async def buscar_cardapio(q: str = "", unidade: str = "", categoria: str = "", limite: int = 10):
    """Usada pela Skill buscar_item no Skyone."""
    rows = await app.state.db.fetch(
        """SELECT id, unidade, categoria, nome, descricao, preco
           FROM cardapio_itens
           WHERE ativo
             AND ($1 = '' OR unaccent(lower(nome || ' ' || coalesce(descricao,'')))
                             LIKE '%' || unaccent(lower($1)) || '%')
             AND ($2 = '' OR unidade IN ($2, 'TODAS'))
             AND ($3 = '' OR unaccent(lower(categoria)) = unaccent(lower($3)))
           ORDER BY categoria, nome LIMIT $4""", q, unidade, categoria, min(limite, 50))
    return {"itens": [{**dict(r), "preco": float(r["preco"])} for r in rows]}


@app.get("/api/cardapio/categorias", dependencies=[Depends(skills)])
async def categorias(unidade: str = ""):
    rows = await app.state.db.fetch(
        "SELECT DISTINCT categoria FROM cardapio_itens WHERE ativo "
        "AND ($1 = '' OR unidade IN ($1, 'TODAS')) ORDER BY categoria", unidade)
    return {"categorias": [r["categoria"] for r in rows]}
