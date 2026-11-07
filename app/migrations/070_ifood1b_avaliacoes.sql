-- IFOOD1-b3 (docs/ESPEC_IFOOD1.md): avaliações do iFood (Review v2) e o resumo de cada loja. Aditiva.
CREATE TABLE IF NOT EXISTS ifood_avaliacoes (
    merchant_id TEXT NOT NULL,
    review_id TEXT NOT NULL CHECK (char_length(review_id) <= 100),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    status TEXT CHECK (char_length(status) <= 40),
    visibilidade TEXT CHECK (char_length(visibilidade) <= 20),
    nota NUMERIC(3,1),
    comentario TEXT CHECK (char_length(comentario) <= 4000),
    cliente_nome TEXT CHECK (char_length(cliente_nome) <= 200),
    criado_em TIMESTAMPTZ,
    pedido_id TEXT CHECK (char_length(pedido_id) <= 100),
    pedido_numero TEXT CHECK (char_length(pedido_numero) <= 40),
    respostas JSONB NOT NULL DEFAULT '[]',
    perguntas JSONB NOT NULL DEFAULT '[]',
    -- Resposta pelo painel: quem e quando; simulada enquanto IFOOD_RESPOSTAS não for live.
    resposta_texto TEXT CHECK (char_length(resposta_texto) <= 300),
    respondida_por TEXT,
    respondida_em TIMESTAMPTZ,
    resposta_simulada BOOLEAN,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, review_id)
);
CREATE INDEX IF NOT EXISTS ix_ifood_avaliacoes_tenant ON ifood_avaliacoes (tenant_id, criado_em);

CREATE TABLE IF NOT EXISTS ifood_avaliacoes_resumo (
    merchant_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    nota NUMERIC(3,2),
    total INTEGER,
    validas INTEGER,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
