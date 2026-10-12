-- CAL1 (docs/ESPEC_CAL1.md): cidade e UF da unidade, para os feriados estaduais e municipais do calendário do pacote.
-- Aditivo: a imagem anterior segue funcionando. Unidade sem cidade recebe só os feriados nacionais.
ALTER TABLE tenant_unidades ADD COLUMN IF NOT EXISTS cidade TEXT;
ALTER TABLE tenant_unidades ADD COLUMN IF NOT EXISTS uf TEXT;
-- By Koji: as unidades são todas de São Paulo capital.
UPDATE tenant_unidades SET cidade='São Paulo',uf='SP' WHERE tenant_id='bykoji' AND cidade IS NULL AND uf IS NULL;
