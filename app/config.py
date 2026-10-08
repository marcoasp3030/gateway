import os


def _f(key: str, default: str) -> float:
    return float(os.getenv(key, default))


DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

ADMIN_KEY = os.getenv("ADMIN_KEY", "")
SKILLS_API_KEY = os.getenv("SKILLS_API_KEY", "")
UAZAPI_WEBHOOK_SECRET = os.getenv("UAZAPI_WEBHOOK_SECRET", "")

DEBOUNCE_S = _f("DEBOUNCE_S", "4")
MAX_WAIT_S = _f("MAX_WAIT_S", "12")
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "20"))
PAUSE_MINUTES = int(os.getenv("PAUSE_MINUTES", "60"))
LOCK_TTL_S = 180

SKYONE_MODE = os.getenv("SKYONE_MODE", "mock")          # mock | live
SKYONE_WEBHOOK_URL = os.getenv("SKYONE_WEBHOOK_URL", "")
SKYONE_TOKEN = os.getenv("SKYONE_TOKEN", "")
SKYONE_TIMEOUT_S = _f("SKYONE_TIMEOUT_S", "60")
SLOW_NOTICE_S = _f("SLOW_NOTICE_S", "15")
MOCK_MIN_S = _f("MOCK_MIN_S", "2")
MOCK_MAX_S = _f("MOCK_MAX_S", "5")

SEND_MODE = os.getenv("SEND_MODE", "dry")              # dry | live
UAZAPI_BASE_URL = os.getenv("UAZAPI_BASE_URL", "").rstrip("/")
UAZAPI_TOKEN = os.getenv("UAZAPI_TOKEN", "")

AVISO_DEMORA = "Só um instante, estou verificando aqui 😊"
FALLBACK = "Tive uma instabilidade agora. Já vou te responder, tudo bem? 🙏"

# --- segurança ---
DOCS_ENABLED = os.getenv("DOCS_ENABLED", "false").lower() == "true"  # SEG08
WEBHOOK_RAW_DIAS = int(os.getenv("WEBHOOK_RAW_DIAS", "30"))           # LGPD01
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(2 * 1024 * 1024)))  # SEG07 (o Nginx também limita)


def problemas_de_seguranca() -> list[str]:
    """SEG01: o gateway não sobe com segredo vazio, curto ou de exemplo."""
    from seguranca import UrlRecusada, validar_formato_url, validar_segredos

    obrigatorios = {
        "ADMIN_KEY": ADMIN_KEY,
        "SKILLS_API_KEY": SKILLS_API_KEY,
        "UAZAPI_WEBHOOK_SECRET": UAZAPI_WEBHOOK_SECRET,
    }
    if SKYONE_MODE == "live":
        obrigatorios["SKYONE_TOKEN"] = SKYONE_TOKEN
    if SEND_MODE == "live":
        obrigatorios["UAZAPI_TOKEN"] = UAZAPI_TOKEN
    problemas = validar_segredos(obrigatorios)

    if SKYONE_MODE not in ("mock", "live"):
        problemas.append("SKYONE_MODE deve ser mock ou live")
    if SEND_MODE not in ("dry", "live"):
        problemas.append("SEND_MODE deve ser dry ou live")
    if SKYONE_MODE == "live":
        try:
            validar_formato_url(SKYONE_WEBHOOK_URL)
        except UrlRecusada as e:
            problemas.append(f"SKYONE_WEBHOOK_URL inválida: {e}")
    if SEND_MODE == "live":
        try:
            validar_formato_url(UAZAPI_BASE_URL)
        except UrlRecusada as e:
            problemas.append(f"UAZAPI_BASE_URL inválida: {e}")
    return problemas
