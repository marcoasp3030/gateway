-- Fase 4, bloco 2: dados do pré-pedido que faltavam na 021.
-- Subtotal e taxa separados do total (taxa nula = a unidade confirma no retorno), quem pediu,
-- bebida alcoólica com maioridade confirmada, e quem cancelou.
ALTER TABLE pedidos ADD COLUMN subtotal NUMERIC(10,2) CHECK (subtotal > 0);
ALTER TABLE pedidos ADD COLUMN taxa_entrega NUMERIC(10,2) CHECK (taxa_entrega >= 0);
ALTER TABLE pedidos ADD COLUMN cliente_nome TEXT;
ALTER TABLE pedidos ADD COLUMN cliente_telefone TEXT;
ALTER TABLE pedidos ADD COLUMN tem_alcoolico BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE pedidos ADD COLUMN maioridade_confirmada BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE pedidos ADD CONSTRAINT pedidos_alcool_check CHECK (NOT tem_alcoolico OR maioridade_confirmada);
ALTER TABLE pedidos ADD COLUMN decidido_por TEXT;
