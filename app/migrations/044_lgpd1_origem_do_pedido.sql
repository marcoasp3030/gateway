-- LGPD1 + origem do pedido (docs/ESPEC_AN1.md, decisão 4 de 16/10/2026).
-- origem: em que condição o pedido nasceu, gravada no fechamento (a análise de vendas só conta 'real').
--   real     = cliente de verdade, agente e envio de verdade
--   piloto   = cliente da lista do piloto, com o piloto ligado
--   simulado = conversa simulada pelo painel, agente em mock ou envio de teste (dry)
--   teste    = empresa de teste, ou pedido anterior a esta marca
-- canal: por onde o pedido entrou (whatsapp, api; ifood/99food/site virão do PEDEXT).
-- Nulas por enquanto: a imagem anterior continua gravando pedidos enquanto esta migração é aplicada.
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS origem TEXT CHECK (origem IN ('real','piloto','simulado','teste'));
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS canal TEXT CHECK (canal ~ '^[a-z0-9_]{2,20}$');
-- Todos os pedidos até aqui são de teste ou do piloto (auditoria AN1: 11 no By Koji, nenhum real).
UPDATE pedidos SET origem='teste' WHERE origem IS NULL;
UPDATE pedidos p SET canal=CASE WHEN k.tipo='uazapi' THEN 'whatsapp' ELSE coalesce(k.tipo,v.canal) END
    FROM conversas v LEFT JOIN tenant_canais k ON k.tenant_id=v.tenant_id AND k.id=v.canal_id
    WHERE p.canal IS NULL AND v.tenant_id=p.tenant_id AND v.conversation_id=p.conversation_id;
CREATE INDEX IF NOT EXISTS ix_pedidos_origem ON pedidos (tenant_id, origem, criado_em);
