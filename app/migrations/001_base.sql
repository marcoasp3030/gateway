CREATE EXTENSION IF NOT EXISTS unaccent;

-- Uma conversa por canal + identificador (ex.: wa:5511999999999, test:ana)
CREATE TABLE IF NOT EXISTS conversas (
    conversation_id   TEXT PRIMARY KEY,
    canal             TEXT NOT NULL,
    telefone          TEXT,
    nome              TEXT,
    unidade_preferida TEXT,
    bot_pausado_ate   TIMESTAMPTZ,
    criado_em         TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- direcao: entrada | saida ; origem: cliente | bot | humano | sistema
CREATE TABLE IF NOT EXISTS mensagens (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversas(conversation_id),
    direcao         TEXT NOT NULL,
    origem          TEXT NOT NULL,
    conteudo        TEXT NOT NULL,
    message_id      TEXT UNIQUE,          -- deduplicação de webhooks
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mensagens_conv ON mensagens (conversation_id, id DESC);

-- Payload bruto de todo webhook: serve para ajustar o normalizador
CREATE TABLE IF NOT EXISTS webhook_raw (
    id          BIGSERIAL PRIMARY KEY,
    fonte       TEXT NOT NULL,
    payload     JSONB NOT NULL,
    recebido_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Uma linha por resposta do agente: é aqui que medimos o tempo
CREATE TABLE IF NOT EXISTS turnos (
    id               BIGSERIAL PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    request_id       UUID NOT NULL,
    n_mensagens      INT NOT NULL,
    primeira_msg_em  TIMESTAMPTZ,
    ultima_msg_em    TIMESTAMPTZ,
    agente_inicio    TIMESTAMPTZ,
    agente_fim       TIMESTAMPTZ,
    enviado_em       TIMESTAMPTZ,
    espera_ms        INT,   -- da última msg do cliente até chamar o agente (debounce)
    agente_ms        INT,   -- tempo do Skyone (ou mock)
    total_ms         INT,   -- da última msg do cliente até a resposta enviada
    aviso_demora     BOOLEAN NOT NULL DEFAULT FALSE,
    modo_agente      TEXT,
    status           TEXT NOT NULL,      -- ok | erro
    erro             TEXT,
    resposta         JSONB
);
CREATE INDEX IF NOT EXISTS ix_turnos_id ON turnos (id DESC);

CREATE TABLE IF NOT EXISTS cardapio_itens (
    id            SERIAL PRIMARY KEY,
    unidade       TEXT NOT NULL,         -- Morumbi, Einstein, Vila Nova Conceição, Pátio Guedala, ou TODAS
    categoria     TEXT NOT NULL,
    nome          TEXT NOT NULL,
    descricao     TEXT,
    preco         NUMERIC(10,2) NOT NULL,
    ativo         BOOLEAN NOT NULL DEFAULT TRUE,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cardapio_unidade ON cardapio_itens (unidade, categoria);
