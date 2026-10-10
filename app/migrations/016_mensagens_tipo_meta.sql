-- Fase 1: tipo da mensagem (texto, audio, imagem, video, documento, localizacao, reacao...)
-- e dados estruturados do provedor (coordenadas, anexo, mensagem citada).
-- Colunas novas com padrão constante: não reescreve a tabela e a imagem anterior segue funcionando.
ALTER TABLE mensagens ADD COLUMN IF NOT EXISTS tipo TEXT NOT NULL DEFAULT 'texto';
ALTER TABLE mensagens ADD COLUMN IF NOT EXISTS meta JSONB;
