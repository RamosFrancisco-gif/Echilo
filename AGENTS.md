# Echilo — Steering

> Documento normativo. Qualquer alteração ao código deve respeitar as regras aqui
> definidas. Em caso de conflito entre este documento e o código, **o código está errado**.

---

## 1. Identidade

**Echilo** é uma plataforma angolana de mediação imobiliária curada. Liga
proprietários a inquilinos e compradores através de uma equipa interna que valida
cada imóvel antes de o publicar.

Não é um portal de anúncios abiertos. É um serviço de mediação.

- Mercado-alvo: Angola. Moeda: **Kwanza (AOA, `Kz`)**. Fuso: `Africa/Luanda` (WAT, UTC+1).
- Idiomas da interface: **Português de Angola** (`pt-AO`). Código de idioma HTTP: `pt-ao`.
- Waktu do sistema: `Africa/Luanda`.
- Número de telefone: formato `+244 9XX XXX XXX`.

---

## 2. Regras de negócio

### 2.1 Fluxo de entrada dos imóveis (lado do proprietário)

O proprietário **nunca publica diretamente**. O fluxo tem três etapas e é sempre
iniciado por contacto directo com a equipa.

#### Etapa 1 — Captação directa

O proprietário contacta a equipa do Echilo por **WhatsApp**, **formulário de
adesão** ou **chamada**.

- O canal de entrada cria um `Lead` de tipo `OWNER_INTAKE` (ver §2.4).
- O formulário de adesão é público e não exige autenticação.
- O `Lead` fica com estado `NEW` e é atribuido a um membro da equipa.

#### Etapa 2 — Curadoria e triagem

Antes de qualquer cadastro, a equipa recolhe e confirma:

| Dado | Obrigatório | Notas |
| --- | --- | --- |
| Fotos do imóvel | Sim | Mínimo 5. Máx. 30. Primeira foto é a capa. |
| Localização via satélite (mapa) | Sim | Ver §2.3. |
| Preço | Sim | Em Kwanza. Ver §2.5. |
| Tipo de contrato | Sim | `RENT` (arrendamento) ou `SALE` (venda). Ver §2.6. |
| Documentação legal | Sim | Ver §2.7. |
| Identificação do proprietário | Sim | Bilhete de identidade ou passaporte. |
| Contacto telefónico | Sim | Usado para a equipa, nunca exposto publicamente. |

Um imóvel **não** avança para cadastro enquanto faltar qualquer um destes campos.
A validação é feita por `PropertySubmission.is_ready_for_review()`.

#### Etapa 3 — Cadastro gerido

A equipa (ou o painel interno de administração) regista o imóvel com
informações padronizadas e verificadas.

- Só membros da equipa com perfil `CURATOR`, `AGENT` ou `ADMIN` criam imóveis.
- O `curator` (curador) responsável fica registado em `Property.curated_by`.
- O imóvel só fica visível publicamente quando `status == PUBLISHED`.

### 2.2 Fluxo do cliente / inquilino / comprador

1. **Navegação e consulta** — o cliente navega e encontra os imóveis
   `PUBLISHED`. Não vê imóveis em `DRAFT`, `IN_REVIEW`, `UNDER_VALIDATION`,
   `CHANGES_REQUESTED` nem `ARCHIVED`. Pode ainda restringir o catálogo a um
   raio desenhado no mapa (ver §2.12).
2. **Atendimento híbrido (IA + equipa humana)** — descritos em §2.4.

### 2.3 Localização via satélite (mapa)

A localização **nunca** é apenas um texto livre. Cada imóvel tem coordenadas.

- `latitude` / `longitude`: `DecimalField(max_digits=9, decimal_places=6)`.
- `location_accuracy_m`: raio de confiança em metros, preenchido pela equipa após
  inspeção por satélite. `NULL` significa localização não verificada.
- `location_verified_at`: quando a equipa confirmou a posição.
- `map_reference`: identificador do pin no mapa (ex.: `9.5833,13.2344`).
- Província, município e localidade são selections de listas fechadas, validados
  contra `apps.properties.reference.ANGOLA_PROVINCES`.

**Regra:** a origem do pin nunca é a morada textual do proprietário. É o ponto
obtido por inspeção de satélite e confirmado pela equipa. A morada exacta só é
divulgada após agendamento de visita.

Para layouts de terreno, o imóvel tem também `land_area_m2` e, quando aplicável,
`boundaries_geojson` (polígono).

### 2.4 Atendimento híbrido


Este é o núcleo do produto. Existem **dois níveis** de atendimento.

#### Nível 1 — IA (24/7, automático)

- Responder a dúvidas sobre imóveis **reais e publicados** na plataforma.
- Exemplos de perguntas suportadas:
  - "Qual é o valor da renda?"
  - "O imóvel tem tanque de água?"
  - "Aceita pagamento anual?"
  - "Mostra-me terrenos em Viana."
- O assistente **nunca** inventa dados. Só responde a partir de resultados de
  `get_property_details`, `search_properties` e `compare_properties`.
- Se não souber, diz que não sabe e escala.
- A IA **não** negocia, **não** promete valores e **não** marca visitas.
- Implementação: `apps.assistant`, provider **Groq** (modelo configurável em
  `ECHILO_AI_MODEL`), arranque em `provider.py`, ferramentas em `tools.py`.

#### Nível 2 — Equipa humana

Assuntos que **sempre** escalam para a equipa:

1. Agendamento de visitas.
2. Propostas formais.
3. Fecho de contrato.
4. Qualquer pedido de negociação de preço.
5. Disputas, queixas ou pedidos de remoção de imóvel.
6. Perguntas sobre documentação legal.

Uma conversa passa a `handled_by = HUMAN` e o `Conversation` fica com estado
`ESCALATED`. O nível 1 continua disponível, mas já não responde sozinho.

### 2.5 Dinheiro

- `price`: `DecimalField(max_digits=14, decimal_places=2)`. **Nunca** `float`.
- `currency`: sempre `AOA` (`ISO 4217`).
- Arrendamento (`RENT`): preço é **mensal**, apresentado como `450.000 Kz/mês`.
- Venda (`SALE`): preço é total, apresentado como `85.000.000 Kz`.
- Formatação: separador de milhares `.`, decimal `,`, símbolo `Kz`.
- Negociação de preço é sempre registada em `Offer` e tratada por um humano.

### 2.6 Tipo de contrato

- `RENT` — arrendamento. `Property.lease_term_months` aplicável.
- `SALE` — venda. Deve existir pelo menos um `Offer` antes de mudar de estado.
- Um imóvel tem **um** tipo de contrato activo de cada vez.
  Trocar de `RENT` para `SALE` exige `status` novo e nova curadoria.

### 2.7 Documentação legal

Cada imóvel tem uma `PropertyDocument` por documento, com `status` de verificação:

- `PENDING` — recebido, por verificar.
- `VERIFIED` — verificado pela equipa.
- `REJECTED` — recusado, com `rejection_reason` obrigatório.

Documentos aceite: escritura, certidão de registo predial, IUR, contrato de
arrendamento, documento de identificação do proprietário, certidão de
conformidade urbanística.

Um imóvel não é `PUBLISHED` sem `ownership_title` e `id_document` em `VERIFIED`.
Ver `Property.missing_verified_documents()`.

### 2.8 Ciclo de vida do imóvel

```
DRAFT ──▶ IN_REVIEW ──▶ UNDER_VALIDATION ──▶ PUBLISHED ──▶ ARCHIVED
                │              │                   │
                │              └──▶ CHANGES_REQUESTED ──┘
                └──▶ REJECTED
```

- Só `PUBLISHED` é público.
- Todas as transições passam por `Property.transition_to()`, que valida o
  destino e regista o autor em `PropertyStatusEvent`.
- `ARCHIVED` e `PUBLISHED` exigem `reason` no formulário interno.

### 2.9 Estados de um `Lead`

```
NEW ──▶ CONTACTED ──▶ QUALIFIED ──▶ CONVERTED
            │            │
            └──▶ DISQUALIFIED
```

`OWNER_INTAKE` → `CONVERTED` significa: o `OwnerProfile` foi criado e o imóvel
foi registado pela equipa.
`CLIENT_ENQUIRY` → `CONVERTED` significa: existe `VisitRequest` ou `Offer`.

### 2.10 Visitas

- `VisitRequest.status`: `PENDING` → `CONFIRMED` / `DECLINED` → `COMPLETED`.
- Só a equipa confirma ou recusa. `requested_by` é sempre um utilizador.
- Conflito: duas visitas confirmadas na mesma `Property` com sobreposição de
  intervalo são recusadas por `VisitRequest.clean()`.

### 2.11 Registo de clientes

O registo público é público e não exige autenticação, mas **ancora a conta a uma
pessoa real**. Não é um cadastro sem compromisso: a equipa-contacta o cliente antes
de tratar de qualquer pedido.

| Campo | Obrigatório | Regra |
| --- | --- | --- |
| Nome completo | Sim | |
| Data de nascimento | Sim | Idade mínima de 18 anos. Ver `validate_adult`. |
| NIF | Sim | Formato angolano `009671373HA093` e **único**. |
| Documento de identificação | Sim | Tipo (`BI` ou `PASSPORT`) e número. |
| E-mail | Sim | Único. |
| Telefone | Sim | Formato de Angola (§1). |
| Província de residência | Não | Lista fechada de `apps.properties.reference`. |
| Género | Não | Inclui "Prefiro não dizer". |

Regras:

- `MINORES DE IDADE NÃO CRIAM CONTA`. A validação está em
  `apps.core.validators.validate_adult`, testada isoladamente.
- O NIF identifica uma pessoa: duas contas com o mesmo NIF são recusadas em
  `ClientRegistrationForm.clean_nif`, e o campo é `unique` na base de dados.
- O NIF é normalizado antes de ser gravado (`normalize_nif`): espaços, hífenes e
  minúsculas são aceites. O formato, não o `max_length`, decide o que é válido.
- As regras de negócio vivem nos validadores e no formulário. A view continua a
  ser apenas orquestração.

### 2.12 Pesquisa por área (cliente)

O cliente pode restringir o catálogo a um raio desenhado no mapa. É o inverso de
§2.3: a equipa publica o pin, o cliente escolhe a zona.

Parâmetros: `center_lat`, `center_lon`, `radius_m`. Todos os três, ou nada.

O mapa **mostra o catálogo** e não é só um filtro sobre ele. Um mapa sem pinos
não tem nada para olhar, e a imagem de Angola inteira no ecrã é um papel de
parede: a 11 de zoom um imóvel é um pixel perdido dentro do país.

- `PropertyQueryService.map_markers()` devolve os imóveis a marcar e
  `build_map_payload()` monta o JSON que o JavaScript lê.
- **A área não esconde pinos.** `apply_filters()` não recebe a área, e
  `search()` aplica-a por cima. Se os pinos desaparecessem à medida que o raio
  apertasse, desenhar parecia não fazer nada: o mapa esvaziava e a única mudança
  visível era um número em cima. O círculo é que diz o que entrou.
- Os pinos **respeitam os restantes filtros** (finalidade, província, município,
  preço). Um pino para "comprar" numa página que diz "arrendar" é uma mentira.
- Só entram imóveis `PUBLISHED` com coordenadas não nulas. O que não tem pin
  verificado não é marcado: a alternativa é inventar a localização (§2.3).
- `MAP_MARKER_LIMIT` (250) corta o que não cabe. Acima do limite, o painel diz
  **quantos** ficaram de fora: pinos a menos do que o número anunciado fazem o
  imóvel "desaparecer" da procura.
- A vista abre enquadrada nos pinos, não no país. Com área na URL, quem manda é
  o círculo. Sem pinos, fica o centro configurado a um zoom útil.
- A cor distingue a finalidade — dourado arrendar, verde vender — e a legenda
  existe. Duas cores sem legenda são duas cores a adivinhar.
- O preço do pino é formatado por `apps.core.money`, o mesmo código dos cartões.
  O browser não escreve Kwanza.

Regras:

- O raio vive entre `MIN_SEARCH_RADIUS_M` (100) e `MAX_SEARCH_RADIUS_M`
  (50 000), em `apps.core.validators`.
- **Área atómica.** Se faltar um parâmetro, se não for número, se a coordenada
  sair de [-90, 90] / [-180, 180] ou se o raio estiver fora dos limites, a área
  é descartada **por inteiro** e o catálogo volta a ser o todo. Um recorte a
  meiofiltrar é pior do que nenhum recorte.
- Só entram imóveis `PUBLISHED` com coordenadas não nulas.
- A área compõe com os restantes filtros e sobrevive a chips e paginação.
- Com área, a ordenação é por distância crescente. Sem área, mantém-se a
  ordenação por data de publicação.
- **Caixa envolvente indexável primeiro, Haversine depois.** O MySQL não tem
  PostGIS, por isso `PropertyQueryService.within_area()` primeiro reduz pela
  caixa envolvente (aproveitando `prop_lat_lon_idx`) e só depois confirma a
  distância exacta em Python. A caixa é arredondada **para fora**: arredondar
  para dentro exclui imóveis válidos na fronteira do círculo.
- A geometria vive em `apps.core.geo` e é partilhada, não reimplementada.
- O estado da pesquisa fica **só no URL**. Nada de persistir nem registar a área
  escolhida: é uma consulta, não um perfil.

O mapa é uma melhoria progressiva, e o formulário não depende dele:

- O mapa escreve nos campos ocultos do formulário e dispara um `change`. O HTMX
  trata do resto — não há uma segunda via de pedido.
- Sem JavaScript, ou sem Leaflet, a secção do mapa desaparece e a URL continua a
  filtrar.
- **O que o mapa está a mostrar é escrito pelo servidor.** O texto com o número
  de imóveis, e a legenda, saem do template: quem abre a página sem JavaScript
  vê a mesma explicação, e quem vê um mapa vazio sabe se falta o mapa ou o
  catálogo. O JavaScript só acrescenta o que só ele sabe.
- **A acção principal tem nome.** Desenhar o círculo é um `<button>` no painel,
  com rótulo em português, e não o ícone de 30×30 do plugin. Esse botão
  funcionava, mas lia-se como um quadrado vazio, e quem não o percebeu partiu do
  princípio de que o mapa estava partido. A barra do `leaflet.draw` fica só com
  o editar e o apagar.
- O popup de cada pino escapa tudo o que a equipa escreveu no imóvel. O título é
  texto livre: sem escape, uma aspa ou um `<` fecham o atributo ou injectam HTML.
- Tem de existir operação por teclado: focar o mapa, mover com as setas, escolher
  o raio, aplicar o centro.
- Leaflet e Leaflet.draw são **vendorizados** em `static/vendor/`, com versão
  fixa. Nada de CDN: os tiles e a biblioteca mudam, o deploy não pode depender
  deles.
- As bibliotecas entram no `head`, antes do `echilo.js`. Com `defer`, o
  `ready()` do nosso código dispara de imediato, e uma biblioteca vinda depois
  no corpo ainda não estaria carregada: o mapa não apareceria, sem erro na
  consola.
- **O mapa diz quando os tiles falham.** Um 403 do fornecedor, um proxy ou uma
  rede fechada dão um aviso no painel com a causa provável. Sem isso, o mapa era
  um rectângulo vazio e o filtro parecia partido. O aviso some assim que um tile
  carrega.
- **Um aviso não é um tile.** O aviso acima só apanha o que falha por HTTP, e há
  fornecedores que respondem `200` com uma imagem de "API KEY REQUIRED" no lugar
  do mapa. O browser mostra-a e o Leaflet conta-a como carregada, sem aviso
  nenhum. Por isso a escolha do fornecedor é verificada com
  `python manage.py verify_map_tiles`, que compara o tile de Luanda com o de
  Nairobi e o de São Paulo: um mapa dá imagens diferentes, um cartaz dá a mesma
  três vezes. Nem o tamanho nem a variedade de tons bastam — um basemap
  minimalista é pequeno e quase liso, e um tile de oceano é liso por definição.
- O filtro por área funciona sem tiles: o raio aplica-se ao centro do mapa sem
  depender de uma única imagem.
- **A escolha do fornecedor de tiles é uma decisão de produção, não de código.**
  Nenhum basemap gratuito serve produção: o `tile.openstreetmap.org` recusa com
  403 o tráfego que não é um site Mozilla, e os termos de uso do CARTO e do Esri
  exigem acordo para uso comercial. Em desenvolvimento o default é a imagem
  aérea do Esri, que não pede chave; para produção entra um fornecedor
  contratado.
- O `{x}` e o `{y}` não são intercambiáveis entre fornecedores: o Leaflet escreve
  `{z}/{x}/{y}` e o Esri serve `{z}/{y}/{x}`. A troca manda o mapa para o lado
  errado do mundo sem dar erro nenhum.

### 2.13 Números que atravessam a interface

`LANGUAGE_CODE = "pt-ao"` localiza números: `{{ value }}` escreve `-8,918430` e
`1.000,00`. Serve para ler, **não** para viajar.

- Um valor que vai para um `<input>`, um atributo, uma URL ou um `JSON` sai pelo
  filtro `coordinate`, que garante ponto decimal e nada de separadores.
- Dinheiro e distâncias para leitura usam `kwanza`, `kwanza_compact` e `distance`.
- `{% localize off %}` **não** é a solução: em Django 4.2 exige `{% load l10n %}` e
  a biblioteca desaparece no 5.0. O filtro funciona nas duas versões.

---

## 3. Perfis e permissões

| Perfil (`User.role`) | Pode | Não pode |
| --- | --- | --- |
| `CLIENT` | Navegar, guardar favoritos, pedir visita, enviar mensagem, fazer oferta | Criar imóveis, ver imóveis não publicados, moderar |
| `CURATOR` | Tudo o que `CLIENT` faz + criar/editar imóveis, submeter a validação | Aprovar publicação final |
| `AGENT` | Tudo o que `CURATOR` faz + validar documentos, confirmar visitas, responder conversas escaladas | Alterar permissões |
| `ADMIN` | Tudo | — |

Regras:

- O registo público só cria `CLIENT`. Não existe auto-promoção de perfil.
- Promoção de perfil é feita em `/admin` ou por `MANAGER_EMAILS` nas settings.
- Nenhum `CLIENT` acede a `/admin`. Devolvir 403.
- O `client_staff` não existe: um `CLIENT` autenticado é sempre `is_staff=False`.

---

## 4. Estrutura do projecto

```
echilo/
├── config/                  # settings, urls, wsgi, asgi
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── apps/
│   ├── core/                # utilitários transversais, contexto, excepções
│   ├── accounts/            # User, sessão, cadastro, recuperação de senha
│   ├── properties/          # imóveis, captação, curadoria, publicação
│   ├── concierge/           # leads, visitas, ofertas, conversas
│   └── assistant/           # atendimento nível 1 (IA)
├── templates/
│   ├── base.html
│   ├── partials/            # componentes reutilizáveis
│   ├── accounts/
│   ├── properties/
│   ├── concierge/
│   └── assistant/
├── static/
│   ├── css/
│   ├── js/
│   ├── img/
│   └── vendor/               # bibliotecas de terceiros, versão fixa, sem CDN
├── templates_tests/         # (não existe; testes vivem em cada app)
├── manage.py
├── requirements.txt
├── .env.example
└── AGENTS.md
```

Regras estruturais:

- **Um app por bounded context.** `properties` não importa `assistant`.
  `assistant` lê imóveis por serviço público (`PropertyQueryService`), nunca
  por `objects.raw()`.
- **`config/` não contém modelos.** Só configuração e routing.
- **Apps usam `apps.<nome>` como label** para colisão zero com pacotes de terceiros.
- **Nada de lógica em views além de orquestração.** Regras em `services.py`,
  `selectors.py`, `validators.py`.
- **`models.py` não importa `views` nem chama I/O externo.**

---

## 5. Convenções de código

### 5.1 Python

- Python 3.11. Type hints em todas as funções públicas de `services.py`,
  `selectors.py` e `views.py`.
- `ruff` para lint e formatação. Linha máxima 100.
- `black` não é usado (conflita com o estilo do ruff format).
- Docstrings: uma linha, linguagem assertiva, terminada com ponto.
  Documentam **porquê**, não **o quê**.

```python
def get_published(*, purpose: Purpose, province: str | None = None) -> QuerySet[Property]:
    """Devolve apenas imóveis visíveis ao público."""
```

### 5.2 Nomes

- Classes de modelo no singular, sem sufixo `Model`.
- Enums em `TextChoices`, sempre dentro do modelo que os usa.
- Nomes de campo em `snake_case`, em inglês, sem abreviaturas.
- Booleanos lidos como afirmação: `is_published`, `has_water_tank`.
- Nomes de função e variável em inglês. Textos de interface em `pt-AO`.
- Signals e tarefas de fundo: `verb_object` no passado (`property_published`).

### 5.3 Django

- QuerySets só dentro de `selectors.py` ou `managers.py` na raiz do app.
  Excepção: `Property.objects` para consultas triviais dentro do próprio modelo.
- `.only()` / `.defer()` em listagens com media.
- `select_related` para `ForeignKey`, `prefetch_related` para `ManyToMany`.
- N+1 é bug. Qualquer listagem passa por um teste com `assertNumQueries`.
- Nenhum `settings` hard-coded fora de `config/settings/`.
- Nenhum segredo em código. Tudo por variável de ambiente (ver §7).
- Migrations são revisadas como código. Nunca `makemigrations` com migrations
  esquecidas no commit.
- `on_delete` é sempre explícito. `PROTECT` para dados que não se podem perder
  (ex.: `Property.curated_by`).

### 5.4 Validação

- `clean()` para validação que envolve mais do que um campo.
- `full_clean()` chamado nos formulários, não em `save()`.
- Validações de negócio em `validators.py` do app, testadas isoladamente.

### 5.5 Templates

- `base.html` define o esqueleto; `partials/` tem os componentes.
- Lógica de apresentação mínima. Filtros de template só para formatação
  (ver §2.5 e §2.13). Cálculos ficam em `@property` no modelo ou em `templatetags`.
- HTMX para interacções de baixo custo (filtros, chat, marcar favorito).
  Não há SPA nem framework JS de UI.
- Todo formulário tem `<label>` explícito. Acessibilidade é requisito, não extra.
- Classes CSS de componente. Nenhum `style=` inline para layout.
- `base.html` tem `head_extra` e `scripts` para os assets de uma página. Só o
  catálogo carrega Leaflet; nenhum outro template paga por ele.

### 5.6 Estados de carregamento

- **O cabeçalho e o rodapé nunca entram em estado de carregamento.** São chrome
  permanente: piscar, esbatê-los ou mostrar um esqueleto é sempre um defeito.
- O esqueleto (`skeleton`) vive **dentro de `<main>`** e só cobre o conteúdo.
- A animação de entrada por scroll usa `main [data-reveal]`, nunca um selector
  global. O chrome fica fora do périmètre por construção, não por convenção.
- O esqueleto é **irmão do alvo do HTMX**, nunca filho: `hx-swap="innerHTML"`
  apagaria um esqueleto que estivesse dentro de `#alvo` e a segunda troca já
  mostraria a página a saltar.
- Enquanto o esqueleto está visível, o alvo leva `aria-busy="true"`. O esqueleto
  é `aria-hidden`; quem precisa de saber que algo carrega é quem usa leitor de
  ecrã, e é o `aria-busy` que lho diz.
- `prefers-reduced-motion` reduz o esqueleto a um bloco estático: a forma é
  mantida, a ondulação desaparece.

### 5.7 Interface

Directrizes visuais herdadas dos protótipos existentes:

- Tema escuro: fundo `#0c0906`, superfícies `#15100b` / `#1c150e`.
- Acento dourado: `#f5a623`, secundário `#d98a12`.
- Texto: `#f6f0e6`; secundário: `#a49a8c`; sucesso: `#2dd4a0`; erro: `#ef5b5b`.
- Tipografia: `Fraunces` (títulos) + `Inter` (texto).
- Border-radius entre 10 e 18 px. Sem sombras pesadas nem gradientes decorativos
  sem função.
- Rótulos em maiúsculas com `letter-spacing` largo — padrão já existente.
- A interface **não** deve parecer gerada automaticamente: espaçamento coerente,
  hierarquia clara, sem texto de preenchimento óbvio, sem emojis na interface.

---

## 6. Segurança

- `SECRET_KEY`, credenciais de base de dados, chave do Groq e credenciais SMTP
  vêm **exclusivamente** de variáveis de ambiente.
- `.env` nunca é versionado. `.env.example` só tem valores vazios ou de exemplo.
- `DEBUG` e `ALLOWED_HOSTS` obrigatórios em produção. Sem defaults inseguros.
- Rate limit em login, cadastro, recuperação de senha e no endpoint da IA.
- Recuperação de senha responde sempre com a **mesma** mensagem, exista ou não
  a conta. Nunca revelar existência de e-mails.
- Tokens de recuperação: `default_token_generator` do Django, com
  `PASSWORD_RESET_TIMEOUT` configurado.
- Upload de imagens: valida extensão e `Content-Type`, dimensiona com Pillow,
  re-codifica. Nunca servir ficheiros enviados pelo utilizador tal como vieram.
- Documentação legal: acesso restrito a `AGENT` e `ADMIN`, com registo de
  auditoria. Nunca em URL pública.
- Escapar saída de IA. As respostas do assistente são texto simples renderizado
  com `{{ answer|linebreaks }}` e `escape` activo. Nunca `|safe` em conteúdo de IA.

---

## 7. Variáveis de ambiente

| Variável | Descrição |
| --- | --- |
| `DJANGO_SECRET_KEY` | Chave secreta. Obrigatória. |
| `DJANGO_DEBUG` | `true` / `false`. |
| `DJANGO_ALLOWED_HOSTS` | Lista separada por vírgulas. |
| `DJANGO_DB_*` | `NAME`, `USER`, `PASSWORD`, `HOST`, `PORT`. |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | SMTP. |
| `EMAIL_USE_TLS` | `true` / `false`. |
| `DEFAULT_FROM_EMAIL` | Remetente do sistema. |
| `ECHILO_AI_API_KEY` | Chave do Groq. |
| `ECHILO_AI_MODEL` | Modelo. Predefinido: `openai/gpt-oss-120b`. |
| `ECHILO_AI_ENABLED` | Liga/desliga o nível 1. |
| `ECHILO_MAP_TILE_URL` | Template de URL dos tiles. `{key}` é substituído pela chave; `{s}`, `{z}`, `{x}`, `{y}` e `{r}` são do Leaflet. |
| `ECHILO_MAP_TILE_ATTRIBUTION` | Atribuição mostrada no canto do mapa. |
| `ECHILO_MAP_TILE_KEY` | Chave do fornecedor de tiles. Opcional. |
| `ECHILO_MAP_CENTER_LAT` | Latitude inicial do mapa. |
| `ECHILO_MAP_CENTER_LON` | Longitude inicial do mapa. |
| `ECHILO_MAP_DEFAULT_ZOOM` | Zoom inicial. |
| `ECHILO_MAP_INVERT_TILES` | `true` só para tiles claros. Um basemap escuro invertido fica claro. |
| `ECHILO_WHATSAPP_NUMBER` | Número de WhatsApp do contacto directo. |
| `MANAGER_EMAILS` | E-mails com acesso ao painel interno. |
| `SITE_URL` | URL pública, usada nos e-mails de recuperação. |

---

## 8. Testes

- `pytest-django` entra quando a suíte crescer; até lá: `manage.py test`.
- Cada regra de negócio em §2 tem pelo menos um teste.
- Testes de views verificam estado HTTP **e** efeito no banco.
- Testes de serviço usam `TestCase`, nunca dados reais.
- Nada de acesso à rede em testes. O provider de IA é mockado em
  `apps.assistant.tests.fakes`.

---

## 9. Git

- Commits no formato `<tipo>(<âmbito>): <descrição>`.
  Tipos: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `style`.
- Âmbito: nome do app (`feat(properties): ...`).
- Descrição em português, imperativo, sem ponto final, máx. 72 caracteres.
- Um commit = uma mudança coerente. Misturar refactor com feat é proibido.
- `.env`, `*.sqlite3`, `__pycache__`, `media/` e `staticfiles/` não são versionados.
