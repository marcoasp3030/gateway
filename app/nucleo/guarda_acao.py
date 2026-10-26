"""Guarda da ação do agente (análise de comportamento, 26/10/2026, falha 4).

- `transferir_humano` com motivo fora da lista do perfil do agente (`perfil_atendimento.motivos_transferencia`; os fixos sempre valem);
- `notificar_equipe` com o recurso desligado.

Como as outras guardas da resposta: o turno é refeito uma vez com o recado em `aviso_do_gateway`; se repetir, a ação é trocada pela segura
(transferir por `pediu_humano`, ou `nenhuma`), o texto do agente é mantido e a operação é avisada. A transferência que o próprio gateway
monta (silêncio sem motivo) não passa por aqui."""

COLUNA='acao_fora_da_regra'


def _motivos(cfg):
    return [m for m in ((cfg.get('perfil_atendimento') or {}).get('motivos_transferencia') or [])]


def fora_da_regra(cfg,resultado):
    """'motivo' | 'notificar_equipe' | None."""
    if not resultado or resultado.get('silencio_sem_motivo'): return None
    acao=resultado.get('acao')
    if acao=='transferir_humano':
        motivo=(resultado.get('motivo') or '').strip().lower()
        # Sem motivo não é "fora da lista": a transferência segue como sempre.
        return None if not motivo or motivo in _motivos(cfg) else 'motivo'
    if acao=='notificar_equipe' and cfg.get('notificar_equipe')!='on': return 'notificar_equipe'
    return None


def guarda(cfg):
    """A guarda no formato de `_guarda_da_resposta` (coluna, avaliar, recado, resposta_segura, auditoria, log, alerta)."""
    motivos=_motivos(cfg)

    async def avaliar(db,tenant,conversa,resultado,desde):
        return fora_da_regra(cfg,resultado) is not None

    def resposta_segura(original):
        segurado={k:original.get(k) for k in ('resposta','acao','desfecho','motivo')}
        if original.get('acao')=='transferir_humano':
            nova={'resposta':list(original.get('resposta') or []),'acao':'transferir_humano','motivo':'pediu_humano','segurado':segurado}
            if original.get('solicitacao'): nova['solicitacao']=original['solicitacao']
            return nova
        return {'resposta':list(original.get('resposta') or []),'acao':'nenhuma','segurado':segurado}

    recado=('A ação devolvida não vale neste agente. Para transferir, use um destes motivos: '+', '.join(motivos)+'. '
            'Avisar a equipe (notificar_equipe) só quando recursos.notificar_equipe for true; senão responda com acao "nenhuma" '
            'ou transfira por pediu_humano. Não prometa ao cliente um aviso à equipe que não foi feito.')
    return {'coluna':COLUNA,'avaliar':avaliar,'recado':recado,'resposta_segura':resposta_segura,'auditoria':'turno.acao_fora_da_regra_segurada',
            'log':'Ação fora da regra do agente repetida: trocada pela ação segura',
            'alerta':('ação fora da regra do agente','O agente insistiu num motivo de transferência que não está liberado ou em avisar a equipe com '
                      'o recurso desligado. A ação foi trocada pela segura (transferir por pediu_humano, ou nenhuma); o texto foi mantido.')}
