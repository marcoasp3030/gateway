-- IFOOD1-b2 (docs/ESPEC_IFOOD1.md): eventos financeiros do iFood (vendas, comissões, taxas, cancelamentos) e estado das coletas. Aditiva.
-- A API não dá id ao evento: cada coleta substitui os eventos da loja no intervalo de datas coletado.
CREATE TABLE IF NOT EXISTS ifood_fin_eventos (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    merchant_id TEXT NOT NULL,
    data DATE NOT NULL,
    nome TEXT NOT NULL CHECK (char_length(nome) <= 80),
    descricao TEXT CHECK (char_length(descricao) <= 200),
    gatilho TEXT CHECK (char_length(gatilho) <= 80),
    produto TEXT CHECK (char_length(produto) <= 40),
    competencia TEXT CHECK (char_length(competencia) <= 7),
    ref_tipo TEXT CHECK (char_length(ref_tipo) <= 40),
    ref_id TEXT CHECK (char_length(ref_id) <= 100),
    ref_data DATE,
    com_repasse BOOLEAN NOT NULL DEFAULT false,
    valor NUMERIC(12,2) NOT NULL,
    base NUMERIC(12,2),
    taxa_pct NUMERIC(8,4),
    parcela TEXT CHECK (char_length(parcela) <= 20),
    pagamento_previsto DATE,
    forma_pagamento TEXT CHECK (char_length(forma_pagamento) <= 40),
    bandeira TEXT CHECK (char_length(bandeira) <= 40),
    responsabilidade TEXT CHECK (char_length(responsabilidade) <= 20)
);
CREATE INDEX IF NOT EXISTS ix_ifood_fin_loja_data ON ifood_fin_eventos (merchant_id, data);
CREATE INDEX IF NOT EXISTS ix_ifood_fin_tenant_data ON ifood_fin_eventos (tenant_id, data);
CREATE INDEX IF NOT EXISTS ix_ifood_fin_ref ON ifood_fin_eventos (tenant_id, ref_id);

CREATE TABLE IF NOT EXISTS ifood_coletas (
    merchant_id TEXT NOT NULL,
    area TEXT NOT NULL CHECK (area IN ('financeiro','avaliacoes')),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    iniciada_em TIMESTAMPTZ,
    sucesso_em TIMESTAMPTZ,
    coletado_desde DATE,
    erro TEXT CHECK (char_length(erro) <= 60),
    PRIMARY KEY (merchant_id, area)
);
