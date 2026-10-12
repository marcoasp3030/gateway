-- CFG1-c (docs/ESPEC_CFG1C.md): reações (R1), fora do horário (H1-a) e transbordo (T1). Tudo aditivo: a imagem anterior segue funcionando.
-- R1: o que aconteceu com a reação pedida pelo agente no turno ({"emoji","resultado","motivo"}).
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS reacao JSONB;
-- T1: a transferência do turno caiu fora do horário da equipe? (nulo = o turno não transferiu)
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS equipe_fora_do_horario BOOLEAN;
-- H1-a: até quando o aviso de "fora do horário" já foi dado nesta conversa (um por período fechado).
ALTER TABLE conversas ADD COLUMN IF NOT EXISTS fora_do_horario_avisado_ate TIMESTAMPTZ;
-- T1: número da própria instância do canal (lido do provedor), para recusar como destino de alerta.
ALTER TABLE tenant_canais ADD COLUMN IF NOT EXISTS numero_instancia TEXT;
-- T1: o telefone de alerta passa a ser configuração versionada; quem já tinha um número na empresa continua com ele.
UPDATE tenant_config c SET transbordo_alerta=t.whatsapp_alerta_transbordo FROM tenants t
    WHERE t.id=c.tenant_id AND c.transbordo_alerta IS NULL AND t.whatsapp_alerta_transbordo ~ '^[0-9]{10,15}$';
-- Versão 3 do segmento delivery_restaurante: a 2, com reações ligadas (❤️ 😊 👍 🙏) e fora do horário "atender"
-- (revoga o "so_avisar" gravado no CFG1). Ninguém é movido aqui.
INSERT INTO modelos_agente(id,versao,tenant_id,nome,tipo,modulos,comportamento_padrao,desfechos,followups_padrao,skyone_fluxo_modelo,perfil_padrao)
    SELECT id,3,tenant_id,nome,tipo,modulos,
        comportamento_padrao || '{"reacoes":"on","reacoes_emojis":["❤️","😊","👍","🙏"],"fora_do_horario":"atender"}'::jsonb,
        desfechos,followups_padrao,skyone_fluxo_modelo,perfil_padrao
    FROM modelos_agente WHERE id='delivery_restaurante' AND versao=2 AND tenant_id IS NULL
    ON CONFLICT (id,versao) DO NOTHING;
