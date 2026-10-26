-- Análise de comportamento (26/10/2026). Tudo aditivo.
-- Guarda da ação: motivo de transferência fora da lista do perfil, ou notificar_equipe com o recurso desligado.
-- 'refeito' = o turno foi refeito com aviso ao agente; 'segurado' = repetiu e a ação foi trocada pela segura.
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS acao_fora_da_regra TEXT CHECK (acao_fora_da_regra IN ('refeito','segurado'));
-- Emojis tirados dos balões quando o perfil pede "emojis: nenhum" (contagem; o texto original fica em turnos.resposta).
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS emojis_removidos INTEGER CHECK (emojis_removidos >= 0);
