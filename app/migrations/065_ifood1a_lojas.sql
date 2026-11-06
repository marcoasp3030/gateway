-- IFOOD1-a (docs/ESPEC_IFOOD1.md): loja do iFood ligada a uma empresa (e, se quiser, a uma unidade). Aditiva.
-- Só a plataforma liga: a lista de lojas que autorizaram o aplicativo traz as de todas as empresas.
CREATE TABLE IF NOT EXISTS ifood_lojas (
    merchant_id TEXT PRIMARY KEY CHECK (merchant_id ~ '^[A-Za-z0-9-]{1,64}$'),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    nome TEXT CHECK (char_length(nome) <= 200),
    unidade TEXT,
    ativo BOOLEAN NOT NULL DEFAULT true,
    situacao TEXT NOT NULL DEFAULT 'conectada' CHECK (situacao IN ('conectada','sem_permissao')),
    conferida_em TIMESTAMPTZ,
    ligado_por TEXT NOT NULL,
    ligado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, merchant_id)
);
CREATE INDEX IF NOT EXISTS ix_ifood_lojas_tenant ON ifood_lojas (tenant_id);
