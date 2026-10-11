-- SOL1 (docs/ESPEC_SOL1.md). Tudo aditivo: a imagem anterior segue funcionando.
-- Solicitação para a equipe (evento, catering, encomenda…): o agente avisa e continua atendendo.
CREATE TABLE IF NOT EXISTS solicitacoes (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    numero TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    contato_id TEXT,
    agente_id TEXT,
    motivo TEXT NOT NULL,
    motivo_informado TEXT,
    tipo TEXT NOT NULL CHECK (tipo IN ('evento','catering','encomenda','orcamento','parceria','outro')),
    dados JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'aberta' CHECK (status IN ('aberta','em_andamento','concluida','cancelada')),
    origem TEXT NOT NULL CHECK (origem IN ('agente','convertida')),
    atualizacoes INTEGER NOT NULL DEFAULT 0,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    alertado_em TIMESTAMPTZ,
    atendido_por TEXT,
    concluido_em TIMESTAMPTZ,
    nota TEXT CHECK (char_length(nota) <= 500),
    UNIQUE (tenant_id, numero),
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversas(tenant_id, conversation_id) ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_solicitacoes_tenant ON solicitacoes (tenant_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_solicitacoes_conversa ON solicitacoes (tenant_id, conversation_id, id DESC);
-- Uma solicitação aberta (ou em andamento) por conversa e motivo: nova ação atualiza a existente.
CREATE UNIQUE INDEX IF NOT EXISTS ux_solicitacoes_aberta ON solicitacoes (tenant_id, conversation_id, motivo) WHERE status IN ('aberta','em_andamento');
-- O alerta à equipe sai pela mesma caixa de avisos internos.
ALTER TABLE avisos_internos DROP CONSTRAINT IF EXISTS avisos_internos_tipo_check;
ALTER TABLE avisos_internos ADD CONSTRAINT avisos_internos_tipo_check CHECK (tipo IN ('pedido_unidade','resposta_unidade','transbordo','pedido_cliente','solicitacao'));
-- Transferência que o gateway converteu em aviso à equipe: o que o agente pediu fica registrado.
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS acao_original TEXT;
-- Configuração (nulo = herda): liga/desliga do recurso e destino de alerta marcado como grupo externo (mascara o telefone).
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS notificar_equipe TEXT CHECK (notificar_equipe IN ('on','off'));
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS notificar_equipe TEXT CHECK (notificar_equipe IN ('on','off'));
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS transbordo_destino_externo BOOLEAN;
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS transbordo_destino_externo BOOLEAN;
-- Versão 2 do segmento delivery_restaurante: igual à 1, com notificar_equipe ligado. Ninguém é movido aqui:
-- o agente só passa a usá-la quando a plataforma o move (docs/ESPEC_CFG1.md, seção 14.3).
INSERT INTO modelos_agente(id,versao,tenant_id,nome,tipo,modulos,comportamento_padrao,desfechos,followups_padrao,skyone_fluxo_modelo,perfil_padrao)
    SELECT id,2,tenant_id,nome,tipo,modulos,comportamento_padrao || '{"notificar_equipe":"on"}'::jsonb,desfechos,followups_padrao,skyone_fluxo_modelo,perfil_padrao
    FROM modelos_agente WHERE id='delivery_restaurante' AND versao=1 AND tenant_id IS NULL
    ON CONFLICT (id,versao) DO NOTHING;
