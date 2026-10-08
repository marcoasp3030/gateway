-- Operações de modelos globais têm escopo de plataforma, não de empresa fictícia.
ALTER TABLE audit_log ALTER COLUMN tenant_id DROP NOT NULL;
