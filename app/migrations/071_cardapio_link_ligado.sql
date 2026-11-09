-- CARD-LINK (docs/ESPEC_CARD_LINK.md): comportamento do agente "enviar_link_cardapio" (on/off; nulo herda; padrão on). Aditiva.
-- off: o pacote ao agente não traz o link do cardápio digital, e ele apresenta as opções na conversa.
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS enviar_link_cardapio TEXT CHECK (enviar_link_cardapio IN ('on','off'));
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS enviar_link_cardapio TEXT CHECK (enviar_link_cardapio IN ('on','off'));
