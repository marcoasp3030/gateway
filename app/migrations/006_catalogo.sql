-- Extração física da tabela legada para a capacidade catálogo; dados preservados.
ALTER TABLE cardapio_itens RENAME TO catalogo_itens;
COMMENT ON TABLE catalogo_itens IS 'Módulo catalogo; evolução funcional na Fase 4';
