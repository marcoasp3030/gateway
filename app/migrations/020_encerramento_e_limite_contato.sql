-- Fase 3, bloco 3.
-- Encerramento: quando o agente devolve "encerrar" (ou a conversa fica vazia), a conversa guarda
-- quando e como terminou. Uma nova mensagem do cliente reabre (os dois campos voltam a nulo).
ALTER TABLE conversas ADD COLUMN IF NOT EXISTS encerrada_em TIMESTAMPTZ;
ALTER TABLE conversas ADD COLUMN IF NOT EXISTS desfecho TEXT;
ALTER TABLE conversas DROP CONSTRAINT IF EXISTS conversas_encerramento_check;
ALTER TABLE conversas ADD CONSTRAINT conversas_encerramento_check CHECK (desfecho IS NULL OR encerrada_em IS NOT NULL);
-- SEG07 por contato: contato que manda mensagens demais em um minuto fica sem chamar o agente até este instante.
ALTER TABLE conversas ADD COLUMN IF NOT EXISTS limitado_ate TIMESTAMPTZ;
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS max_msgs_por_minuto_contato INTEGER CHECK (max_msgs_por_minuto_contato BETWEEN 1 AND 600);
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS max_msgs_por_minuto_contato INTEGER CHECK (max_msgs_por_minuto_contato BETWEEN 1 AND 600);
