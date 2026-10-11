-- UX2 (docs/ESPEC_UX2.md), blocos P1 e P2. Tudo aditivo: a imagem anterior segue funcionando.
-- P1: espera própria para mensagem que parece incompleta (nulo = usa debounce_s, como antes).
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS debounce_incompleta_s REAL CHECK (debounce_incompleta_s BETWEEN 1 AND 15);
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS debounce_incompleta_s REAL CHECK (debounce_incompleta_s BETWEEN 1 AND 15);
-- P2: refazer o turno quando chega mensagem do cliente antes de a resposta sair (padrão da plataforma: off).
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS refazer_turno TEXT CHECK (refazer_turno IN ('on','off'));
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS refazer_turno TEXT CHECK (refazer_turno IN ('on','off'));
-- Turno que nasceu de um refazer aponta para o turno descartado (e por isso não pode ser refeito de novo).
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS refeito_de BIGINT;
-- Havia mensagem nova mas o turno não foi refeito: por quê (métrica).
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS refazer_recusado TEXT CHECK (refazer_recusado IN ('ja_refeito','acao_com_efeito','skill_com_efeito'));
-- Chamadas de Skill que alteram carrinho ou pedido, para dizer ao turno refeito o que o descartado já fez.
-- Liga-se ao turno por conversa + janela (agente_inicio..agente_fim). Sem dado pessoal: nunca guarda endereço nem observação.
CREATE TABLE IF NOT EXISTS skill_chamadas (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    conversation_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    dados JSONB NOT NULL DEFAULT '{}',
    resultado TEXT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_skill_chamadas_conversa ON skill_chamadas (tenant_id, conversation_id, criado_em);
