"""Acesso a entidades sempre com tenant explícito; SQL identificadores em allowlist."""
import json

TABELAS = {
    'agentes': 'id', 'tenant_canais': 'id', 'tenant_api_keys': 'id',
    'tenant_config': 'tenant_id', 'conversas': 'conversation_id',
    'mensagens': 'id', 'turnos': 'id', 'contatos': 'id',
    'audit_log': 'id', 'modulos_tenant': 'modulo',
}
JSON = {'credenciais_enc', 'triagem', 'dados', 'config', 'resposta', 'perfil'}
# Colunas de escrita são declaradas pelo serviço, nunca por SQL enviado pelo cliente.
COLUNAS = {
    'nome','modelo_id','modelo_versao','status','provedor','skyone_estrategia','skyone_webhook_url',
    'skyone_auth_tipo','skyone_auth_cabecalho','skyone_auth_enc','timeout_s','instrucoes_agente',
    'modo_resposta','debounce_s','max_wait_s','media_wait_s','pause_minutes','history_turns',
    'slow_notice_s','aviso_demora','fallback','alerta_telefone','send_mode','agente_mode',
    'modo_piloto','contatos_permitidos','encerrar_vazia_min','max_lotes_simultaneos',
    'max_concorrencia','skyone_dominios_permitidos','tipo','agente_id','triagem','webhook_secret',
    'credenciais_enc','publico','ativo','status_conexao','ultimo_evento_em','hash','escopos',
    'expira_em','ips_permitidos','revogada_em','ultimo_uso_em','actor','acao','alvo','dados',
    'config','modulo',
}


def escopo(tenant_id):
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError('tenant_id obrigatório')
    return tenant_id


def registro(row):
    if row is None:
        return None
    result = dict(row)
    for k in JSON | {'comportamento_padrao'}:
        if isinstance(result.get(k), str):
            result[k] = json.loads(result[k])
    return result


class Repositorio:
    def __init__(self, db):
        self.db = db

    async def tenant(self, tenant_id):
        return registro(await self.db.fetchrow('SELECT * FROM tenants WHERE id=$1', escopo(tenant_id)))

    async def obter(self, tenant_id, tabela, identidade):
        chave = TABELAS[tabela]
        return registro(await self.db.fetchrow(
            f'SELECT * FROM {tabela} WHERE tenant_id=$1 AND {chave}=$2', escopo(tenant_id), identidade))

    async def listar(self, tenant_id, tabela, limite=50, cursor=None):
        chave = TABELAS[tabela]
        if not 1 <= limite <= 100:
            raise ValueError('Limite inválido')
        return [registro(r) for r in await self.db.fetch(
            f'SELECT * FROM {tabela} WHERE tenant_id=$1 AND ($2::text IS NULL OR {chave}::text>$2) '
            f'ORDER BY {chave}::text LIMIT $3', escopo(tenant_id), cursor, limite)]

    async def inserir(self, tenant_id, tabela, identidade, dados):
        chave = TABELAS[tabela]
        if not set(dados) <= COLUNAS:
            raise ValueError('Campo de escrita não permitido')
        valores = {'tenant_id': escopo(tenant_id)}
        if chave != 'tenant_id':
            valores[chave] = identidade
        valores.update(dados)
        colunas = list(valores)
        args = [json.dumps(v) if k in JSON and v is not None else v for k,v in valores.items()]
        return registro(await self.db.fetchrow(
            f'INSERT INTO {tabela} ({",".join(colunas)}) VALUES '
            f'({",".join("$"+str(i) for i in range(1,len(args)+1))}) RETURNING *', *args))

    async def alterar(self, tenant_id, tabela, identidade, dados):
        chave = TABELAS[tabela]
        if not dados:
            return await self.obter(tenant_id,tabela,identidade)
        if not set(dados) <= COLUNAS:
            raise ValueError('Campo de escrita não permitido')
        sets = ','.join(f'{k}=${i}' for i,k in enumerate(dados,3))
        args = [json.dumps(v) if k in JSON and v is not None else v for k,v in dados.items()]
        return registro(await self.db.fetchrow(
            f'UPDATE {tabela} SET {sets} WHERE tenant_id=$1 AND {chave}=$2 RETURNING *',
            escopo(tenant_id),identidade,*args))

    async def modelo(self, tenant_id, modelo_id, versao=1):
        return registro(await self.db.fetchrow(
            'SELECT * FROM modelos_agente WHERE id=$2 AND versao=$3 '
            'AND (tenant_id=$1 OR tenant_id IS NULL)',escopo(tenant_id),modelo_id,versao))

