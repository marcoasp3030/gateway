class FalhaAgente(Exception):
    """Somente mensagens fixas, sem dados de terceiros."""

class CredencialRecusada(FalhaAgente):
    def __str__(self): return 'credencial do Skyone recusada'

class CircuitoAberto(FalhaAgente):
    def __str__(self): return 'circuito do Skyone aberto'

class TempoEsgotado(FalhaAgente):
    """Agente não respondeu dentro do timeout_s (espera de cota + chamada)."""
    def __str__(self): return 'tempo limite do agente esgotado'
