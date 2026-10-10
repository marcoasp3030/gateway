-- Gestão, bloco G2 (docs/ESPEC_GESTAO.md, seção 10.2): WhatsApp de pedidos da unidade e de alerta de
-- transbordo da empresa no formato E.164 sem "+": só dígitos, de 10 a 15, com DDI.
-- Nenhum dado é alterado: se existir valor fora da regra nova, a migração falha e nada muda.
ALTER TABLE tenant_unidades DROP CONSTRAINT IF EXISTS tenant_unidades_whatsapp_pedidos_check;
ALTER TABLE tenant_unidades ADD CONSTRAINT tenant_unidades_whatsapp_pedidos_check CHECK (whatsapp_pedidos ~ '^[0-9]{10,15}$');
ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_whatsapp_alerta_transbordo_check;
ALTER TABLE tenants ADD CONSTRAINT tenants_whatsapp_alerta_transbordo_check CHECK (whatsapp_alerta_transbordo ~ '^[0-9]{10,15}$');
