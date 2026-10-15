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
    'botoes_interativos','debounce_incompleta_s','refazer_turno','aviso_demora_janela_s','carrinho_expira_h','acoes_recentes_min',
    # CFG1-a: já no contrato; a execução vem nos blocos R1 (reações) e G3 (fora do horário e transbordo).
    'aviso_espera_modo','aviso_espera_textos','reacoes','reacoes_emojis','reacoes_max_dia','fora_do_horario','fora_do_horario_mensagem','fora_do_horario_aceita_agendado',
    'transbordo_alerta','transbordo_horario_equipe','transbordo_fora_do_horario',
    'notificar_equipe','transbordo_destino_externo',
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
    # Carrinho sem alteração há mais de carrinho_expira_h horas é arquivado na próxima mensagem do cliente.
    aviso_demora_janela_s=120,carrinho_expira_h=6,
    # AVS1. Enquanto o agente processa: 'presenca' = só "digitando…" (padrão); 'presenca_e_texto_contextual' = também um texto curto
    # depois de slow_notice_s, e só no meio de um pedido ou de uma busca; 'off' = nada. Os textos são sorteados sem repetir o último.
    aviso_espera_modo='presenca',aviso_espera_textos=['Já te respondo 😊','Só mais um instantinho','Um momento, por favor'],
    # UX3.1. acoes_recentes do pacote: chamadas de Skill da conversa nos últimos N minutos (0 = lista vazia).
    acoes_recentes_min=15,
    # CFG1-a/CFG1-c. Reações, fora do horário e transbordo: executados desde o CFG1-c (pedido agendado ainda não).
    reacoes='off',reacoes_emojis=['❤️','😊','👍','🙏'],reacoes_max_dia=2,
    fora_do_horario='atender',fora_do_horario_aceita_agendado=False,
    fora_do_horario_mensagem='Oi! 😊 No momento a {nome_empresa} está fechada.[[ Voltamos {proxima_abertura}.]] Quando abrirmos, é só mandar uma mensagem por aqui que a gente te atende!',
    transbordo_alerta=None,transbordo_horario_equipe=None,
    transbordo_fora_do_horario='Nossa equipe não está disponível agora.[[ Voltamos {retorno_equipe}.]] Deixei seu recado e, enquanto isso, sigo te ajudando por aqui.',
    # SOL1. notificar_equipe: o agente avisa a equipe (evento, catering, encomenda…) sem pausar o bot.
    notificar_equipe='off',transbordo_destino_externo=False)


class Configuracoes:
    def __init__(self, repo, ttl=30, relogio=time.monotonic):
        self.repo, self.ttl, self.relogio = repo, ttl, relogio
        self._cache = {}
        self._geracao = 0

    def invalidar(self, tenant_id=None):
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
        from modulos import modulos_efetivos,motivos_efetivos,chaves_da_empresa,MOTIVOS_NUCLEO,_em_ordem,ORDEM_MOTIVOS,REGISTRO
        chaves = await chaves_da_empresa(self.repo.db,tenant_id);motivos = list(MOTIVOS_NUCLEO);modulos = set()
        for a in await self.repo.db.fetch("SELECT modelo_id,modelo_versao,modulos_ligados FROM agentes WHERE tenant_id=$1",tenant_id):
            modelo = await self.repo.modelo(tenant_id,a['modelo_id'],a['modelo_versao'])
            if not modelo: continue
            mods = modulos_efetivos(modelo,a['modulos_ligados'],chaves);modulos.update(mods);motivos += motivos_efetivos(modelo,mods)
        return _em_ordem(motivos,ORDEM_MOTIVOS),[m for m in REGISTRO if m in modulos]
