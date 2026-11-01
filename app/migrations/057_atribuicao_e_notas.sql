-- Responsável humano da conversa: vale enquanto a pausa do agente durar e a conversa estiver aberta.
ALTER TABLE conversas ADD COLUMN atribuida_a TEXT CHECK (atribuida_a IS NULL OR char_length(atribuida_a) BETWEEN 1 AND 200);
ALTER TABLE conversas ADD COLUMN atribuida_em TIMESTAMPTZ;
CREATE INDEX ix_conversas_atribuida ON conversas (tenant_id, atribuida_a) WHERE atribuida_a IS NOT NULL;

-- Notas internas da equipe. Tabela própria de propósito: o histórico do agente e o envio ao cliente só leem `mensagens`.
CREATE TABLE conversa_notas (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    atendimento_id TEXT,
    autor TEXT NOT NULL CHECK (char_length(autor) BETWEEN 1 AND 200),
    texto TEXT NOT NULL CHECK (char_length(btrim(texto)) BETWEEN 1 AND 2000),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversas (tenant_id, conversation_id) ON UPDATE CASCADE DEFERRABLE INITIALLY IMMEDIATE
);
CREATE INDEX ix_conversa_notas ON conversa_notas (tenant_id, conversation_id, id);
