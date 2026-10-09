ALTER TABLE agentes DROP CONSTRAINT agentes_skyone_auth_tipo_check;
ALTER TABLE agentes ADD CONSTRAINT agentes_skyone_auth_tipo_check
 CHECK (skyone_auth_tipo IN ('nenhuma','cabecalho','bearer','basic'));
ALTER TABLE agentes ADD COLUMN skyone_auth_usuario_enc TEXT;
CREATE TABLE agente_circuitos (
 tenant_id TEXT NOT NULL, agente_id TEXT NOT NULL,
 falhas INTEGER NOT NULL DEFAULT 0, geracao BIGINT NOT NULL DEFAULT 0,
 janela_inicio TIMESTAMPTZ NOT NULL DEFAULT now(),
 aberto_ate TIMESTAMPTZ, sonda_ate TIMESTAMPTZ, ultimo_alerta TIMESTAMPTZ,
 PRIMARY KEY(tenant_id,agente_id),
 FOREIGN KEY(tenant_id,agente_id) REFERENCES agentes(tenant_id,id)
);
