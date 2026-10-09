import ipaddress
import json
import re
import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from pydantic import ValidationError
from nucleo.banco import Repositorio, registro
from nucleo.configuracao import Configuracoes, COMPORTAMENTO
from nucleo.autenticacao import hash_chave
from seguranca import validar_formato_url
from .modelos import Tenant, Agente, Canal, Chave, Comportamento

ENTIDADES = {'agentes':('agentes',Agente,'ag_'), 'canais':('tenant_canais',Canal,'can_'),
             'api-keys':('tenant_api_keys',Chave,'key_')}


def limpar(valor):
    if isinstance(valor,list): return [limpar(v) for v in valor]
    if not isinstance(valor,dict): return valor
    result = {}
    for k,v in valor.items():
        if k in ('hash','webhook_secret') or k.endswith('_enc'):
            if k != 'hash': result[k.removesuffix('_enc')+'_configurado'] = bool(v)
        else: result[k] = limpar(v)
    return result


def validar(modelo, dados):
    try:
        return modelo.model_validate(dados).model_dump(exclude_unset=True)
    except ValidationError as exc:
        from erros_api import campo_validacao
        raise HTTPException(400,{"campo":campo_validacao(exc.errors())}) from None


async def auditar(conn, tenant, actor, acao, alvo):
    # Serializa a alocação de IDs por tenant até o commit: polling não pula
    # um evento de transação concorrente que confirmar depois. Chamador usa transação.
    if not conn.is_in_transaction(): raise RuntimeError("Auditoria exige transação")
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', 'auditoria:'+(tenant or 'plataforma'))
    # Auditoria só com identificadores/ação; nunca corpo ou segredos.
    await conn.execute('INSERT INTO audit_log(tenant_id,actor,acao,alvo,dados) VALUES ($1,$2,$3,$4,$5::jsonb)',
        tenant,actor,acao,alvo,'{}')


class Administracao:
    def __init__(self, db, cofre, configuracoes, infra):
        self.db,self.cofre,self.configuracoes,self.infra = db,cofre,configuracoes,infra

    async def salvar_tenant(self, dados, actor, tenant_id=None):
        dados = validar(Tenant,dados)
        novo = tenant_id is None
        if novo:
            tenant_id = dados.pop('id',None)
            if not tenant_id or not re.fullmatch(r'[a-z0-9_]{3,40}',tenant_id) or not dados.get('nome'):
                raise HTTPException(400,'VALIDATION_ERROR')
        elif 'id' in dados: raise HTTPException(400,'ID imutável')
        if any(v is None for k,v in dados.items() if k in ('nome','timezone','status','retencao_dias','tenant_teste')):
            raise HTTPException(400,'VALIDATION_ERROR')
        if 'timezone' in dados:
            try: ZoneInfo(dados['timezone'])
            except (ValueError,KeyError): raise HTTPException(400,'Timezone inválido') from None
        async with self.db.acquire() as conn, conn.transaction():
            if novo:
                cols = ['id']+list(dados)
                result = await conn.fetchrow(f'INSERT INTO tenants({",".join(cols)}) VALUES '
                    f'({",".join("$"+str(i) for i in range(1,len(cols)+1))}) RETURNING *',tenant_id,*dados.values())
                await conn.execute('INSERT INTO tenant_config(tenant_id) VALUES ($1)',tenant_id)
            else:
                if not await Repositorio(conn).tenant(tenant_id): raise HTTPException(404,'TENANT_NOT_FOUND')
                if dados:
                    result = await conn.fetchrow('UPDATE tenants SET '+','.join(f'{k}=${i}' for i,k in enumerate(dados,2))+
                        ' WHERE id=$1 RETURNING *',tenant_id,*dados.values())
                else: result = await conn.fetchrow('SELECT * FROM tenants WHERE id=$1',tenant_id)
            if not novo:
                for agente in await conn.fetch('SELECT * FROM agentes WHERE tenant_id=$1',tenant_id):
                    await self.checar_agente(Repositorio(conn),dict(result),dict(agente))
            await auditar(conn,tenant_id,actor,'tenant.criado' if novo else 'tenant.alterado',tenant_id)
        self.configuracoes.invalidar(tenant_id)
        return dict(result)

    async def checar_agente(self,repo,tenant,agente):
        cfg = await Configuracoes(repo).efetiva(tenant['id'],agente['id'])
        if cfg['max_wait_s'] < cfg['debounce_s']: raise HTTPException(400,'max_wait_s menor que debounce_s')
        for telefone in (cfg['contatos_permitidos'] or []) + ([cfg['alerta_telefone']] if cfg.get('alerta_telefone') else []):
            if not re.fullmatch(r'\d{8,16}',telefone): raise HTTPException(400,'Telefone inválido')
        if cfg['agente_mode']=='live':
            if agente['provedor']!='skyone' or not agente['skyone_webhook_url']:
                raise HTTPException(400,'Provedor/URL live inválidos')
            if not tenant['tenant_teste'] and (agente['skyone_auth_tipo']=='nenhuma' or not agente['skyone_auth_enc']):
                raise HTTPException(400,'SEG13: credencial obrigatória')
        if agente['skyone_webhook_url']:
            validar_formato_url(agente['skyone_webhook_url'],cfg['skyone_dominios_permitidos'])
        if agente['skyone_auth_tipo']=='cabecalho' and (
                not re.fullmatch(r'[A-Za-z0-9-]+',agente['skyone_auth_cabecalho'] or '') or
                agente['skyone_auth_cabecalho'].lower() in ('host','content-length','transfer-encoding','connection')):
            raise HTTPException(400,'Cabeçalho inválido')

    async def salvar_config(self,tenant_id,dados,actor):
        dados = validar(Comportamento,dados)
        async with self.db.acquire() as conn, conn.transaction():
            repo = Repositorio(conn)
            tenant = await repo.tenant(tenant_id)
            if not tenant: raise HTTPException(404,'TENANT_NOT_FOUND')
            result = await repo.alterar(tenant_id,'tenant_config',tenant_id,dados)
            # Toda configuração afetada é validada antes de confirmar a escrita.
            for agente in await conn.fetch('SELECT * FROM agentes WHERE tenant_id=$1',tenant_id):
                await self.checar_agente(repo,tenant,dict(agente))
            await auditar(conn,tenant_id,actor,'config.alterada',tenant_id)
        self.configuracoes.invalidar(tenant_id)
        return result

    async def salvar(self,tenant_id,entidade,dados,actor,identidade=None):
        tabela,modelo,prefixo = ENTIDADES[entidade]
        dados = validar(modelo,dados)
        novo = identidade is None
        identidade = identidade or prefixo+secrets.token_hex(12)
        unico = {}
        async with self.db.acquire() as conn, conn.transaction():
            repo = Repositorio(conn)
            tenant = await repo.tenant(tenant_id)
            if not tenant: raise HTTPException(404,'TENANT_NOT_FOUND')
            anterior = None if novo else await repo.obter(tenant_id,tabela,identidade)
            if not novo and not anterior: raise HTTPException(404,'NOT_FOUND')
            if novo and not dados.get('nome'): raise HTTPException(400,'Nome obrigatório')
            if entidade=='agentes':
                if novo and not dados.get('modelo_id'): raise HTTPException(400,'Modelo obrigatório')
                combinado = {**(anterior or {}),**dados}
                modelo_reg = await repo.modelo(tenant_id,combinado['modelo_id'],combinado.get('modelo_versao',1))
                if not modelo_reg: raise HTTPException(404,'NOT_FOUND')
                habilitados = await conn.fetch('SELECT modulo FROM modulos_tenant WHERE tenant_id=$1 AND ativo',tenant_id)
                if not set(modelo_reg['modulos']) <= {r['modulo'] for r in habilitados}: raise HTTPException(400,'Módulos não habilitados')
                for campo in ('skyone_auth','skyone_auth_usuario'):
                    if campo in dados:
                        valor = dados.pop(campo)
                        if valor is not None and (any(ord(c)<32 or ord(c)==127 for c in valor) or (campo=='skyone_auth_usuario' and ':' in valor)):
                            raise HTTPException(400,'Credencial inválida')
                        dados[campo+'_enc'] = self.cofre.cifrar(tenant_id,valor) if valor else None
                combinado = {**(anterior or {}),**dados}
                if combinado.get('skyone_auth_tipo')=='basic' and (
                    not combinado.get('skyone_auth_enc') or not combinado.get('skyone_auth_usuario_enc')):
                    raise HTTPException(400,'Basic exige usuário e senha')
                if dados.get('skyone_webhook_url'): validar_formato_url(dados['skyone_webhook_url'])
            if entidade=='canais':
                if novo and (not dados.get('tipo') or not dados.get('agente_id')): raise HTTPException(400,'Tipo e agente obrigatórios')
                if not novo and 'tipo' in dados: raise HTTPException(400,'Tipo imutável')
                if dados.get('agente_id') and not await repo.obter(tenant_id,'agentes',dados['agente_id']): raise HTTPException(404,'NOT_FOUND')
                if 'credenciais' in dados:
                    cred = dados.pop('credenciais')
                    if cred and cred.get('base_url'): validar_formato_url(cred['base_url'])
                    dados['credenciais_enc'] = {'fernet':self.cofre.cifrar(tenant_id,cred)} if cred else {}
                if novo:
                    segredo = secrets.token_urlsafe(32)
                    dados['webhook_secret'] = self.cofre.cifrar(tenant_id,segredo)
                    unico['webhook_url'] = f'{self.infra.public_url}/webhooks/{dados["tipo"]}/{identidade}?s={segredo}'
            if entidade=='api-keys':
                if novo and not dados.get('escopos'): raise HTTPException(400,'Escopos obrigatórios')
                if 'escopos' in dados and (not dados['escopos'] or any(not re.fullmatch(
                        r'(notificar|conversar|callback|eventos:ler|ferramentas:[a-z_]+)',e) for e in dados['escopos'])):
                    raise HTTPException(400,'Escopo inválido')
                if dados.get('agente_id') and not await repo.obter(tenant_id,'agentes',dados['agente_id']): raise HTTPException(404,'NOT_FOUND')
                if dados.get('expira_em'):
                    dt = datetime.fromisoformat(dados['expira_em'].replace('Z','+00:00'))
                    if dt.tzinfo is None: raise HTTPException(400,'Data exige fuso')
                    dados['expira_em'] = dt
                if dados.get('ips_permitidos') is not None:
                    dados['ips_permitidos'] = [ipaddress.ip_network(ip) for ip in dados['ips_permitidos']]
                if novo:
                    unico['chave'] = secrets.token_urlsafe(32)
                    dados['hash'] = hash_chave(unico['chave'],self.infra.pepper)
            result = (await repo.inserir(tenant_id,tabela,identidade,dados) if novo
                      else await repo.alterar(tenant_id,tabela,identidade,dados))
            if entidade=='agentes': await self.checar_agente(repo,tenant,result)
            await auditar(conn,tenant_id,actor,entidade+('.criado' if novo else '.alterado'),identidade)
        self.configuracoes.invalidar(tenant_id)
        return {**limpar(result),**unico}
