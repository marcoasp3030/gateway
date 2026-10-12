-- PED2 (docs/ESPEC_PED2.md): horário agendado como dado, registro de todas as chamadas de Skill e anúncio de pedido sem fechar.
-- Tudo aditivo: a imagem anterior segue funcionando.
-- Horário agendado do carrinho e do pedido (nulo = sem agendamento, "o quanto antes"). O texto continua em `horario`.
ALTER TABLE carrinhos ADD COLUMN IF NOT EXISTS agendado_para TIMESTAMPTZ;
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS agendado_para TIMESTAMPTZ;
-- O que o gateway fez com um turno em que o agente anunciou pedido fechado sem fechar_pedido de sucesso:
-- 'refeito' (resposta descartada, turno refeito com aviso) ou 'segurado' (texto do agente não enviado).
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS anuncio_falso TEXT;
-- Registro de TODAS as chamadas de Skill (consultas e as recusadas na entrada): nomes dos campos recebidos, nunca os valores.
CREATE TABLE IF NOT EXISTS skill_registro (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    conversation_id TEXT,
    skill TEXT NOT NULL,
    metodo TEXT NOT NULL,
    campos TEXT[] NOT NULL DEFAULT '{}',
    status INT NOT NULL,
    motivo TEXT,
    duracao_ms INT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_skill_registro_conversa ON skill_registro (tenant_id, conversation_id, criado_em);
CREATE INDEX IF NOT EXISTS ix_skill_registro_tempo ON skill_registro (tenant_id, criado_em);
