"""IFOOD1 (docs/ESPEC_IFOOD1.md): cliente do iFood como aplicativo centralizado.

Um token para todas as lojas (grantType=client_credentials), guardado cifrado no Redis até perto de vencer. Só um processo
renova por vez, e só ao vencer ou com 401: pedir token demais bloqueia o aplicativo no iFood.
Credenciais: as gravadas pelo painel (integracao_credenciais, cifradas, só com a chave de plataforma) valem sobre as do .env
(IFOOD_CLIENT_ID / IFOOD_CLIENT_SECRET). Nunca vão para log nem resposta."""
import asyncio
import hashlib
import os
import httpx

TIMEOUT_S=20
CHAVE_COFRE='_ifood'


class ErroIfood(Exception):
    """motivo: nao_configurado, autenticacao, sem_permissao, nao_encontrado, limite, indisponivel, recusado, formato_inesperado."""
    def __init__(self,motivo,status=None):
        super().__init__(motivo);self.motivo=motivo;self.status=status


class Ifood:
    def __init__(self,http,redis,cofre,prefixo='gw',client_id=None,client_secret=None):
        self.http=http;self.redis=redis;self.cofre=cofre;self.prefixo=prefixo
        self.base=os.getenv('IFOOD_BASE_URL','https://merchant-api.ifood.com.br').rstrip('/')
        self.origem=None
        self._usar(client_id if client_id is not None else os.getenv('IFOOD_CLIENT_ID',''),
                   client_secret if client_secret is not None else os.getenv('IFOOD_CLIENT_SECRET',''),
                   'informada' if client_id is not None else 'env')

    def _usar(self,client_id,segredo,origem):
        self._id=client_id or '';self._segredo=segredo or '';self.origem=origem if self.configurado else None
        # Token separado por aplicativo: trocar as credenciais não reaproveita o token das antigas.
        marca=hashlib.sha256(self._id.encode()).hexdigest()[:12]
        self.k_token=f'{self.prefixo}:ifood:token:{marca}';self.k_trava=f'{self.prefixo}:ifood:token:{marca}:trava'

    async def carregar(self,db):
        """Usa as credenciais gravadas pelo painel, se houver; senão, as do .env."""
        v=await db.fetchval("SELECT valor_enc FROM integracao_credenciais WHERE provedor='ifood'")
        if v:
            try: d=self.cofre.abrir(CHAVE_COFRE,v)
            except Exception: raise ErroIfood('nao_configurado') from None
            self._usar(d.get('client_id'),d.get('client_secret'),'painel')
        return self

    @property
    def configurado(self): return bool(self._id and self._segredo)

    def cifradas(self):
        return self.cofre.cifrar(CHAVE_COFRE,{'client_id':self._id,'client_secret':self._segredo})

    async def _guardado(self):
        cifrado=await self.redis.get(self.k_token)
        if not cifrado: return None
        try: return self.cofre.abrir(CHAVE_COFRE,cifrado)
        except Exception: return None

    async def token(self,falhou=None):
        """Token válido. `falhou`: o que recebeu 401; se outro processo já renovou, usa o novo sem pedir outro."""
        if not self.configurado: raise ErroIfood('nao_configurado')
        t=await self._guardado()
        if t and t!=falhou: return t
        if not await self.redis.set(self.k_trava,'1',nx=True,ex=30):
            for _ in range(75):
                await asyncio.sleep(0.2)
                t=await self._guardado()
                if t and t!=falhou: return t
            raise ErroIfood('indisponivel')
        try:
            try:
                r=await self.http.post(self.base+'/authentication/v1.0/oauth/token',timeout=TIMEOUT_S,
                                       data={'grantType':'client_credentials','clientId':self._id,'clientSecret':self._segredo})
            except httpx.HTTPError: raise ErroIfood('indisponivel') from None
            if r.status_code>=500: raise ErroIfood('indisponivel',r.status_code)
            if r.status_code==429: raise ErroIfood('limite',429)
            if r.status_code!=200: raise ErroIfood('autenticacao',r.status_code)
            try: d=r.json()
            except ValueError: raise ErroIfood('formato_inesperado',r.status_code) from None
            novo=d.get('accessToken') if isinstance(d,dict) else None
            if not isinstance(novo,str) or not novo: raise ErroIfood('formato_inesperado',r.status_code)
            try: validade=int(d.get('expiresIn') or 3600)
            except (TypeError,ValueError): validade=3600
            # Renova 5 min antes de vencer (mínimo de 1 min guardado).
            await self.redis.set(self.k_token,self.cofre.cifrar(CHAVE_COFRE,novo),ex=max(60,validade-300))
            return novo
        finally:
            await self.redis.delete(self.k_trava)

    async def chamar(self,metodo,caminho,**kw):
        """Chamada autenticada. 401 renova o token uma vez; os outros erros viram ErroIfood com o motivo."""
        t=await self.token()
        for tentativa in (1,2):
            try: r=await self.http.request(metodo,self.base+caminho,headers={'Authorization':'Bearer '+t},timeout=TIMEOUT_S,**kw)
            except httpx.HTTPError: raise ErroIfood('indisponivel') from None
            if r.status_code==401 and tentativa==1:
                t=await self.token(falhou=t);continue
            break
        s=r.status_code
        if s==401: raise ErroIfood('autenticacao',s)
        if s==403: raise ErroIfood('sem_permissao',s)
        if s==404: raise ErroIfood('nao_encontrado',s)
        if s==429: raise ErroIfood('limite',s)
        if s>=500: raise ErroIfood('indisponivel',s)
        if s>=400: raise ErroIfood('recusado',s)
        if not r.content: return None
        try: return r.json()
        except ValueError: raise ErroIfood('formato_inesperado',s) from None

    async def lojas(self,tamanho=100,max_paginas=50):
        """Lojas que autorizaram o aplicativo (de todas as empresas): [{merchant_id, nome, razao_social}]."""
        lojas=[]
        for pagina in range(1,max_paginas+1):
            d=await self.chamar('GET','/merchant/v1.0/merchants',params={'page':pagina,'size':tamanho})
            if not isinstance(d,list): raise ErroIfood('formato_inesperado')
            for m in d:
                if not isinstance(m,dict) or not isinstance(m.get('id'),str): raise ErroIfood('formato_inesperado')
                lojas.append({'merchant_id':m['id'],'nome':m.get('name'),'razao_social':m.get('corporateName')})
            if len(d)<tamanho: break
        return lojas

    async def loja(self,merchant_id):
        d=await self.chamar('GET',f'/merchant/v1.0/merchants/{merchant_id}')
        if not isinstance(d,dict): raise ErroIfood('formato_inesperado')
        return {'merchant_id':d.get('id') or merchant_id,'nome':d.get('name'),'razao_social':d.get('corporateName')}
