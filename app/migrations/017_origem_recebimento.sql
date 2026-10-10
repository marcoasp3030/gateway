-- Fase 1: por onde a mensagem do canal chegou ('webhook' ou 'varredura' de reconciliação).
-- Nulo nas demais (API, simulação, sistema, respostas).
ALTER TABLE mensagens ADD COLUMN IF NOT EXISTS origem_recebimento TEXT;
