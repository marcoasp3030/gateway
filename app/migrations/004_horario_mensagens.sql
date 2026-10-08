ALTER TABLE mensagens ADD COLUMN enviada_em TIMESTAMPTZ;
UPDATE mensagens SET enviada_em=criado_em WHERE enviada_em IS NULL;
ALTER TABLE mensagens ALTER COLUMN enviada_em SET DEFAULT now();
ALTER TABLE mensagens ALTER COLUMN enviada_em SET NOT NULL;
CREATE INDEX ix_mensagens_tenant_historico ON mensagens(tenant_id,conversation_id,enviada_em DESC,id DESC);
