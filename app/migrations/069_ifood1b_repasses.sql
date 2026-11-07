-- IFOOD1-b2 (docs/ESPEC_IFOOD1.md): repasses do iFood (títulos da API Settlement), sem dados bancários. Aditiva.
CREATE TABLE IF NOT EXISTS ifood_repasses (
    merchant_id TEXT NOT NULL,
    titulo_id TEXT NOT NULL CHECK (char_length(titulo_id) <= 100),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    tipo TEXT CHECK (char_length(tipo) <= 40),
    produto TEXT CHECK (char_length(produto) <= 40),
    valor NUMERIC(14,2) NOT NULL,
    status TEXT CHECK (char_length(status) <= 60),
    pagamento_em DATE,
    periodo_inicio DATE,
    periodo_fim DATE,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, titulo_id)
);
CREATE INDEX IF NOT EXISTS ix_ifood_repasses_tenant ON ifood_repasses (tenant_id, pagamento_em);
