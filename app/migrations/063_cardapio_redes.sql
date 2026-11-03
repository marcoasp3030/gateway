-- CARD-REDES (03/11/2026): Instagram, Facebook e telefone da empresa no cardápio público. Opcional; por empresa (não por unidade).
-- Grava só o usuário, a página ou o número (55 + DDD + número, TEL1); o link é montado pelo gateway.
CREATE TABLE IF NOT EXISTS cardapio_redes (
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL CHECK (tipo IN ('instagram','facebook','telefone')),
    valor TEXT NOT NULL CHECK (char_length(valor) BETWEEN 1 AND 100),
    ativo BOOLEAN NOT NULL DEFAULT true,
    ordem SMALLINT NOT NULL DEFAULT 0,
    atualizado_por TEXT NOT NULL,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, tipo)
);
