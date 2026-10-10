class FalhaAgente(Exception):
    """Somente mensagens fixas, sem dados de terceiros."""

class CredencialRecusada(FalhaAgente):
    def __str__(self): return 'credencial do Skyone recusada'

class CircuitoAberto(FalhaAgente):
    def __str__(self): return 'circuito do Skyone aberto'

class TempoEsgotado(FalhaAgente):
    """Agente não respondeu dentro do timeout_s (espera de cota + chamada)."""
    def __str__(self): return 'tempo limite do agente esgotado'

class FalhaTransitoria(FalhaAgente):
    """Falha de conexão ou HTTP 502/503/504 antes de qualquer resposta: admite uma nova tentativa."""
    def __str__(self): return 'falha transitória na chamada ao agente'

class SilencioSemMotivo(FalhaAgente):
    """INV04: resposta vazia sem acao "silencio", ou silêncio sem motivo. Admite uma reconsulta."""
    def __str__(self): return 'agente não respondeu e não declarou o motivo do silêncio'
