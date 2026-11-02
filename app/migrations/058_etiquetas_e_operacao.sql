-- Etiquetas da empresa, aplicadas às conversas.
CREATE TABLE etiquetas (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    nome TEXT NOT NULL CHECK (char_length(btrim(nome)) BETWEEN 1 AND 30),
    cor TEXT NOT NULL DEFAULT 'roxo' CHECK (cor IN ('roxo','dourado','verde','vermelho','cinza')),
    criado_por TEXT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_etiquetas_nome ON etiquetas (tenant_id, lower(nome));
CREATE TABLE conversa_etiquetas (
    tenant_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    etiqueta_id BIGINT NOT NULL REFERENCES etiquetas(id) ON DELETE CASCADE,
    aplicada_por TEXT NOT NULL,
    aplicada_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, conversation_id, etiqueta_id),
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversas (tenant_id, conversation_id) ON UPDATE CASCADE DEFERRABLE INITIALLY IMMEDIATE
);
CREATE INDEX ix_conversa_etiquetas_filtro ON conversa_etiquetas (tenant_id, etiqueta_id);

-- Operação: quem respondeu e quanto cada conversa esperou. A espera some de `conversas` quando alguém assume, então fica guardada aqui.
ALTER TABLE mensagens ADD COLUMN autor TEXT CHECK (autor IS NULL OR char_length(autor) BETWEEN 1 AND 200);
CREATE INDEX ix_mensagens_autor ON mensagens (tenant_id, enviada_em) WHERE autor IS NOT NULL;
CREATE TABLE conversa_esperas (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    inicio TIMESTAMPTZ NOT NULL,
    fim TIMESTAMPTZ NOT NULL DEFAULT now(),
    motivo TEXT,
    atendente TEXT,
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversas (tenant_id, conversation_id) ON UPDATE CASCADE DEFERRABLE INITIALLY IMMEDIATE
);
CREATE INDEX ix_conversa_esperas ON conversa_esperas (tenant_id, fim);
