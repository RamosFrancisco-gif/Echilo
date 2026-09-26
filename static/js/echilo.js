/* Comportamentos de baixo custo: menu, separadores, galeria e sugestões do chat. */
(function () {
  'use strict';

  var DESKTOP_BREAKPOINT = 900;

  function setupBurger() {
    var burger = document.getElementById('nav-burger');
    var menu = document.getElementById('nav-menu');
    if (!burger || !menu) return;

    function setOpen(open) {
      menu.classList.toggle('is-open', open);
      burger.setAttribute('aria-expanded', open ? 'true' : 'false');
      burger.setAttribute('aria-label', open ? 'Fechar menu' : 'Abrir menu');
    }

    function isOpen() {
      return burger.getAttribute('aria-expanded') === 'true';
    }

    burger.addEventListener('click', function () {
      setOpen(!isOpen());
    });

    // Escape fecha o menu e devolve o foco ao botão.
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && isOpen()) {
        setOpen(false);
        burger.focus();
      }
    });

    // Clicar fora do cabeçalho fecha o menu.
    document.addEventListener('click', function (event) {
      if (!isOpen()) return;
      if (event.target.closest('.nav')) return;
      setOpen(false);
    });

    // Ao passar para desktop o menu perde o significado: deve ficar fechado.
    window.addEventListener('resize', function () {
      if (window.innerWidth > DESKTOP_BREAKPOINT) {
        setOpen(false);
      }
    });
  }

  function setupScopeTabs() {
    var tabs = document.querySelectorAll('.search-panel .tab[data-scope]');
    if (!tabs.length) return;

    var purposeField = document.getElementById('search-purpose');
    var typeField = document.getElementById('search-type');

    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        tabs.forEach(function (other) {
          other.setAttribute('aria-selected', other === tab ? 'true' : 'false');
        });
        var parts = tab.dataset.scope.split(':');
        var key = parts[0];
        var value = parts[1];
        if (key === 'purpose') {
          purposeField.value = value;
          typeField.value = value === 'LAND' ? 'LAND' : '';
        } else {
          typeField.value = value;
          purposeField.value = '';
        }
      });
    });
  }

  function setupChatSuggestions() {
    var container = document.getElementById('chat-suggestions');
    var field = document.getElementById('chat-question');
    if (!container || !field) return;

    container.addEventListener('click', function (event) {
      var button = event.target.closest('.suggestion');
      if (!button) return;
      field.value = button.dataset.question;
      field.form.requestSubmit();
    });
  }

  function setupFieldToggles() {
    document.querySelectorAll('.field-control').forEach(function (control) {
      control.addEventListener('invalid', function () {
        control.setAttribute('aria-invalid', 'true');
      });
      control.addEventListener('input', function () {
        control.removeAttribute('aria-invalid');
      });
    });
  }

  /* Entrada por scroll. Só o conteúdo dentro de `<main>` anima: o cabeçalho e o
     rodapé são chrome permanente e nunca podem piscar. Onde o browser suporta
     `animation-timeline: view()` a animação é feita em CSS e não nos importamos. */
  function setupReveal() {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    if (window.CSS && CSS.supports && CSS.supports('animation-timeline', 'view()')) return;
    if (!window.IntersectionObserver) return;

    document.documentElement.classList.add('has-reveal');

    if (!setupReveal.observer) {
      setupReveal.observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            entry.target.classList.add('is-revealed');
            setupReveal.observer.unobserve(entry.target);
          });
        },
        { rootMargin: '0px 0px -10% 0px', threshold: 0.05 }
      );
    }

    document.querySelectorAll('main [data-reveal]:not(.is-revealed)').forEach(function (node) {
      setupReveal.observer.observe(node);
    });
  }

  /* O esqueleto é decorativo; quem precisa de saber que algo carrega é quem usa
     leitor de ecrã. `aria-busy` é que comunica essa espera. */
  function setBusy(event, busy) {
    var id = event.detail && event.detail.target && event.detail.target.id;
    var node = id && document.getElementById(id);
    if (!node) return;
    if (busy) {
      node.setAttribute('aria-busy', 'true');
    } else {
      node.removeAttribute('aria-busy');
    }
  }

  /* A galeria troca a fotografia principal sem recarregar a página. O esmaecimento
     é feito por CSS; aqui só mudamos a origem e marcamos a miniatura activa. */
  function setupGallery() {
    var main = document.getElementById('galeria-img');
    var thumbs = document.querySelectorAll('.gallery__thumb');
    if (!main || !thumbs.length) return;

    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function select(thumb) {
      if (thumb.classList.contains('is-active')) return;
      var source = thumb.dataset.full;
      if (!source) return;

      thumbs.forEach(function (other) {
        other.classList.toggle('is-active', other === thumb);
      });

      function commit() {
        main.src = source;
        var label = thumb.getAttribute('aria-label');
        if (label) main.alt = label;
        main.classList.remove('is-swapping');
      }

      if (reduceMotion) {
        commit();
        return;
      }

      main.classList.add('is-swapping');
      window.setTimeout(commit, 180);
    }

    thumbs.forEach(function (thumb) {
      thumb.addEventListener('click', function () {
        select(thumb);
      });
    });
  }

  /* O `leaflet.draw` traz as strings em inglês: os títulos dos botões e o
     aviso do raio. Numa interface pt-AO, um "Draw a circle" é um defeito, e o
     título é a única pista de que o quadrado serve para alguma coisa.

     As ferramentas vivem em `draw.toolbar.buttons` como strings simples, e não
     como objectos com `title` e `text`. Só traduzimos as que activámos. */
  function translateDrawLocal() {
    var d = L.drawLocal;

    d.draw.toolbar.buttons.circle = 'Desenhar círculo';
    d.draw.toolbar.actions.title = 'Cancelar desenho';
    d.draw.toolbar.actions.text = 'Cancelar';
    d.draw.toolbar.finish.title = 'Terminar desenho';
    d.draw.toolbar.finish.text = 'Terminar';
    d.draw.toolbar.undo.title = 'Apagar o último ponto';
    d.draw.toolbar.undo.text = 'Apagar';
    d.draw.handlers.circle.tooltip.start = 'Clique e arraste para desenhar o círculo.';
    d.draw.handlers.circle.radius = 'Raio';

    d.edit.toolbar.actions.save.title = 'Guardar alterações';
    d.edit.toolbar.actions.save.text = 'Guardar';
    d.edit.toolbar.actions.cancel.title = 'Cancelar edição e descartar as alterações';
    d.edit.toolbar.actions.cancel.text = 'Cancelar';
    d.edit.toolbar.actions.clearAll.title = 'Apagar todas as áreas';
    d.edit.toolbar.actions.clearAll.text = 'Apagar tudo';
    d.edit.toolbar.buttons.edit = 'Mover a área';
    d.edit.toolbar.buttons.editDisabled = 'Não há área para mover';
    d.edit.toolbar.buttons.remove = 'Apagar a área';
    d.edit.toolbar.buttons.removeDisabled = 'Não há área para apagar';
    d.edit.handlers.edit.tooltip = {
      text: 'Arraste o ponto para mover a área.',
      subtext: 'Clique em Cancelar para desfazer.'
    };
    d.edit.handlers.remove.tooltip.text = 'Clique na área para a apagar.';
  }

  /* Pesquisa por área (§2.3).
     O mapa não sabe nada do catálogo: escreve nos três campos ocultos do
     formulário de filtros e dispara um `change`. O pedido, o esqueleto e a
     ordenação por distância são os de sempre, e sem JavaScript a URL continua a
     filtrar. Se o Leaflet não carregar, a secção desaparece em vez de deixar
     uma caixa cinzenta onde devia estar um mapa. */
  function setupAreaSearch() {
    var container = document.getElementById('area-map');
    if (!container) return;

    var section = container.closest('.area-search');
    var config = readMapConfig();

    if (!window.L || !L.Draw || !config.tileUrl) {
      if (section) section.hidden = true;
      return;
    }

    if (L.drawLocal) translateDrawLocal();

    var latField = document.getElementById('f-area-lat');
    var lonField = document.getElementById('f-area-lon');
    var radiusField = document.getElementById('f-area-raio');
    if (!latField || !lonField || !radiusField) return;

    var radiusInput = document.getElementById('area-raio');
    var summary = document.getElementById('area-summary');
    var clearButton = document.getElementById('area-clear');
    var applyButton = document.getElementById('area-apply');
    var drawButton = document.getElementById('area-draw');
    var errorBox = document.getElementById('area-error');
    var locateButton = document.getElementById('area-centrar');
    var locateBox = document.getElementById('area-local');
    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var minRadius = Number(config.minRadiusM) || 100;
    var maxRadius = Number(config.maxRadiusM) || 50000;
    var markers = readMapMarkers();

    // O dourado vive nos tokens do CSS; o Leaflet precisa de uma cor concreta.
    var gold = window.getComputedStyle(document.documentElement)
      .getPropertyValue('--gold').trim() || '#f5a623';
    // A venda é a única cor com significado próprio no mapa, e é a mesma que a
    // legenda usa: as duas leem o token, por isso não se podem separar. E o
    // token chama-se `--ok`: com `--success` a leitura dava vazio e a cor vinha
    // do valor de recurso, o que fazia as duas afastarem-se em silêncio.
    var verde = window.getComputedStyle(document.documentElement)
      .getPropertyValue('--ok').trim() || '#2dd4a0';
    // A localização de quem pede é uma terceira cor, fora da escala do catálogo:
    // dourado é arrendar, verde é vender, e o azul não é nenhum dos dois.
    var azul = window.getComputedStyle(document.documentElement)
      .getPropertyValue('--accent').trim() || '#4c9bff';

    /* A vista abre sobre o que há para ver. Um mapa de Angola inteiro no ecrã não
       é um mapa: a 11 de zoom um imóvel é um pixel perdido dentro do país. Com
       catálogo, enquadramos os pinos; sem catálogo, Luanda a um zoom útil. */
    var opcoes = {
      center: [Number(config.centerLat), Number(config.centerLon)],
      zoom: Number(config.zoom) || 12,
      // A roda do rato pertence à página: só enlargemos com os botões e o teclado.
      scrollWheelZoom: false,
      zoomAnimation: !reduceMotion,
      fadeAnimation: !reduceMotion
    };
    if (markers.caixa && !(latField.value && lonField.value)) {
      opcoes.bounds = [
        [markers.caixa[0][0], markers.caixa[0][1]],
        [markers.caixa[1][0], markers.caixa[1][1]]
      ];
      opcoes.fitBoundsOptions = { padding: [26, 26], maxZoom: 16 };
    }

    var map = L.map(container, opcoes);


    if (config.invertTiles) container.classList.add('area-search__map--invert');

    var tiles = L.tileLayer(config.tileUrl, {
      attribution: config.attribution,
      maxZoom: 19
    });

    /* Um fornecedor que recusa o pedido com 403, um proxy corporativo ou uma
       rede fechada deixavam o mapa como um rectângulo vazio, sem nenhuma pista
       de que a culpa não era do código. Dizer qual foi o endereço recusado
       transforma um mistério numa linha de `.env`. */
    var failedTiles = 0;

    function showTileError() {
      failedTiles += 1;
      if (!errorBox || failedTiles < 2) return;
      errorBox.textContent = 'O mapa não carregou: o fornecedor de tiles recusou o pedido ('
        + failedTiles + ' tiles). Confirme a rede ou ajuste ECHILO_MAP_TILE_URL no .env. '
        + 'O filtro por área continua a funcionar pelo raio ao lado.';
      errorBox.hidden = false;
    }

    function clearTileError() {
      if (errorBox) errorBox.hidden = true;
    }

    tiles.on('tileerror', showTileError);
    tiles.on('tileload', function () {
      if (failedTiles) clearTileError();
    });

    if (config.configError) {
      // Um `{key}` por substituir não se corrige no browser: dizemos o que
      // falta e seguimos sem tiles, que o raio não precisa de uma única imagem.
      if (errorBox) {
        errorBox.textContent = config.configError;
        errorBox.hidden = false;
      }
    } else {
      tiles.addTo(map);
    }

    var areaGroup = new L.FeatureGroup();
    map.addLayer(areaGroup);

    /* Onde está quem pede é uma camada à parte do círculo, porque as duas têm
       vidas diferentes: o círculo muda de sítio cada vez que o raio muda, e o
       ponto de quem pediu não. */
    var localGroup = new L.FeatureGroup();
    map.addLayer(localGroup);

    /* Se o centro actual é a posição de quem perguntou ou um sítio escolhido à
       mão. O resumo escreve em coordenadas, e "em torno de -8,8390, 13,2894" não
       diz a ninguém que aquilo era ele. */
    var origemCentro = '';

    function avisoLocal(texto, isErro) {
      if (!locateBox) return;
      locateBox.textContent = texto || '';
      locateBox.classList.toggle('area-search__local--erro', !!isErro);
    }

    /* A barra do `leaflet.draw` fica só com o editar e o apagar. Desenhar passa
       a ser o botão do painel, que tem nome, teclado e rótulo em português: um
       ícone de 30×30 sem legenda foi lido como mapa partido, e era um botão
       que funcionava. */
    map.addControl(new L.Control.Draw({
      position: 'topleft',
      draw: false,
      edit: { featureGroup: areaGroup, remove: true }
    }));

    function marcadores() {
      var group = L.featureGroup();
      map.addLayer(group);
      markers.items.forEach(function (item) {
        var pin = L.circleMarker([item.lat, item.lon], {
          radius: 6,
          weight: 2,
          color: '#15100b',
          fillColor: item.por_mes ? gold : verde,
          fillOpacity: 0.96
        });
        pin.bindPopup(popupDoImovel(item), { maxWidth: 260, className: 'pin-popup' });
        group.addLayer(pin);
      });
      return group;
    }

    function popupDoImovel(item) {
      var preco = esc(item.preco) + (item.por_mes ? ' <span class="pin-popup__periodo">por mês</span>' : '');
      return '<h3 class="pin-popup__titulo">' + esc(item.titulo) + '</h3>'
        + '<p class="pin-popup__preco">' + preco + '</p>'
        + (item.local ? '<p class="pin-popup__local">' + esc(item.local) + '</p>' : '')
        + '<a class="pin-popup__link" href="' + esc(item.url) + '">Ver imóvel</a>'
        + '<button class="pin-popup__procurar" type="button" data-procurar-aqui'
        + ' data-lat="' + esc(item.lat) + '" data-lon="' + esc(item.lon) + '">'
        + 'Procurar nesta zona</button>';
    }

    /* O conteúdo do popup vem do texto que a equipa escreveu no imóvel, por isso
       escapa-se tudo. Sem isto, um título com uma tag entraria como HTML. */
    function esc(valor) {
      if (valor === null || valor === undefined) return '';
      return String(valor)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    if (markers.items.length) marcadores();

    /* Fica com o mapa a dizer que divisão está debaixo do círculo, porque quem
       o cria chama-o e sem isto o texto continuaria a dizer a província anterior
       até o utilizador mexer no mapa. */
    var fronteiras = setupFronteiras(map, config, function () {
      return areaGroup.getLayers()[0] || null;
    });

    function clampRadius(value) {
      var metres = Math.round(Number(value));
      if (!isFinite(metres)) return minRadius;
      return Math.min(maxRadius, Math.max(minRadius, metres));
    }

    function ptNumber(value) {
      return String(value).replace('.', ',');
    }

    function describe(lat, lon, metres) {
      var size = metres >= 1000
        ? ptNumber((metres / 1000).toFixed(1)) + ' km'
        : metres + ' m';
      if (origemCentro === 'utilizador') {
        return 'Raio de ' + size + ' em torno da sua localização.';
      }
      return 'Raio de ' + size + ' em torno de '
        + ptNumber(lat.toFixed(4)) + ', ' + ptNumber(lon.toFixed(4)) + '.';
    }

    function requestResults() {
      // O formulário escuta `change`: um único evento chega para o HTMX pedir o
      // catálogo e mostrar o esqueleto, sem duplicar aquery à mão.
      radiusField.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function applyArea(lat, lon, metres, options) {
      var settings = options || {};
      var centre = L.latLng(lat, lon);
      var radius = clampRadius(metres);
      origemCentro = settings.origem || '';

      areaGroup.clearLayers();
      var circle = L.circle(centre, { radius: radius, color: gold, weight: 2, fillOpacity: 0.12 });
      areaGroup.addLayer(circle);

      latField.value = centre.lat.toFixed(6);
      lonField.value = centre.lng.toFixed(6);
      radiusField.value = radius;
      if (summary) summary.textContent = describe(centre.lat, centre.lng, radius);
      if (radiusInput) radiusInput.value = radius;
      if (clearButton) clearButton.hidden = false;
      if (settings.fit) map.fitBounds(circle.getBounds(), { padding: [28, 28] });
      /* Um círculo que não é a posição de quem perguntou desfaz a mensagem que
         dizia que era. "A mostrar imóveis perto da sua localização" continua
         verdade durante dois segundos e depois é mentira. */
      if (origemCentro !== 'utilizador') avisoLocal('');
      /* Desenhar o círculo é a pergunta que o mapa responde: que municípios
         apanha. A camada dos municípios vem agora, sem esperar por um zoom. */
      fronteiras.pedirMunicipios();
      fronteiras.nomeia();
      if (!settings.silent) requestResults();
    }

    function clearArea() {
      areaGroup.clearLayers();
      localGroup.clearLayers();
      latField.value = '';
      lonField.value = '';
      radiusField.value = '';
      if (summary) {
        summary.textContent = 'Sem área definida: o mapa mostra o catálogo inteiro.';
      }
      if (clearButton) clearButton.hidden = true;
      fronteiras.nomeia();
      requestResults();
    }

    /* O raio tem duas autoridades e elas não são a mesma pessoa. Quem arrasta o
       círculo com a ferramenta do `leaflet.draw` está a dizer qual é o raio pela
       geometria; quem escreve no campo está a dizer por palavras. Passar o
       raio errado ao segundo leva a Two coisas erradas: o campo parece morto
       depois de haver um círculo, e arrastar o círculo com a ferramenta
       transformava-o em 100 m, porque o evento do Leaflet ia parar a
       `clampRadius` como se fosse um número. */
    function redrawFromLayer(raioEscolhido) {
      var layer = areaGroup.getLayers()[0];
      if (!layer) return false;
      var centre = layer.getLatLng();
      applyArea(
        centre.lat,
        centre.lng,
        raioEscolhido === undefined ? layer.getRadius() : raioEscolhido
      );
      return true;
    }

    map.on(L.Draw.Event.CREATED, function (event) {
      if (!(event.layer instanceof L.Circle)) return;
      areaGroup.clearLayers();
      areaGroup.addLayer(event.layer);
      event.layer.setStyle({ color: gold });
      var centre = event.layer.getLatLng();
      applyArea(centre.lat, centre.lng, event.layer.getRadius());
    });

    map.on(L.Draw.Event.EDITED, function () { redrawFromLayer(); });

    map.on(L.Draw.Event.DELETED, clearArea);

    /* Onde está quem pergunta, e não onde está o círculo.

       A recusa é o caso comum, não a excepção: a permissão é pedida por
       Navegador, um site em HTTP simples não a dá, e há quem não a quer dar.
       Por isso cada recusa diz o que aconteceu e volta sempre ao caminho que
       já funcionava — desenhar o círculo, que não depende de ninguém. Um botão
       que falha em silêncio parece um botão que não funciona. */
    function motivoDaRecusa(erro) {
      if (erro && erro.code === 1) {
        return 'O navegador não deu permissão para a localização. Active-a nas '
          + 'permissões deste site, ou desenhe o círculo à mão.';
      }
      if (erro && erro.code === 2) {
        return 'O navegador não consegue determinar onde está. Desenhe o círculo à mão.';
      }
      if (erro && erro.code === 3) {
        return 'A localização demorou demais a chegar. Tente outra vez, ou desenhe o círculo à mão.';
      }
      return 'Não foi possível ler a sua localização. Desenhe o círculo à mão.';
    }

    function centrarNaLocalizacao() {
      if (locateButton) {
        locateButton.disabled = true;
        locateButton.setAttribute('aria-busy', 'true');
      }
      avisoLocal('A pedir a sua localização…');

      if (!navigator.geolocation) {
        /* A geolocalização só existe em contexto seguro. Servir o site por HTTP
           num endereço de rede é o caso normal de um servidor em Angola, e aí
           `navigator.geolocation` nem existe. Dizer "active o HTTPS" vale mais
           do que um botão que nunca responde. */
        if (locateButton) {
          locateButton.disabled = false;
          locateButton.removeAttribute('aria-busy');
        }
        avisoLocal('Este site não está em HTTPS, e só um site em HTTPS pode ler a '
          + 'sua localização. Desenhe o círculo à mão.', true);
        return;
      }

      navigator.geolocation.getCurrentPosition(
        function (posicao) {
          if (locateButton) {
            locateButton.disabled = false;
            locateButton.removeAttribute('aria-busy');
          }
          var lat = posicao.coords.latitude;
          var lon = posicao.coords.longitude;
          localGroup.clearLayers();
          L.circleMarker([lat, lon], {
            pane: 'limites',
            radius: 7,
            color: window.getComputedStyle(document.documentElement)
              .getPropertyValue('--bg').trim() || '#0c0906',
            weight: 2,
            fillColor: azul,
            fillOpacity: 1
          }).addTo(localGroup);
          /* Um zoom baixo deixaria a pessoa como um pixel no meio de Angola. A
             vista guarda o zoom que o utilizador já tinha escolhido, desde que
             já esteja perto. */
          map.setView([lat, lon], Math.max(map.getZoom(), 14));
          applyArea(lat, lon, radiusInput ? radiusInput.value : minRadius, {
            origem: 'utilizador'
          });
          avisoLocal('A mostrar imóveis perto da sua localização.');
        },
        function (erro) {
          if (locateButton) {
            locateButton.disabled = false;
            locateButton.removeAttribute('aria-busy');
          }
          avisoLocal(motivoDaRecusa(erro), true);
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
      );
    }

    if (locateButton) locateButton.addEventListener('click', centrarNaLocalizacao);

    if (applyButton) {
      applyButton.addEventListener('click', function () {
        if (redrawFromLayer(radiusInput ? radiusInput.value : undefined)) return;
        // Sem círculo no mapa, o raio escolhido aplica-se ao centro visível.
        var centre = map.getCenter();
        applyArea(centre.lat, centre.lng, radiusInput ? radiusInput.value : minRadius);
      });
    }

    if (drawButton) {
      drawButton.addEventListener('click', function () {
        // `L.Draw.Circle` é o mesmo objecto que a barra do plugin usava, agora
        // com um botão que se lê. O evento `CREATED` abaixo trata o resultado.
        new L.Draw.Circle(map, {
          shapeOptions: { color: gold, weight: 2, fillOpacity: 0.12 },
          showRadius: true,
          metric: true
        }).enable();
      });
    }

    /* O botão vive dentro do popup, que o Leaflet cria e destrói. Delegar no
       contentor do mapa é o que faz o botão funcionar em todos os pinos sem
       um listener por pino. */
    container.addEventListener('click', function (event) {
      var botao = event.target.closest('[data-procurar-aqui]');
      if (!botao) return;
      event.preventDefault();
      applyArea(
        Number(botao.getAttribute('data-lat')),
        Number(botao.getAttribute('data-lon')),
        radiusInput ? radiusInput.value : minRadius,
        { fit: true }
      );
      map.closePopup();
    });

    if (clearButton) clearButton.addEventListener('click', clearArea);

    if (radiusInput) {
      radiusInput.addEventListener('change', function () {
        if (!redrawFromLayer(radiusInput.value)) {
          var centre = map.getCenter();
          applyArea(centre.lat, centre.lng, radiusInput.value);
        }
      });
    }

    // Uma área que veio na URL é redesenhada sem voltar a pedir o catálogo: o
    // servidor já a aplicou a esta mesma página.
    var hasArea = latField.value && lonField.value && Number(radiusField.value);
    if (hasArea) {
      applyArea(
        Number(latField.value),
        Number(lonField.value),
        Number(radiusField.value),
        { fit: true, silent: true }
      );
    } else {
      if (clearButton) clearButton.hidden = true;
      if (radiusInput && !radiusInput.value) radiusInput.value = 2000;
    }

    function refreshSize() {
      map.invalidateSize();
    }

    window.addEventListener('resize', refreshSize);
    document.body.addEventListener('htmx:afterSettle', refreshSize);
  }

  /* A camada administrativa: as divisões que o mapa está a mostrar.

     Um mapa sem nomes obriga o utilizador a adivinhar. Ele vê um círculo e não
     sabe se pegou em Viana ou em Talatona, e a única forma de saber é sair do
     mapa e procurar. As etiquetas respondem a isso.

     As províncias vêm no primeiro carregamento porque são 73 KB e porque uma
     província é a resposta que quase toda a gente quer. Os municípios vão num
     ficheiro separado, pedido só quando o utilizador se aproxima ou desenha um
     círculo: 556 KB para desenhar 157 contornos que ninguém ia ver a zoom 9
     pagariam-se na primeira pintura. */
  function setupFronteiras(map, config, circuloActual) {
    var contexto = document.getElementById('area-fraccao');
    if (!contexto) return { nomeia: function () {}, pedirMunicipios: function () {} };
    var container = map.getContainer();

    var provincias = null;
    var municipios = null;
    var aPedirMunicipios = false;
    var linhas = {};

    /* As linhas vão para um pane próprio, abaixo dos pinos. No `overlayPane` de
       um Leaflet normal, que está acima dos marcadores, o contorno de uma
       fronteira tapava o pino que estava a descrever. */
    map.createPane('limites');
    map.getPane('limites').style.zIndex = 350;
    map.getPane('limites').style.pointerEvents = 'none';

    /* O ficheiro é uma `FeatureCollection`, e o Leaflet sabe desenhá-la. Cada
       contorno recebe a sua classe: os municípios precisam de desaparecer quando
       se afasta, e 157 linhas que não se apagam enchem o mapa de arame de Angola
       a ver o país inteiro.

       O nome não vai no centro da camada, que é o centro da caixa envolvente: numa
       província comprida como a de Luanda isso dá um ponto no mar, e o mapa
       escreve o nome onde não há nada. Vai no `ponto` que o comando calculou
       dentro do contorno. */
    function desenha(dados, classe) {
      var rotulos = L.layerGroup();
      var camada = L.geoJSON(dados, {
        pane: 'limites',
        style: function () {
          return {
            color: '#f5a623', weight: 1, opacity: 0.5, fill: false, interactive: false
          };
        },
        onEachFeature: function (feature) {
          var p = feature.properties.ponto;
          if (!p) return;
          /* Um círculo de raio zero não se vê e serve só de âncora ao tooltip,
             que precisa de uma camada com posição para se prender. */
          L.circleMarker([p[0], p[1]], {
            pane: 'limites', radius: 0, weight: 0, fill: false, interactive: false
          }).bindTooltip(feature.properties.nome, {
            permanent: true, direction: 'center', className: classe
          }).addTo(rotulos);
        }
      });
      camada.addTo(map);
      rotulos.addTo(map);
      return { linhas: camada, rotulos: rotulos };
    }

    /* O `MultiPolygon` do GeoJSON é uma lista de polígonos, cada um com os seus
       anéis. O teste de ponto-em-polígono quer os anéis todos, e só ele sabe os
       pedidos uns a uns. */
    function aneisDe(geojson) {
      var saida = [];
      geojson.geometry.coordinates.forEach(function (poligono) {
        saida = saida.concat(poligono);
      });
      return saida;
    }

    /* Teste de ponto-em-polígono por crossings, o mesmo do comando que gerou a
       camada. `anterior` fecha o anel no fim, senão o último segmento não conta.

       A geometria vem em `[lon, lat]`, a ordem do GeoJSON, e o `x` é a
       longitude. O `properties.ponto` vem ao contrário, em `[lat, lon]`, porque
       esse é o `L.circleMarker` que recebe. São dois contratos diferentes no
       mesmo ficheiro, e trocar um deles punha o mapa a dizer que Luanda estava
       no mar. */
    function dentroDe(lat, lon, aneis) {
      var dentro = false;
      for (var a = 0; a < aneis.length; a += 1) {
        var anel = aneis[a];
        for (var i = 0, j = anel.length - 1; i < anel.length; j = i, i += 1) {
          var xi = anel[i][0];
          var yi = anel[i][1];
          var xj = anel[j][0];
          var yj = anel[j][1];
          if ((yi > lat) !== (yj > lat)
            && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) {
            dentro = !dentro;
          }
        }
      }
      return dentro;
    }

    /* Distância em metros, para saber que municípios cabem no raio escolhido.
       É o mesmo haversine de `apps.core.geo`, reescrito porque o Python não
       chega ao browser. */
    function distanciaMetros(aLat, aLon, bLat, bLon) {
      var raio = 6371000;
      var dLat = (bLat - aLat) * Math.PI / 180;
      var dLon = (bLon - aLon) * Math.PI / 180;
      var h = Math.sin(dLat / 2) * Math.sin(dLat / 2)
        + Math.cos(aLat * Math.PI / 180) * Math.cos(bLat * Math.PI / 180)
        * Math.sin(dLon / 2) * Math.sin(dLon / 2);
      return 2 * raio * Math.asin(Math.min(1, Math.sqrt(h)));
    }

    /* A província vem do município, não do contorno provincial.

       As duas camadas da fonte não coincidem: o município de Luanda cobre a
       baía e o centro, e o contorno provincial da fonte tem esse recorte a
       menos. Perguntar à província sozinha dava "fora dos contornos" com o
       círculo desenhado no meio de Luanda, que é a última coisa que se quer
       ler. Derivada do município, as duas respostas concordam por construção, e
       o município sai do mesmo acerto. O contorno provincial continua a ser o
       que se desenha. */
    function municipioEm(lat, lon) {
      var achado = null;
      if (municipios) {
        municipios.features.forEach(function (m) {
          if (!achado && dentroDe(lat, lon, aneisDe(m))) achado = m.properties;
        });
      }
      return achado;
    }

    function provinciaDe(lat, lon) {
      var nome = '';
      provincias.features.forEach(function (p) {
        if (!nome && dentroDe(lat, lon, aneisDe(p))) nome = p.properties.nome;
      });
      return nome;
    }

    function nomeDaProvincia(codigo) {
      var nome = '';
      provincias.features.forEach(function (p) {
        if (!nome && p.properties.codigo === codigo) nome = p.properties.nome;
      });
      return nome;
    }

    function divisoesDe(lat, lon) {
      var municipio = municipioEm(lat, lon);
      var nome = municipio ? nomeDaProvincia(municipio.provincia) : '';
      if (!nome) nome = provinciaDe(lat, lon);
      return { provincia: nome, municipio: municipio ? municipio.nome : '' };
    }

    /* O que o círculo apanha. A província vem do centro: é a resposta que se
       espera e não depende de o raio cair dentro ou fora de uma fronteira. Os
       municípios listam-se por rótulo dentro do raio, o que permite dizer
       "inclui Talatona" num círculo de 20 km em vez de escolher um e esconder o
       resto. */
    function nomeia() {
      if (!provincias) return;
      var circulo = circuloActual();

      if (!circulo) {
        var centro = map.getCenter();
        var aqui = divisoesDe(centro.lat, centro.lng);
        var texto = aqui.provincia ? 'A mostrar: província de ' + aqui.provincia : '';
        if (aqui.municipio) texto += ', município de ' + aqui.municipio;
        contexto.textContent = texto ? texto + '.' : '';
        return;
      }

      var ponto = circulo.getLatLng();
      var raio = circulo.getRadius();

      if (!municipios) {
        contexto.textContent = 'A carregar os municípios deste círculo.';
        return;
      }

      // O número de divisões que o mapa desenha e o número de províncias que o
      // projecto tem não são o mesmo número, e este texto não deve prometer
      // nenhum dos dois: o que o mapa sabe é se o ponto caiu dentro de algum
      // contorno, e é isso que ele diz.
      var aqui = divisoesDe(ponto.lat, ponto.lng);
      var texto = aqui.provincia
        ? 'Província de ' + aqui.provincia
        : 'Fora dos contornos desenhados';

      var incluidos = [];
      municipios.features.forEach(function (m) {
        var p = m.properties.ponto;
        if (incluidos.length < 6
          && distanciaMetros(ponto.lat, ponto.lng, p[0], p[1]) <= raio) {
          incluidos.push(m.properties.nome);
        }
      });
      if (incluidos.length) texto += ', inclui ' + incluidos.join(', ');
      contexto.textContent = texto + '.';
    }

    function pedeMunicipios() {
      if (aPedirMunicipios || municipios) return;
      aPedirMunicipios = true;
      fetch(config.municipiosUrl)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (dados) {
          if (!dados) return;
          municipios = dados;
          linhas.municipios = desenha(
            dados, 'divisao-label divisao-label--municipio'
          );
          nomeia();
        })
        .catch(function () { aPedirMunicipios = false; });
    }

    /* Aproximar pede a camada dos municípios, e afastar esconde-a. A classe é
       o gancho do CSS para os nomes; as linhas e os rótulos precisam do seu,
       senão o mapa fica coberto de fronteiras que já não significam nada a esta
       escala. */
    function talvezMunicipios() {
      var perto = map.getZoom() >= (config.zoomMunicipios || 10);
      container.classList.toggle('zoom-municipios', perto);
      if (linhas.municipios) põeMunicipios(perto);
      if (perto) pedeMunicipios();
    }

    function põeMunicipios(visivel) {
      if (visivel) {
        linhas.municipios.linhas.addTo(map);
        linhas.municipios.rotulos.addTo(map);
      } else {
        map.removeLayer(linhas.municipios.linhas);
        map.removeLayer(linhas.municipios.rotulos);
      }
    }

    fetch(config.provinciasUrl)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (dados) {
        if (!dados) return;
        provincias = dados;
        linhas.provincias = desenha(dados, 'divisao-label divisao-label--provincia');
        nomeia();
        talvezMunicipios();
      })
      .catch(function () {
        // As linhas são um extra. O raio, os pinos e a pesquisa não dependem
        // delas, e um ficheiro em falta não pode levar a página atrás.
      });

    map.on('zoomend', talvezMunicipios);
    map.on('moveend', nomeia);

    /* Desenhar o círculo também traz os municípios. Quem escolhe uma área está
       a perguntar exactamente por que municípios ela cobre, e esperar por um
       zoom de fora responder à pergunta que ele já fez é deixá-lo à espera. */
    return {
      nomeia: nomeia,
      pedirMunicipios: function () {
        container.classList.add('zoom-municipios');
        if (linhas.municipios) põeMunicipios(true);
        pedeMunicipios();
      }
    };
  }


  function readJson(id, fallback) {
    var node = document.getElementById(id);
    if (!node) return fallback;
    try {
      return JSON.parse(node.textContent) || fallback;
    } catch (error) {
      return fallback;
    }
  }

  function readMapConfig() {
    return readJson('area-map-config', {});
  }

  function readMapMarkers() {
    // Sem pinos o mapa continua a ser um filtro. Perder o desenho é chato;
    // perder a página inteira por causa de um JSON mau, não.
    return readJson('area-map-markers', {
      items: [], total: 0, mostrados: 0, incompletos: false, caixa: null
    });
  }

  /* As sugestões de município seguem a província escolhida.
     O campo é texto livre com `<datalist>`, não um `<select>`: também aceita
     localidade, e fechar a pesquisa a município era perder metade do catálogo.
     O que muda é a lista de sugestões, e quem a escreve é o servidor — o
     JavaScript limita-se a pedir a lista da província nova e a trocar as opções.

     A lista vai no pedido e não na página porque a página não tem nada a fazer
     com cento e setenta e um nomes: escrevê-los todos no HTML seria oferecer
     todos, que é o contrário do que o filtro promete. E vai num pedido por
     mudança e não a cada tecla, porque quem escolhe a província sabe o que
     escolheu. */
  function setupMunicipalityOptions() {
    var url = document.body.getAttribute('data-municipality-options');
    if (!url) return;

    // As duas páginas não usam os mesmos ids, e o campo do catálogo público
    // chama-se "f-municipio" enquanto o do formulário interno é o do Django.
    // Em vez de uma lista de ids por página, o par encontra-se pelo `list` do
    // input, que é o atributo que liga o campo às sugestões.
    var campos = document.querySelectorAll('input[list]');
    Array.prototype.forEach.call(campos, function (campo) {
      var lista = document.getElementById(campo.getAttribute('list'));
      if (!lista) return;

      var form = campo.form;
      if (!form) return;
      var provincia = form.querySelector('select[name="province"], select[name="province_ref"]');
      if (!provincia) return;

      function escrever(sugestoes) {
        lista.textContent = '';
        sugestoes.forEach(function (nome) {
          var opcao = document.createElement('option');
          opcao.value = nome;
          lista.appendChild(opcao);
        });
      }

      provincia.addEventListener('change', function () {
        fetch(url + '?province=' + encodeURIComponent(provincia.value), {
          headers: { 'HX-Request': 'true' }
        })
          .then(function (resposta) {
            return resposta.ok ? resposta.json() : null;
          })
          .then(function (dados) {
            // Uma resposta que não chega deixa as opções como estavam. Trocar
            // a lista por nada faria o campo parecer partido, e o que está no
            // ecrã continua a ser verdade sobre a página que a desenhou.
            if (dados && Array.isArray(dados.sugestoes)) escrever(dados.sugestoes);
          })
          .catch(function () { /* a lista que já está é a da última escolha */ });
      });
    });
  }

  function ready() {
    setupBurger();
    setupScopeTabs();
    setupGallery();
    setupChatSuggestions();
    setupFieldToggles();
    setupMunicipalityOptions();
    setupReveal();
    setupAreaSearch();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }

  // Os swaps do HTMX trocam listas inteiras: o que entra tem de ser observado.
  document.body.addEventListener('htmx:afterSwap', setupReveal);
  document.body.addEventListener('htmx:afterSettle', setupReveal);
  document.body.addEventListener('htmx:beforeRequest', function (event) {
    setBusy(event, true);
  });
  ['htmx:afterSettle', 'htmx:responseError', 'htmx:sendError'].forEach(function (name) {
    document.body.addEventListener(name, function (event) {
      setBusy(event, false);
    });
  });
})();
