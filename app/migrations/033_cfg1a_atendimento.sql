-- CFG1-a (docs/ESPEC_CFG1.md). Tudo aditivo: a imagem anterior segue funcionando.
-- Segmento: o modelo do agente passa a carregar também o perfil de atendimento padrão.
ALTER TABLE modelos_agente ADD COLUMN IF NOT EXISTS perfil_padrao JSONB NOT NULL DEFAULT '{}';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS segmento TEXT;
-- Perfil publicado de cada escopo (só o que o escopo personalizou) e o número da versão em vigor.
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS perfil JSONB NOT NULL DEFAULT '{}';
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS perfil_versao INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS perfil JSONB NOT NULL DEFAULT '{}';
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS perfil_versao INTEGER NOT NULL DEFAULT 0;
-- Comportamentos que entram no contrato agora e só passam a ser executados nos blocos R1 e G3 (nulo = herda).
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['tenant_config','agentes'] LOOP
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS reacoes TEXT CHECK (reacoes IN (''on'',''off''))',t);
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS reacoes_emojis TEXT[]',t);
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS reacoes_max_dia INTEGER CHECK (reacoes_max_dia BETWEEN 0 AND 10)',t);
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS fora_do_horario TEXT CHECK (fora_do_horario IN (''atender'',''so_avisar''))',t);
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS fora_do_horario_mensagem TEXT CHECK (char_length(fora_do_horario_mensagem) <= 500)',t);
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS fora_do_horario_aceita_agendado BOOLEAN',t);
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS transbordo_alerta TEXT CHECK (transbordo_alerta ~ ''^[0-9]{10,15}$'')',t);
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS transbordo_horario_equipe JSONB',t);
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS transbordo_fora_do_horario TEXT CHECK (char_length(transbordo_fora_do_horario) <= 500)',t);
  END LOOP;
END $$;
-- Histórico: cada publicação (ou restauração, ou alteração direta pelas rotas antigas) é uma versão do escopo.
-- agente_id nulo = versão da empresa. conteudo = {"perfil": {...}, "comportamentos": {...}} só com o que o escopo define.
CREATE TABLE IF NOT EXISTS atendimento_versoes (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    agente_id TEXT,
    versao INTEGER NOT NULL CHECK (versao > 0),
    conteudo JSONB NOT NULL,
    alteracoes JSONB NOT NULL DEFAULT '[]',
    origem TEXT NOT NULL CHECK (origem IN ('publicacao','restauracao','alteracao_direta')),
    restaurada_de INTEGER,
    nota TEXT CHECK (char_length(nota) <= 300),
    publicado_por TEXT NOT NULL,
    publicado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_atendimento_versoes ON atendimento_versoes (tenant_id, coalesce(agente_id,''), versao);
CREATE TABLE IF NOT EXISTS atendimento_rascunhos (
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    agente_id TEXT,
    baseado_em INTEGER NOT NULL DEFAULT 0,
    conteudo JSONB NOT NULL,
    atualizado_por TEXT NOT NULL,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_atendimento_rascunhos ON atendimento_rascunhos (tenant_id, coalesce(agente_id,''));
-- Com qual versão do perfil cada resposta foi dada.
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS perfil_versao TEXT;
-- Segmento delivery_restaurante: mesmos módulos e comportamentos do atendente_delivery (para quem migrar não mudar nada),
-- mais os padrões de um delivery: botões e listas, refazer o turno, esperas maiores para mensagem incompleta, fora do horário só avisa.
INSERT INTO modelos_agente(id,versao,tenant_id,nome,tipo,modulos,comportamento_padrao,desfechos,followups_padrao,skyone_fluxo_modelo,perfil_padrao)
    SELECT 'delivery_restaurante',1,NULL,'Delivery de restaurante',m.tipo,m.modulos,
        m.comportamento_padrao || '{"botoes_interativos":"on","refazer_turno":"on","debounce_incompleta_s":7,"debounce_curto_s":2.5,"fora_do_horario":"so_avisar"}'::jsonb,
        m.desfechos,m.followups_padrao,m.skyone_fluxo_modelo,
        '{"tom":"equilibrado","emojis":"moderado","tamanho_respostas":"curtas","tratamento":"voce","apresentacao_inicial":true,"pedir_nome":false,"sugestoes":"leve"}'::jsonb
    FROM modelos_agente m WHERE m.id='atendente_delivery' AND m.tenant_id IS NULL
        AND m.versao=(SELECT max(versao) FROM modelos_agente WHERE id='atendente_delivery' AND tenant_id IS NULL)
    ON CONFLICT (id,versao) DO NOTHING;
