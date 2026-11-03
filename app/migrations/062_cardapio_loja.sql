-- Link fixo da loja: o cliente monta o pedido sem conversa e finaliza pelo WhatsApp, com um código. Aditiva: a imagem anterior segue funcionando.
ALTER TABLE cardapio_links ADD COLUMN IF NOT EXISTS loja BOOLEAN NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS cardapio_links_loja_unica ON cardapio_links(tenant_id) WHERE loja;

CREATE TABLE IF NOT EXISTS cardapio_rascunhos (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    codigo TEXT NOT NULL CHECK (codigo ~ '^[A-Z0-9]{6}$'),
    dados JSONB NOT NULL,
    expira_em TIMESTAMPTZ NOT NULL,
    usado_em TIMESTAMPTZ,
    pedido_numero TEXT,
    total NUMERIC(12,2),
    erro TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,codigo)
);
CREATE INDEX IF NOT EXISTS cardapio_rascunhos_validade ON cardapio_rascunhos(tenant_id,expira_em);
