-- DISP1 (docs/ESPEC_DISP1.md): disponibilidade do item por dia, feriado e horário; itens vistos na busca; valores não pessoais no registro de Skills.
-- Tudo aditivo: a imagem anterior segue funcionando.
-- Nulo = sem restrição (todo item que já existe). {"dias_semana":["seg",…],"exceto_feriado":true,"horario":[["11:30","15:00"]]}
ALTER TABLE catalogo_itens ADD COLUMN IF NOT EXISTS disponibilidade JSONB;
-- Itens que apareceram num resultado de buscar_catalogo da conversa: adicionar_item só aceita o que foi buscado há pouco.
CREATE TABLE IF NOT EXISTS catalogo_vistos (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    conversation_id TEXT NOT NULL,
    item_id BIGINT NOT NULL,
    visto_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, conversation_id, item_id)
);
CREATE INDEX IF NOT EXISTS ix_catalogo_vistos_tempo ON catalogo_vistos (tenant_id, visto_em);
-- Valores da chamada que não são dado de cliente (item, unidade, quantidade, página…), numa lista fechada por Skill.
ALTER TABLE skill_registro ADD COLUMN IF NOT EXISTS ref JSONB;
-- By Koji: os 4 almoços executivos (decisão 4 do Marco). Dias e feriado como no texto de cada item; a faixa de horário
-- fica sem preencher até o Marco informar a de cada unidade.
UPDATE catalogo_itens SET disponibilidade='{"dias_semana":["seg","ter","qua","qui","sex"],"exceto_feriado":true}'::jsonb
    WHERE tenant_id='bykoji' AND codigo IN ('EIN-0001','GUE-0001','VNC-0001') AND disponibilidade IS NULL;
UPDATE catalogo_itens SET disponibilidade='{"dias_semana":["ter","qua","qui","sex"],"exceto_feriado":true}'::jsonb
    WHERE tenant_id='bykoji' AND codigo='MOR-0001' AND disponibilidade IS NULL;
