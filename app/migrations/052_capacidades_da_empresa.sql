-- Interruptor da plataforma por empresa para capacidades transversais (hoje: documentos). Vazio = liberado (padrão).
-- {"documentos": {"ativo": false}} bloqueia documentos em todos os agentes da empresa, qualquer que seja o modelo.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS capacidades JSONB NOT NULL DEFAULT '{}'::jsonb;
