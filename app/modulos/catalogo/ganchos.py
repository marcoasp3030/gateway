"""Ganchos do módulo de catálogo (MULTI1-b): o que o núcleo pergunta sobre cardápio sem saber o que é cardápio."""


async def itens_no_cardapio(db,tenant):
    """{código da unidade: quantos itens podem ser pedidos} — ativo, com preço e que não seja só do salão (a conta do modo `categorias` da busca)."""
    rows=await db.fetch("""SELECT unidade,count(*) n FROM catalogo_itens WHERE tenant_id=$1 AND ativo AND preco IS NOT NULL AND delivery<>'nao'
        AND tipo IN ('item','adicional') GROUP BY 1""",tenant)
    return {r['unidade']:r['n'] for r in rows}


async def unidades_no_pacote(db,tenant):
    """DISP1: campos de cada unidade no pacote. tem_cardapio=false = nenhum item desta unidade pode ser pedido; o agente não oferece cardápio dela.
    Devolve {código: campos} e a função que dá os campos de uma unidade sem item."""
    cardapio=await itens_no_cardapio(db,tenant)
    return lambda codigo: {'tem_cardapio':cardapio.get(codigo,0)>0,'itens_no_cardapio':cardapio.get(codigo,0)}


async def unidades_que_atendem(db,tenant):
    """HOR2: unidades onde dá para pedir (com cardápio). A próxima abertura que vai ao agente é a de uma delas.
    Conjunto vazio = empresa sem nenhum item com preço: vale qualquer unidade."""
    return {codigo for codigo,n in (await itens_no_cardapio(db,tenant)).items() if n>0}
