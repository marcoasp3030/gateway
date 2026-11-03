# CARD-REDES — redes sociais e telefone no cardápio

**Situação: proposta para revisão do Marco, 03/11/2026. Nada implementado.**

## 1. O que muda

- **A empresa pode cadastrar, se quiser,** Instagram, Facebook e telefone na Aparência do cardápio.
  - Tudo é opcional: sem nada cadastrado, o cardápio fica como hoje.
  - Cada item pode ser ligado ou desligado sem apagar o que foi digitado.
- **Valem para a empresa inteira,** não por unidade: as mesmas redes aparecem no cardápio de todas as unidades.
- **Quem edita:** gestor e acima, como o resto da aparência. Supervisor e funcionário não veem a tela.
- **Onde o cliente vê:** no cardápio público (link da conversa e Link da loja) e na prévia.

## 2. Regras

| Tipo | O que a empresa digita | O que o gateway grava | Recusa |
|---|---|---|---|
| `instagram` | `@bykoji`, `bykoji` ou `https://instagram.com/bykoji` | o usuário (`bykoji`) | usuário fora de `[A-Za-z0-9._]{1,30}`; link de outro domínio |
| `facebook` | `https://facebook.com/bykoji` (ou `fb.com`, `m.facebook.com`) ou só `bykoji` | o caminho da página (`bykoji`) | link de outro domínio; caminho fora de `[A-Za-z0-9.\-/]{1,100}` |
| `telefone` | `(11) 3333-4444`, `11 91234-5678`… | 55 + DDD + número (mesma regra do TEL1) | fora da regra do TEL1: 400 com "Use DDD + número, ex.: 11912345678" |

- **No máximo um de cada tipo,** porque a lista é fechada (instagram, facebook, telefone).
- **O link é montado pelo gateway** a partir do que foi gravado (`https://instagram.com/<usuario>`, `https://facebook.com/<caminho>`, `tel:+<numero>`). A empresa nunca grava um link livre, e por isso ninguém consegue pôr no cardápio um endereço que leve para outro site.
- **Ordem:** a ordem em que a empresa cadastra é a ordem mostrada ao cliente.
- **Recusa:** com a recusa, nada é gravado; a resposta diz `detalhes.campo` e `detalhes.linha`.

## 3. Rotas

Ficam separadas da aparência porque o `PUT` da aparência substitui tudo. Se as redes entrassem nele, um painel antigo apagaria as redes ao salvar as cores.

| Rota | Corpo / resposta | Papel |
|---|---|---|
| `GET /admin/v1/tenants/{t}/cardapio/redes` | `{"itens":[{"tipo","valor","ativo","url"}]}` | gestor |
| `PUT /admin/v1/tenants/{t}/cardapio/redes` | `{"itens":[{"tipo":"instagram","valor":"@bykoji","ativo":true}, …]}`: substitui a lista; `[]` apaga tudo | gestor |

- **Página pública:** `POST /api/v1/publico/cardapio/ler` passa a trazer `redes: [{"tipo","url","rotulo"}]`, só com as ativas. Sem nenhuma, vem `[]`.
  - Os rótulos são: `@bykoji` (Instagram), `By Koji` (Facebook, o caminho da página) e `(11) 3333-4444` (telefone, formatado).
- **Auditoria:** cada `PUT` que muda algo grava `cardapio.redes_salvas` com o antes e o depois.

## 4. Dados

Migração `063_cardapio_redes.sql`, aditiva:

- tabela `cardapio_redes`, com as colunas `tenant_id`, `tipo`, `valor`, `ativo`, `ordem`, `atualizado_por` e `atualizado_em`;
- chave `(tenant_id, tipo)`;
- `CHECK` do tipo na lista fechada.

## 5. Painel ([PAINEL])

- **Tela de edição:** em Aparência do cardápio, a seção "Redes e contato" tem 3 linhas fixas (Instagram, Facebook, Telefone).
  - Cada linha tem um campo e um botão liga/desliga; campo vazio significa que a rede não é mostrada.
  - O erro do gateway aparece embaixo da linha.
- **Topo do cardápio:** ícones redondos abaixo do slogan, nas cores do tema.
  - Instagram e Facebook abrem o app no celular (ou o site no computador); telefone abre a ligação.
  - Cada ícone tem nome acessível ("Instagram do By Koji").
- **Rodapé do cardápio:** bloco "Siga a gente", com os mesmos itens e o rótulo ao lado de cada ícone.
- **Tela de pedido enviado:** "Enquanto preparamos, siga a gente no Instagram", com um botão, só se o Instagram estiver ativo. Fica junto do "Fazer outro pedido".
- **Sem redes ativas:** nada disso aparece.

## 6. Testes (gateway)

- Normalização e recusa de cada tipo, incluindo link de outro domínio, usuário inválido e telefone fora do TEL1.
- `PUT` substitui a lista e mantém a ordem; tipo repetido é recusado; `[]` apaga.
- Só gestor e acima: supervisor recebe 403 `papel_insuficiente`; empresa de outro escopo recebe 404.
- A página pública traz só as ativas, com o link montado pelo gateway. Prévia e Link da loja trazem o mesmo.
- Salvar a aparência (cores) não mexe nas redes.
- Auditoria só quando algo muda.

## 7. Decisões que ficam com o Marco

1. **Telefone:** ao tocar, abre a **ligação** (proposta) ou abre o **WhatsApp** daquele número?
2. **Botão na tela de pedido enviado:** só o Instagram (proposta) ou a rede que vier primeiro na lista?

## 8. O que não entra

- Outras redes (TikTok, YouTube, iFood…). A lista fechada pode crescer depois, uma por vez.
- Redes por unidade.
- Contagem de cliques por rede.
