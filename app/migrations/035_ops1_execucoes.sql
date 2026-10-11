-- OPS1 (docs/ESPEC_OPS1.md). Aditiva: a imagem anterior segue funcionando.
-- Registro de cada backup e de cada teste de restauração, escrito pelos scripts da VPS e lido pelo /health/ops e pelo painel.
CREATE TABLE IF NOT EXISTS ops_execucoes (
    id BIGSERIAL PRIMARY KEY,
    tipo TEXT NOT NULL CHECK (tipo IN ('backup','restauracao')),
    -- ok = feito e conferido (backup: enviado ao destino externo); local = backup só na VPS (destino não configurado); falha = não concluiu.
    resultado TEXT NOT NULL CHECK (resultado IN ('ok','local','falha')),
    inicio TIMESTAMPTZ NOT NULL,
    fim TIMESTAMPTZ NOT NULL DEFAULT now(),
    detalhe JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_ops_execucoes_tipo ON ops_execucoes (tipo, id DESC);
