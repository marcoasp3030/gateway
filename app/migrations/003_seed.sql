-- Seed sem credenciais, sem canais externos e sem ativar atendimento.
INSERT INTO modelos_agente(id,nome,tipo,modulos,comportamento_padrao) VALUES
 ('atendente_delivery','Atendente delivery','atendimento',ARRAY['catalogo','pedidos'],
  '{"debounce_s":4,"max_wait_s":12,"history_turns":20,"send_mode":"dry","agente_mode":"mock","modo_piloto":true}'),
 ('assistente_generico','Assistente genérico','interno','{}',
  '{"send_mode":"dry","agente_mode":"mock","modo_piloto":true}');
INSERT INTO tenants(id,nome) VALUES ('bykoji','By Koji');
INSERT INTO tenant_config(tenant_id) VALUES ('bykoji');
INSERT INTO modulos_tenant(tenant_id,modulo) VALUES ('bykoji','catalogo'),('bykoji','pedidos');
INSERT INTO agentes(id,tenant_id,modelo_id,nome,provedor,send_mode,agente_mode,modo_piloto)
 VALUES ('bykoji_atendimento','bykoji','atendente_delivery','Atendimento By Koji','mock','dry','mock',true);
