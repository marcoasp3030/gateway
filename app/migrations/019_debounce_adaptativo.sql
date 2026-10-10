-- Fase 3, bloco 1: debounce adaptativo. Mensagem que parece completa (pontuação final ou
-- mais de debounce_palavras palavras) espera debounce_curto_s; as demais, debounce_s.
-- Nulo herda (agente → empresa → modelo → padrão da plataforma: 1,5 s e 6 palavras).
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS debounce_curto_s REAL CHECK (debounce_curto_s >= 0.5 AND debounce_curto_s <= 15);
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS debounce_palavras INTEGER CHECK (debounce_palavras BETWEEN 1 AND 50);
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS debounce_curto_s REAL CHECK (debounce_curto_s >= 0.5 AND debounce_curto_s <= 15);
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS debounce_palavras INTEGER CHECK (debounce_palavras BETWEEN 1 AND 50);
