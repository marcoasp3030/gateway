-- Link do cardápio interativo. O token não é guardado: só o hash, e o nonce que, com o pepper, gera o mesmo link.
-- conversation_id nulo = prévia do painel, que nunca fecha pedido.
CREATE TABLE cardapio_links (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id TEXT,
    token_hash TEXT NOT NULL UNIQUE,
    nonce TEXT NOT NULL,
    expira_em TIMESTAMPTZ NOT NULL,
    usado_em TIMESTAMPTZ,
    pedido_numero TEXT,
    janela TIMESTAMPTZ,
    chamadas INTEGER NOT NULL DEFAULT 0,
    criado_por TEXT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Como nas demais tabelas da conversa: a anonimização troca o identificador, e o gancho do módulo apaga o link depois.
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversas(tenant_id, conversation_id) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE INDEX ix_cardapio_links_conversa ON cardapio_links (tenant_id, conversation_id, expira_em DESC);
CREATE INDEX ix_cardapio_links_expira ON cardapio_links (expira_em);
