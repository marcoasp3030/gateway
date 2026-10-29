-- PAG1 (29/10/2026): forma de pagamento e troco no carrinho e no pedido. Tudo aditivo.
-- forma_pagamento = código de unidade_pagamentos da unidade (ativo na hora de escolher e de fechar).
-- troco_para = valor em dinheiro que o cliente vai entregar (só na forma dinheiro com aceita_troco; maior que o total).
ALTER TABLE carrinhos ADD COLUMN IF NOT EXISTS forma_pagamento TEXT CHECK (forma_pagamento ~ '^[a-z0-9_]{1,40}$');
ALTER TABLE carrinhos ADD COLUMN IF NOT EXISTS troco_para NUMERIC(10,2) CHECK (troco_para > 0);
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS forma_pagamento TEXT CHECK (forma_pagamento ~ '^[a-z0-9_]{1,40}$');
-- Rótulo copiado no fechamento: o pedido não muda se a unidade renomear a forma depois.
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS pagamento_rotulo TEXT CHECK (length(pagamento_rotulo) <= 60);
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS troco_para NUMERIC(10,2) CHECK (troco_para > 0);
