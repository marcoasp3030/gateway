-- G4-b: taxa por bairro ou CEP no carrinho e no pedido. Aditiva: a imagem anterior segue funcionando.
ALTER TABLE carrinhos
    ADD COLUMN IF NOT EXISTS bairro TEXT CHECK (char_length(bairro) <= 80),
    ADD COLUMN IF NOT EXISTS cep CHAR(8) CHECK (cep ~ '^[0-9]{8}$'),
    ADD COLUMN IF NOT EXISTS bairro_confirmado BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE pedidos
    ADD COLUMN IF NOT EXISTS bairro TEXT CHECK (char_length(bairro) <= 80),
    ADD COLUMN IF NOT EXISTS cep CHAR(8) CHECK (cep ~ '^[0-9]{8}$'),
    ADD COLUMN IF NOT EXISTS taxa_origem TEXT CHECK (taxa_origem IN ('fixa','bairro','cep','frete_gratis','a_confirmar')),
    ADD COLUMN IF NOT EXISTS frete_gratis BOOLEAN NOT NULL DEFAULT false;
