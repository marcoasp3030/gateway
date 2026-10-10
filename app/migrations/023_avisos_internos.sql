-- Fase 4, bloco 3: avisos que o gateway envia por conta própria (não são resposta do agente):
-- pedido novo para a unidade, resposta à unidade, alerta de transbordo e aviso ao cliente sobre o pedido.
-- Ficam numa caixa de saída gravada na mesma transação do fato; o agendador envia depois.
ALTER TABLE tenants ADD COLUMN whatsapp_alerta_transbordo TEXT CHECK (whatsapp_alerta_transbordo ~ '^[0-9]{8,16}$');

CREATE TABLE avisos_internos (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('pedido_unidade','resposta_unidade','transbordo','pedido_cliente')),
    -- Telefone de destino (unidade ou alerta); nulo quando o destino é o cliente da conversa.
    destino TEXT CHECK (destino ~ '^[0-9]{8,16}$'),
    conversation_id TEXT,
    -- Identifica o fato avisado: o mesmo fato nunca gera dois avisos.
    referencia TEXT NOT NULL,
    texto TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente','enviando','enviado','simulado','descartado','falhou')),
    tentativas INTEGER NOT NULL DEFAULT 0,
    erro TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    enviado_em TIMESTAMPTZ,
    UNIQUE (tenant_id, tipo, referencia),
    CHECK ((tipo = 'pedido_cliente') = (destino IS NULL)),
    CHECK (tipo <> 'pedido_cliente' OR conversation_id IS NOT NULL)
);
CREATE INDEX ix_avisos_pendentes ON avisos_internos (id) WHERE status IN ('pendente','enviando');
CREATE INDEX ix_avisos_tenant ON avisos_internos (tenant_id, id DESC);
