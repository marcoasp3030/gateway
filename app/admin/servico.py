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


# Campos que o banco exige (NOT NULL): null enviado pelo cliente vira 400 com o campo,
# antes de chegar ao banco, em vez de um 409 genérico de integridade.
OBRIGATORIOS = {
    'Tenant': ('nome','timezone','status','retencao_dias','tenant_teste'),
    'Agente': ('nome','status','provedor','skyone_estrategia','skyone_auth_tipo','timeout_s',
               'instrucoes_agente','modo_resposta','modelo_id','modelo_versao'),
    'Canal': ('nome','tipo','publico','ativo'),
    'Chave': ('nome','escopos'),
    'Unidade': ('nome','horarios','ativo'),
    'Saida': ('url','eventos','ativo'),
    'Modelo': ('nome','tipo','modulos','comportamento_padrao','desfechos','followups_padrao'),
}


def validar(modelo, dados):
    try:
        result = modelo.model_validate(dados).model_dump(exclude_unset=True)
    except ValidationError as exc:
        from erros_api import campo_validacao
        raise HTTPException(400,{"campo":campo_validacao(exc.errors())}) from None
    for campo in OBRIGATORIOS.get(modelo.__name__, ()):
        if campo in result and result[campo] is None:
            raise HTTPException(400,{"campo":campo})
    if 'fallback' in result: result['fallback'] = fallback_valido(result['fallback'])
    return result


FALLBACK_MINIMO = 15


def fallback_valido(texto):
    """Texto que o cliente recebe quando o agente falha. Vazio ou nulo = usa o padrão (herda).
    Senão, pelo menos FALLBACK_MINIMO caracteres e uma letra; caso contrário 400 com o campo."""
    if texto is None or not texto.strip(): return None
    if len(texto.strip()) < FALLBACK_MINIMO or not any(c.isalpha() for c in texto):
        raise HTTPException(400,{"campo":"fallback"})
    return texto


async def auditar(conn, tenant, actor, acao, alvo, dados=None):
    # Serializa a alocação de IDs por tenant até o commit: polling não pula
    # um evento de transação concorrente que confirmar depois. Chamador usa transação.
    if not conn.is_in_transaction(): raise RuntimeError("Auditoria exige transação")
    await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', 'auditoria:'+(tenant or 'plataforma'))
    # Auditoria só com identificadores/ação; nunca corpo ou segredos. `dados`: só o que já vem limpo de dado pessoal (ex.: motivo).
    await conn.execute('INSERT INTO audit_log(tenant_id,actor,acao,alvo,dados) VALUES ($1,$2,$3,$4,$5::jsonb)',
        tenant,actor,acao,alvo,json.dumps(dados or {},ensure_ascii=False))


async def versao_por_alteracao_direta(conn,tenant_id,agente_id,actor,antes,depois):
    """Mudança feita pelas rotas antigas (PUT /config, PATCH agente) num comportamento versionado
    também entra no histórico de versões: nada fica fora dele (CFG1-a)."""
    from admin.atendimento import registrar_versao,definicao_do_escopo,COMPORTAMENTOS
    alteracoes=[{'campo':'comportamentos.'+c,'antes':antes.get(c),'depois':depois.get(c)} for c in COMPORTAMENTOS if antes.get(c)!=depois.get(c)]
    if alteracoes: await registrar_versao(conn,tenant_id,agente_id,definicao_do_escopo(depois),alteracoes,'alteracao_direta',actor,'Alteração direta')


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
            if dados.get('segmento') and not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM modelos_agente WHERE id=$1 AND (tenant_id IS NULL OR tenant_id=$2))',dados['segmento'],tenant_id):
                raise HTTPException(400,{"campo":"segmento"})
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
        # Piloto: telefone (8 a 16 dígitos) ou, para o canal api (ARQ1), um identificador com letras ("teste-marco-01").
        for contato in cfg['contatos_permitidos'] or []:
            if not (re.fullmatch(r'\d{8,16}',contato) or (re.fullmatch(r'[A-Za-z0-9_@+.-]{3,100}',contato) and not contato.isdigit())):
                raise HTTPException(400,'Telefone inválido')
        if cfg.get('alerta_telefone') and not re.fullmatch(r'\d{8,16}',cfg['alerta_telefone']): raise HTTPException(400,'Telefone inválido')
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
        # MULTI1-a: configuração de módulo só para empresa em que algum agente usa o módulo.
        from modulos import COMPORTAMENTOS_DE_MODULO
        fora = [c for c in dados if c in COMPORTAMENTOS_DE_MODULO and dados[c] is not None]
        if fora:
            _,em_uso = await self.configuracoes.da_empresa_listas(tenant_id)
            fora = [c for c in fora if COMPORTAMENTOS_DE_MODULO[c] not in em_uso]
            if fora: raise HTTPException(400,{'campo':fora[0],'motivo':'modulo_desligado'})
        async with self.db.acquire() as conn, conn.transaction():
            repo = Repositorio(conn)
            tenant = await repo.tenant(tenant_id)
            if not tenant: raise HTTPException(404,'TENANT_NOT_FOUND')
            antes = await repo.obter(tenant_id,'tenant_config',tenant_id) or {}
            result = await repo.alterar(tenant_id,'tenant_config',tenant_id,dados)
            await versao_por_alteracao_direta(conn,tenant_id,None,actor,antes,result)
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
                if novo and not dados.get('modelo_versao'):
                    # MULTI1-a: agente novo sem versão nasce na versão mais recente do modelo (antes: versão 1).
                    recente = await repo.segmento(tenant_id,dados['modelo_id'])
                    if recente: dados['modelo_versao'] = recente['versao']
                combinado = {**(anterior or {}),**dados}
                modelo_reg = await repo.modelo(tenant_id,combinado['modelo_id'],combinado.get('modelo_versao',1))
                if not modelo_reg: raise HTTPException(404,'NOT_FOUND')
                # Modelo inativo não serve para agente novo nem para quem troca de modelo; quem já usa continua.
                if modelo_reg.get('ativo') is False and (novo or combinado['modelo_id']!=anterior.get('modelo_id')): raise HTTPException(400,{'campo':'modelo_id','motivo':'modelo_inativo'})
                from modulos import modulos_efetivos,chaves_da_empresa,COMPORTAMENTOS_DE_MODULO,REGISTRO
                permitidos = modelo_reg.get('modulos_permitidos') if modelo_reg.get('modulos_permitidos') is not None else modelo_reg.get('modulos') or []
                ligados = combinado.get('modulos_ligados')
                if ligados is not None and (any(m not in REGISTRO or m not in permitidos for m in ligados) or len(set(ligados))!=len(ligados)):
                    raise HTTPException(400,{'campo':'modulos_ligados','motivo':'fora_do_modelo'})
                efetivos = modulos_efetivos(modelo_reg,ligados,await chaves_da_empresa(conn,tenant_id))
                fora = [c for c in dados if c in COMPORTAMENTOS_DE_MODULO and dados[c] is not None and COMPORTAMENTOS_DE_MODULO[c] not in efetivos]
                if fora: raise HTTPException(400,{'campo':fora[0],'motivo':'modulo_desligado'})
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
                        r'(notificar|conversar|callback|eventos:ler|ferramentas:[a-z_]+|documentos:enviar)',e) for e in dados['escopos'])):
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
            if entidade=='agentes':
                await self.checar_agente(repo,tenant,result)
                if not novo: await versao_por_alteracao_direta(conn,tenant_id,identidade,actor,anterior,result)
            await auditar(conn,tenant_id,actor,entidade+('.criado' if novo else '.alterado'),identidade)
        self.configuracoes.invalidar(tenant_id)
        return {**limpar(result),**unico}
