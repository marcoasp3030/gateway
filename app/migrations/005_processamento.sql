ALTER TABLE mensagens ADD COLUMN processada_em TIMESTAMPTZ;
CREATE INDEX ix_mensagens_pendentes ON mensagens(tenant_id,conversation_id,id)
 WHERE processada_em IS NULL AND origem='cliente' AND direcao='entrada';
