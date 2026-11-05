-- TELAS1 (03/11/2026): telas do painel que a plataforma bloqueia por empresa. Vazio = tudo liberado (padrão; ninguém perde acesso).
-- Guarda o que está bloqueado, não o que está liberado: tela nova do painel nasce liberada para quem já existe.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS telas_bloqueadas TEXT[] NOT NULL DEFAULT '{}';
