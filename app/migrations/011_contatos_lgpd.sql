ALTER TABLE contatos ADD COLUMN anonimizado_em TIMESTAMPTZ;
ALTER TABLE conversas ADD COLUMN contato_id TEXT;
ALTER TABLE conversas ADD FOREIGN KEY(tenant_id,contato_id) REFERENCES contatos(tenant_id,id);
CREATE INDEX ix_conversas_tenant_contato ON conversas(tenant_id,contato_id);
INSERT INTO contatos(id,tenant_id,telefone)
 SELECT 'ct_'||gen_random_uuid()::text,tenant_id,telefone FROM
 (SELECT DISTINCT tenant_id,telefone FROM conversas WHERE telefone IS NOT NULL) c
 ON CONFLICT(tenant_id,telefone) DO NOTHING;
UPDATE conversas v SET contato_id=c.id FROM contatos c WHERE c.tenant_id=v.tenant_id AND c.telefone=v.telefone;
ALTER TABLE webhook_raw ADD COLUMN contato_id TEXT;
ALTER TABLE webhook_raw ADD FOREIGN KEY(tenant_id,contato_id) REFERENCES contatos(tenant_id,id);
-- Permite substituir IDs de conversa com identificador pessoal atomicamente.
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT conrelid::regclass AS tabela,conname FROM pg_constraint
   WHERE contype='f' AND confrelid='conversas'::regclass
 LOOP EXECUTE format('ALTER TABLE %s ALTER CONSTRAINT %I DEFERRABLE INITIALLY IMMEDIATE',r.tabela,r.conname); END LOOP;
END $$;
