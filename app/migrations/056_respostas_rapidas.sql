-- Respostas rápidas do atendimento humano. Sem dono: vale para a equipe da empresa. Com dono: só aquela pessoa vê.
CREATE TABLE respostas_rapidas (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    atalho TEXT NOT NULL CHECK (atalho ~ '^[a-z0-9][a-z0-9_-]{0,29}$'),
    texto TEXT NOT NULL CHECK (char_length(btrim(texto)) BETWEEN 1 AND 2000),
    dono TEXT CHECK (dono IS NULL OR char_length(dono) BETWEEN 1 AND 200),
    usos INTEGER NOT NULL DEFAULT 0,
    criado_por TEXT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_respostas_rapidas_equipe ON respostas_rapidas (tenant_id, atalho) WHERE dono IS NULL;
CREATE UNIQUE INDEX ux_respostas_rapidas_pessoal ON respostas_rapidas (tenant_id, dono, atalho) WHERE dono IS NOT NULL;
