CREATE TABLE consumo_avisos (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), mes TEXT NOT NULL, percentual INT NOT NULL CHECK(percentual IN (80,100)),
 turnos BIGINT NOT NULL, limite BIGINT NOT NULL, criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,mes,percentual)
);
CREATE INDEX ix_turnos_tenant_data ON turnos(tenant_id,agente_inicio,agente_id);
