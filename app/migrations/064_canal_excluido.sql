-- CANAL-EXC (04/11/2026): exclusão de canal pela plataforma. O histórico das conversas é apagado; os pedidos ficam, sem dados pessoais,
-- presos a uma conversa vazia e anônima. Se sobrar alguma, o canal fica marcado como excluído (some das listas, sem credenciais,
-- não volta a ser ativado); sem nenhuma, a linha do canal é apagada.
ALTER TABLE tenant_canais ADD COLUMN IF NOT EXISTS excluido_em TIMESTAMPTZ;
