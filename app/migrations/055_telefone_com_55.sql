-- TEL1 (29/10/2026): lista do piloto e telefones de alerta já gravados no formato do WhatsApp (55 + DDD + número, só dígitos),
-- a mesma regra que a API passa a aplicar (app/nucleo/telefone.py). Tira espaços, traços, parênteses e +; 10 ou 11 dígitos
-- ganham o 55; 12 ou 13 começando com 55 ficam. Fora disso (e identificador com letras do canal api) o valor fica como está.
-- Na lista do piloto, o número que ficar repetido depois de normalizar entra uma vez só, na primeira posição em que aparecia.
CREATE FUNCTION pg_temp.tel1(valor TEXT) RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN d ~ '^[0-9]{10,11}$' THEN '55'||d WHEN d ~ '^55[0-9]{10,11}$' THEN d ELSE valor END
    FROM (SELECT regexp_replace(valor,'[[:space:]()+-]','','g') AS d) x
$$;
CREATE FUNCTION pg_temp.tel1_lista(lista TEXT[]) RETURNS TEXT[] LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(array_agg(n ORDER BY o),'{}') FROM (
        SELECT DISTINCT ON (n) n,o FROM (SELECT pg_temp.tel1(x) AS n,o FROM unnest(lista) WITH ORDINALITY u(x,o)) s ORDER BY n,o) d
$$;
UPDATE agentes SET contatos_permitidos=pg_temp.tel1_lista(contatos_permitidos)
    WHERE contatos_permitidos IS NOT NULL AND contatos_permitidos IS DISTINCT FROM pg_temp.tel1_lista(contatos_permitidos);
UPDATE tenant_config SET contatos_permitidos=pg_temp.tel1_lista(contatos_permitidos)
    WHERE contatos_permitidos IS NOT NULL AND contatos_permitidos IS DISTINCT FROM pg_temp.tel1_lista(contatos_permitidos);
UPDATE agentes SET alerta_telefone=pg_temp.tel1(alerta_telefone) WHERE alerta_telefone IS DISTINCT FROM pg_temp.tel1(alerta_telefone);
UPDATE tenant_config SET alerta_telefone=pg_temp.tel1(alerta_telefone) WHERE alerta_telefone IS DISTINCT FROM pg_temp.tel1(alerta_telefone);
UPDATE agentes SET transbordo_alerta=pg_temp.tel1(transbordo_alerta) WHERE transbordo_alerta IS DISTINCT FROM pg_temp.tel1(transbordo_alerta);
UPDATE tenant_config SET transbordo_alerta=pg_temp.tel1(transbordo_alerta) WHERE transbordo_alerta IS DISTINCT FROM pg_temp.tel1(transbordo_alerta);
UPDATE tenants SET whatsapp_alerta_transbordo=pg_temp.tel1(whatsapp_alerta_transbordo)
    WHERE whatsapp_alerta_transbordo IS DISTINCT FROM pg_temp.tel1(whatsapp_alerta_transbordo);
