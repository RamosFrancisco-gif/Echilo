# Echilo — plano de funcionamento e mapa de módulos

> Documento de sistema. Descreve o que a plataforma faz hoje, como o faz, o que
> falta para ser um serviço de mediação completo, e em que ordem isso deve ser
> construído.
>
> Escreve-se a partir do código, não da intenção. Onde o código e o `AGENTS.md`
> discordam, o código está certo e o `AGENTS.md` precisa de ser corrigido — e o
> documento aponta essas divergências em vez de as esconder.
>
> Estado à data: `main` em `1739585`, 610 testes verdes, duas migrations por
> aplicar em produção (`accounts.0005`, `properties.0009`).

---

## 1. O que o Echilo é, e o que é que o distingue

O Echilo é uma plataforma angolana de mediação imobiliária **curada**. Liga
proprietários a inquilinos e compradores através de uma equipa interna que valida
cada imóvel antes de o publicar.

A palavra que define o produto é **curado**, e ela impõe três consequências que
explicam quase todas as decisões técnicas do projecto:

| | Portal de anúncios | Mediação (o que o Echilo faz) |
| --- | --- | --- |
| Quem publica | o próprio dono, sozinho | a equipa, depois de verificar |
| O que está visível | o que o dono carregou | o que a equipa validou e publicou |
| Preço | o que o dono escreveu | registado, e uma oferta é sempre humana |
| Conversas | cada um com cada um | uma fila com dono, estado e motivo |

Um portal de anúncios é um CRUD com upload de fotos. Uma mediação é um fluxo de
estados com uma pessoa no meio, e a diferença entre os dois está toda no
**§2 do `AGENTS.md`** e na congruência entre esse texto e o que o código faz.

Consequência prática: **o produto tem duas metades, e só uma está construída.** A
metade do cliente — navegar, registar-se, pedir visita, fazer proposta, subir
fotografias, conversar com o assistente — está completa e testada. A metade da
equipa tem a curadoria de imóveis e nada mais. A secção 5 detalha esta lacuna,
que é a única coisa que separa o que existe hoje de um serviço de mediação a
funcionar.

---

## 2. Como o sistema funciona

### 2.1 As cinco etapas do lado do proprietário

Fluxo descrito no §2.1 do `AGENTS.md` e implementado em `properties/services.py`
(`create_property`, `resolve_owner`, `confirm_submission`, `add_images`,
`verify_document`, `publish_property`).

```
 WhatsApp / formulário / chamada
            │
            ▼
   ┌──────────────────────┐
   │ 1. CAPTAÇÃO          │  Lead(OWNER_INTAKE) estado NEW, atribuído a um
   │    concierge/        │  membro da equipa. Rate limit 4/30 min por IP.
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │ 2. TRIAGEM           │  PropertySubmission: 5–15 fotos, pin de satélite,
   │    properties/       │  preço, tipo de contrato, 2 documentos verificados,
   │                      │  identificação do dono, telefone.
   │                      │  is_ready_for_review() é a porta.
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │ 3. CADASTRO GERIDO   │  Property criado em DRAFT com
   │    /curadoria/novo/  │  curated_by = quem curou. Imóvel não público.
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │ 4. CICLO DE VIDA     │  DRAFT → IN_REVIEW → UNDER_VALIDATION →
   │    /curadoria/<ref>/ │  PUBLISHED → ARCHIVED, com CHANGES_REQUESTED e
   │                      │  REJECTED. Toda a transição regista autor e motivo
   │                      │  em PropertyStatusEvent.
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │ 5. PUBLICAÇÃO        │  Exige OWNERSHIP_TITLE + ID_DOCUMENT em
   │                      │  VERIFIED, localização verificada por satélite
   │                      │  e uma `reason`. Só PUBLISHED é público.
   └──────────────────────┘
```

O estado do imóvel é a única fonte de verdade sobre visibilidade. Não há campo
`is_public`: quem filtra por `status == PUBLISHED` filtra bem, e um imóvel em
`CHANGES_REQUESTED` desaparece do catálogo sem ninguém ter de se lembrar de o
esconder. Os índices `prop_status_purpose_idx` e `prop_status_province_idx`
existem para essa consulta ser barata.

**Publicar exige três coisas, e a terceira é a que se esquece.** A porta está em
`Property.transition_to()`, que recusa `PUBLISHED` sem `location_verified_at`, e
em `clean()`, que recusa o mesmo num `save()` directo. Com
`missing_verified_documents()` são três: os dois documentos em `VERIFIED`, a
localização verificada, e o `reason`. O `location_verified_at` é a mais fácil de
contornar por engano — `apply_quick_edit()` carimba-o sozinho (ver §7, Fase 1).

### 2.2 As cinco etapas do lado do cliente

```
 /  home          6 imóveis em destaque, fotos a rodar de 40 em 40 s
 /pesquisa/       catálogo + mapa, filtro por área desenhada no mapa
 /imovel/<ref>/   ficha pública: galeria, atributos, pin, preço
        │
        ├── /conta/registo/          conta ancorada a NIF + BI (§2.11)
        ├── /imovel/<ref>/visita/    VisitRequest PENDING
        ├── /imovel/<ref>/proposta/  Offer SUBMITTED
        └── /assistente/            nível 1 da IA, 24/7
                │
                │  escalação (visita, proposta, contrato, preço,
                │  disputa, documentação) → handled_by=HUMAN
                ▼
        Equipa humana  ←  ESTE É O PROBLEMA: não há onde isto acontece (§5)
```

O assistente é deliberadamente limitado: responde só a partir de imóveis
publicados (`get_property_details`, `search_properties`, `compare_properties`),
não negoceia, não promete valores, não marca visitas. Uma falha do fornecedor
**não** escala a conversa — é o nosso problema, não um assunto da equipa
(`_persist_outage` deixa a conversa em `OPEN` com nota interna).

### 2.3 Os quatro perfis e onde cada um pode

| | `CLIENT` | `CURATOR` | `AGENT` | `ADMIN` |
| --- | --- | --- | --- | --- |
| Navegar catálogo e mapa | sim | sim | sim | sim |
| Conta, perfil, fotografia | sim | sim | sim | sim |
| Pedir visita, proposta | sim | sim | sim | sim |
| Assistente de IA | sim | sim | sim | sim |
| Criar/editar imóvel | — | sim | sim | sim |
| Fazer transição de estado | — | sim | sim | sim |
| Fotografias do imóvel | — | sim | sim | sim |
| **Validar documentos** | — | — | **sim** | **sim** |
| **Confirmar visitas** | — | — | **sim** | **sim** |
| **Gerir contas** | — | — | — | **sim** |
| Painel `/admin` | — | — | — | — |

A tabela está escrita em dois sítios e lida em dois. Escrevem: as três
propriedades `User.can_curate` / `can_validate` / `can_manage_users` no modelo, e a
tabela do `AGENTS.md` §3. Leem: `core/permissions.py`, cujas guardas perguntam
pelas mesmas propriedades, e `core/navigation.py`, que monta o menu a partir
delas. Existe um teste que obriga o menu e a view a concordarem, e as guardas
levantam `PermissionDenied` — um `CLIENT` recebe 403, não 404, porque o endereço
existe e "não tens perfil para isto" é informação útil para quem lê por curiosidade
e não para quem erra o endereço.

### 2.4 Geometria e dinheiro: as duas transversalidades

**Money** (`core/money.py`): `Decimal`, nunca `float`. Kwanza, separador de
milhares `.`, decimal `,`. O preço aparece em quatro sítios (cartão, ficha, pino
do mapa, texto de e-mail) e é formatado por uma função só, porque dois
formatadores divergem no primeiro valor com mil milhões.

**Números que viajam** (`AGENTS.md` §2.13): `LANGUAGE_CODE = "pt-ao"` localiza
números, o que é bom para ler e mau para viajar. `{{ value }}` escreve `-8,918430`
e `1.000,00`. Qualquer valor que vá para um `<input>`, um atributo, uma URL ou um
JSON sai pelo filtro `coordinate`, que garante ponto decimal. Isto não é
cosmético: `Number("0,5")` é `NaN`, e um redutor com orçamento `NaN` não reduz
nada — foi um defeito real na pipeline de fotografia de perfil.

**Geometria** (`core/geo.py`): Haversine sobre a caixa envolvente indexável
primeiro, porque o MySQL do Aiven não tem PostGIS. Um recorte de raio a 50 km é
`DecimalField` com 6 casas, arredondado **para fora** na caixa (arredondar para
dentro exclui imóveis válidos na fronteira do círculo) e confirmado em Python
depois.

**A origem do pin nunca é a morada textual** (§2.3). O pin vem de inspecção de
satélite, preenchido pela equipa, com `location_accuracy_m` e `location_verified_at`
que existem para que um clique no mapa não finja ter a precisão de uma inspecção.

---

## 3. Mapa dos módulos que existem

### 3.1 `config/` — configuração e routing, nunca modelos

| Ficheiro | Responsabilidade |
| --- | --- |
| `settings/base.py` | Tudo o comum. `STORAGES` é uma **função** (`storages(manifest=, cloudinary=)`) e não um dicionário no topo, porque `production.py` muda `DEBUG` para `False` depois de o `base` ser importado e um `STORAGES` decidido com o `DEBUG` do ambiente deixa a produção com `StaticFilesStorage` — o sítio aparece sem CSS, com 200 e sem erro na consola. |
| `settings/development.py` | `DEBUG=True`, `runserver`, serviço de media. |
| `settings/production.py` | Recusa arrancar sem `SECRET_KEY`, `ALLOWED_HOSTS`, `CLOUDINARY_URL` e `CLOUDINARY_PUBLICAO`. `CONN_MAX_AGE=0`, cookies e CSRF `secure`, `SSL_REDIRECT`. |
| `urls.py` | Quatro prefixes: `/conta/`, `/` (imóveis), `/atendimento/`, `/assistente/`. Handlers de 403/404/500 próprios. |
| `wsgi.py` / `asgi.py` | Pontos de entrada. |
| `certs/` | `ca.pem` do Aiven versionada, sem a qual a ligação não verifica o certificado do servidor. |

### 3.2 `apps/core/` — transversal, sem pertencer a nenhum domínio

Não tem um único modelo. Tudo o que seria exactamente igual em dois apps vive aqui
e é importado por ambos, para que a segunda resposta a uma pergunta não divirja
da primeira.

| Módulo | Responsabilidade | Nota de risco |
| --- | --- | --- |
| `money.py` | Formatação em Kwanza | Um único formatador para 4 sítios |
| `geo.py` | Haversine, caixa envolvente | Funções puras, testáveis sem BD nem rede |
| `images.py` | `motivo_recusa_imagem`, `prepara_retrato` | A mesma pergunta em dois formulários |
| `storage.py` | Três storages: `CloudinaryImageStorage` (capas), `CloudinaryDocumentStorage` (`raw` + `authenticated`), `CloudinaryAvatarStorage` (512 px, `echilo/perfis`) | Cada campo escolhe a sua por **referência** (`storage_perfis`), não pela instância — a `FileField` resolve a storage uma vez na definição do campo e guardar a instância congelava o disco de quem aplicou a migration |
| `validators.py` | `validate_adult`, `validate_nif`, `normalize_nif`, `validate_angolan_phone`, `LIMITE_PEDIDO_MB` | A fonte única do limite de transporte |
| `permissions.py` | `is_team_member`, `require_team_member`, `require_admin`, `require_document_access` | Guards de acesso. As regras de quem pode o quê são as três propriedades `User.can_curate` / `can_validate` / `can_manage_users`, no modelo — é daí que a vista e o menu leem as duas |
| `navigation.py` | Árvore de menus por perfil | `NoReverseMatch` esconde a entrada em vez de partir o cabeçalho |
| `context.py` | Contexto dos templates | |
| `ratelimit.py` | Limitador por cache | Na Vercel o cache tem de ser o da base de dados, senão o limite é cinco tentativas por invocação |
| `pagination.py` | `pagina_de`, `PAGINA_PADRAO = 6` | |
| `maps.py` | Configuração do mapa lida das settings | |
| `signals.py` | `promote_manager_emails` no login | `MANAGER_EMAILS` é o caminho de promoção |
| `views.py` | Páginas de erro | |
| `testing.py` | `make_user`, `make_owner`, `make_property`, `make_submission`, `make_verified_documents`, `make_image`, `jpeg_bytes`, e os leitores de HTML `ids_dentro_de`, `input_names_inside`, `select_options` | `RateLimitFreeTestCase` neutraliza o limitador. Os leitores de HTML existem para medir o alvo e não a página — é o que o `AGENTS.md` §2.12 exige a propósito do `select` de província |

**Comandos** (5): `recreate_database`, `verify_cloudinary`, `verify_map_tiles`,
`build_admin_boundaries`, `seed_demo`. Os dois `verify_*` e o `build_*` existem
porque **uma falha de configuração não dá erro nenhum em produção** — um
fornecedor de tiles que responde `200` com uma imagem de "API KEY REQUIRED" conta
como tile carregado, e o `destroy` da Cloudinary nunca é exercido por um
`makemigrations`.

### 3.3 `apps/accounts/` — identidade

`User(AbstractUser)` com `role` (4 valores), `full_name`, `phone`, `province`,
`gender`, `nif` (**único**), `birth_date`, `id_document_type`, `id_document_number`,
`photo`. `save()` deriva `is_team_member` e o acesso staff a partir do `role` — é
por isso que toda a criação de contas passa por `register_client` e nunca por
`create_user`.

| View | Quem | O que faz |
| --- | --- | --- |
| `LoginView` | público | e-mail como utilizador, `update_session_auth_hash` |
| `RegistrationView` | público | só cria `CLIENT`; NIF + BI + 18 anos |
| `ProfileView` | sessão | dados, NIF, documento e fotografia num formulário só |
| `PasswordChangeView` | sessão | na página do perfil, com a senha actual obrigatória |
| `TeamListView` / `TeamMemberCreateView` | `ADMIN` | equipa, sem NIF nem data de nascimento |
| `ClientListView` / `ClientCreateView` | `ADMIN` | clientes, com os mesmos campos do registo público |
| `PasswordReset*` (4) | público | mensagem neutra; nunca revela se a conta existe |

### 3.4 `apps/properties/` — o domínio principal

O maior app: 13 ficheiros de código, 10 migrations, 2 comandos de gestão, 1
templatetag e 26 classes de teste (223 métodos). É onde estão as regras de
negócio e onde está a maior parte do risco.

**Modelos** (8): `OwnerProfile`, `Property` (+ o `PropertyQuerySet`, que é um
manager e não um modelo), `PropertyImage`, `PropertyDocument`, `PropertyStatusEvent`,
`DocumentAccessLog`, `PropertyDeletion`, `PropertySubmission`.

**Camada de leitura** (`selectors.py`): `PropertyFilters` (filtros + área,
composição em URL, `chip_query` para os chips), `PropertyQueryService`
(`apply_filters`, `search`, `map_markers`, `within_area`, `featured`, `by_ids`,
`cover_image`, `municipality_suggestions`).

**Camada de escrita** (`services.py`, 13 funções públicas): `build_map_payload`,
`next_reference` (`ECH-LU-0001`), `resolve_owner`, `create_property`,
`apply_quick_edit`, `confirm_submission`, `add_images` / `remove_image` (lote
transacional, renumeração, capa), `verify_document`, `publish_property`,
`normalise_price`, `motivos_para_recusar_apagar`, `delete_property` com
`PropertyDeletion`.

**Índices deliberados**: `prop_lat_lon_idx` (caixa envolvente primeiro),
`prop_status_purpose_idx`, `prop_status_province_idx`, `prop_type_status_idx`.

### 3.5 `apps/concierge/` — a fila da equipa

`Lead` (OWNER_INTAKE / CLIENT_ENQUIRY, NEW→CONTACTED→QUALIFIED→CONVERTED, com
DISQUALIFIED), `VisitRequest` (PENDING→CONFIRMED/DECLINED→COMPLETED, com
detecção de sobreposição em `clean()`), `Offer` (SUBMITTED→UNDER_REVIEW→ACCEPTED/
REJECTED/WITHDRAWN), `Conversation` (OPEN/ESCALATED/RESOLVED, `handled_by`),
`Message` (com `is_internal`).

O `Offer` **não tem estado de contraproposta nem ligação à proposta que está a
contrapor**: os campos são `property`, `submitted_by`, `amount`, `currency`,
`message`, `status`, `responded_by`, `response_notes`, `created_at`,
`responded_at`. Uma negociação (§2.5) é hoje uma segunda `Offer` sem pai, e a
história só se infere pela ordem das datas. É uma lacuna do modelo, não da
interface — ver §6.1.

Serviços: `create_owner_intake`, `request_visit`, `submit_offer`,
`get_or_create_conversation`, `escalate_conversation(conversation, reason, notice)`,
`note_conversation`. `recent_messages` e `messages_after` filtram `is_internal=False`
**na consulta**, e o template volta a filtrar — porque uma só das duas camadas
deixaria o defeito à espera de alguém trocar a outra.

**Só existem 4 rotas e 4 views**, todas do lado do cliente e da entrada de
contactos. A `lead_queue` é uma lista com filtros. Não há consola de trabalho.

### 3.6 `apps/assistant/` — atendimento nível 1

`provider.py` (Groq, `ECHILO_AI_MODEL`, timeout 6 s, 0 tentativas), `tools.py`
(`get_property_details`, `search_properties`, `compare_properties` — só imóveis
publicados, lidos por `PropertyQueryService`, nunca por `objects.raw()`),
`prompts.py`, `services.py` (`ask`, `_persist_reply`, `_persist_outage`,
`_persist_escalation`), `views.py` (janela, mensagem HTMX, `chat_health`).

Sem modelos. Sem estado entre invocações. A conversa vive em
`Conversation`/`Message`, e o `conversation_id` na sessão é a âncora.

### 3.7 Frontend

`static/js/echilo.js` (91 KB, IIFE, sem build), `static/css/echilo.css` +
`pages.css` (81 KB), HTMX 1.9.12, Leaflet 1.9.4, Leaflet.draw 1.0.4 — todos
vendorizados com versão fixa, **sem CDN**. Zero emoji na interface.

---

## 4. A base técnica, e porque cada parte está como está

| Decisão | Razão | Onde está escrita |
| --- | --- | --- |
| Vercel serverless, `api/index.py` | A função morre a cada pedido: `MEDIA_ROOT` é efémero, o cache em memória é novo, o estado entre invocações não existe | `AGENTS.md` §7.1 |
| Media na Cloudinary, três backends | Fotos são públicas, documentação legal nunca é | `storage.py` |
| `DEFAULT` é a capa; documentos e retratos declaram a sua | Django só tem um "default"; e a transformação de cada tipo é diferente (2000 px a capa, 512 px o retrato) | `storage.py` |
| `DocumentAccessLog` sem `CASCADE` | Apagar o ficheiro não pode levar a prova de quem o leu | `models.py` |
| Limite de upload abaixo do limite de pedido | `LIMITE_PEDIDO_MB = 4.5` (a Vercel recusa **na edge**, antes do Django ver a requisição), `MARGEM = 0.5`, `LIMITE_UPLOAD_MB = 4` | `core/validators.py` |
| Redução **client-side** do lote | Redimensionar no servidor não evita o 413: a requisição já chegou grande | `echilo.js` + `AGENTS.md` §2.1 |
| `pt-ao` + filtro `coordinate` | Localizar números é bom para ler e mau para viajar | §2.13 |
| Sem SPA, sem build de JS | HTMX + um ficheiro versionado. Um build introduz um passo que falha em produção sem aviso | §5.5 |
| `Decimal`, nunca `float` | Dinheiro | §2.5 |
| Datas de `ordering` com `-id` de desempate | Duas linhas criadas no mesmo instante ficam com a mesma data, e o desempate passa a ser do servidor. Medido: 7 linhas deram **5 datas distintas**; sem desempate, uma linha repete-se e outra falta. Com `[:n]` e paginação, isso é visível ao cliente | `properties/models.py`, `tests.OrdemTotalTests` |
| `maxDuration: 10` s | A invocação inteira — BD, Pillow, Cloudinary, Groq — cabe em 10 s | `vercel.json` |

---

## 5. A lacuna central: falta a consola da equipa

Este é o achado mais importante deste documento, e vale a pena dizê-lo sem
amortecimento: **o produto tem o cliente e tem a curadoria de imóveis, mas não tem
a mesa de trabalho de quem atende clientes.**

O que o §2.4 do `AGENTS.md` promete — "uma conversa passa a `handled_by = HUMAN`
e o `Conversation` fica `ESCALATED`. O nível 1 continua disponível, mas já não
responde sozinho" — é verdade no modelo e **não é verdade em lado nenhum da
interface**. Uma conversa escalada existe na base de dados, tem um motivo interno
que ninguém vê, e **não existe página onde alguém a leia ou responda**.

O mesmo se repete, com a mesma causa, em quatro lugares:

| O que a regra promete | Onde a regra está | Onde a equipa trabalha | Estado |
| --- | --- | --- | --- |
| Conversa `ESCALATED` vai para a equipa | `concierge/services.py:escalate_conversation` | — | **sem página** |
| `AGENT` confirma, recusa ou completa visitas | §3, `VisitRequest.Status` | — | **sem página**; só o cliente cria `PENDING` |
| `AGENT`/`ADMIN` tratam de propostas formais | §2.4 ponto 2, `Offer.Status` | — | **sem página**; só o cliente submete |
| Negociação de preço é registada em `Offer` | §2.5 | — | **o modelo não tem contraproposta** |
| `AGENT`/`ADMIN` validam documentos | §2.7, `verify_document()` | Django admin | **fora do produto** |
| `Lead` percorre `NEW→CONTACTED→QUALIFIED→CONVERTED` | §2.9 | `lead_queue` (só lista) | **sem transições** |
| `PropertySubmission.is_ready_for_review()` é a porta da triagem | §2.1 etapa 2 | Django admin | **fora do produto** |

Duas das regras acima estão a meio de caminho, e isso é diferente de não
existirem. `is_ready_for_review()` e `pending_items()` já respondem "falta o quê,
em linguagem de equipa" — as cinco confirmações, o mínimo de fotografias, as
coordenadas, a localização por satélite, o preço e o telefone do dono. A triagem
precisa de uma página que as desenhe e de um `POST` que as grave, e não de regra
nova.

Duas consequências que já custaram trabalho:

1. **A documentação legal é um beco sem saída dentro do produto.** A ficha de
   curadoria mostra `missing_documents`, `documents` e `can_validate_documents` —
   a equipa vê o que falta e não pode fazer nada sobre isso sem sair para o
   `/admin`. E sem `OWNERSHIP_TITLE` + `ID_DOCUMENT` em `VERIFIED` não há
   publicação, ou seja: **sem o `/admin` não há catálogo.**

2. **A escalação da IA é uma carta no Fundo do mar.** A nota interna existe, o
   `reason` está gravado, e a fila de contactos lista `Lead` — que não inclui
   conversas do assistente. O `AGENTS.md` já diagnosticou isto, mas a correção
   proposta (ler a causa interna) é o que falta: uma inbox.

Este é o único bloco de trabalho que separa o que existe de um serviço de
mediação a funcionar. Está na Fase 2 do plano.

---

## 6. Módulos que têm de existir

Cada módulo com os modelos, as permissões, as rotas e os testes que o prendem. A
ordenação dos números é a ordem em que convém construir, e o §7 diz porquê.

### 6.1 `apps/concierge` — a mesa de trabalho da equipa ⬅ **primeiro**

O módulo mais importante em falta. Tudo aqui é leitura e decisão sobre registos que
**já existem** e que hoje não têm onde ser tratados.

**`Conversation` inbox**
- `ConversationQueueView` (listar, filtrar por `status`/`handled_by`, paginar)
- `ConversationDetailView` (transcrição com `is_internal` **oculto**, campo de
  resposta, botão de resolver)
- `answer_conversation(conversation, author, text)` em `services.py` — grava
  `Message` de `STAFF`, `handled_by = HUMAN`, devolve ao cliente
- Permissão: `AGENT`, `ADMIN`
- Regra: a mensagem interna de escalação aparece **só** na inbox, com o cliente a
  ver apenas `notice`

**`LeadQueue` que trabalha**
- `LeadDetailView` com `LeadStatusForm` e a transição validada por
  `Lead.transition_to()`, a par de `Property.transition_to()`
- `CONVERTED` num `OWNER_INTAKE` mostra o `Property` que a conversa produziu
- Permissão: `AGENT`, `ADMIN`

**`VisitRequest`**
- `visit_queue` + `visit_detail`: confirmar, recusar, marcar concluída, com
  `reason` obrigatório na recusa
- A detecção de sobreposição de `VisitRequest.clean()` passa a ser usada por
  alguém, em vez de só por teste
- Permissão: `AGENT`, `ADMIN` (coincide com §3)

**`Offer`**
- `offer_list` por imóvel e global, com `OfferDecisionForm`
- `ACCEPTED` liberta a venda; `UNDER_REVIEW` marca "a ver"; `REJECTED` e
  `WITHDRAWN` fecham
- **Contraproposta**: decidir se `COUNTERED` volta ao modelo com um `parent` (ou
  `in_reply_to`) a `Offer`, ou se a contraproposta é uma `Offer` nova que
  referencia a anterior. Recomendo o campo: sem ele não há como dizer que a
  segunda proposta é a resposta à primeira, e a conversa de negociação fica
  dependente da ordem das datas
- Permissão: `AGENT`, `ADMIN`

**Um modelo novo: `TeamNote`** ou um `Message` sem `conversation`. A equipa precisa
de escrever sobre um imóvel e sobre um cliente sem que isso seja uma conversa de
assistente. Alternativa mais barata: `PropertyNote` ligado a `Property` e
`User`, com autor e data. Recomendo `PropertyNote` — a nota de conversa está
prisioneira ao assistente, e o motivo de uma recusa de visita vive na visita.

### 6.2 `apps/properties` — documentos dentro do produto ⬅ **segundo**

Fecha o beco do §5 e é pré-requisito de qualquer publicação em produção.

- `PropertyDocumentUploadView` — `POST` separado, por documento e por tipo, com
  **`CampoDeFotografias`-equivalente para PDF**: valida bytes com um leitor
  (`%PDF-`), não só a extensão; **um POST por ficheiro**, para não recriar o 413
- `PropertyDocumentVerifyView` — `verify_document(document, agent, approved, reason)`;
  `REJECTED` exige `rejection_reason` (§2.7)
- Remoção do documento com confirmação (`partials/confirmacao.html`)
- A ficha de curadoria passa a ter o botão de enviar e o de verificar, condicionados
  por `can_validate_documents`
- Testes: `assertNumQueries` na ficha com documentos; o 413 não volta porque são
  pedidos separados; `REJECTED` sem motivo é recusado

### 6.3 `apps/notifications` — app novo ⬅ **terceiro**

Hoje existe **um único e-mail** em todo o sistema: a recuperação de senha. Quem
pede uma visita não recebe nada, a equipa não é avisada de nada, e o cliente não
sabe que a proposta foi recusada.

Um app `notifications`, com **regra única**:
`notify(recipient, kind, context)` resolve o canal; cada `kind` declara os seus
destinatários. Nenhum outro módulo escreve e-mail.

| `kind` | Para | Comportamento |
| --- | --- | --- |
| `visit_requested` | equipa | notificação interna; **nada** ao cliente sem resposta |
| `visit_confirmed` / `visit_declined` | cliente | com `reason` na recusa |
| `offer_submitted` | equipa | |
| `offer_under_review` / `offer_accepted` / `offer_declined` | cliente | |
| `document_verified` / `document_rejected` | equipa + dono | |
| `property_published` | dono | |
| `conversation_escalated` | equipa | **esta é a que falta hoje** |
| `lead_created` | equipa | |
| `password_reset` | cliente | já existe, migra para aqui |

**Regras técnicas**:
- `EmailMultiAlternatives` com template versionado em `templates/notifications/`
- Nunca em `urls` de confirmação sem um `token` de uso único
- `transaction.on_commit` para o envio: um e-mail de "publicado" para um imóvel
  que a transacção reverteu é pior que nenhum e-mail
- **Falha de envio não desfaz a operação de negócio** — um 500 porque o SMTP
  está em baixo seria um imóvel publicado que ninguém vê
- O `DEFAULT_FROM_EMAIL` é a única identidade; o remetente é o produto

### 6.4 `apps/accounts` — favoritos e preferências ⬅ **quarto**

O `AGENTS.md` §3 diz que o `CLIENT` pode "guardar favoritos". **Não existe uma
linha de código disso** — nem modelo, nem view, nem URL, nem rota no menu. A
capacidade está documentada como se existisse.

- `Favorite(user, property)` com `unique_together`, `property` com `PROTECT`
- `toggle_favorite` idempotente; `on_delete=CASCADE` (o favorito não sobrevive ao
  imóvel)
- `favorites/` — lista paginada com a mesma grelha e o mesmo `property_card.html`
- Ações na ficha: "Guardar" / "Guardado", com o `hx-swap` do cabeçalho do botão
- Botão no `property_card.html` para o catálogo inteiro
- Contagem no menu, lida do mesmo sítio que o resto

### 6.5 `apps/billing` — contrato e comissão ⬅ **quinto**

O negócio da mediação é o dinheiro. Hoje o sistema regista que houve uma oferta
e que o imóvel existe, e nada mais.

- `Contract(imovel, cliente, tipo, valor, início, fim, estado)` — `PROTECT` no
  imóvel e no cliente
- `Installment(valor, vencimento, estado, pago_em)` com `Decimal` e Kwanza
- `Commission(contrato, percentagem, valor)` — derivada, nunca escrita à mão
- Estados: `DRAFT → SENT → SIGNED → ACTIVE → CLOSED`, mais `CANCELLED`
- **Assinatura**: PDF gerado, com o `public_id` guardado, entregue por link
  assinado como a documentação legal. A assinatura é um campo de bytes, não um
  botão que promete
- Relatório: receita por mês, por município, por agente

**Advertência honesta**: isto é um módulo financeiro e cada regra de
arredondamento de comissão vai precisar de um teste que a fixe. Não é um
`DecimalField` e uma coluna.

### 6.6 Observabilidade, operação e dados ⬅ **sexto**

- **Logs estruturados** com `request_id` por invocação. Hoje o `logging` é o
  do Django, e num `serverless` a stderr é a única saída. Sem isto, "o sítio
  deu 500" não é investigável
- **Erro de cliente através de e-mail**, com o `request_id` no texto, para a
  equipa pedir a identificação exacta à Vercel
- **`/saude/`** que verifica BD, Cloudinary e Groq e responde 200/503. Hoje só o
  assistente tem `chat_health`
- **`check --deploy`** (naming do `AGENTS.md` já usado nos testes): corre
  `verify_cloudinary`, `verify_map_tiles`, `makemigrations --check` e confirma
  `CLOUDINARY_PUBLICAO`, `CONN_MAX_AGE=0` e o backend do cache
- **Rotação de credenciais documentada e ensaiada**: Aiven e Cloudinary foram
  expostos e têm de ser trocados; o procedimento tem de ser um comando, não um
  memória
- **Backup e restauração**: o Aiven faz cópias, mas ninguém as testou. Um
  `restore` ensaiado numa base descartável é a diferença entre "temos backups" e
  "temos backups"
- **`PropertyDeletion` com retenção**: apagar apaga o ficheiro (a Cloudinary
  cobra por lixo), e o registo de auditoria tem de responder "o que era este
  imóvel" sem o imóvel

### 6.7 Integrações

- **WhatsApp Business API** para os avisos de visita, oferta e documento. É o canal
  que o produto promete no §2.1 e é o canal que as pessoas usam em Angola. Tem
  pré-requisitos (conta, template aprovado, janela de 24 h) e por isso é uma fase
  própria, não um extra
- **API de leitura** para portais parceiros: `PropertyQueryService` já é a única
  fonte de imóveis publicados, o que faz um `/api/v1/imoveis/` com
  `select_related`/`prefetch_related` e rate limit um trabalho pequeno. O que
  **não** é pequeno: autenticação, versionamento e um contrato de estabilidade
  separado do do produto. **Decisão de dono, não de engenharia** (§9)

### 6.8 O que **não** deve ser construído

Registado para não voltar a ser discutido:

| Ideia | Porquê não |
| --- | --- |
| SPA / React | HTMX resolve o que existe; um framework de UI introduz um build, um `node_modules` e uma classe nova de erro em produção |
| PostGIS | Não há `PostGIS` no plano do Aiven. A caixa envolvente + Haversine resolve a busca por área e usa um índice |
| Fila de tarefas | Nada pede tarefas assíncronas: as operações são curtas e a função vive 10 s |
| CMS | O conteúdo é dado, não texto editorial. O que é texto editorial é o `AGENTS.md` |
| Push notifications | O canal é WhatsApp |
| Multi-idioma | O mercado é Angola. A interface é `pt-ao` e uma tradução para inglês sem segundo cliente é um custo sem retorno |
| Testes end-to-end com browser | 610 testes cobrem o servidor. O JavaScript tem os seus testes por ficheiro e por `read` do ficheiro. Um Playwright aqui custaria mais do que o que encontraria |

---

## 7. Plano por fases

Cada fase deixa o sistema **melhor do que o encontrou e testado**, e nenhuma
depende de uma fase que não esteja feita.

### Fase 0 — Publicar o que está feito ⬅ **agora**

Não é código: é fazer o que já está escrito chegar ao ar. Tudo o que está aqui
precisa de ti — o dashboard da Vercel, o painel do Aiven e a palavra-passe não
passam por mim.

1. `accounts.0006` + `concierge.0004` + `properties.0010` em produção, de uma
   máquina com as variáveis todas, **antes** de promover o deploy
   (`AGENTS.md` §7.1). São as da ordenação total dos modelos (Fase 1.2)
2. `vercel --prod`; confirmar `CLOUDINARY_PUBLICAO` na função
3. `verify_cloudinary` e um retrato real pela página do perfil
4. Rotacionar **as credenciais Aiven e Cloudinary que ficaram expostas**
5. Criar a conta `CURATOR` em `/conta/equipa/novo/` e um `AGENT` (sem `AGENT`
   não há validação de documentos, e sem documentos não há publicação)
6. Definir `ECHILO_DEMO_PASSWORD` se o `seed_demo` vai correr em produção

**Critério de aceitação**: um retrato carregado numa conta de produção aparece no
cabeçalho, aos 28 px, e não desaparece no pedido seguinte.

Os dois itens que aqui estavam e já não: corrigir a tabela de perfis do
`AGENTS.md` §3 sobre os favoritos (saiu, porque não há código nenhum) e
desempatar os 8 modelos (feito na Fase 1.2 — `SEM_DESEMPATE` deixou de existir
e o teste passou a varrer todos os modelos). O número das migrations mudou duas
vezes desde que esta lista foi escrita, e é a segunda vez que a lista envelhece
sem ninguém dar por isso: é escrita à mão e nada a confere contra as
migrations em disco.

### Fase 1 — Corrigir o que está errado

Pequeno, e não procrastinável.

| # | Item | Porquê | Estado |
| --- | --- | --- | --- |
| 1.1 | `apply_quick_edit()` carimba `location_verified_at` quando o pin muda | Deveria **limpar** a confirmação: um clique no mapa não é uma inspecção de satélite, e o campo existe para medir a fiabilidade da inspecção (§2.3). Agora diz "verificado" sobre um pin que ninguém verificou | **Feito** — limpar sem confirmar, e confirmar passa a ser uma caixa explícita na ficha. Limpar sem forma de re-verificar deixaria o imóvel impedido de publicar para sempre, porque a ficha era o único escritor do campo |
| 1.2 | Os 8 modelos sem ordem total | Um cartão repetido e outro ausente num `[:n]` é o sistema a mentir sobre o que tem | **Feito** — os 8 fecham com `id`/`-id`. A lista de pendências `SEM_DESEMPATE` desapareceu: o teste varre agora todos os modelos do projecto, e uma lista de nomes que alguém tem de actualizar é a forma de os esquecer |
| 1.3 | `seed_demo` re-executado em produção sem `--flush` | Já exige `--permitir-producao`; falta garantir que não duplica referências | **Feito** — mas o defeito era outro, e pior. O guarda comparava a referência, e `next_reference()` só devolve referências livres por construção: o `continue` nunca corria e cada execução publicava o catálogo inteiro outra vez. Passa a identificar a linha pelo título e pela província |
| 1.4 | Favoritos, se a Fase 0.6 não os remover do `AGENTS.md` | Uma capacidade declarada e inexistente é pior que uma capacidade não declarada | **Feito** — saíram da tabela do §3. Os favoritos são da Fase 5, e até lá o produto não os promete |

Além da tabela, e porque apareceram no caminho:

- **`is_team_member` era lista negra, e estava escrito em três sítios** (modelo,
  `permissions`, `context`). Todos davam acesso a qualquer `role` desconhecido, `""`
  incluído. Passa a lista branca em `User.is_team_role`, lida pelos três. Isto é
  o P1 do plano v2, e era maior do que o `OWNER` que lá se propunha.
- **`ruff` não tem configuração no projecto.** O §5.1 diz "ruff para lint e
  formatação, linha máxima 100" e não há ficheiro que diga isso; as regras
  que correm hoje vêm da configuração global de quem as invoca, e o código não
  passa nenhuma das duas. Escolher o conjunto de regras é decisão de dono — 137
  avisos pré-existentes esperam por essa escolha.

### Fase 2 — Correcções: o código que diz uma coisa e faz outra

Oito sítios onde o sistema escreve o que o interface promete e não faz. Nenhum é
um módulo novo: cinco são código morto, e os outros três divergências que
esperam por alguém.

| O que | Onde | O que fazer |
| --- | --- | --- |
| Verificação e rejeição de documentos | `services.py:300 verify_document()` | Ligar na Fase 3. A lógica está certa e não é chamada uma vez |
| Checklist de triagem | `services.py:212 confirm_submission()` | Ligar na Fase 3, na ficha de curadoria |
| Sobreposição de visitas | `models.py:163 clean()` | Ligar na Fase 4, quando existir quem confirme uma visita. Até lá é uma regra que nunca corre |
| Transições de `Lead` | `models.py:95 transition_to()` | Ligar na Fase 4 |
| Ficha do imóvel fechado | `views.py:820 property_not_published` | Dar-lhe a rota que não tem e as alternativas, na Fase 6 |
| Selo "Documentação verificada" | `property_detail.html:43` | Feito. Ver abaixo |
| Tecto de upload | `validators.py:39` (4,5 MB) e `base.py:262` + `:342` (5 MiB) | Feito. Ver abaixo — e eram três sítios, não dois |
| `assertNumQueries` em três listagens | `team_list`, `client_list`, `lead_queue` | Feito. Ver abaixo |

**Porque primeiro**: uma correção que vem no fim é uma correcção que fica por
fazer, porque cada módulo novo aumenta o que há para rever. E o selo é a excepção
que não espera: é público, está numa ficha de um imóvel no ar, e afirma uma coisa
que ninguém calculou.

**Critério de aceitação**: não há nenhum caminho que se alcance a partir de uma
página, e o selo diz o que foi verificado.

#### O que ficou feito, e o que só ficou marcado

O critério acima não está cumprido, e é preciso dizer qual das duas metades está.

**Feito, com teste que falha se o defeito voltar:**

- O selo nomeia a escritura e a identificação — o que o portão exige — em vez de
  «documentação», e desaparece quando a documentação deixa de estar verificada. A
  descrição para motores de busca repete a mesma frase e cai com o selo. Duas
  mutações verificadas.
- O tecto de upload tem uma fonte. Eram **três**: `LIMITE_PEDIDO_MB` nos
  validadores, a definição com `env_int` em `base.py:262`, e uma segunda
  definição escrita à mão em `base.py:342` que a silenciava. O
  `DATA_UPLOAD_MAX_MEMORY_SIZE` documentado no `AGENTS.md` §7 e posto no
  `.env.example` nunca chegava a correr. Além disso o valor estava 0,5 MiB acima
  do limite da plataforma, o que tornava a validação do Django código morto: o
  pedido morria na edge antes de o Django o ver. Duas mutações verificadas.
- Três listagens com `assertNumQueries`, medindo o custo *por linha* e não um
  total. Um total absoluto muda sempre que se toca no `base.html`, e aí o teste
  falha sem que a listagem tenha piorado. A da fila de contactos é a única que
  guarda um `select_related` que existe no código; as duas das contas são
  guardas, não a reprodução de um defeito — não há N+1 nelas hoje.

**Marcado, e é o que fica por fazer:** os cinco trechos de código morto. Nenhum
deles se corrige sem o caminho que falta, e escrevê-los agora era deixar a lista
igual com mais um adjectivo. Têm dono: Fase 3 para os dois de documentos, Fase 4
para a visita e o `Lead`, Fase 6 para a ficha do imóvel fechado.

### Fase 3 — Documentos e triagem dentro do produto

`apps/properties`: envio por `POST` separado, um ficheiro por pedido, com
validação dos bytes `%PDF-`; verificação e rejeição com motivo; remoção com
confirmação; checklist de triagem editável na ficha.

**Porque antes da mesa, e é o único inversionamento face ao plano anterior**: a
lógica já está escrita — `verify_document()`, `missing_verified_documents()`,
`is_ready_for_review()`, `pending_items()` — e nada a chama. A ficha de curadoria
existe. Falta o caminho de escrita, que é mais barato que a lógica, e é a etapa
que tira a documentação legal do `/admin` do Django, onde vive hoje sem ligação
de nenhum template.

A validação existente é por extensão (`storage.py:103`). O `%PDF-` que o plano
pede não existe em código de produção — só aparece em dados de teste.

**Critério de aceitação**: um imóvel chega a `PUBLISHED` sem sair do produto
nenhuma vez.

### Fase 4 — As acções da equipa, e o que acorda sozinho

`apps/concierge`: confirmar e recusar visita com motivo, `NO_SHOW`, o passo "quer
fazer proposta?" depois de concluída, e as listas de visitas e de propostas, que
**não existem hoje** — há quatro rotas e três delas são pedidos do cliente.
`Offer` ganha `parent` e a máquina de estados
(`SUBMITTED → UNDER_REVIEW → COUNTERED → ACCEPTED | REJECTED | WITHDRAWN`); hoje
não tem `ALLOWED_TRANSITIONS` nem `transition_to()`, ao contrário do `Lead`. As
transições de `Lead` ganham o caminho que lhes falta dentro do produto.

**E uma decisão de infra-estrutura que ninguém tomou**: `vercel.json` não tem
`crons`, e não há Celery, APScheduler nem fila. O "lembrete 24 h antes" e a fila
de reconfirmação dependem de algo que acorde sozinho. As opções são o Vercel
Cron a chamar um endpoint, ou um agendador externo a chamar o mesmo endpoint.
Decidir aqui evita que a Fase 6 e a Fase 7 inventem respostas diferentes.

**Porque antes da mesa**: a mesa são botões, e um botão que aponta para uma acção
que não existe é uma inbox que não trata de nada.

**Critério de aceitação**: cada fila tem a acção que lhe falta, e nada se resolve
pelo `/admin`.

### Fase 5 — A mesa de trabalho da equipa

`apps/concierge`: inbox única de conversas escaladas, leads, visitas e propostas,
cada uma com dono, estado e prazo de resposta; `PropertyNote`; resposta a
conversas sem as mensagens internas visíveis ao cliente.

**É a fase que faz o produto, e é a mais cara**: não é uma página, são quatro
conjuntos de acções, e a Fase 4 é o que as dá. Aqui é navegação, resposta e
prazo.

Existe hoje uma fila de `Lead` (`views.py:126`) que é só de leitura e aparece no
menu em Atendimento → Contactos. As conversas escaladas só se veem pelo
`/admin`.

**Critério de aceitação**: uma conversa escalada aparece na inbox com o motivo
interno, um agente responde, o cliente vê a resposta, e nenhum destes passa pelo
`/admin`.

### Fase 6 — O que acontece depois de haver negócio

`Property.Status` ganha `RESERVED`, `RENTED`, `SOLD` e `PAUSED`. Uma proposta
`ACCEPTED` reserva o imóvel; o fecho passa-o a `RENTED` ou `SOLD`; a falta de
reconfirmação do dono passa-o a `PAUSED`. Só `PUBLISHED` aparece na pesquisa; os
outros aparecem na ficha, com o aviso e as alternativas.

As condições comerciais passam a campos estruturados — caução, meses adiantados,
pagamento anual, água, energia, gerador, condomínio. Hoje há um booleano
(`accepts_annual_payment`) e um `TextField`, e a caução não existe no repositório
nem como palavra. É esta etapa que dá à IA dados para responder em vez de dizer
que não sabe, e o `AGENTS.md` §2.4 já promete que ela não inventa.

O selo "Verificado pelo Echilo" passa a enumerar o que foi verificado.
`property_not_published` ganha a rota que não tem.

**Critério de aceitação**: uma proposta aceite reserva o imóvel, e um imóvel
fechado deixa de aparecer na pesquisa.

### Fase 7 — Notificações

Uma regra só: `notify(destinatário, tipo, contexto)`, envio em
`transaction.on_commit`, e-mail primeiro. Nenhum módulo envia mensagens por fora
dela. Falha de envio nunca desfaz a operação de negócio.

Hoje há um envio em todo o projecto — a recuperação da senha — e zero `on_commit`.

O WhatsApp fica para depois e é um projecto próprio: conta Business, templates
aprovados e janela de 24 h. `ECHILO_WHATSAPP_NUMBER` está nas settings e não é
usado por um único template.

**Critério de aceitação**: cliente e equipa sabem, sem pedir, o que aconteceu a um
pedido — e um SMTP em baixo não devolve 500.

### Fase 8 — Dinheiro: mandato, contrato, comissão

`Mandate` (comissão, prazo, exclusividade, e a cláusula de comissão devida se o
negócio fechar com um cliente apresentado pelo Echilo), `Contract`
(`DRAFT → SENT → SIGNED → ACTIVE → CLOSED | CANCELLED`), e a comissão
**derivada**, nunca digitada, com o arredondamento fixado por teste. O PDF
assinado sai por link assinado, como a documentação legal. `apps/billing` é app
novo.

O mandato é o que trava a fuga de comissão, que é o risco de gravidade alta que
depende de mais ninguém.

**Depende das decisões do §9** — base de cálculo no arrendamento, quem paga os
8 %, renovações, moedas. Sem elas a comissão não tem regra e a etapa não fecha.

**Critério de aceitação**: a comissão de cada contrato é calculada, não digitada.

### Fase 9 — Favoritos, portal do dono, registo progressivo

`Favorite(user, property)` com unicidade por par e a mesma lista paginada do
catálogo. O perfil `OWNER` e a ligação que **não existe hoje** entre
`OwnerProfile` e `User` — sem ela o dono não tem conta, e o portal seria um link
sem dono. O portal é só de leitura: estado do imóvel, visitas, propostas,
comissão prevista.

O registo progressivo — navegar sem conta, pedir visita com telefone verificado,
NIF e BI só na proposta formal — **mexe no `AGENTS.md` §2.11**, que hoje ancora a
conta a uma pessoa real e manda a equipa-contactar o cliente antes de tratar de
qualquer pedido. Aquele anchor é o que impede o produto de virar um formulário de
anúncios; trocá-lo é preço a pagar, e o preço escreve-se no `AGENTS.md`, não
numa nota de rodapé.

`Favorite` é pequeno e independente das outras duas, e pode ser puxado para
antes se houver vontade.

**Critério de aceitação**: o dono vê os seus imóveis sem perguntar pelo WhatsApp.

### Fase 10 — Operação e qualidade

Logs estruturados com `request_id` — hoje o `LOGGING` é um `StreamHandler` e o
`server_error` não regista nada. `/saude/` que diga qual dos três serviços
falhou: o único health check que há é o do assistente, e não testa nada, devolve
settings. O comando `check --deploy`, que existe hoje só como 45 testes em
`test_deploy.py`. Rotação de credenciais como comando. Um backup restaurado com
sucesso ao menos uma vez.

Os KPIs de operação — tempo de resposta, visita→proposta, proposta→fecho, dias
até publicar, receita por mês — só são possíveis depois da Fase 8: hoje
`Offer.responded_at` existe e nunca é escrito, e não há campo de tempo de
resposta em visita.

**Esta é a única fase que não bloqueia ninguém e não é bloqueada por ninguém**,
fora os KPIs. Pode correr em paralelo a partir de qualquer ponto.

**Critério de aceitação**: `/saude/` diz qual dos três serviços falhou, e um 500
tem `request_id` e chega a alguém.

### O que mudou de posição, e porquê

A leitura do código pôs os documentos (M8) antes da mesa (M11), e não é
cosmético.

- **O M8 é barato porque já está escrito.** `verify_document()` faz a verificação,
  a rejeição com motivo, `verified_by` e `verified_at`, e não é chamada uma vez.
  Falta o caminho de escrita, que é mais barato que a lógica.
- **O M11 é caro porque são acções, não páginas.** Inbox de conversas, gestão de
  visitas, de propostas e de leads são quatro fluxos, e cada um precisa do seu
  serviço antes de ter o seu botão. Construir a mesa primeiro é construir quatro
  listas de botões que não fazem nada.

O plano anterior punha a mesa primeiro, com a justificação de que a equipa
precisa de um sítio onde trabalhar. É verdade, e a Fase 4 dá-lhe o sítio: cada
fila passa a ter a acção que lhe falta. A Fase 5 é então navegação e prazo, que é
a parte barata.

Nenhum dos treze módulos está completo. E os rótulos `[existe]`, `[parcial]` e
`[falta]` do plano em HTML vêm do plano anterior, por admissão do próprio
documento, e não de uma leitura do código. Este §7 é a versão que passou pela
leitura; o HTML é a versão anterior e não serve de referência para o estado de
nada.

### Fora do âmbito, para sempre

Sem analytics de comportamiento, sem testes A/B, sem motor de recomendação, sem
"semelhantes ao seu". Um produto de mediação em Angola cresce a responder bem a
uma mensagem no WhatsApp, e não a perceber que 3 % dos utilizadores viram a cor
do botão.

---

## 8. Riscos técnicos concretos

Os que estão no código hoje e que valem um olho antes de subir tráfego.

| Risco | Onde | Gravidade | Mitigação |
| --- | --- | --- | --- |
| **`maxDuration: 10`** | `vercel.json` | alta | Um retrato de 8000 px consome a invocação. `motivo_recusa_imagem` valida **antes** de `prepara_retrato` por isso, e o limite de 4 MB limita os pixels. Ver se 10 s chega com a Cloudinary no meio |
| **Upload acima de 4,5 MB** | edge da Vercel | alta | Redução client-side + tecto de validação. O terceiro caminho (lote de 15 sem JS) é o que ainda não foi provado em produção |
| **Sem fila de tarefas** | arquitectura | média | Nenhuma operação devia passar de um segundo. `add_images` com 15 ficheiros é a mais pesada: 15 imagens + 15 chamadas à Cloudinary, dentro dos 10 s. Medir |
| **Busca por `LIKE`** | `selectors.py` | média | Aceitável até dezenas de milhares de imóveis. Depois, índice de texto completo ou um motor dedicado |
| **MySQL sem PostGIS** | Aiven | baixa (assumido) | Caixa envolvente + Haversine, com o índice certo |
| **`Offer`/`Lead`/`VisitRequest` sem consola** | §5 | **alta** | Fase 2 |
| **Credenciais expostas** | histórico | **alta** | Rodar. Hoje. O `AGENTS.md` §6 já diz que nunca há segredo em código, e a consequência de não rodar é o sistema todo |
| **Sem backup ensaiado** | operação | **alta** | Fase 7 |
| **Fornecedor de tiles em produção** | §2.12 | média | Nenhum basemap gratuito serve produção. `ECHILO_MAP_TILE_URL` está preparada para um fornecedor contratado; a escolha é de contrato, não de código |
| **Um único ficheiro JS de 91 KB** | `static/js` | baixa | Servido uma vez e com cache. Se crescer, dividir por página — `base.html` já tem `scripts` para isso |
| **Escala do `Lead` por IP** | `ratelimit.py` | média | 4 adesões por 30 min por IP. Um escritório com NAT partilhado fica sem cadastrar. A regra de negócio é "poucas adesões por pessoa", e o IP não é a pessoa |

---

## 9. Decisões que exigem o dono

Nenhuma destas é técnica. Cada uma muda o que o projecto é.

1. **API pública?** Um portal parceiro ou um imobiliário a consumir o catálogo é
   receita. Também é um contrato público para sempre. §6.7
2. **Comissão da mediação?** Percentagem fixa, escalonada por volume, ou por
   imóvel? Define o modelo de `Commission` e a contabilidade de §6.5
3. **Avisos por WhatsApp obrigatórios?** Se sim, a ficha de adesão tem de passar
   a enviar, e a equipa tem de tratar recusas de envio
4. **O cliente paga, ou paga ao sair?** Altera o modelo de `Installment` e a
   relação com a assinatura
5. **Propriedade do imóvel e registo registado?** Se a plataforma guarda
   escrituras, é um novo problema de retenção e de Rectificação
6. **O `CLIENT` tem de pagar para ver o contacto?** Com o telefone do dono nunca
   público (§2.1), o produto vende o acesso, não o anúncio. É a decisão de
   modelo de negócio do projecto inteiro, e o código actual não o impede

---

## 10. Como se sabe que o sistema está profissional

Não por haver muitas funcionalidades. Por cada uma destas afirmações ser
verdadeira e ter um teste que a prova.

**Sobre o produto**
- [ ] Um imóvel chega a `PUBLISHED` sem sair do produto
- [ ] Um pedido de visita é criado, confirmado e concluído dentro do produto
- [ ] Uma conversa escalada pelo assistente é lida e respondida dentro do produto
- [ ] Toda a transição de estado tem autor, motivo e registo
- [ ] Um cliente que chega pelo catálogo encontra o imóvel, pede visita e recebe
      resposta, sem e-mail de contacto e sem telefone

**Sobre a verdade do que o sistema diz**
- [ ] Nenhuma lista paginada mostra a mesma linha duas vezes nem esconde outra
- [ ] Nenhum `[:n]` corta o registo errado
- [ ] O número de imóveis que o mapa diz é o número que o mapa mostra
- [ ] Nenhuma capacidade está no `AGENTS.md` sem código, e nenhum código faz
      alguma coisa que o `AGENTS.md` não prometa
- [ ] Nenhum número escrito à mão num atributo, URL ou JSON usa vírgula decimal

**Sobre a operação**
- [ ] Todo o 500 tem `request_id` e chega a alguém
- [ ] `/saude/` responde 200 ou 503 a dizer qual dos três falhou
- [ ] Um backup foi restaurado com sucesso, por alguém, uma vez
- [ ] As credenciais foram rodadas depois de terem estado expostas
- [ ] `python manage.py test` verde, `makemigrations --check` limpo, `ruff` sem
      avisos novos

**Sobre o código**
- [ ] Nenhuma função pública sem type hints em `services.py`, `selectors.py` e
      `views.py`
- [ ] Nenhuma lista sem `assertNumQueries`
- [ ] Nenhum `except` nu que engula um `AttributeError`
- [ ] Nenhuma constante escrita em três sítios
- [ ] Nenhuma frase num comentário que o código não cumpra

---

## 11. Como manter este documento

Este documento é um mapa, e um mapa desatualizado é pior do que nenhum.

- O **§3** (módulos que existem) responde à pergunta "o que há aqui?". Actualiza-se
  quando entra um `urls.py`, um modelo ou um comando
- O **§6** (módulos que têm de existir) responde a "o que falta?". Um item só sai
  daqui quando a sua **fase** estiver feita e testada — não quando o ficheiro
  existir
- O **§10** só se desliga quando o teste que está ao lado da caixa está verde
- A tabela de permissões do **§2.3** é a mesma de `core/permissions.py` e de
  `core/navigation.py`. Se divergirem, são os dois código que estão errados e o
  `AGENTS.md` tem de ser corrigido

O `AGENTS.md` continua a ser o documento **normativo** — as regras, e o porquê de
cada uma. Este é o documento **descritivo**: o que o sistema faz, o que não faz, e
pelo que ordem. Nenhum dos dois substitui o outro, e o código, como em todos os
dois, é o que está errado se os três não concordarem.
