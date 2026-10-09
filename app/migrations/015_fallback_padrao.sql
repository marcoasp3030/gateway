-- Fallback sem promessa falsa (Fase 0.6, item A). O padrão da plataforma vem do
-- código (FALLBACK_PADRAO). Aqui só mudam agentes e configurações de empresa que
-- gravaram um dos textos antigos; textos personalizados ficam como estão.
-- Cada troca é auditada sob a trava por tenant (ordem dos IDs de evento).
DO $$
DECLARE
  novo CONSTANT TEXT := 'Tive uma instabilidade agora. Pode repetir sua mensagem em instantes? 🙏';
  antigos CONSTANT TEXT[] := ARRAY[
    'Tive uma instabilidade agora. Já vou te responder, tudo bem? 🙏',
    'Tive uma instabilidade. Por favor, tente novamente.'];
  r RECORD;
BEGIN
  FOR r IN SELECT tenant_id, id FROM agentes WHERE fallback = ANY(antigos) ORDER BY tenant_id, id LOOP
    PERFORM pg_advisory_xact_lock(hashtextextended('auditoria:' || r.tenant_id, 0));
    UPDATE agentes SET fallback = novo WHERE tenant_id = r.tenant_id AND id = r.id;
    INSERT INTO audit_log(tenant_id, actor, acao, alvo, dados)
      VALUES (r.tenant_id, 'migracao015', 'agentes.alterado', r.id, '{}');
  END LOOP;
  FOR r IN SELECT tenant_id FROM tenant_config WHERE fallback = ANY(antigos) ORDER BY tenant_id LOOP
    PERFORM pg_advisory_xact_lock(hashtextextended('auditoria:' || r.tenant_id, 0));
    UPDATE tenant_config SET fallback = novo WHERE tenant_id = r.tenant_id;
    INSERT INTO audit_log(tenant_id, actor, acao, alvo, dados)
      VALUES (r.tenant_id, 'migracao015', 'config.alterada', r.tenant_id, '{}');
  END LOOP;
END $$;
