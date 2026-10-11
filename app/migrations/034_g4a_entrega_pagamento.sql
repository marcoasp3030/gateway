-- Gestão, bloco G4-a (docs/ESPEC_GESTAO.md, seção 11): cadastro de entrega e pagamento da unidade.
-- Só cadastro: carrinho e pedido não leem nada daqui até o G4-b (as colunas deles entram lá).
-- Nulo = não informado; unidade sem nada configurado continua como hoje ("a unidade confirma").
ALTER TABLE tenant_unidades ADD COLUMN delivery_ativo BOOLEAN;
ALTER TABLE tenant_unidades ADD COLUMN taxa_modo TEXT CHECK (taxa_modo IN ('fixa','bairro','cep'));
ALTER TABLE tenant_unidades ADD COLUMN frete_gratis_acima NUMERIC(10,2) CHECK (frete_gratis_acima > 0);
ALTER TABLE tenant_unidades ADD COLUMN tempo_entrega_min INTEGER CHECK (tempo_entrega_min BETWEEN 1 AND 600);
ALTER TABLE tenant_unidades ADD COLUMN tempo_retirada_min INTEGER CHECK (tempo_retirada_min BETWEEN 1 AND 600);

-- Taxa por bairro ou por faixa de CEP. *_busca = forma de comparação (sem acento, minúscula, sem pontuação).
CREATE TABLE unidade_taxas (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    unidade_id TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('bairro','cep')),
    ordem INTEGER NOT NULL DEFAULT 0,
    bairro TEXT,
    bairro_busca TEXT,
    apelidos TEXT[] NOT NULL DEFAULT '{}',
    apelidos_busca TEXT[] NOT NULL DEFAULT '{}',
    atendido BOOLEAN NOT NULL DEFAULT true,
    cep_inicio CHAR(8) CHECK (cep_inicio ~ '^[0-9]{8}$'),
    cep_fim CHAR(8) CHECK (cep_fim ~ '^[0-9]{8}$'),
    taxa NUMERIC(6,2) CHECK (taxa BETWEEN 0 AND 999.99),
    ativo BOOLEAN NOT NULL DEFAULT true,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, unidade_id) REFERENCES tenant_unidades(tenant_id, id) ON DELETE CASCADE,
    CHECK ((tipo = 'bairro' AND bairro IS NOT NULL AND bairro_busca IS NOT NULL AND cep_inicio IS NULL AND cep_fim IS NULL)
        OR (tipo = 'cep' AND bairro IS NULL AND cep_inicio IS NOT NULL AND cep_fim IS NOT NULL AND cep_inicio <= cep_fim AND atendido)),
    -- Linha atendida tem taxa; bairro marcado como não atendido não tem.
    CHECK ((atendido AND taxa IS NOT NULL) OR (NOT atendido AND taxa IS NULL))
);
CREATE INDEX ix_unidade_taxas ON unidade_taxas (tenant_id, unidade_id, ordem, id);

-- Formas de pagamento aceitas pela unidade, na ordem mostrada ao cliente.
CREATE TABLE unidade_pagamentos (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    unidade_id TEXT NOT NULL,
    codigo TEXT NOT NULL CHECK (codigo ~ '^[a-z0-9_]{2,30}$'),
    rotulo TEXT NOT NULL CHECK (length(rotulo) BETWEEN 2 AND 40),
    aceita_troco BOOLEAN NOT NULL DEFAULT false CHECK (NOT aceita_troco OR codigo = 'dinheiro'),
    instrucoes TEXT CHECK (length(instrucoes) <= 300),
    ativo BOOLEAN NOT NULL DEFAULT true,
    ordem INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, unidade_id, codigo),
    FOREIGN KEY (tenant_id, unidade_id) REFERENCES tenant_unidades(tenant_id, id) ON DELETE CASCADE
);
