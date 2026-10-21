-- ARQ1-a1 (docs/ESPEC_ARQ1.md, seções 13 e 14, decisões de 21/10/2026): receber e ler documentos por API (chave da empresa ou
-- chave de formulário). Tudo aditivo: a imagem anterior segue funcionando (capacidade ausente = desligada em todos os modelos).

-- Capacidades declaradas na versão do modelo de agente (MULTI1): {"documentos": {"ativo", "tipos", "max_bytes", "max_paginas", "retencao_dias", …}}.
-- No agente só restringe (desligar, menos tipos, menos tamanho, menos retenção).
ALTER TABLE modelos_agente ADD COLUMN IF NOT EXISTS capacidades JSONB NOT NULL DEFAULT '{}';
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS capacidades JSONB NOT NULL DEFAULT '{}';

-- Chave de formulário: tenant_api_keys com tipo 'formulario', presa a uma empresa e a um agente, só para enviar documento,
-- com limite por minuto, tamanho, tipos, modos e origens (navegador) próprios. Revogada pelo painel como as demais.
ALTER TABLE tenant_api_keys ADD COLUMN IF NOT EXISTS tipo TEXT NOT NULL DEFAULT 'integracao' CHECK (tipo IN ('integracao','formulario'));
ALTER TABLE tenant_api_keys ADD COLUMN IF NOT EXISTS limite_por_minuto INTEGER CHECK (limite_por_minuto BETWEEN 1 AND 600);
ALTER TABLE tenant_api_keys ADD COLUMN IF NOT EXISTS max_bytes INTEGER CHECK (max_bytes BETWEEN 1024 AND 15728640);
ALTER TABLE tenant_api_keys ADD COLUMN IF NOT EXISTS tipos_aceitos TEXT[];
ALTER TABLE tenant_api_keys ADD COLUMN IF NOT EXISTS modos TEXT[];
ALTER TABLE tenant_api_keys ADD COLUMN IF NOT EXISTS origens_permitidas TEXT[];
ALTER TABLE tenant_api_keys ADD COLUMN IF NOT EXISTS revogada_por TEXT;

-- Documento recebido. Bytes cifrados no volume `arquivos` (nunca no banco); o resultado da leitura, cifrado, em `resultado`.
-- A chave de dados de cada documento vai embrulhada pela chave mestra (ARQUIVOS_CHAVE_MESTRA) em `chave_cifrada`.
CREATE TABLE IF NOT EXISTS anexos (
    id TEXT PRIMARY KEY CHECK (id ~ '^anx_[0-9a-f]{24}$'),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    agente_id TEXT,
    conversation_id TEXT,
    atendimento_id TEXT,
    mensagem_id BIGINT,
    -- Por onde chegou: api (chave da empresa), formulario (chave de formulário), simulador (painel); whatsapp fica para depois.
    origem TEXT NOT NULL CHECK (origem IN ('api','formulario','simulador','whatsapp')),
    chave_id TEXT,
    modo TEXT NOT NULL CHECK (modo IN ('extrair','agente')),
    nome TEXT NOT NULL CHECK (char_length(nome) BETWEEN 1 AND 120),
    formato TEXT NOT NULL CHECK (formato IN ('pdf','xml','jpg','png','webp')),
    tamanho_bytes INTEGER NOT NULL CHECK (tamanho_bytes > 0),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    estado TEXT NOT NULL DEFAULT 'processando' CHECK (estado IN ('processando','pronto','sem_texto','rejeitado','falhou','expirado','apagado')),
    motivo TEXT CHECK (motivo ~ '^[a-z0-9_]{1,60}$'),
    paginas INTEGER,
    tipo_documento TEXT,
    referencia TEXT CHECK (char_length(referencia) <= 100),
    idempotencia TEXT CHECK (char_length(idempotencia) <= 100),
    chave_versao INTEGER,
    chave_cifrada BYTEA,
    caminho TEXT,
    resultado BYTEA,
    tentativas INTEGER NOT NULL DEFAULT 0,
    processando_ate TIMESTAMPTZ,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    processado_em TIMESTAMPTZ,
    expira_em TIMESTAMPTZ NOT NULL,
    apagado_em TIMESTAMPTZ,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversas(tenant_id, conversation_id) ON UPDATE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_anexos_idempotencia ON anexos (tenant_id, chave_id, idempotencia) WHERE idempotencia IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_anexos_conversa ON anexos (tenant_id, conversation_id, criado_em DESC);
CREATE INDEX IF NOT EXISTS ix_anexos_pendentes ON anexos (estado, processando_ate) WHERE estado='processando';
CREATE INDEX IF NOT EXISTS ix_anexos_expira ON anexos (expira_em) WHERE apagado_em IS NULL;
CREATE INDEX IF NOT EXISTS ix_anexos_chave ON anexos (tenant_id, chave_id, criado_em DESC);
