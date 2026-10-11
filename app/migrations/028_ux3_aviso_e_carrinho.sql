-- UX3. Tudo aditivo: a imagem anterior segue funcionando.
-- Aviso de demora não sai se o bot falou há pouco na conversa (nulo herda; padrão da plataforma 120 s; 0 desliga a regra).
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS aviso_demora_janela_s INTEGER CHECK (aviso_demora_janela_s BETWEEN 0 AND 3600);
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS aviso_demora_janela_s INTEGER CHECK (aviso_demora_janela_s BETWEEN 0 AND 3600);
-- Carrinho parado há mais de N horas é arquivado na próxima mensagem do cliente (nulo herda; padrão 6 h).
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS carrinho_expira_h INTEGER CHECK (carrinho_expira_h BETWEEN 1 AND 720);
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS carrinho_expira_h INTEGER CHECK (carrinho_expira_h BETWEEN 1 AND 720);
-- Estado novo do carrinho: expirado (os itens ficam guardados para o agente oferecer refazer).
ALTER TABLE carrinhos DROP CONSTRAINT IF EXISTS carrinhos_status_check;
ALTER TABLE carrinhos ADD CONSTRAINT carrinhos_status_check CHECK (status IN ('aberto','fechado','cancelado','expirado'));
ALTER TABLE carrinhos ADD COLUMN IF NOT EXISTS expirado_em TIMESTAMPTZ;
