-- Fase 2: o que o agente decidiu no turno (Apêndice A do plano).
-- acao: nenhuma | silencio | transferir_humano | encerrar | pedido_fechado.
-- motivo_silencio: obrigatório quando acao = silencio (INV04). desfecho: como a conversa terminou.
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS acao TEXT;
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS motivo_silencio TEXT;
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS desfecho TEXT;

-- Motivos novos de "aguardando atendente": o agente pediu a transferência, ou ficou em
-- silêncio sem motivo nas duas consultas (INV04).
ALTER TABLE conversas DROP CONSTRAINT IF EXISTS conversas_aguardando_motivo_check;
ALTER TABLE conversas ADD CONSTRAINT conversas_aguardando_motivo_check
  CHECK (aguardando_motivo IN ('erro_agente','tempo_esgotado','circuito_aberto','entrega_incerta',
                               'transferido_pelo_agente','silencio_sem_motivo'));
