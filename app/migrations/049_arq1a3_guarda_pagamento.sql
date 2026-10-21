-- ARQ1-a3 (decisão 32): guarda da resposta que afirma pagamento confirmado/recebido num atendimento com documento.
-- 'refeito' = o turno foi refeito uma vez com o recado; 'segurado' = repetiu e a resposta foi trocada pela segura (equipe avisada).
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS pagamento_afirmado TEXT CHECK (pagamento_afirmado IN ('refeito','segurado'));
