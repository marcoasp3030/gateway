-- MULTI1-a (docs/ESPEC_MULTI1.md): modelo de agente como cadastro (segmento, módulos permitidos e ligados por padrão,
-- motivos de transferência e tipos de solicitação com rótulo, referência do Skyone, ativo) e módulos ligados por agente.
-- Aditivo: a imagem anterior segue funcionando (a coluna "modulos" do modelo continua igual aos permitidos).
ALTER TABLE modelos_agente ADD COLUMN IF NOT EXISTS segmento TEXT;
ALTER TABLE modelos_agente ADD COLUMN IF NOT EXISTS modulos_permitidos TEXT[];
ALTER TABLE modelos_agente ADD COLUMN IF NOT EXISTS modulos_padrao TEXT[];
ALTER TABLE modelos_agente ADD COLUMN IF NOT EXISTS motivos_transferencia JSONB;
ALTER TABLE modelos_agente ADD COLUMN IF NOT EXISTS tipos_solicitacao JSONB;
ALTER TABLE modelos_agente ADD COLUMN IF NOT EXISTS skyone_ref TEXT;
ALTER TABLE modelos_agente ADD COLUMN IF NOT EXISTS ativo BOOLEAN NOT NULL DEFAULT true;
-- Módulos ligados no agente (nulo = os ligados por padrão no modelo).
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS modulos_ligados TEXT[];

-- Modelos que já existem: módulos permitidos e padrão = os de hoje.
UPDATE modelos_agente SET modulos_permitidos=coalesce(modulos_permitidos,modulos),modulos_padrao=coalesce(modulos_padrao,modulos);

-- Delivery (todas as versões): mesmas listas que valem hoje para o By Koji.
-- Motivos do núcleo (pediu_humano, reclamacao) e do módulo de pedidos (pedido_para_confirmar) não entram aqui: vêm das outras camadas.
UPDATE modelos_agente SET segmento='delivery',
    motivos_transferencia='[{"id":"pagamento","rotulo":"Pagamento"},
        {"id":"restricao_alimentar","rotulo":"Restrição alimentar"},
        {"id":"evento_ou_encomenda","rotulo":"Evento ou encomenda","converte_em_solicitacao":true},
        {"id":"catering","rotulo":"Catering","converte_em_solicitacao":true},
        {"id":"fora_do_escopo","rotulo":"Fora do escopo"},
        {"id":"assunto_proibido","rotulo":"Assunto proibido"}]'::jsonb,
    tipos_solicitacao='[{"id":"evento","rotulo":"Evento"},{"id":"catering","rotulo":"Catering"},{"id":"encomenda","rotulo":"Encomenda"},
        {"id":"orcamento","rotulo":"Orçamento"},{"id":"parceria","rotulo":"Parceria"}]'::jsonb
    WHERE id='delivery_restaurante' AND segmento IS NULL;

-- Modelos antigos: inativos para criação; quem já usa continua com as listas de hoje.
UPDATE modelos_agente m SET ativo=false,segmento=CASE WHEN id='atendente_delivery' THEN 'delivery' ELSE 'geral' END,
    motivos_transferencia=d.motivos_transferencia,tipos_solicitacao=d.tipos_solicitacao
    FROM (SELECT motivos_transferencia,tipos_solicitacao FROM modelos_agente WHERE id='delivery_restaurante' ORDER BY versao DESC LIMIT 1) d
    WHERE m.id IN ('assistente_generico','atendente_delivery') AND m.segmento IS NULL;

-- Delivery v4: o v3 com o nome novo. Ninguém é movido.
INSERT INTO modelos_agente(id,versao,tenant_id,nome,tipo,modulos,comportamento_padrao,desfechos,followups_padrao,skyone_fluxo_modelo,perfil_padrao,
                           segmento,modulos_permitidos,modulos_padrao,motivos_transferencia,tipos_solicitacao,skyone_ref,ativo)
    SELECT id,4,tenant_id,'Delivery',tipo,modulos,comportamento_padrao,desfechos,followups_padrao,skyone_fluxo_modelo,perfil_padrao,
           segmento,modulos_permitidos,modulos_padrao,motivos_transferencia,tipos_solicitacao,skyone_ref,true
    FROM modelos_agente WHERE id='delivery_restaurante' AND versao=3 AND tenant_id IS NULL
    ON CONFLICT (id,versao) DO NOTHING;

-- Atendimento geral v1: sem módulos de negócio.
INSERT INTO modelos_agente(id,versao,tenant_id,nome,tipo,modulos,comportamento_padrao,desfechos,followups_padrao,skyone_fluxo_modelo,perfil_padrao,
                           segmento,modulos_permitidos,modulos_padrao,motivos_transferencia,tipos_solicitacao,skyone_ref,ativo)
    SELECT 'atendimento_geral',1,NULL,'Atendimento geral','atendimento','{}',comportamento_padrao,'{}','[]'::jsonb,NULL,perfil_padrao,
           'geral','{}','{}',
           '[{"id":"pagamento","rotulo":"Pagamento"},{"id":"fora_do_escopo","rotulo":"Fora do escopo"},{"id":"assunto_proibido","rotulo":"Assunto proibido"}]'::jsonb,
           '[{"id":"orcamento","rotulo":"Orçamento"},{"id":"parceria","rotulo":"Parceria"}]'::jsonb,NULL,true
    FROM modelos_agente WHERE id='delivery_restaurante' AND versao=3 AND tenant_id IS NULL
    ON CONFLICT (id,versao) DO NOTHING;
