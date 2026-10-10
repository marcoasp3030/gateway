-- UX1, bloco B2 (docs/ESPEC_BOTOES.md): liga/desliga dos botões interativos, com a herança de sempre
-- (plataforma → modelo → empresa → agente). Nulo herda; o padrão da plataforma é 'off'.
-- Colunas novas e nulas: a imagem anterior segue funcionando.
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS botoes_interativos TEXT CHECK (botoes_interativos IN ('on','off'));
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS botoes_interativos TEXT CHECK (botoes_interativos IN ('on','off'));
