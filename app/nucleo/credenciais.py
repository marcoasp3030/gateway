"""Fernet com contexto de tenant: um ciphertext não pode ser transplantado."""
import json
from cryptography.fernet import Fernet, InvalidToken


class CredencialInvalida(ValueError):
    pass


class Cofre:
    def __init__(self, chave):
        try:
            self._fernet = Fernet(chave)
        except (ValueError, TypeError):
            raise CredencialInvalida('ENCRYPTION_KEY inválida') from None

    def cifrar(self, tenant_id, valor):
        if not tenant_id:
            raise CredencialInvalida('Tenant obrigatório')
        return self._fernet.encrypt(json.dumps({'tenant': tenant_id, 'valor': valor}).encode()).decode()

    def abrir(self, tenant_id, cifrado):
        try:
            dados = json.loads(self._fernet.decrypt(cifrado.encode()))
            if not tenant_id or dados['tenant'] != tenant_id:
                raise ValueError()
            return dados['valor']
        except (InvalidToken, ValueError, KeyError, TypeError, AttributeError):
            raise CredencialInvalida('Credencial indisponível') from None
