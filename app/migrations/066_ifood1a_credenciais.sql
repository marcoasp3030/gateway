-- IFOOD1-a (docs/ESPEC_IFOOD1.md, decisão do Marco de 06/11/2026): credenciais do aplicativo do iFood gravadas pelo painel,
-- só com a chave de plataforma. Cifradas (Fernet do gateway); nunca devolvidas. Aditiva.
CREATE TABLE IF NOT EXISTS integracao_credenciais (
    provedor TEXT PRIMARY KEY CHECK (provedor IN ('ifood')),
    valor_enc TEXT NOT NULL,
    id_final TEXT NOT NULL CHECK (char_length(id_final) <= 4),
    atualizado_por TEXT NOT NULL,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
