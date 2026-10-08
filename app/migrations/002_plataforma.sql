-- Não atribuir dados legados a um tenant presumido. Conversão requer roteiro aprovado.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM conversas) OR EXISTS (SELECT 1 FROM mensagens)
       OR EXISTS (SELECT 1 FROM turnos) OR EXISTS (SELECT 1 FROM webhook_raw)
       OR EXISTS (SELECT 1 FROM cardapio_itens) THEN
        RAISE EXCEPTION 'Banco legado contém dados; migração de dados exige plano explícito';
    END IF;
END $$;

CREATE TABLE tenants (
    id TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]*$'),
    nome TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo','suspenso')),
    plano TEXT, limite_turnos_mes INTEGER CHECK (limite_turnos_mes >= 0),
    timezone TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
    retencao_dias INTEGER NOT NULL DEFAULT 30 CHECK (retencao_dias > 0),
    tenant_teste BOOLEAN NOT NULL DEFAULT false, criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE modelos_agente (
    id TEXT NOT NULL, versao INTEGER NOT NULL DEFAULT 1 CHECK (versao > 0),
    tenant_id TEXT REFERENCES tenants(id), nome TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('atendimento','interno')),
    modulos TEXT[] NOT NULL DEFAULT '{}', comportamento_padrao JSONB NOT NULL DEFAULT '{}',
    desfechos TEXT[] NOT NULL DEFAULT '{}', followups_padrao JSONB NOT NULL DEFAULT '[]',
    skyone_fluxo_modelo TEXT, PRIMARY KEY (id, versao)
);
CREATE INDEX ix_modelos_tenant ON modelos_agente(tenant_id, id);
CREATE TABLE modulos_tenant (
    tenant_id TEXT NOT NULL REFERENCES tenants(id), modulo TEXT NOT NULL,
    ativo BOOLEAN NOT NULL DEFAULT true, config JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, modulo)
);
CREATE TABLE tenant_config (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
    debounce_s INTEGER CHECK (debounce_s >= 0),
    max_wait_s INTEGER CHECK (max_wait_s > 0),
    media_wait_s INTEGER CHECK (media_wait_s >= 0),
    pause_minutes INTEGER CHECK (pause_minutes >= 0),
    history_turns INTEGER CHECK (history_turns >= 0),
    slow_notice_s INTEGER CHECK (slow_notice_s >= 0),
    aviso_demora TEXT, fallback TEXT, alerta_telefone TEXT,
    send_mode TEXT CHECK (send_mode IN ('dry','live')),
    agente_mode TEXT CHECK (agente_mode IN ('mock','live')),
    modo_piloto BOOLEAN, contatos_permitidos TEXT[],
    encerrar_vazia_min INTEGER CHECK (encerrar_vazia_min > 0),
    max_lotes_simultaneos INTEGER CHECK (max_lotes_simultaneos > 0),
    max_concorrencia INTEGER CHECK (max_concorrencia > 0),
    skyone_dominios_permitidos TEXT[]
);
CREATE TABLE agentes (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
    modelo_id TEXT NOT NULL, modelo_versao INTEGER NOT NULL DEFAULT 1,
    nome TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'rascunho'
        CHECK (status IN ('rascunho','ativo','pausado')),
    provedor TEXT NOT NULL DEFAULT 'mock' CHECK (provedor IN ('skyone','mock','llm_direto')),
    skyone_estrategia TEXT NOT NULL DEFAULT 'por_modelo' CHECK (skyone_estrategia IN ('por_modelo','por_agente')),
    skyone_webhook_url TEXT,
    skyone_auth_tipo TEXT NOT NULL DEFAULT 'nenhuma' CHECK (skyone_auth_tipo IN ('nenhuma','cabecalho','bearer')),
    skyone_auth_cabecalho TEXT, skyone_auth_enc TEXT,
    timeout_s INTEGER NOT NULL DEFAULT 90 CHECK (timeout_s > 0),
    instrucoes_agente TEXT NOT NULL DEFAULT '',
    modo_resposta TEXT NOT NULL DEFAULT 'sync' CHECK (modo_resposta IN ('sync','async')),
    debounce_s INTEGER CHECK (debounce_s >= 0),
    max_wait_s INTEGER CHECK (max_wait_s > 0),
    media_wait_s INTEGER CHECK (media_wait_s >= 0),
    pause_minutes INTEGER CHECK (pause_minutes >= 0),
    history_turns INTEGER CHECK (history_turns >= 0),
    slow_notice_s INTEGER CHECK (slow_notice_s >= 0),
    aviso_demora TEXT, fallback TEXT, alerta_telefone TEXT,
    send_mode TEXT CHECK (send_mode IN ('dry','live')),
    agente_mode TEXT CHECK (agente_mode IN ('mock','live')),
    modo_piloto BOOLEAN, contatos_permitidos TEXT[],
    encerrar_vazia_min INTEGER CHECK (encerrar_vazia_min > 0),
    max_lotes_simultaneos INTEGER CHECK (max_lotes_simultaneos > 0),
    max_concorrencia INTEGER CHECK (max_concorrencia > 0),
    skyone_dominios_permitidos TEXT[],
    UNIQUE (tenant_id, id),
    FOREIGN KEY (modelo_id, modelo_versao) REFERENCES modelos_agente(id, versao),
    CHECK (skyone_auth_tipo = 'nenhuma' OR length(skyone_auth_enc) > 0 AND skyone_auth_enc IS NOT NULL),
    CHECK (skyone_auth_tipo <> 'cabecalho' OR skyone_auth_cabecalho ~ '^[A-Za-z0-9-]+$' AND skyone_auth_cabecalho IS NOT NULL)
);
-- Modelos globais são compartilháveis; modelos privados só pelo tenant proprietário.
-- Escopo do modelo é imutável para não invalidar referências de agentes existentes.
CREATE FUNCTION validar_modelo_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE dono TEXT;
BEGIN
    SELECT tenant_id INTO dono FROM modelos_agente
      WHERE id = NEW.modelo_id AND versao = NEW.modelo_versao FOR SHARE;
    IF dono IS NOT NULL AND dono <> NEW.tenant_id THEN
        RAISE EXCEPTION 'Modelo pertence a outro tenant' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER agentes_modelo_tenant BEFORE INSERT OR UPDATE ON agentes
    FOR EACH ROW EXECUTE FUNCTION validar_modelo_tenant();
CREATE FUNCTION manter_escopo_modelo() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
        RAISE EXCEPTION 'Escopo do modelo é imutável' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER modelos_escopo BEFORE UPDATE ON modelos_agente
    FOR EACH ROW EXECUTE FUNCTION manter_escopo_modelo();
CREATE TABLE tenant_canais (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('uazapi','meta','site','email','teams','luxbrain','api')),
    nome TEXT NOT NULL, agente_id TEXT, triagem JSONB,
    webhook_secret TEXT, credenciais_enc JSONB NOT NULL DEFAULT '{}',
    publico TEXT NOT NULL DEFAULT 'clientes' CHECK (publico IN ('clientes','interno')),
    ativo BOOLEAN NOT NULL DEFAULT false, status_conexao TEXT, ultimo_evento_em TIMESTAMPTZ,
    UNIQUE (tenant_id,id), UNIQUE (tenant_id,id,agente_id),
    FOREIGN KEY (tenant_id,agente_id) REFERENCES agentes(tenant_id,id),
    CHECK ((agente_id IS NOT NULL) <> (triagem IS NOT NULL))
);
CREATE TABLE tenant_unidades (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), nome TEXT NOT NULL,
    telefone_whatsapp TEXT, horarios JSONB NOT NULL DEFAULT '{}', ativo BOOLEAN NOT NULL DEFAULT true,
    UNIQUE(tenant_id,id)
);
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
    actor TEXT NOT NULL, acao TEXT NOT NULL, alvo TEXT NOT NULL,
    dados JSONB NOT NULL DEFAULT '{}', criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_tenant ON audit_log(tenant_id,criado_em DESC);
CREATE TABLE tenant_api_keys (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), nome TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE CHECK (hash ~ '^[0-9a-f]{64}$'),
    escopos TEXT[] NOT NULL DEFAULT '{}', agente_id TEXT, expira_em TIMESTAMPTZ,
    ips_permitidos CIDR[], ultimo_uso_em TIMESTAMPTZ, criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    revogada_em TIMESTAMPTZ,
    FOREIGN KEY (tenant_id,agente_id) REFERENCES agentes(tenant_id,id)
);
CREATE INDEX ix_api_keys_tenant ON tenant_api_keys(tenant_id,id);
CREATE TABLE tenant_webhooks_saida (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), url TEXT NOT NULL,
    segredo_enc TEXT NOT NULL, eventos TEXT[] NOT NULL DEFAULT '{}', ativo BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX ix_webhooks_saida_tenant ON tenant_webhooks_saida(tenant_id,id);
CREATE TABLE contatos (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), telefone TEXT, email TEXT,
    nome TEXT, perfil JSONB NOT NULL DEFAULT '{}', resumo TEXT, atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(tenant_id,id), UNIQUE(tenant_id,telefone), UNIQUE(tenant_id,email)
);
ALTER TABLE conversas ADD COLUMN tenant_id TEXT NOT NULL REFERENCES tenants(id);
CREATE INDEX ix_conversas_tenant ON conversas(tenant_id);
ALTER TABLE mensagens ADD COLUMN tenant_id TEXT NOT NULL REFERENCES tenants(id);
CREATE INDEX ix_mensagens_tenant ON mensagens(tenant_id);
ALTER TABLE turnos ADD COLUMN tenant_id TEXT NOT NULL REFERENCES tenants(id);
CREATE INDEX ix_turnos_tenant ON turnos(tenant_id);
ALTER TABLE webhook_raw ADD COLUMN tenant_id TEXT NOT NULL REFERENCES tenants(id);
CREATE INDEX ix_webhook_raw_tenant ON webhook_raw(tenant_id);
ALTER TABLE cardapio_itens ADD COLUMN tenant_id TEXT NOT NULL REFERENCES tenants(id);
CREATE INDEX ix_cardapio_itens_tenant ON cardapio_itens(tenant_id);
ALTER TABLE conversas
    ADD COLUMN agente_id TEXT NOT NULL,
    ADD COLUMN canal_id TEXT NOT NULL,
    ADD UNIQUE (tenant_id,conversation_id),
    ADD FOREIGN KEY (tenant_id,agente_id) REFERENCES agentes(tenant_id,id),
    ADD FOREIGN KEY (tenant_id,canal_id) REFERENCES tenant_canais(tenant_id,id),
    ADD CHECK (starts_with(conversation_id, tenant_id || ':' || canal_id || ':'));
ALTER TABLE mensagens
    ADD FOREIGN KEY (tenant_id,conversation_id) REFERENCES conversas(tenant_id,conversation_id);
ALTER TABLE turnos
    ADD COLUMN agente_id TEXT NOT NULL,
    ADD COLUMN canal_id TEXT NOT NULL,
    ADD FOREIGN KEY (tenant_id,conversation_id) REFERENCES conversas(tenant_id,conversation_id),
    ADD FOREIGN KEY (tenant_id,agente_id) REFERENCES agentes(tenant_id,id),
    ADD FOREIGN KEY (tenant_id,canal_id) REFERENCES tenant_canais(tenant_id,id);
CREATE INDEX ix_mensagens_tenant_conversa ON mensagens(tenant_id,conversation_id,id DESC);
CREATE INDEX ix_turnos_tenant_agente ON turnos(tenant_id,agente_id,id DESC);
CREATE INDEX ix_conversas_tenant_agente ON conversas(tenant_id,agente_id);
COMMENT ON TABLE cardapio_itens IS 'Tabela legada sob responsabilidade do módulo catalogo; evolução na Fase 4';
-- ID externo de mensagem pode se repetir em provedores de tenants distintos.
ALTER TABLE mensagens DROP CONSTRAINT mensagens_message_id_key;
ALTER TABLE mensagens ADD UNIQUE (tenant_id,message_id);
