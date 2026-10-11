-- PED1-c (docs/ESPEC_PED1.md, seções 6 e 14). Aditiva: a imagem anterior segue funcionando (ela só usa contatos.nome).
-- Três origens de nome, guardadas separadas. Prioridade de exibição: cadastro > informado > perfil > telefone.
ALTER TABLE contatos
    ADD COLUMN IF NOT EXISTS nome_cadastro TEXT CHECK (char_length(nome_cadastro) BETWEEN 2 AND 60),
    ADD COLUMN IF NOT EXISTS nome_informado TEXT CHECK (char_length(nome_informado) BETWEEN 2 AND 60),
    ADD COLUMN IF NOT EXISTS nome_perfil TEXT CHECK (char_length(nome_perfil) BETWEEN 1 AND 100),
    ADD COLUMN IF NOT EXISTS nome_cadastro_por TEXT,
    ADD COLUMN IF NOT EXISTS nome_cadastro_em TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS nome_informado_em TIMESTAMPTZ;

-- O nome que existe hoje é o de perfil (senderName da primeira mensagem); o da conversa cobre contato sem nome.
UPDATE contatos ct SET nome_perfil=left(x.nome,100)
    FROM (SELECT o.tenant_id,o.id,coalesce(nullif(btrim(o.nome),''),(SELECT nullif(btrim(c.nome),'') FROM conversas c
              WHERE c.tenant_id=o.tenant_id AND c.contato_id=o.id AND nullif(btrim(c.nome),'') IS NOT NULL
              ORDER BY c.atualizado_em DESC LIMIT 1)) AS nome
          FROM contatos o WHERE o.anonimizado_em IS NULL AND o.nome_perfil IS NULL) x
    WHERE ct.tenant_id=x.tenant_id AND ct.id=x.id AND x.nome IS NOT NULL;

-- Conversa que nasceu de simulação no painel (canal_info.teste). As antigas são reconhecidas pela auditoria
-- "agente.simulado", gravada logo depois de a conversa ser criada pela simulação.
ALTER TABLE conversas ADD COLUMN IF NOT EXISTS simulada BOOLEAN NOT NULL DEFAULT false;
UPDATE conversas c SET simulada=true
    WHERE NOT c.simulada
      AND EXISTS(SELECT 1 FROM tenant_canais k WHERE k.tenant_id=c.tenant_id AND k.id=c.canal_id AND k.tipo='api')
      AND EXISTS(SELECT 1 FROM audit_log a WHERE a.tenant_id=c.tenant_id AND a.acao='agente.simulado' AND a.alvo=c.agente_id
                 AND a.criado_em BETWEEN c.criado_em AND c.criado_em+interval '60 seconds');

-- ordem=recentes: última atividade primeiro, com desempate pelo id.
CREATE INDEX IF NOT EXISTS ix_conversas_recentes ON conversas(tenant_id, atualizado_em DESC, conversation_id DESC);
