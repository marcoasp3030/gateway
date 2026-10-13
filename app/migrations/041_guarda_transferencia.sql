-- HOR2 (docs/ESPEC_HOR2.md): transferência por "pedido_para_confirmar" sem nenhuma recusa de Skill no turno.
-- 'refeito' = a resposta foi descartada e o turno refeito com aviso; 'mantida' = o agente repetiu e a transferência seguiu (com alerta).
-- Aditivo: a imagem anterior segue funcionando.
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS transferencia_sem_recusa TEXT;
