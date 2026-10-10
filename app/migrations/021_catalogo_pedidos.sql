-- Fase 4, bloco 1: módulos catalogo e pedidos.

-- Itens do catálogo: chave estável por empresa (codigo), tipo, bebida alcoólica, delivery e observação.
-- Preço vazio é NULO (item sem preço não entra em carrinho); nunca zero.
ALTER TABLE catalogo_itens ALTER COLUMN preco DROP NOT NULL;
ALTER TABLE catalogo_itens ADD CONSTRAINT catalogo_itens_preco_check CHECK (preco IS NULL OR preco > 0);
ALTER TABLE catalogo_itens ADD COLUMN codigo TEXT CHECK (codigo ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$');
ALTER TABLE catalogo_itens ADD COLUMN tipo TEXT NOT NULL DEFAULT 'item' CHECK (tipo IN ('item','adicional','opcao_executivo','regra'));
ALTER TABLE catalogo_itens ADD COLUMN alcoolico BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE catalogo_itens ADD COLUMN delivery TEXT NOT NULL DEFAULT 'a_confirmar' CHECK (delivery IN ('sim','nao','a_confirmar'));
ALTER TABLE catalogo_itens ADD COLUMN observacao TEXT;
CREATE UNIQUE INDEX ux_catalogo_itens_codigo ON catalogo_itens (tenant_id, codigo) WHERE codigo IS NOT NULL;
ALTER TABLE catalogo_itens ADD CONSTRAINT catalogo_itens_tenant_id_id_key UNIQUE (tenant_id, id);

-- Configuração por unidade. Campo vazio = o agente não afirma nada e a unidade confirma no retorno do pré-pedido.
-- codigo liga a unidade aos itens do catálogo (catalogo_itens.unidade).
ALTER TABLE tenant_unidades ADD COLUMN codigo TEXT CHECK (codigo ~ '^[a-z0-9][a-z0-9_]{0,39}$');
ALTER TABLE tenant_unidades ADD COLUMN endereco TEXT;
ALTER TABLE tenant_unidades ADD COLUMN telefone TEXT;
ALTER TABLE tenant_unidades ADD COLUMN whatsapp_pedidos TEXT CHECK (whatsapp_pedidos ~ '^[0-9]{8,16}$');
ALTER TABLE tenant_unidades ADD COLUMN formas_pagamento TEXT;
ALTER TABLE tenant_unidades ADD COLUMN taxa_entrega NUMERIC(10,2) CHECK (taxa_entrega >= 0);
ALTER TABLE tenant_unidades ADD COLUMN pedido_minimo NUMERIC(10,2) CHECK (pedido_minimo >= 0);
ALTER TABLE tenant_unidades ADD COLUMN area_entrega TEXT;
ALTER TABLE tenant_unidades ADD COLUMN aceita_retirada BOOLEAN;
ALTER TABLE tenant_unidades ADD COLUMN regras TEXT;
CREATE UNIQUE INDEX ux_tenant_unidades_codigo ON tenant_unidades (tenant_id, codigo) WHERE codigo IS NOT NULL;

-- Carrinho: um aberto por conversa, de uma unidade só.
CREATE TABLE carrinhos (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    conversation_id TEXT NOT NULL,
    unidade TEXT,
    modalidade TEXT CHECK (modalidade IN ('delivery','retirada')),
    endereco TEXT,
    horario TEXT,
    observacao TEXT,
    maioridade_confirmada BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'aberto' CHECK (status IN ('aberto','fechado','cancelado')),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversas(tenant_id, conversation_id) ON UPDATE CASCADE
);
CREATE UNIQUE INDEX ux_carrinhos_aberto ON carrinhos (tenant_id, conversation_id) WHERE status = 'aberto';

-- Preço unitário copiado do catálogo no momento em que o item entra (nunca vem do agente).
CREATE TABLE carrinho_itens (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    carrinho_id BIGINT NOT NULL,
    item_id INTEGER NOT NULL,
    quantidade INTEGER NOT NULL CHECK (quantidade BETWEEN 1 AND 99),
    preco_unitario NUMERIC(10,2) NOT NULL CHECK (preco_unitario > 0),
    observacao TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, carrinho_id) REFERENCES carrinhos(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, item_id) REFERENCES catalogo_itens(tenant_id, id)
);
CREATE INDEX ix_carrinho_itens_carrinho ON carrinho_itens (tenant_id, carrinho_id);

-- Pré-pedido: só vale depois da confirmação da unidade.
CREATE TABLE pedidos (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    numero TEXT NOT NULL CHECK (numero ~ '^[A-Z0-9]{4,12}$'),
    conversation_id TEXT NOT NULL,
    carrinho_id BIGINT,
    unidade TEXT NOT NULL,
    modalidade TEXT NOT NULL CHECK (modalidade IN ('delivery','retirada')),
    endereco TEXT,
    horario TEXT,
    observacao TEXT,
    itens JSONB NOT NULL,
    total NUMERIC(10,2) NOT NULL CHECK (total > 0),
    status TEXT NOT NULL DEFAULT 'aguardando_loja' CHECK (status IN ('aguardando_loja','confirmado','recusado','cancelado')),
    motivo TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    decidido_em TIMESTAMPTZ,
    UNIQUE (tenant_id, numero),
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversas(tenant_id, conversation_id) ON UPDATE CASCADE,
    FOREIGN KEY (tenant_id, carrinho_id) REFERENCES carrinhos(tenant_id, id)
);
CREATE INDEX ix_pedidos_conversa ON pedidos (tenant_id, conversation_id, id DESC);
CREATE INDEX ix_pedidos_status ON pedidos (tenant_id, status, id DESC);
