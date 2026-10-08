import os,sys
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
import uvicorn
sys.path.insert(0,"/workspace/app")
from plataforma import Infra,criar_app
url=urlsplit(os.environ["DATABASE_URL"])._replace(path="/gateway_migrations_test").geturl()
assert urlsplit(url).path.endswith("_test")
infra=Infra("a"*32,"b"*32,"c"*32,Fernet.generate_key().decode(),url,os.environ["REDIS_URL"])
uvicorn.run(criar_app(infra),host="0.0.0.0",port=8000,access_log=False)
