-- Aparência do cardápio público: identidade, cores e imagens de cada empresa.
-- Sem linha, a página usa o visual padrão. Imagem fica no banco: entra no backup e não depende de disco gravável.
CREATE TABLE cardapio_imagens (
    id TEXT PRIMARY KEY CHECK (id ~ '^[0-9a-f]{32}$'),
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL CHECK (tipo IN ('logo','banner')),
    mime TEXT NOT NULL CHECK (mime IN ('image/png','image/jpeg','image/webp')),
    conteudo BYTEA NOT NULL,
    criado_por TEXT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_cardapio_imagens_tenant ON cardapio_imagens (tenant_id, criado_em);

CREATE TABLE cardapio_aparencia (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    nome_exibicao TEXT CHECK (char_length(nome_exibicao) <= 60),
    slogan TEXT CHECK (char_length(slogan) <= 120),
    cor_primaria TEXT NOT NULL CHECK (cor_primaria ~ '^#[0-9a-f]{6}$'),
    cor_destaque TEXT NOT NULL CHECK (cor_destaque ~ '^#[0-9a-f]{6}$'),
    cor_fundo TEXT NOT NULL CHECK (cor_fundo ~ '^#[0-9a-f]{6}$'),
    cantos TEXT NOT NULL DEFAULT 'suave' CHECK (cantos IN ('retos','suave','arredondado')),
    titulos TEXT NOT NULL DEFAULT 'classica' CHECK (titulos IN ('classica','moderna')),
    logo_id TEXT REFERENCES cardapio_imagens(id) ON DELETE SET NULL,
    banner_id TEXT REFERENCES cardapio_imagens(id) ON DELETE SET NULL,
    banner_foco INTEGER NOT NULL DEFAULT 50 CHECK (banner_foco BETWEEN 0 AND 100),
    atualizado_por TEXT NOT NULL,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
