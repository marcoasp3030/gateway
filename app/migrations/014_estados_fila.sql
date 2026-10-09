-- Fase 0.6, bloco 1: estados do turno, envio idempotente por parte, callback
-- assíncrono e marcação "aguardando atendente". Só acréscimos com padrão
-- compatível: a imagem anterior (gateway:f5a1c22) continua funcionando.

-- Turno: etapa da máquina de estados. Turnos gravados pelo código atual já
-- nascem concluídos, então o padrão é 'concluido'.
ALTER TABLE turnos ADD COLUMN etapa TEXT NOT NULL DEFAULT 'concluido'
  CONSTRAINT turnos_etapa_check
  CHECK (etapa IN ('processando','respondido','aguardando_callback','concluido','abandonado'));
-- IDs das mensagens de entrada do lote, para retomar sem recalcular.
ALTER TABLE turnos ADD COLUMN mensagem_ids BIGINT[];
CREATE UNIQUE INDEX ux_turnos_request ON turnos (request_id);
CREATE INDEX ix_turnos_em_andamento ON turnos (tenant_id, conversation_id)
  WHERE etapa <> 'concluido' AND etapa <> 'abandonado';

-- Saída: cada parte da resposta de um turno é gravada uma única vez.
-- entrega: enviando (antes do canal) → enviada; 'incerta' = presa em
-- enviando quando o worker caiu; nunca é reenviada (revisão humana).
ALTER TABLE mensagens ADD COLUMN turno_request_id UUID;
ALTER TABLE mensagens ADD COLUMN parte SMALLINT CONSTRAINT mensagens_parte_check CHECK (parte >= 0);
ALTER TABLE mensagens ADD COLUMN entrega TEXT
  CONSTRAINT mensagens_entrega_check CHECK (entrega IN ('enviando','enviada','incerta'));
ALTER TABLE mensagens ADD CONSTRAINT mensagens_parte_turno_check
  CHECK ((turno_request_id IS NULL) = (parte IS NULL));
CREATE UNIQUE INDEX ux_mensagens_turno_parte ON mensagens (tenant_id, turno_request_id, parte)
  WHERE turno_request_id IS NOT NULL;
CREATE INDEX ix_mensagens_entrega_incerta ON mensagens (tenant_id, id) WHERE entrega = 'incerta';

-- Conversa que recebeu fallback (erro, tempo esgotado, circuito aberto) ou
-- teve entrega incerta: fica aguardando atendente até alguém assumir/devolver.
ALTER TABLE conversas ADD COLUMN aguardando_atendente_desde TIMESTAMPTZ;
ALTER TABLE conversas ADD COLUMN aguardando_motivo TEXT
  CONSTRAINT conversas_aguardando_motivo_check
  CHECK (aguardando_motivo IN ('erro_agente','tempo_esgotado','circuito_aberto','entrega_incerta'));
ALTER TABLE conversas ADD CONSTRAINT conversas_aguardando_check
  CHECK ((aguardando_atendente_desde IS NULL) = (aguardando_motivo IS NULL));
CREATE INDEX ix_conversas_aguardando ON conversas (tenant_id, aguardando_atendente_desde)
  WHERE aguardando_atendente_desde IS NOT NULL;

-- Callback assíncrono: um registro por request_id, token de uso único
-- guardado só como hash; recebido_em preenchido = token já usado.
CREATE TABLE agente_callbacks (
  request_id UUID PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  agente_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  token_hash TEXT NOT NULL,
  criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
  expira_em TIMESTAMPTZ NOT NULL,
  recebido_em TIMESTAMPTZ,
  CONSTRAINT agente_callbacks_prazo_check CHECK (expira_em > criado_em),
  FOREIGN KEY (tenant_id, agente_id) REFERENCES agentes(tenant_id, id)
);
CREATE INDEX ix_agente_callbacks_pendentes ON agente_callbacks (expira_em) WHERE recebido_em IS NULL;
CREATE INDEX ix_agente_callbacks_tenant ON agente_callbacks (tenant_id, conversation_id);
