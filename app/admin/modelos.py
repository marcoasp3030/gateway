from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Estrito(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Tenant(Estrito):
    id: str | None = None
    nome: str | None = Field(None,min_length=1,max_length=200)
    plano: str | None = None
    limite_turnos_mes: int | None = Field(None,ge=0)
    timezone: str | None = None
    retencao_dias: int | None = Field(None,gt=0)
    tenant_teste: bool | None = None
    status: Literal['ativo','suspenso'] | None = None
    # Fase 4: número que recebe o alerta quando uma conversa fica aguardando atendente (nulo = desligado).
    whatsapp_alerta_transbordo: str | None = Field(None,pattern=r'^\d{10,15}$')


class Comportamento(Estrito):
    debounce_s: int | None = Field(None,ge=1,le=15)
    debounce_curto_s: float | None = Field(None,ge=0.5,le=15)
    debounce_palavras: int | None = Field(None,ge=1,le=50)
    max_wait_s: int | None = Field(None,ge=1,le=30)
    media_wait_s: int | None = Field(None,ge=0,le=60)
    pause_minutes: int | None = Field(None,ge=0)
    history_turns: int | None = Field(None,ge=0,le=100)
    slow_notice_s: int | None = Field(None,ge=0)
    aviso_demora: str | None = None
    fallback: str | None = None
    alerta_telefone: str | None = None
    send_mode: Literal['dry','live'] | None = None
    agente_mode: Literal['mock','live'] | None = None
    modo_piloto: bool | None = None
    contatos_permitidos: list[str] | None = None
    encerrar_vazia_min: int | None = Field(None,gt=0)
    max_lotes_simultaneos: int | None = Field(None,gt=0,le=100)
    max_concorrencia: int | None = Field(None,gt=0,le=100)
    max_msgs_por_minuto_contato: int | None = Field(None,ge=1,le=600)
    botoes_interativos: Literal['on','off'] | None = None
    debounce_incompleta_s: float | None = Field(None,ge=1,le=15)
    refazer_turno: Literal['on','off'] | None = None
    aviso_demora_janela_s: int | None = Field(None,ge=0,le=3600)
    carrinho_expira_h: int | None = Field(None,ge=1,le=720)
    acoes_recentes_min: int | None = Field(None,ge=0,le=1440)
    skyone_dominios_permitidos: list[str] | None = None


class Agente(Comportamento):
    nome: str | None = Field(None,min_length=1,max_length=200)
    modelo_id: str | None = None
    modelo_versao: int | None = Field(None,gt=0)
    status: Literal['rascunho','ativo','pausado'] | None = None
    provedor: Literal['skyone','mock'] | None = None
    skyone_estrategia: Literal['por_modelo','por_agente'] | None = None
    skyone_webhook_url: str | None = None
    skyone_auth_tipo: Literal['nenhuma','cabecalho','bearer','basic'] | None = None
    skyone_auth_cabecalho: str | None = None
    skyone_auth: str | None = None
    skyone_auth_usuario: str | None = None
    timeout_s: int | None = Field(None,gt=0,le=600)
    instrucoes_agente: str | None = None
    modo_resposta: Literal['sync'] | None = None


class Canal(Estrito):
    nome: str | None = Field(None,min_length=1,max_length=200)
    tipo: Literal['uazapi','meta','site','email','teams','luxbrain','api'] | None = None
    agente_id: str | None = None
    publico: Literal['clientes','interno'] | None = None
    ativo: bool | None = None
    credenciais: dict[str,str] | None = None


class Chave(Estrito):
    nome: str | None = Field(None,min_length=1,max_length=200)
    escopos: list[str] | None = None
    agente_id: str | None = None
    expira_em: str | None = None
    ips_permitidos: list[str] | None = None
