import copy
import json
import time

# Texto sem promessa de retorno: o cliente sabe que precisa repetir (Fase 0.6, item A).
FALLBACK_PADRAO="Tive uma instabilidade agora. Pode repetir sua mensagem em instantes? 🙏"

COMPORTAMENTO = {
    'debounce_s','max_wait_s','media_wait_s','pause_minutes','history_turns','slow_notice_s',
    'aviso_demora','fallback','alerta_telefone','send_mode','agente_mode','modo_piloto',
    'contatos_permitidos','encerrar_vazia_min','max_lotes_simultaneos','max_concorrencia',
    'skyone_dominios_permitidos','debounce_curto_s','debounce_palavras','max_msgs_por_minuto_contato',
    'botoes_interativos','debounce_incompleta_s','refazer_turno','aviso_demora_janela_s',
    # CFG1-a: já no contrato; a execução vem nos blocos R1 (reações) e G3 (fora do horário e transbordo).
    'aviso_espera_modo','aviso_espera_textos','reacoes','reacoes_emojis','reacoes_max_dia','fora_do_horario','fora_do_horario_mensagem','fora_do_horario_aceita_agendado',
    'transbordo_alerta','transbordo_horario_equipe','transbordo_fora_do_horario',
    'notificar_equipe','transbordo_destino_externo',
    # FUP1-a
    'follow_up_ativo','follow_up_escopo','follow_up_maximo','follow_up_primeiro_min','follow_up_segundo_min','follow_up_janela',
    'follow_up_fora_unidade','follow_up_limite_dia',
}
# debounce_curto_s vale quando a mensagem parece completa (pontuação final ou mais de
# debounce_palavras palavras); mensagem curta/picada espera debounce_s.
PADROES = dict(debounce_s=4,debounce_curto_s=1.5,debounce_palavras=6,max_wait_s=12,media_wait_s=30,pause_minutes=60,history_turns=20,
    slow_notice_s=30,aviso_demora='Só um instante, estou verificando.',fallback=FALLBACK_PADRAO,
    send_mode='dry',agente_mode='mock',modo_piloto=True,contatos_permitidos=[],
    encerrar_vazia_min=30,max_lotes_simultaneos=5,max_concorrencia=5,skyone_dominios_permitidos=[],
    max_msgs_por_minuto_contato=20,
    # Botões de resposta do agente (docs/ESPEC_BOTOES.md). Desligado: as opções saem numeradas em texto.
    botoes_interativos='off',
    # UX2. debounce_incompleta_s nulo = mensagem incompleta espera o debounce normal (como antes).
    # refazer_turno: chegou mensagem antes de a resposta sair → descarta a resposta e refaz com o lote completo.
    debounce_incompleta_s=None,refazer_turno='off',
    # UX3. Aviso de demora não sai se o bot falou na conversa há menos de aviso_demora_janela_s (0 = sempre sai).
    aviso_demora_janela_s=120,
    # AVS1. Enquanto o agente processa: 'presenca' = só "digitando…" (padrão); 'presenca_e_texto_contextual' = também um texto curto
    # depois de slow_notice_s, e só no meio de um pedido ou de uma busca; 'off' = nada. Os textos são sorteados sem repetir o último.
    aviso_espera_modo='presenca',aviso_espera_textos=['Já te respondo 😊','Só mais um instantinho','Um momento, por favor'],
    # CFG1-a/CFG1-c. Reações, fora do horário e transbordo: executados desde o CFG1-c (pedido agendado ainda não).
    reacoes='off',reacoes_emojis=['❤️','😊','👍','🙏'],reacoes_max_dia=2,
    fora_do_horario='atender',fora_do_horario_aceita_agendado=False,
    fora_do_horario_mensagem='Oi! 😊 No momento a {nome_empresa} está fechada.[[ Voltamos {proxima_abertura}.]] Quando abrirmos, é só mandar uma mensagem por aqui que a gente te atende!',
    transbordo_alerta=None,transbordo_horario_equipe=None,
    transbordo_fora_do_horario='Nossa equipe não está disponível agora.[[ Voltamos {retorno_equipe}.]] Deixei seu recado e, enquanto isso, sigo te ajudando por aqui.',
    # SOL1. notificar_equipe: o agente avisa a equipe (evento, catering, encomenda…) sem pausar o bot.
    notificar_equipe='off',transbordo_destino_externo=False,
    # FUP1-a (docs/ESPEC_FUP1A.md). Começa desligado em todo agente; ligado, só ensaio (registra o que enviaria, não envia).
    # Janela 09:00–22:00 todos os dias, sempre cruzada com o horário da unidade; fora dela cancela (adiar_abertura existe, desligado).
    follow_up_ativo='off',follow_up_escopo='pedido_em_andamento',follow_up_maximo=2,follow_up_primeiro_min=15,follow_up_segundo_min=60,
    follow_up_janela={d:[['09:00','22:00']] for d in ('seg','ter','qua','qui','sex','sab','dom')},follow_up_fora_unidade='cancelar',
    follow_up_limite_dia=2)
# MULTI1-b: comportamentos que um módulo declara (com o padrão) entram aqui; só valem para agente com o módulo.
from modulos import REGISTRO as _MODULOS
for _m in _MODULOS.values():
    for _campo,_spec in _m.comportamentos.items(): COMPORTAMENTO.add(_campo);PADROES[_campo]=_spec[6]


class Configuracoes:
    def __init__(self, repo, ttl=30, relogio=time.monotonic):
        self.repo, self.ttl, self.relogio = repo, ttl, relogio
        self._cache = {}
        self._geracao = 0

    # Chamado depois de cada invalidação local (o app liga ao Redis: os workers e o agendador limpam o cache deles na hora).
    aviso = None

    def invalidar(self, tenant_id=None):
        self.invalidar_local(tenant_id)
        if self.aviso:
            try: self.aviso(tenant_id)
            except Exception: pass

    def invalidar_local(self, tenant_id=None):
        self._geracao += 1
        self._cache = {k:v for k,v in self._cache.items() if tenant_id is not None and k[0]!=tenant_id}


    async def efetiva(self, tenant_id, agente_id):
        if not tenant_id:
            raise ValueError('tenant_id obrigatório')
        key = (tenant_id,agente_id)
        item = self._cache.get(key)
        if item and item[0] > self.relogio():
            return copy.deepcopy(item[1])
        geracao = self._geracao
        agente = await self.repo.obter(tenant_id,'agentes',agente_id)
        if agente is None:
            raise LookupError('Agente não encontrado')
        modelo = await self.repo.modelo(tenant_id,agente['modelo_id'],agente['modelo_versao'])
        if modelo is None:
            raise LookupError('Modelo não encontrado')
        tenant = await self.repo.obter(tenant_id,'tenant_config',tenant_id) or {}
        cfg, origem = copy.deepcopy(PADROES), {k:'plataforma' for k in PADROES}
        for nome,dados in [('modelo',modelo['comportamento_padrao']),('tenant',tenant),('agente',agente)]:
            for campo in COMPORTAMENTO:
                if dados.get(campo) is not None:
                    cfg[campo] = copy.deepcopy(dados[campo]); origem[campo] = nome
        cfg.update({k:agente[k] for k in ('id','tenant_id','modelo_id','modelo_versao','status','provedor',
            'skyone_estrategia','skyone_webhook_url','skyone_auth_tipo','skyone_auth_cabecalho',
            'timeout_s','instrucoes_agente','modo_resposta')})
        # Não cachear credenciais nem ciphertexts.
        cfg['origem_config'] = origem
        # MULTI1-a: módulos que valem para o agente (modelo E empresa E agente), segmento e listas efetivas de motivos e tipos.
        from modulos import modulos_efetivos,motivos_efetivos,tipos_efetivos,chaves_da_empresa,COMPORTAMENTOS_DE_MODULO
        cfg['modulos'] = modulos_efetivos(modelo,agente.get('modulos_ligados'),await chaves_da_empresa(self.repo.db,tenant_id))
        cfg['segmento'] = modelo.get('segmento'); cfg['modelo_nome'] = modelo.get('nome')
        cfg['motivos_transferencia_permitidos'] = motivos_efetivos(modelo,cfg['modulos'])
        cfg['tipos_solicitacao_permitidos'] = tipos_efetivos(modelo,cfg['modulos'])
        # Configuração de um módulo que o agente não tem não aparece nem vale.
        for campo,modulo in COMPORTAMENTOS_DE_MODULO.items():
            if modulo not in cfg['modulos']: cfg.pop(campo,None); origem.pop(campo,None)
        # Perfil de atendimento (CFG1-a): mesma herança, sempre a versão publicada de cada escopo.
        from nucleo.atendimento import resolver_perfil,bloco_do_pacote,versao_texto
        empresa = await self.repo.tenant(tenant_id) or {}
        perfil,origem_perfil = resolver_perfil(('segmento',modelo.get('perfil_padrao')),('empresa',tenant.get('perfil')),('agente',agente.get('perfil')),
                                               nome_da_empresa=empresa.get('nome'),motivos=[m['id'] for m in cfg['motivos_transferencia_permitidos']])
        cfg['perfil_atendimento'] = bloco_do_pacote(perfil); cfg['origem_perfil'] = origem_perfil
        cfg['perfil_versao'] = versao_texto(tenant.get('perfil_versao'),agente.get('perfil_versao'))
        if geracao == self._geracao:
            self._cache[key] = (self.relogio()+self.ttl,copy.deepcopy(cfg))
        return cfg

    async def da_empresa(self, tenant_id):
        """Configuração no nível da empresa (sem agente): plataforma → segmento da empresa → empresa.
        Devolve (valores, origem, perfil, origem do perfil, versão). Sem cache: só o painel usa."""
        from nucleo.atendimento import resolver_perfil,bloco_do_pacote,versao_texto
        empresa = await self.repo.tenant(tenant_id)
        if empresa is None: raise LookupError('Empresa não encontrada')
        tenant = await self.repo.obter(tenant_id,'tenant_config',tenant_id) or {}
        modelo = await self.repo.segmento(tenant_id,empresa.get('segmento')) if empresa.get('segmento') else None
        cfg, origem = copy.deepcopy(PADROES), {k:'plataforma' for k in PADROES}
        for nome,dados in [('modelo',(modelo or {}).get('comportamento_padrao') or {}),('tenant',tenant)]:
            for campo in COMPORTAMENTO:
                if dados.get(campo) is not None:
                    cfg[campo] = copy.deepcopy(dados[campo]); origem[campo] = nome
        # MULTI1-a: na empresa valem os motivos de todos os agentes dela, e a configuração dos módulos que algum deles usa.
        motivos,modulos = await self.da_empresa_listas(tenant_id)
        from modulos import COMPORTAMENTOS_DE_MODULO
        for campo,modulo in COMPORTAMENTOS_DE_MODULO.items():
            if modulo not in modulos: cfg.pop(campo,None); origem.pop(campo,None)
        perfil,origem_perfil = resolver_perfil(('segmento',(modelo or {}).get('perfil_padrao')),('empresa',tenant.get('perfil')),nome_da_empresa=empresa.get('nome'),
                                               motivos=[m['id'] for m in motivos])
        return cfg,origem,bloco_do_pacote(perfil),origem_perfil,versao_texto(tenant.get('perfil_versao'))

    async def da_empresa_listas(self, tenant_id):
        """(motivos de transferência, módulos em uso) somando os agentes da empresa. Sem agente, os do núcleo e nenhum módulo."""
        from modulos import modulos_efetivos,chaves_da_empresa,MOTIVOS_NUCLEO,_em_ordem,REGISTRO
        chaves = await chaves_da_empresa(self.repo.db,tenant_id);dos_modelos = [];modulos = set()
        for a in await self.repo.db.fetch("SELECT modelo_id,modelo_versao,modulos_ligados FROM agentes WHERE tenant_id=$1 ORDER BY id",tenant_id):
            modelo = await self.repo.modelo(tenant_id,a['modelo_id'],a['modelo_versao'])
            if not modelo: continue
            modulos.update(modulos_efetivos(modelo,a['modulos_ligados'],chaves));dos_modelos += list(modelo.get('motivos_transferencia') or [])
        # Mesma ordem do agente: núcleo, módulos em uso, modelos.
        em_uso = [m for m in REGISTRO if m in modulos]
        return _em_ordem(list(MOTIVOS_NUCLEO)+[i for m in em_uso for i in REGISTRO[m].motivos_transferencia]+dos_modelos),em_uso


CANAL_INVALIDACAO = ':config:invalidar'


def avisar_pelo_redis(obter_redis, obter_prefixo):
    """aviso → PUBLISH no canal de invalidação (empresa, ou "*" para todas). Sem Redis, vale a validade de 30 s de cada processo."""
    import asyncio
    def aviso(tenant_id):
        redis = obter_redis()
        if redis is None or not hasattr(redis, 'publish'): return
        tarefa = asyncio.get_running_loop().create_task(redis.publish(obter_prefixo()+CANAL_INVALIDACAO, tenant_id or '*'))
        tarefa.add_done_callback(lambda t: t.cancelled() or t.exception())
    return aviso


async def escutar_invalidacoes(redis, configuracoes, prefixo, log=None):
    """Workers e agendador: limpam o próprio cache quando o gateway publica uma configuração. Cai e volta sozinho."""
    import asyncio
    while True:
        try:
            canal = redis.pubsub()
            await canal.subscribe(prefixo+CANAL_INVALIDACAO)
            async for m in canal.listen():
                if m.get('type') == 'message':
                    dado = m.get('data')
                    configuracoes.invalidar_local(None if dado in ('*', b'*') else (dado.decode() if isinstance(dado, bytes) else dado))
        except asyncio.CancelledError:
            raise
        except Exception as erro:
            if log: log.warning('Escuta de invalidação de configuração caiu (%s); tenta de novo', type(erro).__name__)
            await asyncio.sleep(5)
