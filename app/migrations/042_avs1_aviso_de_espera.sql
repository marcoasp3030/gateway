-- AVS1 (docs/ESPEC_AVS1.md): modo do aviso de espera e textos variados. Aditivo: a imagem anterior segue funcionando.
-- Nulo = herda. Padrão da plataforma: 'presenca' (só "digitando…", sem texto).
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS aviso_espera_modo TEXT CHECK (aviso_espera_modo IN ('off','presenca','presenca_e_texto_contextual'));
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS aviso_espera_modo TEXT CHECK (aviso_espera_modo IN ('off','presenca','presenca_e_texto_contextual'));
ALTER TABLE tenant_config ADD COLUMN IF NOT EXISTS aviso_espera_textos TEXT[];
ALTER TABLE agentes ADD COLUMN IF NOT EXISTS aviso_espera_textos TEXT[];
