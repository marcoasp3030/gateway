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
    'botoes_interativos','debounce_incompleta_s','refazer_turno','aviso_demora_janela_s','carrinho_expira_h',
}
# debounce_curto_s vale quando a mensagem parece completa (pontuação final ou mais de
# debounce_palavras palavras); mensagem curta/picada espera debounce_s.
PADROES = dict(debounce_s=4,debounce_curto_s=1.5,debounce_palavras=6,max_wait_s=12,media_wait_s=30,pause_minutes=60,history_turns=20,
    slow_notice_s=20,aviso_demora='Só um instante, estou verificando.',fallback=FALLBACK_PADRAO,
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
    aviso_demora_janela_s=120,carrinho_expira_h=6)


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
        if geracao == self._geracao:
            self._cache[key] = (self.relogio()+self.ttl,copy.deepcopy(cfg))
        return cfg
