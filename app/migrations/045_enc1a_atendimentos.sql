-- ENC1-a (docs/ESPEC_ENC1A.md, decisões de 20/10/2026). Tudo aditivo: a imagem anterior segue funcionando
-- enquanto esta migração é aplicada (os gatilhos preenchem atendimento_id e turno_id nas linhas que ela gravar).
--
-- Atendimento: um trecho da conversa, do começo ao fim. A conversa continua uma só (empresa + canal + telefone);
-- mensagem do cliente numa conversa sem atendimento aberto abre o próximo (numero 1, 2, 3…). Nenhum é apagado nem reaproveitado.
-- estado: aberto | encerrado. "Em atendimento humano" é derivado da conversa (aguardando atendente ou pausada), não gravado.
CREATE TABLE IF NOT EXISTS atendimentos (
    id TEXT PRIMARY KEY CHECK (id ~ '^at_[0-9a-f]{24}$'),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    conversation_id TEXT NOT NULL,
    numero INTEGER NOT NULL CHECK (numero > 0),
    agente_id TEXT,
    aberto_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    motivo_inicio TEXT NOT NULL CHECK (motivo_inicio IN ('primeiro_contato','retorno_apos_encerramento','reaberto_manual','migracao')),
    estado TEXT NOT NULL DEFAULT 'aberto' CHECK (estado IN ('aberto','encerrado')),
    encerrado_em TIMESTAMPTZ,
    motivo_encerramento TEXT CHECK (motivo_encerramento IN ('concluido','pedido_concluido','pedido_sem_retorno','vazio','manual')),
    desfecho TEXT,
    -- Factual, montado pelo gateway ao encerrar (até 300 caracteres, sem texto da conversa nem dado pessoal).
    resumo TEXT CHECK (char_length(resumo) <= 300),
    UNIQUE (tenant_id, conversation_id, numero),
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversas(tenant_id, conversation_id) ON UPDATE CASCADE
);
-- No máximo um aberto por conversa.
CREATE UNIQUE INDEX IF NOT EXISTS ux_atendimentos_aberto ON atendimentos (tenant_id, conversation_id) WHERE estado='aberto';
CREATE INDEX IF NOT EXISTS ix_atendimentos_conversa ON atendimentos (tenant_id, conversation_id, numero DESC);

ALTER TABLE conversas ADD COLUMN IF NOT EXISTS atendimento_id TEXT;
ALTER TABLE mensagens ADD COLUMN IF NOT EXISTS atendimento_id TEXT;
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS atendimento_id TEXT;
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS atendimento_id TEXT;
ALTER TABLE solicitacoes ADD COLUMN IF NOT EXISTS atendimento_id TEXT;
ALTER TABLE skill_registro ADD COLUMN IF NOT EXISTS atendimento_id TEXT;
ALTER TABLE skill_chamadas ADD COLUMN IF NOT EXISTS atendimento_id TEXT;
-- Instrumentação: versão do prompt que respondeu (do retorno do agente) e o turno de cada chamada de Skill.
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS prompt_versao TEXT CHECK (prompt_versao ~ '^v[0-9]+(\.[0-9]+)*$' AND char_length(prompt_versao) <= 40);
ALTER TABLE skill_registro ADD COLUMN IF NOT EXISTS turno_id BIGINT;
ALTER TABLE skill_registro ADD COLUMN IF NOT EXISTS fora_do_turno BOOLEAN;
ALTER TABLE skill_chamadas ADD COLUMN IF NOT EXISTS turno_id BIGINT;
ALTER TABLE skill_chamadas ADD COLUMN IF NOT EXISTS fora_do_turno BOOLEAN;

-- Conversas existentes: um atendimento cada (aberto, ou encerrado com o desfecho de hoje), com tudo ligado a ele.
INSERT INTO atendimentos (id,tenant_id,conversation_id,numero,agente_id,aberto_em,motivo_inicio,estado,encerrado_em,motivo_encerramento,desfecho)
SELECT 'at_'||substr(md5(c.conversation_id||':enc1a'),1,24),c.tenant_id,c.conversation_id,1,c.agente_id,c.criado_em,'migracao',
       CASE WHEN c.encerrada_em IS NULL THEN 'aberto' ELSE 'encerrado' END,c.encerrada_em,
       CASE WHEN c.encerrada_em IS NULL THEN NULL WHEN c.desfecho='vazia' THEN 'vazio' ELSE 'concluido' END,c.desfecho
FROM conversas c
WHERE NOT EXISTS (SELECT 1 FROM atendimentos a WHERE a.tenant_id=c.tenant_id AND a.conversation_id=c.conversation_id);
UPDATE conversas c SET atendimento_id=a.id FROM atendimentos a
    WHERE c.atendimento_id IS NULL AND a.tenant_id=c.tenant_id AND a.conversation_id=c.conversation_id AND a.numero=1;
UPDATE mensagens x SET atendimento_id=c.atendimento_id FROM conversas c
    WHERE x.atendimento_id IS NULL AND c.tenant_id=x.tenant_id AND c.conversation_id=x.conversation_id;
UPDATE turnos x SET atendimento_id=c.atendimento_id FROM conversas c
    WHERE x.atendimento_id IS NULL AND c.tenant_id=x.tenant_id AND c.conversation_id=x.conversation_id;
UPDATE pedidos x SET atendimento_id=c.atendimento_id FROM conversas c
    WHERE x.atendimento_id IS NULL AND c.tenant_id=x.tenant_id AND c.conversation_id=x.conversation_id;
UPDATE solicitacoes x SET atendimento_id=c.atendimento_id FROM conversas c
    WHERE x.atendimento_id IS NULL AND c.tenant_id=x.tenant_id AND c.conversation_id=x.conversation_id;
UPDATE skill_registro x SET atendimento_id=c.atendimento_id FROM conversas c
    WHERE x.atendimento_id IS NULL AND c.tenant_id=x.tenant_id AND c.conversation_id=x.conversation_id;
UPDATE skill_chamadas x SET atendimento_id=c.atendimento_id FROM conversas c
    WHERE x.atendimento_id IS NULL AND c.tenant_id=x.tenant_id AND c.conversation_id=x.conversation_id;
CREATE INDEX IF NOT EXISTS ix_mensagens_atendimento ON mensagens (tenant_id, atendimento_id, id);
CREATE INDEX IF NOT EXISTS ix_turnos_atendimento ON turnos (tenant_id, atendimento_id, id);
CREATE INDEX IF NOT EXISTS ix_pedidos_atendimento ON pedidos (tenant_id, atendimento_id);
CREATE INDEX IF NOT EXISTS ix_skill_registro_turno ON skill_registro (tenant_id, turno_id);

-- Toda linha nova destas tabelas nasce ligada ao atendimento ABERTO da conversa (nenhum aberto = nulo; a mensagem do sistema
-- que chega depois do encerramento, como o aviso de status do pedido, fica sem atendimento e é adotada pelo próximo que abrir).
CREATE OR REPLACE FUNCTION enc1a_atendimento_da_linha() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.atendimento_id IS NULL AND NEW.conversation_id IS NOT NULL THEN
        SELECT a.id INTO NEW.atendimento_id FROM conversas c JOIN atendimentos a ON a.tenant_id=c.tenant_id AND a.id=c.atendimento_id
         WHERE c.tenant_id=NEW.tenant_id AND c.conversation_id=NEW.conversation_id AND a.estado='aberto';
    END IF;
    RETURN NEW;
END $$;
-- Chamada de Skill: o turno em andamento da conversa (há um por vez, pela trava da conversa). Sem turno em andamento
-- (Skill depois de o gateway desistir por tempo, chamada manual), o último turno da conversa, com fora_do_turno=true.
CREATE OR REPLACE FUNCTION enc1a_turno_da_skill() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.turno_id IS NULL AND NEW.conversation_id IS NOT NULL THEN
        SELECT t.id INTO NEW.turno_id FROM turnos t
         WHERE t.tenant_id=NEW.tenant_id AND t.conversation_id=NEW.conversation_id AND t.status='em_andamento' AND t.etapa='processando'
         ORDER BY t.id DESC LIMIT 1;
        IF NEW.turno_id IS NOT NULL THEN
            NEW.fora_do_turno := false;
        ELSE
            SELECT t.id INTO NEW.turno_id FROM turnos t WHERE t.tenant_id=NEW.tenant_id AND t.conversation_id=NEW.conversation_id
             ORDER BY t.id DESC LIMIT 1;
            IF NEW.turno_id IS NOT NULL THEN NEW.fora_do_turno := true; END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['mensagens','turnos','pedidos','solicitacoes','skill_registro','skill_chamadas'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS enc1a_atendimento ON %I', t);
        EXECUTE format('CREATE TRIGGER enc1a_atendimento BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION enc1a_atendimento_da_linha()', t);
    END LOOP;
    FOREACH t IN ARRAY ARRAY['skill_registro','skill_chamadas'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS enc1a_turno ON %I', t);
        EXECUTE format('CREATE TRIGGER enc1a_turno BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION enc1a_turno_da_skill()', t);
    END LOOP;
END $$;
