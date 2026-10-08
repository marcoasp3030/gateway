# Gateway no domínio do painel

Endereço de saúde: https://painel.luxbrain.com.br/gateway/health

O gateway é uma API; a interface de gestão será construída no painel após a API multiempresa da Fase 0.5. `/gateway/` redireciona para o health.

## Rotas

| Caminho | Comportamento público |
|---|---|
| `/gateway/health` | `200 {"ok":true}` |
| `/gateway/webhooks/*` | Encaminhado ao gateway; autenticação do provedor obrigatória |
| `/gateway/api/*` | Encaminhado ao gateway; chave de integração obrigatória |
| `/gateway/admin/*`, `/gateway/test/*`, `/gateway/metricas`, `/gateway/docs` | 404, mesmo com chave |
| Outros caminhos sob `/gateway/` | 404 |

O HTTP de `/gateway` e `/gateway/*` redireciona para HTTPS. Os sistemas externos devem usar diretamente HTTPS.

## Infraestrutura aplicada

- Proxy existente: Caddy, container `painel_koji-proxy-1`.
- Configuração ativa: `/home/twadmin/painel_koji/docker/Caddyfile`.
- Referência versionada da configuração aplicada: [`Caddyfile.painel.example`](Caddyfile.painel.example). Revisar antes de substituir qualquer configuração existente.
- Rede compartilhada dedicada: `luxbrain_gateway_proxy`, criada com `--internal`. Somente proxy e gateway participam; Postgres e Redis permanecem na rede própria do gateway.
- Destino do proxy: `luxbrain-gateway:8000`. O gateway mantém a porta administrativa local `127.0.0.1:8100`.
- `docker-compose.proxy.yml` adiciona a rede/alias ao gateway. O `.env` desta VPS tem `COMPOSE_FILE=docker-compose.yml:docker-compose.proxy.yml` para preservar a rede nos próximos `docker compose up`.
- O Compose do painel declara a mesma rede como externa no serviço `proxy`. A conexão inicial foi feita sem recriar os serviços do painel.
- Caddy sobrescreve os cabeçalhos de origem, remove `/gateway` antes do encaminhamento, limita corpo a 2 MiB e mascara query strings/chaves nos logs de erro.
- O gateway conta os bytes reais do corpo (inclusive chunked) e limita por IP com Redis: webhooks 30/s com capacidade 60, API 10/s com capacidade 20; excedentes recebem 429 e `Retry-After: 1`.
- Certificado público emitido pelo Let's Encrypt. A emissão anterior havia falhado com DNS antigo (`177.67.71.227`); o DNS consultado nesta implantação aponta para `177.67.71.211`. O emissor do domínio foi explicitado e o Caddy recarregado.

As rotas de autenticação, dados e frontend do painel foram preservadas. O backend do painel, quando estiver em container, deverá usar uma rede privada e o nome do serviço do gateway: `127.0.0.1` dentro do container do painel não aponta para o host nem para o gateway. A ligação administrativa será definida na Fase 0.5.

## Atualizar o gateway

```bash
cd /home/twadmin/gateway
docker compose config --quiet
docker compose up -d --build gateway
docker compose ps
curl --fail https://painel.luxbrain.com.br/gateway/health
```

Em outro ambiente, criar antes a rede `docker network create --internal luxbrain_gateway_proxy` e conectar o proxy conforme o Compose do painel. Não aplicar `docs/nginx-gateway.conf` nesta VPS.

Para alterar o Caddy, validar antes de recarregar:

```bash
docker exec painel_koji-proxy-1 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec painel_koji-proxy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

Referências usadas: [reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy), [request_body](https://caddyserver.com/docs/caddyfile/directives/request_body), [filtros de log](https://caddyserver.com/docs/caddyfile/directives/log) e [TLS](https://caddyserver.com/docs/caddyfile/directives/tls).

## Desativar apenas a publicação

Substituir o conteúdo do snippet `gateway_https` por um matcher de `/gateway /gateway/*` que responda 404; manter as rotas do painel e o certificado. Validar e recarregar o Caddy. Isso preserva os containers, volumes e o acesso local ao gateway.

## Estado do atendimento

Ambiente ainda de teste: `bykoji_test`, `SKYONE_MODE=mock`, `SEND_MODE=dry`. O webhook atual da Fase 0 é `/gateway/webhooks/uazapi?s=<segredo-local>`; os webhooks por canal e a API `/admin/v1` serão criados na Fase 0.5. Não conectar um número de produção antes das validações previstas no plano.
