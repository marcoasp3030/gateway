-- Tempo limite padrão do agente passa de 90 s para 60 s (teste de tempo do Skyone,
-- 09/10/2026: síncrono comprovado até ~66 s; acima disso, só no modo assíncrono).
-- O limite máximo aceito pela API continua 600 s.
ALTER TABLE agentes ALTER COLUMN timeout_s SET DEFAULT 60;

-- Agentes ainda no valor de fábrica antigo (90) passam a 60, por decisão do Marco.
-- Cada troca é auditada com a mesma trava por tenant da aplicação, para manter
-- a ordem dos IDs de evento (eventos/cursor).
DO $$
DECLARE a RECORD;
BEGIN
  FOR a IN SELECT tenant_id, id FROM agentes WHERE timeout_s = 90 ORDER BY tenant_id, id LOOP
    PERFORM pg_advisory_xact_lock(hashtextextended('auditoria:' || a.tenant_id, 0));
    UPDATE agentes SET timeout_s = 60 WHERE tenant_id = a.tenant_id AND id = a.id;
    INSERT INTO audit_log(tenant_id, actor, acao, alvo, dados)
      VALUES (a.tenant_id, 'migracao013', 'agentes.alterado', a.id, '{}');
  END LOOP;
END $$;
