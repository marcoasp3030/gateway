-- PED1-a (docs/ESPEC_PED1.md). Tudo aditivo: a imagem anterior segue funcionando.
-- Ciclo completo do pedido: preparo, pronto/saiu e conclusão.
ALTER TABLE pedidos DROP CONSTRAINT IF EXISTS pedidos_status_check;
ALTER TABLE pedidos ADD CONSTRAINT pedidos_status_check CHECK (status IN
    ('aguardando_loja','confirmado','em_preparo','pronto_retirada','saiu_entrega','concluido','recusado','cancelado'));
-- Tempo estimado informado pela unidade ao confirmar (entra no aviso ao cliente).
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS previsao_min INTEGER CHECK (previsao_min BETWEEN 1 AND 600);
-- Linha do tempo: uma linha por mudança de status. Como o pedido nunca volta, cada status aparece no máximo uma vez.
CREATE TABLE IF NOT EXISTS pedido_eventos (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    pedido_id BIGINT NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    de TEXT,
    para TEXT NOT NULL,
    por TEXT NOT NULL,
    origem TEXT NOT NULL CHECK (origem IN ('agente','cliente','painel','unidade_whatsapp','sistema')),
    motivo TEXT,
    previsao_min INTEGER,
    -- 'enfileirado': há aviso ao cliente em avisos_internos (referência numero:status); 'desligado': status sem aviso; nulo: não se aplica.
    aviso TEXT CHECK (aviso IN ('enfileirado','desligado')),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pedido_id, para)
);
CREATE INDEX IF NOT EXISTS ix_pedido_eventos_pedido ON pedido_eventos (tenant_id, pedido_id, id);
CREATE INDEX IF NOT EXISTS ix_pedidos_criado ON pedidos (tenant_id, criado_em DESC);
-- Pedidos que já existem: linha do tempo reconstruída com o que há (criação e decisão).
INSERT INTO pedido_eventos(tenant_id,pedido_id,de,para,por,origem,criado_em)
    SELECT tenant_id,id,NULL,'aguardando_loja','agente','agente',criado_em FROM pedidos
    ON CONFLICT (pedido_id,para) DO NOTHING;
INSERT INTO pedido_eventos(tenant_id,pedido_id,de,para,por,origem,motivo,aviso,criado_em)
    SELECT p.tenant_id,p.id,'aguardando_loja',p.status,coalesce(p.decidido_por,'desconhecido'),
        CASE WHEN p.decidido_por='cliente' THEN 'cliente' WHEN p.decidido_por='unidade:whatsapp' THEN 'unidade_whatsapp' ELSE 'painel' END,
        p.motivo,
        CASE WHEN EXISTS(SELECT 1 FROM avisos_internos a WHERE a.tenant_id=p.tenant_id AND a.tipo='pedido_cliente' AND a.referencia=p.numero||':'||p.status) THEN 'enfileirado' END,
        coalesce(p.decidido_em,p.atualizado_em)
    FROM pedidos p WHERE p.status<>'aguardando_loja'
    ON CONFLICT (pedido_id,para) DO NOTHING;
