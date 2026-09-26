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
- **As sugestões de município seguem a província, e é o servidor que as
  escreve.** `PropertyQueryService.municipality_suggestions()` devolve a união
  dos municípios da lista de referência com as áreas que têm imóveis
  publicados, porque o campo aceita localidade e `municipality` não é a única
  coluna que a pesquisa lê. A lista de sugestões entra no `<datalist>` do
  servidor; o JavaScript troca-a por um `fetch` a `properties:municipalities`
  quando a província muda. **Não se escreve a lista inteira no HTML**: cento e
  setenta e um nomes no código-fonte é oferecer todos, que é o contrário
  do que o filtro promete, e ainda pesa cada página do catálogo.
- **O campo continua a ser texto livre.** Um `<select>` fechava a pesquisa a
  município e perdia a localidade, que é metade do catálogo. O que muda é a
  lista de sugestões.
- **O formulário de curadoria usa o mesmo caminho.** O `datalist` vive em
  `partials/municipality_datalist.html` e as duas páginas internas apontam para
  ele. Antes de ele existir, `list="municipality-options"` apontava para um `id`
  que não estava em lado nenhum, e o campo da equipa ficava sem sugestão
  nenhuma. Um atributo que aponta para nada não dá erro: o campo continua a
  aceitar texto e a equipa escreve o município à mão, sem perceber que as opções
  não estão lá.
- **A form da curadoria recebe a província por três sítos, e o `initial` é um
  deles.** `PropertyCuratorForm.__init__()` lê `self.data`, depois
  `self.initial`, e só depois a instância. A ficha interna passa os dados do
  imóvel em `initial` e não como instância, e `self.instance` é ali um
  `Property()` vazio: ler só a instância dava província vazia, que faz
  `municipalities_for("")` devolver a união das 21 listas. O resultado era a ficha
  de um imóvel de Benguela a oferecer os cento e setenta e um nomes, e a equipa
  a concluir que o campo não era scoped. Um `<datalist>` com conteúdo a mais não
  dá erro nenhum — dá a lista errada, e a lista errada parece uma lista.
- **Todos os selectores de província oferecem as 21, e não só as que têm
  imóveis.** A home, o catálogo, a curadoria e o registo de conta são a mesma
  pergunta com quatro formulários, e respostas diferentes entre eles é uma
  resposta que muda conforme se entra pela porta da frente ou por trás. A
  home chegou a listar só as províncias com imóveis publicados, e quem não
  chegasse ao catálogo nunca via Icolo e Bengo existir. Uma província sem
  imóveis dá um resultado vazio, que é honesto; uma província que o produto
  não menciona é uma que não existe para quem lê.
- **Um teste de `<select>` diz de que campo está a falar.** Medir a página
  inteira mede o formulário ao lado: o registo tem três `select` e o do tipo de
  documento escreve `BI` e `PASSPORT`, que passam por províncias. É o que
  `select_options(html, field_id)` evita, e é o mesmo cuidado que o `datalist`
  exige — medir o alvo, não a página.
- **Sem JavaScript, mudar a província e submeter devolve a página com a lista
  certa.** O formulário é que fica desatualizado até alguém submeter, e isso é
  diferente de estar partido.
- **O que o mapa está a mostrar é escrito pelo servidor.** O texto com o número
  de imóveis, e a legenda, saem do template: quem abre a página sem JavaScript
  vê a mesma explicação, e quem vê um mapa vazio sabe se falta o mapa ou o
  catálogo. O JavaScript só acrescenta o que só ele sabe.
- **A acção principal tem nome.** Desenhar o círculo é um `<button>` no painel,
  com rótulo em português, e não o ícone de 30×30 do plugin. Esse botão
  funcionava, mas lia-se como um quadrado vazio, e quem não o percebeu partiu do
  princípio de que o mapa estava partido. A barra do `leaflet.draw` fica só com
  o editar e o apagar.
- **Centrar usa a localização de quem pergunta, e diz quando não a consegue
  ler.** O botão vive dentro de `#area-map`, para ficar debaixo dos olhos de quem
  está a olhar para o mapa. No sucesso: `map.setView`, um ponto azul na
  `localGroup` — nem dourado nem verde, porque nenhum dos dois é o dono do
  imóvel — e `applyArea()` com `origem: 'utilizador'`, que é o caminho já
  existente, não um segundo pedido.
- **A recusa da permissão é o caso comum, não a excepção.** Cada código de
  `getCurrentPosition` diz o que aconteceu e **volta sempre ao desenho do
  círculo**, que não depende de ninguém. Um botão que falha em silêncio parece um
  botão que não funciona. E a geolocalização só existe em contexto seguro:
  servida por HTTP simples, `navigator.geolocation` nem existe, e a resposta tem
  de dizer isso em vez de ficar muda.
- **A mensagem da geolocalização não é a dos tiles, e a cura é outra.** Um 403 do
  fornecedor a apagar a recusa da permissão mandava o utilizador seguir a pista
  errada, por isso são caixas separadas. A dos tiles é `alert`; a da localização é
  `status`, porque dois alertas ao mesmo tempo fazem o leitor de ecrã ler a
  mensagem errada.
- **O resumo diz de onde veio o centro.** "Raio de 2 km em torno de -8,8390,
  13,2894" não diz a ninguém que aquilo era ele. Com a origem GPS escreve "da sua
  localização", e um círculo que não seja de lá desfaz a mensagem, porque ela
  deixaria de ser verdade dois segundos depois. O chip que o servidor redesenha
  depois do HTMX continua a escrever coordenadas: uma URL partilhada não deve
  dizer a quem a abre que a área era a sua.
- **O raio tem duas autoridades, e são pessoas diferentes.** Quem arrasta o
  círculo com a ferramenta do `leaflet.draw` diz o raio pela geometria; quem
  escreve no campo diz por palavras. `redrawFromLayer()` recebe o raio em
  argumento e usa o da camada só quando não lhe deram nenhum.
- **`map.on(tipo, fn)` passa o evento a `fn`, e um evento não é um raio.**
  Registrar `redrawFromLayer` directamente no `EDITED` mandava o evento do
  Leaflet para dentro de `clampRadius`: `Number(evento)` dá `NaN`, a função
  devolve o mínimo, e arrastar um círculo encolhia-o para 100 m sem um único
  erro. A chamada vai dentro de uma função que não recebe argumentos, e
  `test_o_ouvinte_do_circulo_nao_recebe_o_evento_como_raio` trava isso.
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
- **O mapa diz onde está.** Sob os pinos desenham-se as divisões de Angola, e o
  mapa diz em que província e município está cada área. As camadas são
  vendorizadas, com a fonte e a licença escritas no ficheiro e no mapa:
  `static/vendor/geo/angola-provincias.json` e `angola-municipios.json`, do
  geoBoundaries `gbOpen` (CC BY 4.0), geradas por
  `python manage.py build_admin_boundaries` e verificadas com `--check`.
- **A geometria e o ponto do rótulo não seguem a mesma ordem, e essa é a
  armadilha.** `geometry.coordinates` vai em `[lon, lat]`, que é o GeoJSON e o
  que o `L.geoJSON` lê ao desenhar. `properties.ponto` vai em `[lat, lon]`,
  porque é o `L.circleMarker` que o recebe. Trocar um dos dois não dá erro: o
  mapa desenha Angola no Atlântico a oeste, ou escreve os nomes no mar, e o DOM
  continua cheio de `path`. O comando converte em `geometria()` e desfaz em
  `aneis_de()`; o JavaScript faz o mesmo em `dentroDe()`.
- **Um nome é um ponto, não o centro da camada.** O centro da caixa envolvente
  de uma divisão recortada cai fora dela, e o mapa escreve o nome ao lado.
  `properties.ponto` é calculado pelo comando com o ponto mais distante da
  margem, e um teste exige que caia dentro do próprio contorno — nas duas
  camadas.
- **A província vem do município.** As camadas ADM1 e ADM2 da fonte não
  coincidem: o município de Luanda cobre o centro e a baía, e o contorno
  provincial tem esse recorte a menos. Perguntar só à província dava "fora dos
  contornos" com o círculo no meio de Luanda. A província vem do município,
  com o ADM1 como recurso.
- **A lista do projecto tem vinte e uma entradas e a fonte desenha dezoito.**
  Não são a mesma coisa e nenhuma cede: `ANGOLA_PROVINCES` e
  `MUNICIPALITIES_BY_PROVINCE` são o que o filtro oferece, e
  `PROVINCE_BOUNDARY_CODES` é o que a fonte tem. Onde não há correspondence há
  uma entrada a menos, não um contorno inventado — `Icolo e Bengo` e `Moxico
  Leste` aparecem nos filtros e não se desenham. Onde a lista dá dois nomes a uma
  divisão, como `Cuando` e `Cubango`, os dois apontam para o mesmo `shapeName`
  e a geometria é desenhada uma vez. `provinces_without_boundary()` é a lista
  dessa diferença, e o comando imprime-a em vez de falhar: a igualdade entre as
  duas listas é a excepção, e quando acontecer é porque a fonte mudou. O número
  de províncias não é escrito em código nem na interface — o mapa diz o que sabe,
  que é se o ponto caiu dentro de um contorno.
- **Os municípios não se desenham longe, e a carga é o que torna o mapa
  possível.** As províncias vão no primeiro carregamento; os municípios só são
  pedidos quando o mapa atinge `ZOOM_MUNICIPIOS`, ou quando o utilizador desenha
  um círculo. O catálogo abre enquadrado nos pinos, tipicamente numa cidade e
  acima do limiar, e nesse caso a camada entra logo; numa vista de país, o mapa
  nunca pede os 157 municípios. 157 nomes a zoom 9 não se leem, e 157 linhas que
  não se apagam enchem Angola de arame, por isso abaixo do limiar somem as linhas
  **e** os rótulos, ao mesmo tempo.
- **O que a fonte não dá, não se inventa.** A camada municipal não cobre trinta
  e tal municípios da lista — entre eles `Caxito`, `N'dalatando`, `Ondjiva` e
  `Dundo` — e não tem contorno provincial para `Icolo e Bengo` nem para `Moxico
  Leste`. Ficam sem contorno e sem nome; o comando imprime a lista de cada vez
  que corre. O mapa não desenha bairro: `Property.locality` é texto, e não há
  fonte administrativa fiável para lhe prometer um limite.
- A simplificação é diferente nas duas camadas — 223 m nos municípios, 1,1 km nas
  províncias — e a atribuição de município a província é mais fina (5,5 m), porque
  aí um erro põe o município na província errada. Um anel que simplifique para
  menos de quatro pontos fica com os vértices que a fonte deu: um município que
  desaparece é pior do que um município desenhado com mais pontos.

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
| `DJANGO_DB_SSL_CA` | Caminho para o `ca.pem` do serviço Aiven. Obrigatório em produção com host remoto. Ver `config/certs/README.md`. |
| `DJANGO_DB_SSL_VERIFICAR_HOST` | `false` só para depurar. `true` por omissão. |
| `DJANGO_DB_TIMEOUT` | Segundos de espera da ligação MySQL. Predefinido: `10`. |
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
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` na Vercel. Sem isto o `collectstatic` do build corre em modo de desenvolvimento. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Origens do formulário, com esquema. O `ALLOWED_HOSTS` incluir `.vercel.app` não chega: o `Origin` do preview é recusado sem esta lista, e o erro é um 403 num formulário. |
| `DJANGO_CONN_MAX_AGE` | Segundos de espera de uma ligação MySQL. `0` na Vercel. |
| `DJANGO_CACHE_BD` | `true` liga o cache na base de dados. |
| `DJANGO_CACHE_LOCATION` | Tabela do cache. Criada por `apps/core/migrations/0001`. |
| `DJANGO_CACHE_TIMEOUT` | Segundos de um item de cache. Vai dentro de `OPTIONS`. |
| `DJANGO_SECURE_SSL_REDIRECT` | `true` em produção. |
| `CLOUDINARY_URL` | `cloudinary://<api_key>:<api_secret>@<cloud_name>`. Obrigatória em produção. |
| `CLOUDINARY_PUBLICAO` | `true` em produção. `false` deixa o `MEDIA_ROOT` de uma função, que é efémero. |
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | Bytes. Predefinido: 5 MiB. |
| `FILE_UPLOAD_MAX_MEMORY_SIZE` | Bytes. Predefinido: 5 MiB. |
| `ECHILO_AI_TIMEOUT` | Segundos por tentativa ao Groq. Predefinido: `6.0`. |
| `ECHILO_AI_MAX_RETRIES` | Tentativas. Predefinido: `0`. |

---

## 7.1 Deploy

O alvo é a Vercel, com a aplicação a correr como função serverless. Isto não é
um detalhe de infraestrutura: muda o que é seguro guardar em disco, e quase
tudo aqui vem dessa mudança.

**A função vive, morre e não volta.** Consequências que não são óbvias:

- `MEDIA_ROOT` é efémero. Um ficheiro escrito por uma invocação desaparece
  antes da seguinte, e o catálogo fica sem fotografias sem nenhum erro.
  Todo o media vai para a Cloudinary, e produção recusa arrancar sem
  `CLOUDINARY_URL` em vez de o descobrir com o cliente à frente.
- **O `requirements.txt` é uma afirmação sobre o que foi testado, e não sobre o
  que existe.** O build da Vercel resolve os limites e instala o que apanhar, e
  um limite largo é uma promessa que ninguém cumpre.
  - **`Django<5.1` instalava o 5.0, e o 5.0 removeu a assinatura de quatro
    argumentos de `assertFormError`** que os testes daqui usam. O build ficava a
    correr um Django que a suite não passa, e o sintoma eram três
    `AttributeError` em testes que já não tinham nada a ver com o deploy. Agora
    é `>=4.2,<5.0`, e a escolha é do §2.13, que razona sobre o comportamento do
    4.2. Um `Django` mais recente não é uma melhoria: é um Django diferente.
  - **`pymysql.__version__` devolve uma versão inventada.**
    `install_as_MySQLdb()` reescreve `__version__` e `version_info` para
    satisfazer a verificação de versão que o Django faz ao `mysqlclient`. Ler
    essa propriedade para saber o que está instalado dá uma resposta errada com
    ar de certa — e foi assim que se declarou um `PyMySQL>=2.2` que não existe
    no PyPI, o que faz o build falhar a resolver. A versão verdadeira é a dos
    metadados, e é essa que o `pip` resolve.
  - **`test_o_intervalo_declarado_contem_a_versao_instalada`** compara cada
    intervalo declarado com a versão instalada, e é o que apanha a deriva. Vale
    para o pacote que vier a seguir, que é o ponto: o teste não é sobre o Django
    nem sobre o PyMySQL.
  - **Uma dependência por declarar desaparece em silêncio.** O `requests` só é
    usado pelo `build_admin_boundaries`, e estava importado no topo do comando
    sem estar no `requirements.txt`. O efeito não foi um erro: os testes de
    `apps.properties` deixaram de ser importados, e a suite perdeu 128 testes sem
    que a contagem o dissesse. Por isso o import é feito dentro da função, e o
    `requests` fica a ser dependência de desenvolvimento — um comando que recusa
    correr fora de desenvolvimento não leva o seu `requests` para dentro de cada
    função serverless.
  - **A versão do Python está em `.python-version`.** Sem ela a Vercel deduce a
    sua, e o build de hoje pode ser o de amanhã com outra interpretação.
- **A base de dados também muda de figura.** É um MySQL do Aiven, e a função
  é remota para ele. A ligação é privilegiada, e um `TypeError` na primeira
  tentativa é um deploy que parece azul e não deixa entrar ninguém.
  - **O `OPTIONS` do MySQL é o `connect()` do driver.** O Django 4.2 copia-o
    tal e qual, depois de tirar `isolation_level`. Uma chave que o PyMySQL não
    conheça não é ignorada: é um `TypeError` na ligação, e a ligação só
    acontece a alguém a abrir o sítio. `apps.core.test_deploy.TlsDoMysqlTests`
    compara as chaves com a assinatura do `connect()` do PyMySQL, e é isso que
    apanha o erro antes do deploy.
  - **O `ssl-mode=REQUIRED` do URI do Aiven não vai para as `OPTIONS`.** O
    PyMySQL não tem `ssl_mode`; o `REQUIRED` dele vive dentro de
    `ssl.verify_mode`. Copiar a palavra do URI dá um `TypeError` numa stack que
    não menciona TLS. Ver `db_options()`.
  - **Sem `DJANGO_DB_SSL_CA` a ligação não é segura, e parece que é.** O
    PyMySQL entra em modo `PREFERRED`: tenta TLS e, se o servidor não oferecer,
    continua em texto claro, sem erro e sem aviso. E mesmo quando há TLS sem
    `ca`, o certificado do servidor não é verificado — encriptado, mas sem
    prova de quem está do outro lado, que é o que o homem no meio quer. Por isso
    a CA do serviço é versionada em `config/certs/` e produção **recusa
    arrancar** com um host remoto sem ela. Recusar é a única forma de este
    erro não aparecer já em produção.
  - **A excepção é a base local**, reconhecida pelo nome e não por um
    interruptor: um interruptor que alguém desligue para testar pode ser
    desligado para sempre.
  - **O `utf8mb4` do `charset` está no sítio certo por um motivo que não se
    vê.** O backend põe `utf8` e só depois faz `kwargs.update(options)`, o que
    faz o `OPTIONS` ganhar. Tirá-lo repõe o `utf8` em silêncio, e o primeiro
    sinal é um `OperationalError` numa tabela com emoji.
- `CONN_MAX_AGE = 0`. Uma ligação MySQL mantida para reaproveitar ocupa uma
  das poucas ligações do plano durante quase mais um minuto do que precisava.
- O cache em memória é novo a cada invocação. O limitador de tentativas do §6
  deixava de ser um limite, e o login passava a aceitar senhas erradas para
  sempre sem dar erro. Por isso o cache é o da base de dados, e a tabela é
  criada por migration.
- Nada de estado entre invocações. A pesquisa por área (§2.12) vive no URL
  exactamente por isto: o estado que a função não pode guardar fica onde o
  browser o pode reenviar.

**Ordem de deploy.** As migrations não correm no build — uma base de dados
com migration a meio é pior do que uma base sem migration:

1. `DJANGO_SETTINGS_MODULE=config.settings.production` nas variáveis da
   Vercel, e as restantes também. O build faz `collectstatic --noinput` e nada
   mais.
2. `python manage.py migrate` contra a base de produção, **a partir de uma
   máquina com as variáveis todas**, antes de promover o deploy novo.
3. `python manage.py createcachetable` se a base já existia antes de
   `core.0001`: a migration só cria a tabela quando o cache configurado é o da
   base de dados, e uma base migrada em desenvolvimento tinha o cache local.
4. `vercel --prod`.

**O `collectstatic` do build tem de correr com as settings de produção.** O
`base.py` é importado antes de o `production.py` fazer `DEBUG = False`, e um
`STORAGES` decidido no `base` a partir do `DEBUG` do ambiente fica com
`StaticFilesStorage`: sem manifesto o `whitenoise` não resolve os nomes com
hash e a página aparece sem CSS, com 200 e sem nada na consola. Por isso
`storages()` é uma função e cada ambiente responde por si.
`apps.core.test_deploy.BuildEstaticoTests` corre o `collectstatic` a sério,
porque um ficheiro em falta só falha no build.

**O que já está em `media/` tem de ser transferido antes de mudar de backend.**
Um `PropertyImage.image.name` que aponta para um caminho local continua a
apontar para esse caminho depois de o backend ser a Cloudinary, e a imagem
some do catálogo. A transferência lê o disco, sobe e reescreve o nome do
campo, e tem de correr com o backend antigo activo — por isso antes do passo 2.

**A documentação legal nunca é um URL público** (§6). Os documentos são
entregas como `authenticated` e abertos por `PropertyDocumentView`, que exige
`AGENT`/`ADMIN`, devolve um link assinado e não deixa o ficheiro em cache nem
no cabeçalho de referência. O registo de quem abriu é `DocumentAccessLog`, não
apagável e sem `CASCADE` para o documento: apagar o ficheiro não pode levar
consigo a prova de quem o leu.

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
