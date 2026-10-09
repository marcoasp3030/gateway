ALTER TABLE tenant_webhooks_saida ADD UNIQUE(tenant_id,id);
CREATE TABLE webhook_entregas (
 id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), webhook_id TEXT NOT NULL,
 evento TEXT NOT NULL, corpo TEXT NOT NULL, tentativas INT NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'pendente' CHECK(status IN ('pendente','entregue','erro','cancelada')),
 proxima_em TIMESTAMPTZ NOT NULL DEFAULT now(), criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
 http_status INT, FOREIGN KEY(tenant_id,webhook_id) REFERENCES tenant_webhooks_saida(tenant_id,id)
);
CREATE INDEX ix_entregas_tenant ON webhook_entregas(tenant_id,id);
