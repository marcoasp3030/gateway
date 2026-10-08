"""Gateway multiempresa. Requer schema migrado; não inicia sobre o banco legado."""
import logging
from plataforma import Infra, criar_app

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
# HTTPX não deve registrar URLs de terceiros em INFO.
logging.getLogger('httpx').setLevel(logging.WARNING)
try:
    app=criar_app(Infra.ambiente())
except ValueError:
    raise SystemExit('Configuração de infraestrutura inválida; gateway não iniciado') from None
