-- IFOOD1-b1 (docs/ESPEC_IFOOD1.md): pedidos do iFood recebidos por webhook, só leitura. Aditiva.
-- Sem telefone, documento, e-mail nem endereço do cliente: só nome, o id do cliente no iFood e o bairro.
CREATE TABLE IF NOT EXISTS ifood_eventos (
    id TEXT PRIMARY KEY CHECK (char_length(id) <= 100),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    merchant_id TEXT NOT NULL,
    order_id TEXT CHECK (char_length(order_id) <= 100),
    codigo TEXT NOT NULL CHECK (char_length(codigo) <= 60),
    criado_em TIMESTAMPTZ,
    recebido_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ifood_eventos_pedido ON ifood_eventos (tenant_id, order_id);

CREATE TABLE IF NOT EXISTS ifood_pedidos (
    order_id TEXT PRIMARY KEY CHECK (char_length(order_id) <= 100),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    merchant_id TEXT NOT NULL,
    numero TEXT CHECK (char_length(numero) <= 40),
    status TEXT NOT NULL DEFAULT 'PLACED' CHECK (char_length(status) <= 60),
    teste BOOLEAN NOT NULL DEFAULT false,
    criado_em TIMESTAMPTZ NOT NULL,
    tipo TEXT,
    momento TEXT,
    canal_venda TEXT,
    categoria TEXT,
    subtotal NUMERIC(12,2),
    taxa_entrega NUMERIC(12,2),
    descontos NUMERIC(12,2),
    taxas_adicionais NUMERIC(12,2),
    total NUMERIC(12,2),
    pagamentos JSONB NOT NULL DEFAULT '[]',
    itens JSONB NOT NULL DEFAULT '[]',
    cliente_id TEXT,
    cliente_nome TEXT CHECK (char_length(cliente_nome) <= 200),
    bairro TEXT CHECK (char_length(bairro) <= 120),
    detalhado_em TIMESTAMPTZ,
    tentativas INTEGER NOT NULL DEFAULT 0,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, order_id)
);
CREATE INDEX IF NOT EXISTS ix_ifood_pedidos_data ON ifood_pedidos (tenant_id, criado_em);
CREATE INDEX IF NOT EXISTS ix_ifood_pedidos_pendentes ON ifood_pedidos (detalhado_em) WHERE detalhado_em IS NULL;
