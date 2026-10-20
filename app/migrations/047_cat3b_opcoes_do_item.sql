-- CAT3-b (docs/ESPEC_CAT3.md, seção 6): opções do item (grupos de complementos). Tudo aditivo: a imagem anterior segue funcionando
-- (item sem grupo = como hoje; linha de carrinho sem escolha = opcoes '[]' e preco_base nulo, lido como o preço unitário).
CREATE TABLE IF NOT EXISTS catalogo_grupos (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    item_id INTEGER NOT NULL,
    nome TEXT NOT NULL CHECK (char_length(btrim(nome)) BETWEEN 1 AND 60),
    -- obrigatório ⇔ minimo ≥ 1 (decisão 10: até 20 escolhas por grupo).
    minimo INTEGER NOT NULL DEFAULT 0 CHECK (minimo BETWEEN 0 AND 20),
    maximo INTEGER NOT NULL DEFAULT 1 CHECK (maximo BETWEEN 1 AND 20),
    repetir BOOLEAN NOT NULL DEFAULT false,
    posicao INTEGER NOT NULL DEFAULT 1,
    ativo BOOLEAN NOT NULL DEFAULT true,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (minimo <= maximo),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, item_id) REFERENCES catalogo_itens(tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_catalogo_grupos_item ON catalogo_grupos (tenant_id, item_id, posicao);
CREATE TABLE IF NOT EXISTS catalogo_opcoes (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    grupo_id BIGINT NOT NULL,
    nome TEXT NOT NULL CHECK (char_length(btrim(nome)) BETWEEN 1 AND 60),
    preco_extra NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (preco_extra >= 0),
    codigo TEXT CHECK (codigo ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$'),
    posicao INTEGER NOT NULL DEFAULT 1,
    ativo BOOLEAN NOT NULL DEFAULT true,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, grupo_id) REFERENCES catalogo_grupos(tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_catalogo_opcoes_grupo ON catalogo_opcoes (tenant_id, grupo_id, posicao);
-- Quando o conjunto de grupos e opções do item mudou pela última vez (conferência de edição concorrente no painel).
ALTER TABLE catalogo_itens ADD COLUMN IF NOT EXISTS opcoes_atualizado_em TIMESTAMPTZ;

-- Linha do carrinho: escolhas COPIADAS (grupo, opção, quantidade e preço extra no momento) e o preço base do item.
-- preco_unitario = preco_base + extras. assinatura = as escolhas em forma comparável: mesmo item com escolhas iguais soma.
ALTER TABLE carrinho_itens ADD COLUMN IF NOT EXISTS preco_base NUMERIC(10,2) CHECK (preco_base > 0);
ALTER TABLE carrinho_itens ADD COLUMN IF NOT EXISTS opcoes JSONB NOT NULL DEFAULT '[]';
ALTER TABLE carrinho_itens ADD COLUMN IF NOT EXISTS assinatura TEXT NOT NULL DEFAULT '';
