-- PED1-b (docs/ESPEC_PED1.md). Aditiva: a imagem anterior segue funcionando (ela usa os textos padrão).
-- Texto e liga/desliga do aviso de status ao cliente, por empresa, status e modalidade. Nulo = padrão da plataforma.
CREATE TABLE IF NOT EXISTS pedido_avisos_config (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    status TEXT NOT NULL CHECK (status IN ('confirmado','em_preparo','pronto_retirada','saiu_entrega','concluido','recusado','cancelado')),
    modalidade TEXT NOT NULL CHECK (modalidade IN ('delivery','retirada')),
    ativo BOOLEAN,
    texto TEXT CHECK (char_length(texto) BETWEEN 1 AND 1000),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_por TEXT,
    PRIMARY KEY (tenant_id, status, modalidade),
    CHECK ((status <> 'pronto_retirada' OR modalidade = 'retirada') AND (status <> 'saiu_entrega' OR modalidade = 'delivery'))
);
