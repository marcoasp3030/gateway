"""Configuração de atendimento (CFG1-a, docs/ESPEC_CFG1.md): perfil enviado ao agente no pacote e
comportamentos técnicos que a empresa pode ajustar. Aqui ficam o esquema, a validação e a herança;
nada aqui executa comportamento nem fala com banco."""
import re
import unicodedata

MOTIVOS_TRANSFERENCIA=('pediu_humano','reclamacao','pedido_para_confirmar','pagamento','restricao_alimentar','evento_ou_encomenda','catering','fora_do_escopo','assunto_proibido')
# Nunca desligáveis: o cliente sempre consegue chegar a uma pessoa, e reclamação sempre sobe.
MOTIVOS_FIXOS=('pediu_humano','reclamacao')
MAX_ASSUNTOS=15
NOME=re.compile(r"[^\W\d_](?:[^\W\d_]|[ '\-]){1,29}")

# Perfil: tipo, limites e padrão da plataforma. Nulo num escopo = herda do de cima.
PERFIL={
    # Quem é a empresa (CFG1-a.1). nome_empresa sem valor em nenhum nível = o nome cadastrado da empresa.
    'nome_empresa':{'tipo':'texto','min':2,'max':60,'padrao':None,'opcional':True,'linhas':0},
    'descricao_negocio':{'tipo':'texto','max':300,'padrao':'','linhas':3},
    'nome_atendente':{'tipo':'texto','min':2,'max':30,'padrao':None,'opcional':True},
    'apresentacao_inicial':{'tipo':'booleano','padrao':True},
    'tom':{'tipo':'escolha','opcoes':['formal','equilibrado','descontraido'],'padrao':'equilibrado'},
    'emojis':{'tipo':'escolha','opcoes':['nenhum','moderado','bastante'],'padrao':'moderado'},
    'tamanho_respostas':{'tipo':'escolha','opcoes':['curtas','medias'],'padrao':'curtas'},
    'tratamento':{'tipo':'escolha','opcoes':['voce','senhor_senhora'],'padrao':'voce'},
    'pedir_nome':{'tipo':'booleano','padrao':False},
    'sugestoes':{'tipo':'escolha','opcoes':['desligado','leve'],'padrao':'leve'},
    'regras_da_casa':{'tipo':'texto','max':1500,'padrao':'','linhas':20},
    'assuntos_proibidos':{'tipo':'lista_assuntos','max_itens':MAX_ASSUNTOS,'acoes':['recusar','transferir'],'padrao':[]},
    'motivos_transferencia':{'tipo':'lista_escolhas','opcoes':list(MOTIVOS_TRANSFERENCIA),'fixos':list(MOTIVOS_FIXOS),'padrao':list(MOTIVOS_TRANSFERENCIA)},
    'mensagem_boas_vindas':{'tipo':'texto','max':300,'padrao':None,'opcional':True},
    'mensagem_despedida':{'tipo':'texto','max':300,'padrao':None,'opcional':True},
}
PERFIL_PADRAO={k:v['padrao'] for k,v in PERFIL.items()}
TEXTOS_LIVRES=('descricao_negocio','regras_da_casa','mensagem_boas_vindas','mensagem_despedida')

EMOJIS_DE_REACAO=('👍','❤️','🙏','😊','😂','👏','🎉','✅')
# Comportamentos que a empresa ajusta e que entram na versão. (grupo, tipo, mínimo, máximo, opções, disponível)
# disponivel=False: já faz parte do contrato, mas o gateway ainda não executa (blocos R1 e G3) — o painel mostra "em breve".
COMPORTAMENTOS={
    'debounce_s':('ritmo','inteiro',1,15,None,True),'debounce_curto_s':('ritmo','decimal',0.5,15,None,True),
    'debounce_incompleta_s':('ritmo','decimal',1,15,None,True),'debounce_palavras':('ritmo','inteiro',1,50,None,True),
    'max_wait_s':('ritmo','inteiro',1,30,None,True),'media_wait_s':('ritmo','inteiro',0,60,None,True),
    'refazer_turno':('ritmo','escolha',None,None,['on','off'],True),'history_turns':('ritmo','inteiro',0,100,None,True),
    'acoes_recentes_min':('ritmo','inteiro',0,1440,None,True),
    'slow_notice_s':('mensagens','inteiro',0,300,None,True),'aviso_demora':('mensagens','texto',0,300,None,True),
    'aviso_demora_janela_s':('mensagens','inteiro',0,3600,None,True),'fallback':('mensagens','texto',15,500,None,True),
    'botoes_interativos':('mensagens','escolha',None,None,['on','off'],True),
    'pause_minutes':('atendimento_humano','inteiro',0,1440,None,True),'encerrar_vazia_min':('atendimento_humano','inteiro',1,1440,None,True),
    'carrinho_expira_h':('pedidos','inteiro',1,720,None,True),
    'reacoes':('reacoes','escolha',None,None,['on','off'],False),'reacoes_emojis':('reacoes','lista_escolhas',1,8,list(EMOJIS_DE_REACAO),False),
    'reacoes_max_dia':('reacoes','inteiro',0,10,None,False),
    'fora_do_horario':('fora_do_horario','escolha',None,None,['atender','so_avisar'],False),
    'fora_do_horario_mensagem':('fora_do_horario','texto',1,500,None,False),'fora_do_horario_aceita_agendado':('fora_do_horario','booleano',None,None,None,False),
    'transbordo_alerta':('transbordo','telefone',10,15,None,False),'transbordo_horario_equipe':('transbordo','horarios',None,None,None,False),
    'transbordo_fora_do_horario':('transbordo','texto',1,500,None,False),
    # SOL1: avisar a equipe sem parar o atendimento; destino do alerta marcado como grupo externo mascara o telefone.
    'notificar_equipe':('transbordo','escolha',None,None,['on','off'],True),'transbordo_destino_externo':('transbordo','booleano',None,None,None,True),
}
# De operação e proteção: fora da versão, efeito imediato, só a plataforma muda (pelas rotas de sempre).
SO_PLATAFORMA=('send_mode','agente_mode','modo_piloto','contatos_permitidos','max_lotes_simultaneos','max_concorrencia',
               'max_msgs_por_minuto_contato','skyone_dominios_permitidos','alerta_telefone')
ORIGENS={'plataforma':'plataforma','modelo':'segmento','tenant':'empresa','agente':'agente'}

# Instrução de sistema escrita no lugar de regra da casa: bloqueia a publicação.
BLOQUEIOS=(
    ('ignorar_instrucoes',r'\b(ignor|desconsider|desobedec)\w*\s+(?:\w+\s+){0,3}(instruc|regra|orientac|ordens|prompt)'),
    ('esquecer_instrucoes',r'\besquec\w*\s+(?:\w+\s+){0,3}(instruc|regra|orientac|tudo|anterior|prompt)'),
    ('formato_de_resposta',r'\brespond\w*\s+(?:\w+\s+){0,2}em\s+(json|xml|html|markdown|codigo)'),
    ('trocar_de_papel',r'\b(voce\s+agora\s+e|a\s+partir\s+de\s+agora\s+voce|finja\s+(ser|que)|aja\s+como|atue\s+como)\b'),
    ('prompt_do_sistema',r'\b(system\s+prompt|prompt\s+d[eo]\s+sistema|instruc\w+\s+d[eo]\s+sistema|regras\s+d[eo]\s+sistema)\b'),
    ('dados_sensiveis',r'\b(pec|ped|solicit|exij)\w*\s+(?:\w+\s+){0,5}(senha|cvv|codigo\s+de\s+seguranca|numero\s+d[eo]\s+cartao|dados\s+d[eo]\s+cartao|foto\s+d[eo]\s+cartao)'),
    ('dados_sensiveis',r'\bcvv\b'),
)
# Duvidoso: pode ser legítimo; quem publica precisa confirmar.
AVISOS=(
    ('parece_instrucao',r'\b(ignore|ignorar|esqueca|json|formato\s+d[ae]\s+resposta|nao\s+siga|sem\s+restric\w+|instruc\w+)\b'),
    ('parece_papel',r'\bvoce\s+e\s+(um|uma|o|a)\b'),
    ('dado_sensivel',r'\b(senha|cartao\s+de\s+credito|cpf)\b'),
    ('link',r'(https?://|www\.)\S+'),
)


def _plano(texto):
    """Mesmo comprimento do original, sem maiúsculas e sem acento: os trechos achados valem como posição no texto."""
    return ''.join((unicodedata.normalize('NFKD',c)[0] if unicodedata.normalize('NFKD',c) else c).casefold()[:1] or ' ' for c in texto)


def analisar_texto(campo,texto):
    """(bloqueios, avisos) de um texto livre da empresa. Cada item: {"campo","motivo","trecho"}."""
    if not isinstance(texto,str) or not texto.strip(): return [],[]
    plano=_plano(texto);bloqueios=[];avisos=[];tomados=[]
    def trecho(m):
        ini=max(0,m.start()-10);fim=min(len(texto),m.end()+10)
        return texto[ini:fim].strip()
    for motivo,padrao in BLOQUEIOS:
        for m in re.finditer(padrao,plano):
            bloqueios.append({'campo':campo,'motivo':motivo,'trecho':trecho(m)});tomados.append((m.start(),m.end()))
    for motivo,padrao in AVISOS:
        for m in re.finditer(padrao,plano):
            if any(a<m.end() and m.start()<b for a,b in tomados): continue
            avisos.append({'campo':campo,'motivo':motivo,'trecho':trecho(m)})
    return bloqueios,avisos


def _texto(valor,regra):
    """Texto limpo ou o motivo da recusa."""
    if not isinstance(valor,str): return None,'tipo'
    if any(ord(c)<32 and c not in '\n\t' or ord(c)==127 for c in valor): return None,'caractere'
    valor='\n'.join(linha.rstrip() for linha in valor.replace('\t',' ').strip().split('\n'))
    if len(valor)>regra['max'] or len(valor)<regra.get('min',0): return None,'tamanho'
    if valor.count('\n')>regra.get('linhas',5): return None,'linhas'
    return valor,None


def validar_perfil(dados):
    """Valida o perfil que um escopo define. Devolve (limpo, erros): chaves com None = herda.
    erros: [{"campo","motivo"}] com todos os problemas de uma vez."""
    limpo={};erros=[]
    if not isinstance(dados,dict): return {},[{'campo':'perfil','motivo':'tipo'}]
    for campo,valor in dados.items():
        regra=PERFIL.get(campo)
        if regra is None: erros.append({'campo':'perfil.'+str(campo)[:40],'motivo':'campo_desconhecido'});continue
        if valor is None: limpo[campo]=None;continue
        erro=None
        if regra['tipo']=='booleano':
            if type(valor) is not bool: erro='tipo'
        elif regra['tipo']=='escolha':
            if valor not in regra['opcoes']: erro='opcao'
        elif regra['tipo']=='texto':
            # Campo opcional enviado vazio = sem valor próprio (herda).
            if regra.get('opcional') and isinstance(valor,str) and not valor.strip(): limpo[campo]=None;continue
            valor,erro=_texto(valor,regra)
            if not erro and campo=='nome_atendente' and not NOME.fullmatch(valor): erro='caractere'
            if not erro and campo in ('mensagem_boas_vindas','mensagem_despedida') and re.search(r'(https?://|www\.|\S+@\S+\.\S+|\d[\d ()\-]{7,}\d)',valor): erro='contato_ou_link'
            if not erro and regra.get('opcional') and valor=='': valor=None
        elif regra['tipo']=='lista_escolhas':
            if not isinstance(valor,list) or any(v not in regra['opcoes'] for v in valor) or len(set(valor))!=len(valor): erro='opcao'
            else: valor=[m for m in regra['opcoes'] if m in valor or m in regra.get('fixos',())]
        elif regra['tipo']=='lista_assuntos':
            if not isinstance(valor,list): erro='tipo'
            elif len(valor)>regra['max_itens']: erro='quantidade'
            else:
                itens=[]
                for item in valor:
                    if not isinstance(item,dict) or set(item)!={'assunto','acao'} or item['acao'] not in regra['acoes']: erro='item';break
                    assunto,ruim=_texto(item['assunto'],{'min':3,'max':80,'linhas':0})
                    if ruim: erro='item';break
                    itens.append({'assunto':assunto,'acao':item['acao']})
                if not erro and len({i['assunto'].casefold() for i in itens})!=len(itens): erro='repetido'
                valor=itens
        if erro: erros.append({'campo':'perfil.'+campo,'motivo':erro})
        else: limpo[campo]=valor
    return limpo,erros


def analisar_perfil(perfil):
    """(bloqueios, avisos) de todos os textos livres do perfil."""
    bloqueios=[];avisos=[]
    for campo in TEXTOS_LIVRES:
        b,a=analisar_texto('perfil.'+campo,perfil.get(campo));bloqueios+=b;avisos+=a
    for n,item in enumerate(perfil.get('assuntos_proibidos') or []):
        b,a=analisar_texto(f'perfil.assuntos_proibidos[{n}]',item.get('assunto'));bloqueios+=b;avisos+=a
    return bloqueios,avisos


def resolver_perfil(*camadas,nome_da_empresa=None):
    """Herança campo a campo. camadas: [(origem, {campo: valor})] da mais geral para a mais específica.
    Devolve (perfil completo, origem de cada campo). Lista do nível mais específico substitui a de cima.
    nome_da_empresa: nome cadastrado da empresa, que vale como nome_empresa enquanto ninguém definir outro."""
    perfil=dict(PERFIL_PADRAO);origem={k:'plataforma' for k in PERFIL}
    if nome_da_empresa: perfil['nome_empresa']=' '.join(str(nome_da_empresa).split())[:60];origem['nome_empresa']='cadastro'
    for nome,dados in camadas:
        for campo in PERFIL:
            if isinstance(dados,dict) and dados.get(campo) is not None:
                perfil[campo]=dados[campo];origem[campo]=nome
    perfil['motivos_transferencia']=[m for m in MOTIVOS_TRANSFERENCIA if m in perfil['motivos_transferencia'] or m in MOTIVOS_FIXOS]
    return perfil,origem


def bloco_do_pacote(perfil):
    """perfil_atendimento exatamente como vai ao agente: todos os campos, na ordem do contrato, já resolvidos."""
    return {campo:perfil[campo] for campo in PERFIL}


def versao_texto(empresa,agente=0):
    """"e7" (versão 7 da empresa) ou "e7.a2" quando o agente tem versão própria."""
    return f'e{int(empresa or 0)}'+(f'.a{int(agente)}' if agente else '')


def esquema(padroes,papel):
    """Descrição dos campos para o painel desenhar a tela sem repetir regras."""
    comportamentos={}
    for campo,(grupo,tipo,minimo,maximo,opcoes,disponivel) in COMPORTAMENTOS.items():
        item={'grupo':grupo,'tipo':tipo,'padrao':padroes.get(campo),'editavel':papel in ('admin','plataforma'),'disponivel':disponivel}
        if minimo is not None: item['min']=minimo
        if maximo is not None: item['max']=maximo
        if opcoes: item['opcoes']=opcoes
        comportamentos[campo]=item
    for campo in SO_PLATAFORMA:
        comportamentos[campo]={'grupo':'operacao','tipo':'so_plataforma','padrao':padroes.get(campo),'editavel':False,'disponivel':True}
    return {'perfil':{k:{**{a:b for a,b in v.items()},'editavel':papel in ('admin','plataforma')} for k,v in PERFIL.items()},'comportamentos':comportamentos}
