-- CAT3-a (docs/ESPEC_CAT3.md, decisões de 20/10/2026). Tudo aditivo: a imagem anterior segue funcionando enquanto esta
-- migração é aplicada (o gatilho liga à categoria os itens que ela gravar pelo texto `categoria`, como sempre).
-- Nada muda para o agente enquanto ninguém ordenar categorias ou itens, criar prato, esgotar ou arquivar item:
-- posição nula = a ordem de hoje (categorias pelo primeiro item importado; itens pelo nome).

-- Prefixo do código gerado para itens da unidade (GUE-0224). Preenchido com o que os códigos de hoje já usam.
ALTER TABLE tenant_unidades ADD COLUMN IF NOT EXISTS prefixo_codigo TEXT CHECK (prefixo_codigo ~ '^[A-Z]{2,4}$');
CREATE UNIQUE INDEX IF NOT EXISTS ux_tenant_unidades_prefixo ON tenant_unidades (tenant_id, prefixo_codigo) WHERE prefixo_codigo IS NOT NULL;
WITH contagem AS (
    SELECT tenant_id,unidade,substring(codigo from '^([A-Z]{2,4})-[0-9]+$') AS p,count(*) AS n
    FROM catalogo_itens WHERE codigo ~ '^[A-Z]{2,4}-[0-9]+$' GROUP BY 1,2,3),
mais_usado AS (SELECT DISTINCT ON (tenant_id,unidade) tenant_id,unidade,p FROM contagem ORDER BY tenant_id,unidade,n DESC,p),
sem_repetir AS (SELECT m.* FROM mais_usado m WHERE (SELECT count(*) FROM mais_usado o WHERE o.tenant_id=m.tenant_id AND o.p=m.p)=1)
UPDATE tenant_unidades u SET prefixo_codigo=s.p FROM sem_repetir s
    WHERE u.tenant_id=s.tenant_id AND u.codigo=s.unidade AND u.prefixo_codigo IS NULL;

-- Categorias como cadastro, por unidade. posicao nula = ordem do cardápio importado (a de hoje).
-- nomes_anteriores: até 3 nomes antigos, que a busca do agente ainda reconhece.
CREATE TABLE IF NOT EXISTS catalogo_categorias (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    unidade TEXT NOT NULL,
    nome TEXT NOT NULL CHECK (char_length(btrim(nome)) BETWEEN 1 AND 120),
    posicao INTEGER CHECK (posicao > 0),
    nomes_anteriores TEXT[] NOT NULL DEFAULT '{}',
    arquivada_em TIMESTAMPTZ,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, unidade, nome)
);
INSERT INTO catalogo_categorias (tenant_id,unidade,nome)
    SELECT tenant_id,unidade,categoria FROM catalogo_itens GROUP BY 1,2,3 ORDER BY 1,2,min(id)
    ON CONFLICT (tenant_id,unidade,nome) DO NOTHING;

-- Mesmo prato em várias unidades: ligação confirmada pelo admin (código comum P-0042).
CREATE TABLE IF NOT EXISTS catalogo_pratos (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    codigo TEXT NOT NULL CHECK (codigo ~ '^P-[0-9]{4,6}$'),
    nome TEXT NOT NULL CHECK (char_length(btrim(nome)) BETWEEN 1 AND 120),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, codigo)
);
-- Sugestões de ligação ignoradas pelo admin (chave = nome normalizado; itens = os que estavam na sugestão).
-- Item novo com o mesmo nome faz a sugestão voltar.
CREATE TABLE IF NOT EXISTS catalogo_sugestoes_ignoradas (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    chave TEXT NOT NULL,
    itens INTEGER[] NOT NULL,
    actor TEXT NOT NULL,
    ignorado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, chave)
);

ALTER TABLE catalogo_itens ADD COLUMN IF NOT EXISTS categoria_id BIGINT;
ALTER TABLE catalogo_itens ADD COLUMN IF NOT EXISTS posicao INTEGER CHECK (posicao > 0);
ALTER TABLE catalogo_itens ADD COLUMN IF NOT EXISTS prato_id BIGINT;
ALTER TABLE catalogo_itens ADD COLUMN IF NOT EXISTS arquivado_em TIMESTAMPTZ;
-- Esgotado enquanto esgotado_ate > hora do pedido: volta sozinho, sem tarefa no agendador (decisão 6).
ALTER TABLE catalogo_itens ADD COLUMN IF NOT EXISTS esgotado_ate TIMESTAMPTZ;
ALTER TABLE catalogo_itens ADD COLUMN IF NOT EXISTS esgotado_motivo TEXT CHECK (char_length(esgotado_motivo) <= 200);
UPDATE catalogo_itens i SET categoria_id=c.id FROM catalogo_categorias c
    WHERE i.categoria_id IS NULL AND c.tenant_id=i.tenant_id AND c.unidade=i.unidade AND c.nome=i.categoria;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='catalogo_itens_categoria_fk') THEN
        ALTER TABLE catalogo_itens ADD CONSTRAINT catalogo_itens_categoria_fk FOREIGN KEY (tenant_id,categoria_id) REFERENCES catalogo_categorias(tenant_id,id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='catalogo_itens_prato_fk') THEN
        ALTER TABLE catalogo_itens ADD CONSTRAINT catalogo_itens_prato_fk FOREIGN KEY (tenant_id,prato_id) REFERENCES catalogo_pratos(tenant_id,id);
    END IF;
END $$;
-- Um item por unidade em cada prato.
CREATE UNIQUE INDEX IF NOT EXISTS ux_catalogo_itens_prato_unidade ON catalogo_itens (tenant_id, prato_id, unidade) WHERE prato_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_catalogo_itens_categoria ON catalogo_itens (tenant_id, categoria_id);

-- Texto `categoria` e categoria_id andam juntos: trocar a categoria pelo id copia o nome; gravar pelo texto (CSV, CAT2, imagem
-- anterior) acha a categoria da unidade com esse nome, ou cria no fim.
CREATE OR REPLACE FUNCTION cat3_categoria_do_item() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cid BIGINT;
BEGIN
    IF TG_OP='UPDATE' AND NEW.categoria_id IS NOT NULL AND NEW.categoria_id IS DISTINCT FROM OLD.categoria_id THEN
        SELECT nome INTO NEW.categoria FROM catalogo_categorias WHERE tenant_id=NEW.tenant_id AND id=NEW.categoria_id;
        RETURN NEW;
    END IF;
    IF NEW.categoria_id IS NULL OR (TG_OP='UPDATE' AND (NEW.categoria IS DISTINCT FROM OLD.categoria OR NEW.unidade IS DISTINCT FROM OLD.unidade)) THEN
        INSERT INTO catalogo_categorias (tenant_id,unidade,nome) VALUES (NEW.tenant_id,NEW.unidade,NEW.categoria)
            ON CONFLICT (tenant_id,unidade,nome) DO NOTHING;
        SELECT id INTO cid FROM catalogo_categorias WHERE tenant_id=NEW.tenant_id AND unidade=NEW.unidade AND nome=NEW.categoria;
        NEW.categoria_id := cid;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS cat3_categoria ON catalogo_itens;
CREATE TRIGGER cat3_categoria BEFORE INSERT OR UPDATE ON catalogo_itens FOR EACH ROW EXECUTE FUNCTION cat3_categoria_do_item();
