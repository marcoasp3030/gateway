-- Gestão, bloco G1 (docs/ESPEC_GESTAO.md): horários da unidade.
-- Fuso próprio (nulo herda o da empresa) e pausa manual ("fechar agora até HH:MM" / "até reabrir").
ALTER TABLE tenant_unidades ADD COLUMN fuso TEXT;
ALTER TABLE tenant_unidades ADD COLUMN pausa_ate TIMESTAMPTZ;
ALTER TABLE tenant_unidades ADD COLUMN pausa_indefinida BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE tenant_unidades ADD COLUMN pausa_motivo TEXT;
ALTER TABLE tenant_unidades ADD CONSTRAINT tenant_unidades_pausa_check CHECK (NOT (pausa_indefinida AND pausa_ate IS NOT NULL));

-- Exceções por data: fechado o dia todo ou horário especial, no lugar da grade semanal daquele dia.
CREATE TABLE unidade_excecoes (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    unidade_id TEXT NOT NULL,
    data DATE NOT NULL,
    fechado BOOLEAN NOT NULL DEFAULT false,
    faixas JSONB NOT NULL DEFAULT '[]',
    motivo TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, unidade_id, data),
    FOREIGN KEY (tenant_id, unidade_id) REFERENCES tenant_unidades(tenant_id, id) ON DELETE CASCADE,
    -- Ou fechado, ou com faixas; nunca os dois nem nenhum.
    CHECK (fechado = (jsonb_array_length(faixas) = 0))
);
CREATE INDEX ix_unidade_excecoes_data ON unidade_excecoes (tenant_id, unidade_id, data);

-- Histórico das alterações de gestão, com antes e depois (só os campos que mudaram).
-- São dados de configuração do negócio (horário, preço, taxa), não de cliente.
CREATE TABLE gestao_alteracoes (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    actor TEXT NOT NULL,
    entidade TEXT NOT NULL,
    entidade_id TEXT NOT NULL,
    acao TEXT NOT NULL,
    antes JSONB,
    depois JSONB,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_gestao_alteracoes ON gestao_alteracoes (tenant_id, id DESC);
CREATE INDEX ix_gestao_alteracoes_entidade ON gestao_alteracoes (tenant_id, entidade, entidade_id, id DESC);
