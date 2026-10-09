ALTER TABLE mensagens ADD COLUMN evento JSONB;
ALTER TABLE turnos ADD COLUMN origem TEXT NOT NULL DEFAULT 'cliente' CHECK(origem IN ('cliente','sistema'));
ALTER TABLE turnos ADD COLUMN uso JSONB;
CREATE INDEX ix_mensagens_sistema_pendentes ON mensagens(tenant_id,conversation_id,id)
 WHERE processada_em IS NULL AND origem='sistema' AND direcao='entrada';
