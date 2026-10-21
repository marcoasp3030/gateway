-- FUP1-a (docs/ESPEC_FUP1A.md, decisões de 21/10/2026): pendência declarada pelo agente, agendamento e cancelamento do follow-up,
-- opt-out. Só ensaio: nada é enviado (o envio é do FUP1-b). Tudo aditivo; nulo nas colunas de configuração = herda.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['tenant_config','agentes'] LOOP
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS follow_up_ativo TEXT CHECK (follow_up_ativo IN (''on'',''off''))',t);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS follow_up_escopo TEXT CHECK (follow_up_escopo IN (''pedido_em_andamento'',''pergunta_pendente''))',t);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS follow_up_maximo INTEGER CHECK (follow_up_maximo BETWEEN 0 AND 2)',t);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS follow_up_primeiro_min INTEGER CHECK (follow_up_primeiro_min BETWEEN 5 AND 1440)',t);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS follow_up_segundo_min INTEGER CHECK (follow_up_segundo_min BETWEEN 5 AND 1440)',t);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS follow_up_janela JSONB',t);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS follow_up_fora_unidade TEXT CHECK (follow_up_fora_unidade IN (''cancelar'',''adiar_abertura''))',t);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS follow_up_limite_dia INTEGER CHECK (follow_up_limite_dia BETWEEN 0 AND 5)',t);
    END LOOP;
END $$;

-- Pendência aceita no turno (auditoria): {"tipo", "pergunta"} já limpa de dado pessoal.
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS pendencia JSONB;

-- Fila de follow-ups (no Postgres: nada se perde numa queda do Redis).
-- estado: agendado | cancelado | ensaio_enviaria (FUP1-a) | gerando | enviado | falhou | incerto (FUP1-b).
CREATE TABLE IF NOT EXISTS follow_up_tarefas (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    atendimento_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    agente_id TEXT NOT NULL,
    contato_id TEXT,
    ordinal INTEGER NOT NULL CHECK (ordinal IN (1,2)),
    pendencia JSONB NOT NULL,
    previsto_em TIMESTAMPTZ NOT NULL,
    estado TEXT NOT NULL DEFAULT 'agendado' CHECK (estado IN ('agendado','cancelado','ensaio_enviaria','gerando','enviado','falhou','incerto')),
    motivo TEXT CHECK (motivo ~ '^[a-z0-9_]{1,60}$'),
    request_id UUID,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversas(tenant_id, conversation_id) ON UPDATE CASCADE,
    FOREIGN KEY (tenant_id, contato_id) REFERENCES contatos(tenant_id, id)
);
-- Uma tarefa agendada por atendimento.
CREATE UNIQUE INDEX IF NOT EXISTS ux_follow_up_agendado ON follow_up_tarefas (tenant_id, atendimento_id) WHERE estado='agendado';
CREATE INDEX IF NOT EXISTS ix_follow_up_vencidas ON follow_up_tarefas (previsto_em) WHERE estado='agendado';
CREATE INDEX IF NOT EXISTS ix_follow_up_conversa ON follow_up_tarefas (tenant_id, conversation_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_follow_up_contato_dia ON follow_up_tarefas (tenant_id, contato_id, criado_em);

-- Opt-out do follow-up por empresa e contato (vale entre atendimentos e agentes; avisos do pedido continuam).
CREATE TABLE IF NOT EXISTS follow_up_opt_out (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    contato_id TEXT NOT NULL,
    origem TEXT NOT NULL CHECK (origem IN ('agente','frase','painel')),
    actor TEXT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    removido_em TIMESTAMPTZ,
    removido_por TEXT,
    PRIMARY KEY (tenant_id, contato_id),
    FOREIGN KEY (tenant_id, contato_id) REFERENCES contatos(tenant_id, id)
);
