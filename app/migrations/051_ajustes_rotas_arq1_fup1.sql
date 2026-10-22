-- Ajustes de rota ARQ1/FUP1 (22/10/2026). Tudo aditivo.
-- Reativar o follow-up do contato exige um motivo (já limpo de dado pessoal, até 300 caracteres).
ALTER TABLE follow_up_opt_out ADD COLUMN IF NOT EXISTS removido_motivo TEXT CHECK (char_length(removido_motivo) <= 300);
-- Rotação do segredo do webhook de saída: durante 24 h as entregas saem assinadas com o novo e com o anterior.
ALTER TABLE tenant_webhooks_saida ADD COLUMN IF NOT EXISTS segredo_anterior_enc TEXT;
ALTER TABLE tenant_webhooks_saida ADD COLUMN IF NOT EXISTS segredo_anterior_ate TIMESTAMPTZ;
ALTER TABLE tenant_webhooks_saida ADD COLUMN IF NOT EXISTS segredo_rotacionado_em TIMESTAMPTZ;
-- Filtro da lista de documentos por tipo e período.
CREATE INDEX IF NOT EXISTS ix_anexos_tipo ON anexos (tenant_id, tipo_documento, id DESC);
