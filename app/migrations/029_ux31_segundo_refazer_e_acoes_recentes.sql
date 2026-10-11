-- UX3.1. Tudo aditivo: a imagem anterior segue funcionando.
-- Segundo refazer: motivos novos de recusa (o turno refeito já chamou Skill ou enviou balão; teto de espera do cliente).
ALTER TABLE turnos DROP CONSTRAINT IF EXISTS turnos_refazer_recusado_check;
ALTER TABLE turnos ADD CONSTRAINT turnos_refazer_recusado_check CHECK (refazer_recusado IN
    ('ja_refeito','acao_com_efeito','skill_com_efeito','skill_chamada','balao_enviado','teto_de_espera'));
-- acoes_recentes no pacote: chamadas de Skill da conversa nos últimos N minutos (nulo herda; padrão 15; 0 = lista sempre vazia).
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS acoes_recentes_min INTEGER CHECK (acoes_recentes_min BETWEEN 0 AND 1440);
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS acoes_recentes_min INTEGER CHECK (acoes_recentes_min BETWEEN 0 AND 1440);
