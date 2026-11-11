-- LOTE1-a (docs/ESPEC_NFSE1_LOTE1.md): envio de notas em lote (PDF, XML ou ZIP), lidas uma por vez em segundo plano. Aditiva.
-- O arquivo de cada nota é um anexo do ARQ1 (cifrado, mesma retenção), com origem 'lote'. Os campos lidos ficam só no resultado cifrado
-- do anexo; aqui fica só o estado, o motivo e a chave de junção (hash) para juntar PDF e XML da mesma nota.
ALTER TABLE anexos DROP CONSTRAINT IF EXISTS anexos_origem_check;
ALTER TABLE anexos ADD CONSTRAINT anexos_origem_check CHECK (origem IN ('api','formulario','simulador','whatsapp','lote'));

CREATE TABLE IF NOT EXISTS lotes (
    id TEXT PRIMARY KEY CHECK (id ~ '^lot_[0-9a-f]{24}$'),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    agente_id TEXT NOT NULL,
    nome TEXT CHECK (char_length(nome) <= 120),
    estado TEXT NOT NULL DEFAULT 'aberto' CHECK (estado IN ('aberto','lendo','concluido','expirado','apagado')),
    origem TEXT NOT NULL DEFAULT 'painel' CHECK (origem IN ('painel','api','ftp')),
    bytes_total BIGINT NOT NULL DEFAULT 0,
    criado_por TEXT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    fechado_em TIMESTAMPTZ,
    concluido_em TIMESTAMPTZ,
    expira_em TIMESTAMPTZ NOT NULL,
    apagado_em TIMESTAMPTZ,
    UNIQUE (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_lotes_empresa ON lotes (tenant_id, criado_em DESC);

CREATE TABLE IF NOT EXISTS lote_arquivos (
    id TEXT PRIMARY KEY CHECK (id ~ '^lar_[0-9a-f]{24}$'),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    lote_id TEXT NOT NULL REFERENCES lotes(id) ON DELETE CASCADE,
    anexo_id TEXT REFERENCES anexos(id),
    nome TEXT NOT NULL CHECK (char_length(nome) BETWEEN 1 AND 120),
    zip TEXT CHECK (char_length(zip) <= 120),
    formato TEXT CHECK (formato IN ('pdf','xml')),
    tamanho_bytes INTEGER,
    sha256 TEXT CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    estado TEXT NOT NULL CHECK (estado IN ('na_fila','lido','sem_texto','recusado','falhou','duplicado')),
    motivo TEXT CHECK (motivo ~ '^[a-z0-9_]{1,60}$'),
    juncao TEXT CHECK (juncao ~ '^[0-9a-f]{32}$'),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    lido_em TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_lote_arquivos_lote ON lote_arquivos (lote_id, criado_em, id);
CREATE INDEX IF NOT EXISTS ix_lote_arquivos_fila ON lote_arquivos (criado_em) WHERE estado='na_fila';
