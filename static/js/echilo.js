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

  /* A conversa cresce para baixo e fica no fundo.

     O HTMX acrescenta as mensagens novas ao fim de #chat-body, e o contentor tem
     max-height com scroll próprio. Sem isto, a conversa ficava a ler de cima
     e a resposta acabada de chegar ficava fora da área visível — a pessoa
     escrevia, vê o esqueleto desaparecer, e não vê a resposta. Isto vale
     igualmente para quem carrega a página numa conversa já longa. */
  function scrollChatToBottom(alvo) {
    var corpo = document.getElementById('chat-body');
    if (!corpo) return;
    if (alvo && alvo.id && alvo.id !== 'chat-body') return;
    corpo.scrollTop = corpo.scrollHeight;
  }

  /* A saudação só pertence ao primeiro render. Depois de haver mensagens ela
     ficava acima das bolhas, a meio da conversa, e com `beforeend` nunca mais
     saía de lá. */
  function removeSaudacao() {
    var saudacao = document.querySelector('#chat-body .chat__empty');
    if (saudacao) saudacao.remove();
  }

  /* O formulário diz ao servidor até onde o cliente já viu, para o servidor
     acrescentar o que falta em vez de trocar a conversa inteira. O valor é o
     id da última bolha desenhada — o que o servidor devolve é lido do DOM, e
     não de um campo que o HTMX actualiza por nós. */
  function rememberLastMessage() {
    var corpo = document.getElementById('chat-body');
    var campo = document.getElementById('ultima-mensagem');
    if (!corpo || !campo) return;
    var bolhas = corpo.querySelectorAll('[data-message-id]');
    if (!bolhas.length) return;
    campo.value = bolhas[bolhas.length - 1].getAttribute('data-message-id');
  }

  function setupChat() {
    var corpo = document.getElementById('chat-body');
    if (!corpo) return;
    scrollChatToBottom();
    rememberLastMessage();
  }

  function onChatSwap(event) {
    var alvo = event.detail && event.detail.target;
    if (alvo && alvo.id === 'chat-body') {
      removeSaudacao();
      rememberLastMessage();
      scrollChatToBottom(alvo);
    }
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


    if (config.invertTiles) container.classList.add('echilo-map--invert');

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
       até o utilizador mexer no mapa. As divisões são as mesmas que o seletor de
       pin consulta, e o desenho é que é só deste mapa. */
    var fronteiras = setupFronteiras(map, config, function () {
      return areaGroup.getLayers()[0] || null;
    }, divisoesDoMapa(config));

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
          avisoLocal(motivoDaRecusa(erro, 'desenhe o círculo à mão'), true);
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

  /* A recusa da geolocalização, com a alternativa de cada mapa.

     Não é uma função por mapa: as duas páginas precisam da mesma tradução, e uma
     versão por mapa é uma versão que um dia diverge. O que muda é a alternativa,
     porque cada mapa tem o seu caminho que não depende de ninguém — desenhar um
     círculo no catálogo, carregar no chão na ficha. Uma recusa que não oferece
     nenhum dos dois parece um botão que não funciona. */
  function motivoDaRecusa(erro, alternativa) {
    if (erro && erro.code === 1) {
      return 'O navegador não deu permissão para a localização. Active-a nas '
        + 'permissões deste site, ou ' + alternativa + '.';
    }
    if (erro && erro.code === 2) {
      return 'O navegador não consegue determinar onde está. ' + alternativa + '.';
    }
    if (erro && erro.code === 3) {
      return 'A localização demorou demais a chegar. Tente outra vez, ou '
        + alternativa + '.';
    }
    return 'Não foi possível ler a sua localização. ' + alternativa + '.';
  }

  /* Seletor de pin da ficha de curadoria (§2.3).

     A equipa não escreve coordenadas: carrega no chão, arrasta, ou diz onde está
     com o próprio corpo. O que o mapa faz é escrever isso nos campos, e o que os
     campos fazem é sobreviver ao mapa desaparecer.

     Três propriedades vêm do resto do ficheiro e valem aqui tanto como lá:

     - O mapa é melhoria progressiva. Se o Leaflet não carregou, a secção
       desaparece e os campos ficam de fora à espera de serem escritos à mão.
     - A recusa da permissão volta sempre ao caminho que não depende de ninguém,
       e o caminho agora é carregar no mapa — não desenhar um círculo.
     - Um ponto com valor por preencher nunca é inventado. Sem pin, o mapa mostra
       o centro configurado; o primeiro clique é que decide onde está o imóvel. */
  function setupConfirmacoes() {
    // Delegado no documento, e não um listener por botão: o botão de apagar
    // nasce e morre com cada carregamento da ficha, e um listener por botão
    // deixava de valer para o botão seguinte. Apagar uma fotografia é
    // irreversível — o ficheiro vai com a linha — e um clique por engano tira
    // uma fotografia que ainda se queria, sem forma de a recuperar.
    var dialogo = document.getElementById('confirmacao');
    var disparador = null;
    var rotulado = false;

    // Uma caixa de selecção não confirma pelo clique: o browser já a marcou
    // quando o `change` chega, e o que a confirmação decide é se essa marca
    // fica. Por isso o caminho do clique não a toca.
    function eCaixa(node) {
      return !!node && node.tagName === 'INPUT' && node.type === 'checkbox';
    }

    function abrir(botao) {
      if (!dialogo || typeof dialogo.showModal !== 'function') return;
      // `showModal()` numa caixa já aberta lança `InvalidStateError`. Sem este
      // `if`, um segundo clique durante a confirmação atira a excepção e a
      // acção destrutiva segue sem ninguém a ter lido.
      if (dialogo.open) return;

      var titulo = dialogo.querySelector('#confirmacao-titulo');
      var texto = dialogo.querySelector('#confirmacao-texto');
      var aceitar = dialogo.querySelector('[data-modal-confirmar]');

      if (titulo) {
        titulo.textContent = botao.getAttribute('data-confirmar-titulo')
          || 'Confirmar acção';
      }
      if (texto) {
        texto.textContent = botao.getAttribute('data-confirmar') || '';
      }
      if (aceitar) {
        aceitar.textContent = botao.getAttribute('data-confirmar-ok') || 'Confirmar';
      }

      disparador = botao;
      dialogo.showModal();
    }

    // `close` dispara para os três ways de sair: `Esc`, `Enter` na caixa e o
    // clique num dos dois botões. O `returnValue` diz qual deles foi, e o que
    // não for `confirmar` é cancelamento — incluindo o `Esc`, que chega com
    // string vazia.
    if (dialogo) {
      dialogo.addEventListener('close', function () {
        var confirmado = dialogo.returnValue === 'confirmar';
        var alvo = disparador;
        disparador = null;
        if (!alvo) return;

        if (eCaixa(alvo)) {
          // Confirmado fica marcada, cancelado volta atrás. Deixar a marca
          // como estava depois de escrever "Cancelar" era dizer uma coisa na
          // caixa e fazer outra no formulário.
          alvo.checked = confirmado;
          return;
        }

        if (!confirmado) return;
        // A marca impede que o clique que estamos a repetir volte a abrir a
        // caixa: o `_click()` passa pelo mesmo delegado, e sem isto a acção
        // pedia confirmação a si própria.
        rotulado = true;
        try {
          alvo.click();
        } finally {
          rotulado = false;
        }
      });
    }

    document.addEventListener('click', function (event) {
      var botao = event.target.closest('[data-confirmar]');
      if (!botao) return;
      if (rotulado) return;
      if (eCaixa(botao)) return;

      // Sem a caixa, ou num browser sem `<dialog>`, o clique segue o seu curso.
      // Bloquear aqui era tornar a acção impossível em vez de confirmada: o
      // botão nasce no servidor e sem JavaScript não há modal, e um `<dialog>`
      // que não abre é uma caixa que engole o clique sem explicar porquê.
      if (!dialogo || typeof dialogo.showModal !== 'function') return;

      event.preventDefault();
      event.stopPropagation();
      abrir(botao);
    });

    // A caixa de selecção é o outro tipo de acção destrutiva do projecto:
    // marcar é o gesto, e é o que a caixa de selecção faz. Sem JavaScript ela
    // continua a funcionar — a fotografia só some quando o formulário é
    // guardado, e quem a marcou foi o próprio titular.
    document.addEventListener('change', function (event) {
      var botao = event.target.closest('[data-confirmar]');
      if (!botao || !eCaixa(botao)) return;
      if (!botao.checked) return;
      if (!dialogo || typeof dialogo.showModal !== 'function') return;
      abrir(botao);
    });
  }

  function setupCardCarousels() {
    var grupos = Array.prototype.slice.call(document.querySelectorAll('[data-fotos]'));
    if (!grupos.length) return;

    // Quem pede menos movimento não recebe a transição; recebe a troca
    // instantânea, que é a mesma informação sem a animação. O que não se faz é
    // desligar a rotação: o pedido foi para as fotos mudarem, e quem pede
    // menos movimento quer menos movimento, não menos fotos. A regra mora no
    // CSS, que é onde o browser sabe melhor, e por isso este código não pergunta
    // nada sobre o `prefers-reduced-motion`.
    var botao = document.querySelector('[data-alvo-fotos]');
    var carrosseis = [];
    var parados = false;

    function avancar(controlador) {
      var fotos = controlador.fotos;
      var seguinte = (controlador.indice + 1) % fotos.length;
      var alvo = fotos[seguinte];
      var anterior = controlador.indice;

      // A fotografia seguinte entra por `data-src` e só troca de lugar depois
      // de estar descodificada. Atribuir o endereço e mudar a opacidade no
      // mesmo instante mostra metade de uma fotografia, que se lê como erro
      // de carregamento e não como transição.
      if (!alvo.getAttribute('src') && alvo.getAttribute('data-src')) {
        alvo.setAttribute('src', alvo.getAttribute('data-src'));
      }
      var pronto = alvo.decode ? alvo.decode().catch(function () {}) : Promise.resolve();

      return pronto.then(function () {
        // Entre a marcação e a troca pode ter passado o intervalo todo se a
        // página esteve em segundo plano. Só se troca se a pessoa não tiver
        // entretanto mexido no cartão.
        if (controlador.indice !== anterior) return;
        fotos[anterior].classList.remove('is-ativa');
        fotos[anterior].setAttribute('aria-hidden', 'true');
        alvo.classList.add('is-ativa');
        alvo.removeAttribute('aria-hidden');
        controlador.indice = seguinte;
      });
    }

    function podeRodar(controlador) {
      return !parados && controlador.visivel && !controlador.apontado && !document.hidden;
    }

    function marcar(controlador, aguardar) {
      window.clearTimeout(controlador.relogio);
      controlador.relogio = window.setTimeout(function () {
        if (podeRodar(controlador)) avancar(controlador);
        marcar(controlador, controlador.intervalo);
      }, aguardar);
    }

    grupos.forEach(function (grupo, posicao) {
      var fotos = Array.prototype.slice.call(grupo.querySelectorAll('.card-property__foto'));
      if (fotos.length < 2) return;

      var controlador = {
        fotos: fotos,
        indice: 0,
        intervalo: Number(grupo.getAttribute('data-intervalo')) || 40000,
        cartao: grupo.closest('.card-property') || grupo,
        relogio: null,
        visivel: true,
        apontado: false
      };
      carrosseis.push(controlador);

      // Escalonados: seis cartões a virar no mesmo segundo parecem um defeito.
      // O desvio é uma fracção do intervalo e começa em `posicao + 1`: com
      // `posicao` a primeira fotografia mudava no carregamento da página, e o
      // escalonamento existia para evitar precisamente isso.
      marcar(
        controlador,
        Math.round(((posicao + 1) * controlador.intervalo) / (grupos.length + 1))
      );

      // Só conta o que está à vista. Uma grelha abaixo da dobra não tem
      // ninguém a olhar, e o temporizador a acordar de meio em meio minuto
      // para ninguém ver nada é trabalho que se paga e não se aproveita.
      if ('IntersectionObserver' in window) {
        new IntersectionObserver(
          function (entradas) {
            controlador.visivel = entradas[0].isIntersecting;
          },
          { threshold: 0.15 }
        ).observe(controlador.cartao);
      }

      // Passar o rato no cartão ou estar a navegar por teclado dentro dele
      // pára a rotação. Quem está a ler o preço de uma casa não quer a
      // fotografia por baixo a mudar a meio de uma linha.
      controlador.cartao.addEventListener('mouseenter', function () {
        controlador.apontado = true;
      });
      controlador.cartao.addEventListener('mouseleave', function () {
        controlador.apontado = false;
        marcar(controlador, controlador.intervalo);
      });
      controlador.cartao.addEventListener('focusin', function () {
        controlador.apontado = true;
      });
      controlador.cartao.addEventListener('focusout', function () {
        controlador.apontado = false;
        marcar(controlador, controlador.intervalo);
      });
    });

    if (!carrosseis.length) return;

    // Um separador em segundo plano não precisa de trocar fotografias: o
    // temporizador continua a correr, o relógio não pára, e a pessoa volta a
    // ver a página com a fotografia trocada sem nunca a ter visto.
    document.addEventListener('visibilitychange', function () {
      carrosseis.forEach(function (controlador) {
        marcar(controlador, controlador.intervalo);
      });
    });

    // O botão é o mecanismo de paragem que a WCAG 2.2.2 pede para conteúdo que
    // muda sozinho, e está escondido no HTML: sem JavaScript não há rotação
    // para parar, e um botão que não faz nada é pior do que não ter botão.
    if (botao) {
      botao.hidden = false;
      // O estado inicial é a rotação a decorrer. Deixar o `aria-pressed` por
      // definir é o mesmo que o botão aparecer sem estado: um leitor de ecrã
      // anuncia "botão premido" a quem o vai carregar pela primeira vez.
      botao.setAttribute('aria-pressed', 'false');
      botao.addEventListener('click', function () {
        parados = !parados;
        botao.textContent = parados ? 'Retomar fotografias' : 'Pausar fotografias';
        botao.setAttribute('aria-pressed', parados ? 'true' : 'false');
        carrosseis.forEach(function (controlador) {
          marcar(controlador, controlador.intervalo);
        });
      });
    }
  }

  function setupPinPicker() {
    var container = document.getElementById('pin-map');
    if (!container) return;

    var section = container.closest('.pin-map');
    var config = readJson('pin-map-config', {});
    var form = container.closest('form');

    if (!window.L || !config.tileUrl) {
      if (section) section.hidden = true;
      return;
    }
    if (!form) return;

    /* Os campos são procurados pelo `name` e não pelo `id`: o `id` é gerado pelo
       Django e muda entre o cadastro e a edição, e um seletor de pin que só
       funciona numa das duas fichas é meia funcionalidade. */
    var latField = form.querySelector('[name="latitude"]');
    var lonField = form.querySelector('[name="longitude"]');
    var accuracyField = form.querySelector('[name="location_accuracy_m"]');
    var refField = form.querySelector('[name="map_reference"]');
    var provinciaField = form.querySelector('[name="province_ref"]');
    var municipioField = form.querySelector('[name="municipality"]');
    if (!latField || !lonField) return;

    var status = document.getElementById('pin-estado');
    var divisao = document.getElementById('pin-divisao');
    var aviso = document.getElementById('pin-aviso');
    var error = document.getElementById('pin-erro');
    var locateButton = document.getElementById('pin-localizar');
    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* Quem clica no mapa sabe que imóvel é e onde fica, e não sabe como se chama
       a divisão administrativa do ponto. A mesma geometria que nomeia o círculo do
       catálogo diz qual é, e escreve nos campos. */
    var divisoes = divisoesDoMapa(config);
    var pediuFronteiras = false;
    var pediuMunicipios = false;

    /* A divisão que veio da posição do aparelho fica trancada, e quem a larga é o
       gesto: um clique, um arrasto ou uma seta no mapa. O trinco é a resposta a uma
       medição — ninguém reescreve a elipsóide do GPS à mão —, e é reversível de
       propósito. Trancado sem volta, o município que o GPS atribuiu ao lado
       errado obrigava a recarregar a página, e o recarregamento levava atrás o
       título, a descrição e o dono já escritos.

       E é o gesto que levanta, não um botão: um botão de "editar" que fica ali
       depois de a equipa carregar em "usar a minha posição" outra vez, ou que se
       esquece ligado, é um estado que ninguém sabe em que está. A origem do ponto
       é a própria coisa que justifica o trinco, e quem a conhece é o código que
       largou o ponto. */
    var espelhos = [];

    function soltaDivisao() {
      espelhos.forEach(function (espelho) {
        if (espelho.parentNode) espelho.parentNode.removeChild(espelho);
      });
      espelhos = [];
      if (provinciaField) provinciaField.disabled = false;
      /* O município volta a editáveis nos dois sentidos: o `readonly` é
         reescrito, porque o `delete` do valor de um atributo é mais lento de
         ler do que um `false` e não muda o que o browser faz. */
      if (municipioField) {
        municipioField.disabled = false;
        municipioField.readOnly = false;
      }
      [provinciaField, municipioField].forEach(function (campo) {
        if (campo) campo.classList.remove('is-fixed');
      });
    }

    /* Trancar um campo é tirá-lo do pedido, e o `POST` é o que grava o imóvel.
       Um `disabled` sem espelho levava a província e o município embora o
       formulário fosse válido, e o sintoma seria um imóvel sem divisão depois de
       a página ter dito que estava tudo certo.

       Cada campo recebe o tratamento que o seu tipo permite: o `select` não tem
       `readonly`, e o `readonly` do texto viaja no pedido — por isso um é
       `disabled` com espelho e o outro é `readonly` sem espelho. Os dois ficam
       com a mesma aparência, e os dois continuam legíveis por quem usa leitor de
       ecrã: um campo desactivado some do alcance do teclado, e o valor que a
       equipa precisa de confirmar é precisamente o que não pode desaparecer. */
    function prendeDivisao(campo, modo) {
      if (!campo || !campo.value) return false;
      campo.classList.add('is-fixed');
      if (modo === 'readonly') {
        campo.readOnly = true;
        return true;
      }
      /* O espelho que já existe é reescrito em vez de duplicado. Uma segunda
         leitura do aparelho pode dar outra divisão, e um espelho com o valor
         antigo gravava a província errada sem nada na página dizer que estava
         errada. */
      var espelho = null;
      espelhos.forEach(function (procurado) {
        if (procurado.name === campo.name) espelho = procurado;
      });
      if (!espelho) {
        espelho = document.createElement('input');
        espelho.type = 'hidden';
        espelho.name = campo.name;
        if (campo.parentNode) campo.parentNode.insertBefore(espelho, campo.nextSibling);
        espelhos.push(espelho);
      }
      espelho.value = campo.value;
      campo.disabled = true;
      return true;
    }

    /* Cada campo prende-se só se o mapa o preencheu, e cada um por si. A fonte não
       desenha trinta e tal municípios (§2.12): um deles fica por escrever à mão
       mesmo com a província trancada ao lado, e o contrário também — trancar um
       campo vazio é um formulário que não sai e não tem como ser corrigido. */

    // Um grau por 1000 dá cerca de 111 m, que é afinar a esquina de um prédio.
    // Com Shift o passo é cinco vezes maior, para quem está a quarteirão.
    var PASSO = 1 / 1000;
    var PASSO_GRANDE = 1 / 5000;

    var latActual = numeroOu(latField.value, null);
    var lonActual = numeroOu(lonField.value, null);
    var temPin = latActual !== null && lonActual !== null;

    var opcoes = {
      center: temPin
        ? [latActual, lonActual]
        : [Number(config.centerLat), Number(config.centerLon)],
      zoom: temPin ? 16 : (Number(config.zoom) || 13),
      scrollWheelZoom: false,
      zoomAnimation: !reduceMotion,
      fadeAnimation: !reduceMotion
    };

    var map = L.map(container, opcoes);

    if (config.invertTiles) container.classList.add('echilo-map--invert');

    var failedTiles = 0;

    /* Os tiles e a permissão têm caixas separadas, e é a mesma razão que vale no
       catálogo: um 403 do fornecedor a apagar a recusa da geolocalização mandava
       a equipa seguir o diagnóstico errado. O `alert` é do fornecedor, o
       `status` é de quem pediu a posição. */
    function avisoDeTiles() {
      failedTiles += 1;
      if (!error || failedTiles < 2) return;
      error.dataset.origem = 'tiles';
      error.textContent = 'O mapa não carregou: o fornecedor de tiles recusou o pedido ('
        + failedTiles + ' tiles). Confirme a rede ou ajuste ECHILO_MAP_TILE_URL no .env. '
        + 'As coordenadas continuam a poder ser escritas à mão.';
      error.hidden = false;
    }

    if (config.configError) {
      /* Um `{key}` por substituir não se corrige no browser. Dizemos o que falta
         e seguimos sem imagem: o pin é uma coordenada, não um retrato. */
      if (error) {
        error.textContent = config.configError;
        error.hidden = false;
      }
    } else {
      var tiles = L.tileLayer(config.tileUrl, {
        attribution: config.attribution,
        maxZoom: 19
      });
      tiles.on('tileerror', avisoDeTiles);
      tiles.on('tileload', function () {
        // Só o aviso do fornecedor se apaga com um tile. A recusa da permissão
        // fica, porque um tile que carregou não a desfaz.
        if (failedTiles && error && error.dataset.origem === 'tiles') {
          error.hidden = true;
          failedTiles = 0;
        }
      });
      tiles.addTo(map);
    }

    var pin = null;

    /* O nome da divisão que o `select` escreve, lido das opções que o servidor
       desenhou. O `value` de um option que não existe deixa o campo vazio sem
       erro nenhum, e um campo que esvaziou sozinho parece um formulário partido. */
    function rotuloDaProvincia(codigo) {
      if (!provinciaField) return '';
      var opcoes = provinciaField.options || [];
      for (var i = 0; i < opcoes.length; i += 1) {
        if (opcoes[i].value === codigo) return opcoes[i].text;
      }
      return '';
    }

    /* Escrever a divisão nos campos. É a resposta do mapa, e o que ele não sabe
       não é escrito: um município que a fonte não desenha fica o que a equipa
       escreveu, e um ponto no mar deixa a divisão como estava, com a frase que
       diz porquê.

       `doDispositivo` diz de onde veio o ponto, e é o que decide se a divisão
       fica trancada: uma posição lida do aparelho é uma medição, e ninguém a
       reescreve a dedo; um clique é uma escolha. */
    function nomeiaDivisao(lat, lon, doDispositivo) {
      if (!divisao && !provinciaField && !municipioField) return;

      if (!divisoes.pronto()) {
        /* Um ficheiro que falta não pode levar a equipa a clicar outra vez: pede-se
           uma vez, e a resposta que vier — mesmo sem ficheiro — é a resposta. */
        if (!pediuFronteiras) {
          pediuFronteiras = true;
          divisoes.pedirProvincias(function () { nomeiaDivisao(lat, lon, doDispositivo); });
        }
        return;
      }

      var aqui = divisoes.divisoesDe(lat, lon);
      var codigos = (config.provinciasPorFronteira || {})[aqui.provincia] || [];
      var partes = [];
      var prendeuAlgum = false;

      if (codigos.length === 1 && provinciaField) {
        provinciaField.value = codigos[0];
        prendeuAlgum = prendeDivisao(provinciaField, 'disabled') || prendeuAlgum;
        /* O `change` não é enfeite: a lista de sugestões de município segue a
           província, e quem não a vê mudar fica com as sugestões da província
           anterior por baixo de um município que não é dela. O evento dispara-se
           depois do trinco, porque é o `select` trancado que fica a pedir as
           sugestões, e é o seu valor que as decide. */
        provinciaField.dispatchEvent(new Event('change', { bubbles: true }));
        partes.push('Província de ' + rotuloDaProvincia(codigos[0]) + '.');
      } else if (codigos.length > 1) {
        var nomes = [];
        codigos.forEach(function (codigo) { nomes.push(rotuloDaProvincia(codigo)); });
        partes.push('A divisão ' + aqui.provincia + ' é ' + nomes.join(' ou ')
          + ' na lista do projecto. Escolha a que o imóvel pertence: o mapa sabe o'
          + ' contorno, não a divisão do negócio.');
      }

      if (aqui.municipio && municipioField) {
        municipioField.value = aqui.municipio;
        prendeuAlgum = prendeDivisao(municipioField, 'readonly') || prendeuAlgum;
        municipioField.dispatchEvent(new Event('change', { bubbles: true }));
        partes.push('Município de ' + aqui.municipio + '.');
      } else if (aqui.provincia) {
        partes.push('O ponto não caiu dentro de nenhum município desenhado; escreva'
          + ' o município.');
      }

      if (!partes.length) {
        partes.push('O ponto caiu fora dos contornos desenhados de Angola. A'
          + ' província e o município ficam como estavam.');
      }

      if (doDispositivo && prendeuAlgum) {
        partes.push('Preenchido a partir da sua posição e trancado, porque uma'
          + ' posição lida do aparelho não se reescreve a dedo. Carregue no mapa'
          + ' se a divisão estiver errada.');
      }

      if (divisao) divisao.textContent = partes.join(' ');

      /* O município vem do ficheiro grande, e a equipa pode estar a olhar para o
         ponto antes de ele chegar. Sem esta espera, a primeira resposta ficava sem
         município para sempre — e pedir outra vez aqui, com o ficheiro já
         carregado, reentrava na função sem sair. */
      if (aqui.municipio || pediuMunicipios) return;
      pediuMunicipios = true;
      divisoes.pedirMunicipios(function () { nomeiaDivisao(lat, lon, doDispositivo); });
    }

    /* Seis casas nos campos, quatro na referência. Os campos vão para o
       `Decimal` e para a base de dados, e uma diferença de 0,0001° é pouco mais
       de dez metros; a referência é o identificador legível do pin (§2.3), e o
       exemplo do próprio AGENTS tem quatro.

       `doDispositivo` atravessa daqui para a divisão porque é a origem do ponto
       que decide se a divisão fica trancada, e a resposta a essa pergunta é a
       mesma para todos os gestos. */
    function escrevePonto(lat, lon, origem, precisao, doDispositivo) {
      latField.value = lat.toFixed(6);
      lonField.value = lon.toFixed(6);
      if (refField) refField.value = lat.toFixed(4) + ',' + lon.toFixed(4);
      /* A precisão só se escreve quando o browser a sabe: um clique no mapa não
         tem precisão, e fingir que tem seria inventar o número que a equipa usa
         para medir a fiabilidade da inspecção. */
      if (accuracyField && precisao) accuracyField.value = Math.round(precisao);
      if (status) {
        status.textContent = 'Pin em ' + lat.toFixed(4) + ', ' + lon.toFixed(4)
          + ' — ' + origem + '.'
          + (precisao ? ' Precisão do navegador: ' + Math.round(precisao) + ' m.'
            : ' A precisão fica por confirmar pela equipa.');
      }
      /* Chamar daqui e não de cada gesto é o que garante que clique, arrasto,
         setas e geolocalização escrevem a divisão todas: um caminho que se
         esquecesse deixaria o pin mudado e a divisão antiga, que é a combinação
         que ninguém percebe. */
      nomeiaDivisao(lat, lon, doDispositivo);
    }

    function novoPin(lat, lon) {
      if (pin) {
        pin.setLatLng([lat, lon]);
        return pin;
      }
      pin = L.marker([lat, lon], {
        draggable: true,
        autoPan: true,
        keyboard: false,
        // O pin da equipa não é uma cor de catálogo: dourado é arrendar e verde é
        // vender, e aqui não há finalidade a comunicar.
        icon: L.divIcon({
          className: 'pin-map__ponto',
          html: '<span aria-hidden="true"></span>',
          iconSize: [18, 18],
          iconAnchor: [9, 9]
        })
      }).addTo(map);
      pin.on('dragend', function () {
        var p = pin.getLatLng();
        escrevePonto(p.lat, p.lng, 'arrastado no mapa', null, false);
      });
      return pin;
    }

    function larga(lat, lon, origem, precisao, doDispositivo) {
      if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return;
      novoPin(lat, lon);
      /* Um ponto posto à mão destranca a divisão: o trinco respondia à
         posição do aparelho, e a mão é outra fonte. */
      if (!doDispositivo) soltaDivisao();
      escrevePonto(lat, lon, origem, precisao, doDispositivo);
    }

    /* Um clique longe do pin arrasta-o para lá, e um clique sobre o pin não o
       duplica. Sem esta distinção, largar o pin duas vezes no mesmo sítio
       deixava dois marcadores a dizerem a mesma coisa. */
    map.on('click', function (evento) {
      if (pin && evento.latlng && pin.getLatLng().distanceTo(evento.latlng) < 1) return;
      larga(evento.latlng.lat, evento.latlng.lng, 'clique no mapa', null, false);
    });

    /* As setas movem o pin, e não o mapa, quando já há pin. Sem pin, o teclado
       continua a fazer o que o Leaflet faz: deslocar a vista para onde não há
       nada para marcar. Inventar um pin a meio de Angola por causa de uma
       seta seria pior do que não fazer nada. */
    container.addEventListener('keydown', function (evento) {
      var delta = evento.shiftKey ? PASSO_GRANDE : PASSO;
      var passos = {
        ArrowUp: [delta, 0],
        ArrowDown: [-delta, 0],
        ArrowLeft: [0, -delta],
        ArrowRight: [0, delta]
      };
      var passo = passos[evento.key];
      if (!passo) return;

      if (!pin) return;
      evento.preventDefault();
      var p = pin.getLatLng();
      largo(Math.max(-90, Math.min(90, p.lat + passo[0])),
        Math.max(-180, Math.min(180, p.lng + passo[1])),
        evento.shiftKey ? 'setas com Shift' : 'setas do teclado',
        null, false);
    });

    function usarPosicao() {
      if (locateButton) {
        locateButton.disabled = true;
        locateButton.setAttribute('aria-busy', 'true');
      }
      if (aviso) aviso.textContent = 'A pedir a sua posição…';

      function terminou() {
        if (locateButton) {
          locateButton.disabled = false;
          locateButton.removeAttribute('aria-busy');
        }
      }

      if (!navigator.geolocation) {
        terminou();
        if (aviso) {
          /* A geolocalização só existe em contexto seguro, e dizer porquê vale
             mais do que um botão que nunca responde. */
          aviso.textContent = 'Este site não está em HTTPS, e só um site em HTTPS '
            + 'pode ler a sua localização. Carregue no mapa.';
        }
        return;
      }

      navigator.geolocation.getCurrentPosition(
        function (posicao) {
          var lat = posicao.coords.latitude;
          var lon = posicao.coords.longitude;
          map.setView([lat, lon], Math.max(map.getZoom(), 16));
          /* A mensagem de "a pedir a sua posição" apaga-se antes de o pin entrar.
             Ao contrário, o `larga` escrevia a divisão e esta linha apagava-a a
             seguir, e a equipa ficava com o pin mudado e a divisão antiga. */
          if (aviso) aviso.textContent = '';
          larga(lat, lon, 'a sua posição actual', posicao.coords.accuracy, true);
          terminou();
        },
        function (erroGeo) {
          terminou();
          /* A recusa é escrita no `status` e não no `alert`: o `alert` é do
             fornecedor de tiles, e trocar as caixas fazia a equipa seguir a pista
             errada. Todas as terminações voltam a carregar no mapa, que não
             depende de ninguém. */
          if (aviso) aviso.textContent = motivoDaRecusa(erroGeo, 'carregue no mapa');
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
      );
    }

    if (locateButton) locateButton.addEventListener('click', usarPosicao);

    if (temPin) novoPin(latActual, lonActual);

    window.addEventListener('resize', function () { map.invalidateSize(); });
    document.body.addEventListener('htmx:afterSettle', function () { map.invalidateSize(); });
  }

  function numeroOu(valor, quandoVazio) {
    if (valor === null || valor === undefined) return quandoVazio;
    var limpo = String(valor).trim().replace(',', '.');
    if (limpo === '') return quandoVazio;
    var n = Number(limpo);
    return isNaN(n) ? quandoVazio : n;
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

  /* As divisões de Angola, e a única coisa que o mapa sabe dizer sobre elas.

     Isto responde a uma pergunta — que província e que município são este ponto
     — e não desenha nada. O desenho é de quem chama: o catálogo põe os contornos
     por baixo dos pinos, e o seletor de pin não precisa de arame nenhum para
     saber em que divisão está a casa que a equipa está a marcar.

     Vive fora do `setupFronteiras` porque as duas páginas com mapa precisam da
     resposta e só uma precisa do desenho. Duas redações do mesmo
     ponto-em-polígono divergem, e a segunda é a que ninguém actualiza. */
  function divisoesDoMapa(config) {
    var provincias = null;
    var municipios = null;
    var aPedir = { provincias: false, municipios: false };
    /* As perguntas que ainda não têm resposta. Carregar o ficheiro dos municípios
       leva tempo, e quem clica no pin nesse intervalo fez uma pergunta que só se
       responde depois: sem esta fila, o primeiro clique ficava sem nome e o
       segundo preenchia o campo com o município do ponto anterior. */
    var pendentes = [];

    function responde() {
      var aChamar = pendentes;
      pendentes = [];
      aChamar.forEach(function (fn) { fn(); });
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
       camada. O índice anterior fecha o anel no fim, senão o último segmento não
       conta.

       A geometria vem em `[lon, lat]`, a ordem do GeoJSON, e o `x` é a longitude.
       O `properties.ponto` vem ao contrário, em `[lat, lon]`, porque esse é o
       `L.circleMarker` que o recebe. São dois contratos diferentes no mesmo
       ficheiro, e trocar um deles punha o mapa a dizer que Luanda estava no mar. */
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

    /* A província vem do município, não do contorno provincial.

       As duas camadas da fonte não coincidem: o município de Luanda cobre a baía e
       o centro, e o contorno provincial da fonte tem esse recorte a menos. Perguntar
       à província sozinha dava "fora dos contornos" com o ponto no meio de Luanda,
       que é a última coisa que se quer ler. Derivada do município, as duas
       respostas concordam por construção. O contorno provincial continua a ser o que
       se desenha. */
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
      if (!provincias) return nome;
      provincias.features.forEach(function (p) {
        if (!nome && dentroDe(lat, lon, aneisDe(p))) nome = p.properties.nome;
      });
      return nome;
    }

    function nomeDaProvincia(codigo) {
      var nome = '';
      if (!provincias) return nome;
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

    /* Os ficheiros são um extra: perdê-los é chato, e a página não pode ir atrás
       por isso. A falha chama as perguntas na mesma, para que cada mapa decida o
       que faz sem resposta em vez de repetir o pedido para sempre. */
    function carrega(url, quandoChegar) {
      fetch(url)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (dados) { if (dados) quandoChegar(dados); })
        .catch(function () { })
        .then(responde);
    }

    /* As províncias vêm no primeiro carregamento porque são 73 KB e porque uma
       província é a resposta que quase toda a gente quer. Os municípios vão num
       ficheiro separado, pedido só quando a pergunta já não se responde sem eles:
       556 KB para 157 contornos que ninguém ia ver a zoom 9. */
    function pedirProvincias(quando) {
      if (provincias) { if (quando) quando(); return; }
      if (quando) pendentes.push(quando);
      if (aPedir.provincias) return;
      aPedir.provincias = true;
      carrega(config.provinciasUrl, function (dados) { provincias = dados; });
    }

    function pedirMunicipios(quando) {
      if (municipios) { if (quando) quando(); return; }
      if (quando) pendentes.push(quando);
      if (aPedir.municipios) return;
      aPedir.municipios = true;
      carrega(config.municipiosUrl, function (dados) { municipios = dados; });
    }

    return {
      divisoesDe: divisoesDe,
      provincias: function () { return provincias; },
      municipios: function () { return municipios; },
      pronto: function () { return provincias !== null; },
      pedirProvincias: pedirProvincias,
      pedirMunicipios: pedirMunicipios
    };
  }

  /* A camada administrativa: os contornos que o mapa desenha por baixo dos pinos.

     Um mapa sem nomes obriga o utilizador a adivinhar. Vê um círculo e não sabe
     se pegou em Viana ou em Talatona, e a única forma de saber é sair do mapa e
     procurar. As etiquetas respondem a isso.

     O desenho é só daqui. Saber que divisão é um ponto é de `divisoesDoMapa`, e
     o seletor de pin usa essa resposta sem levar nenhum destes arames. */
  function setupFronteiras(map, config, circuloActual, divisoes) {
    var container = map.getContainer();

    /* As linhas vão para um pane próprio, abaixo dos pinos. No `overlayPane` de
       um Leaflet normal, que está acima dos marcadores, o contorno de uma
       fronteira tapava o pino que estava a descrever. */
    map.createPane('limites');
    map.getPane('limites').style.zIndex = 350;
    map.getPane('limites').style.pointerEvents = 'none';

    var linhas = {};

    /* O ficheiro é uma `FeatureCollection`, e o Leaflet sabe desenhá-la. Cada
       contorno recebe a sua classe: os municípios precisam de desaparecer quando
       se afasta, e 157 linhas que não se apagam enchem o mapa de arame de Angola a
       ver o país inteiro.

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

    /* O nome do círculo escreve-se no elemento que o catálogo traz no HTML. As
       duas páginas partilham esta função e só uma tem onde mostrar a resposta, e
       escrevê-la na mesma é o que evita a segunda pessoa tentar mostrar uma
       resposta num elemento que não existe. */
    function escreve(texto) {
      var contexto = document.getElementById('area-fraccao');
      if (contexto) contexto.textContent = texto;
    }

    /* O que o círculo apanha. A província vem do centro: é a resposta que se espera
       e não depende de o raio cair dentro ou fora de uma fronteira. Os municípios
       listam-se por rótulo dentro do raio, o que permite dizer "inclui Talatona"
       num círculo de 20 km em vez de escolher um e esconder o resto. */
    function nomeia() {
      if (!divisoes.pronto()) return;
      var circulo = circuloActual();
      var municipios = divisoes.municipios();

      if (!circulo) {
        var centro = map.getCenter();
        var aqui = divisoes.divisoesDe(centro.lat, centro.lng);
        var texto = aqui.provincia ? 'A mostrar: província de ' + aqui.provincia : '';
        if (aqui.municipio) texto += ', município de ' + aqui.municipio;
        escreve(texto ? texto + '.' : '');
        return;
      }

      var ponto = circulo.getLatLng();
      var raio = circulo.getRadius();

      if (!municipios) {
        escreve('A carregar os municípios deste círculo.');
        return;
      }

      // O número de divisões que o mapa desenha e o número de províncias que o
      // projecto tem não são o mesmo número, e este texto não deve prometer nenhum
      // dos dois: o que o mapa sabe é se o ponto caiu dentro de algum contorno, e é
      // isso que ele diz.
      var aqui = divisoes.divisoesDe(ponto.lat, ponto.lng);
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
      escreve(texto + '.');
    }

    function põeMunicipios(visivel) {
      if (!linhas.municipios) return;
      if (visivel) {
        linhas.municipios.linhas.addTo(map);
        linhas.municipios.rotulos.addTo(map);
      } else {
        map.removeLayer(linhas.municipios.linhas);
        map.removeLayer(linhas.municipios.rotulos);
      }
    }

    /* A camada dos municípios entra uma vez, e é preciso no primeiro `zoomend`,
       que acontece antes de alguém pedir nada. */
    function desenhaMunicipios() {
      if (linhas.municipios || !divisoes.municipios()) return false;
      linhas.municipios = desenha(
        divisoes.municipios(), 'divisao-label divisao-label--municipio'
      );
      return true;
    }

    function talvezMunicipios() {
      var perto = map.getZoom() >= (config.zoomMunicipios || 10);
      container.classList.toggle('zoom-municipios', perto);
      if (linhas.municipios) põeMunicipios(perto);
      if (perto) {
        divisoes.pedirMunicipios(function () {
          if (desenhaMunicipios()) põeMunicipios(true);
          nomeia();
        });
      }
    }

    divisoes.pedirProvincias(function () {
      if (!divisoes.provincias() || linhas.provincias) return;
      linhas.provincias = desenha(
        divisoes.provincias(), 'divisao-label divisao-label--provincia'
      );
      talvezMunicipios();
    });

    map.on('zoomend', talvezMunicipios);
    map.on('moveend', nomeia);

    /* Desenhar o círculo também traz os municípios. Quem escolhe uma área está a
       perguntar exactamente por que municípios ela cobre, e esperar por um zoom de
       fora responder à pergunta que ele já fez é deixá-lo à espera. */
    return {
      nomeia: nomeia,
      pedirMunicipios: function () {
        container.classList.add('zoom-municipios');
        divisoes.pedirMunicipios(function () {
          if (desenhaMunicipios()) põeMunicipios(true);
          nomeia();
        });
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

  /* Reduz o lote de fotografias antes de o enviar (§2.1).

     O formulário aceita até MAX_FOTOS fotografias de LIMITE_GB cada, e um lote
     dessas não cabe no pedido que a Vercel aceita: a edge recusa corpos acima de
     4,5 MB antes de o Django ver a requisição, e a resposta é um 413 sem página
     nem traceback. Não há setting que abra aquilo, e a validação do Django nunca
     chega a correr — o pedido morre na plataforma, um tecto antes do tecto.

     Por isso o peso resolve-se aqui, no browser, que é o único sítio onde o
     ficheiro ainda está inteiro. O `ORCAMENTO_LOTE_MB` e o `LADO_MAXIMO_CLIENTE`
     chegam pelo `data-` do formulário e não são repetidos aqui: um número
     escrito em dois sítios diverge, e o que diverge é o limite.

     O que se perde: o tamanho. O que fica: a fotografia, e a ordem de escolha,
     que é a ordem do array e é o que decide a capa. E o que este código não faz
     é decidir se uma fotografia entra — quem valida é o servidor, ficheiro a
     ficheiro, e quem recusa diz porquê. */
  var QUALIDADES = [0.82, 0.72, 0.65, 0.58, 0.52, 0.45, 0.35];
  var ESCALAS = [1, 1, 0.875, 0.75, 0.625, 0.56, 0.45];
  var LADO_MINIMO = 560;

  function mbParaBytes(mb) {
    return Math.floor(mb * 1024 * 1024);
  }

  // A vírgula decimal é a de Angola (§2.13): o que o browser escreve num
  // `textContent` é lido pela equipa, e «3.5 MB» ao lado de «3,5 MB» na ajuda
  // do campo lê-se como duas medidas diferentes.
  function mbLegivel(bytes) {
    return (bytes / (1024 * 1024)).toFixed(1).replace('.', ',') + ' MB';
  }

  function nomeJpeg(original, usados) {
    // O conteúdo passa a JPEG e o nome tem de dizer JPEG: o ficheiro é procurado
    // pelo nome, e uma `sala.png` com bytes JPEG é uma fotografia que não
    // reconhece quem a volta a abrir. O sufixo resolve a colisão de `sala.png`
    // com `sala.jpg` escolhidos no mesmo lote.
    var base = String(original || 'fotografia').replace(/\.[^.]+$/, '') || 'fotografia';
    var nome = base + '.jpg';
    var n = 2;
    while (usados[nome]) {
      nome = base + '-' + n + '.jpg';
      n += 1;
    }
    usados[nome] = true;
    return nome;
  }

  function descodificar(ficheiro) {
    if (typeof createImageBitmap === 'function') {
      return createImageBitmap(ficheiro).then(function (bitmap) {
        return { largura: bitmap.width, altura: bitmap.height, fonte: bitmap };
      });
    }
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(ficheiro);
      var img = new Image();
      img.onload = function () {
        URL.revokeObjectURL(url);
        resolve({ largura: img.naturalWidth, altura: img.naturalHeight, fonte: img });
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error('a imagem não carregou'));
      };
      img.src = url;
    });
  }

  function pintar(origem, largura, altura, qualidade) {
    var canvas = document.createElement('canvas');
    canvas.width = largura;
    canvas.height = altura;
    var ctx = canvas.getContext('2d');
    // O JPEG não tem canal alfa: sem este fundo, o que era transparente sai
    // preto, e uma fotografia com um recorte sobre-transparent é o caso em que
    // isso se nota.
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, largura, altura);
    ctx.drawImage(origem.fonte, 0, 0, largura, altura);
    return new Promise(function (resolve, reject) {
      if (!canvas.toBlob) {
        reject(new Error('o canvas não sabe exportar'));
        return;
      }
      canvas.toBlob(function (blob) {
        if (blob) resolve(blob);
        else reject(new Error('a exportação veio vazia'));
      }, 'image/jpeg', qualidade);
    });
  }

  function reencodar(ficheiro, limite, ladoMaximo) {
    // Já cabe: segue como está. Re-codificar o que já cabe só perderia qualidade,
    // e o `lado_maximo` do servidor continua a ser o dele a dizer.
    if (ficheiro.size <= limite) return Promise.resolve(ficheiro);

    return descodificar(ficheiro).then(function (origem) {
      var i = 0;
      var melhor = null;

      function degrau() {
        if (i >= QUALIDADES.length) return Promise.resolve(melhor);
        var lado = Math.max(LADO_MINIMO, Math.round(ladoMaximo * ESCALAS[i]));
        var reducao = Math.min(1, lado / Math.max(origem.largura, origem.altura));
        var largura = Math.max(1, Math.round(origem.largura * reducao));
        var altura = Math.max(1, Math.round(origem.altura * reducao));
        return pintar(origem, largura, altura, QUALIDADES[i]).then(function (blob) {
          if (!melhor || blob.size < melhor.size) melhor = blob;
          // O primeiro degrau que cabe é o que fica: mais agressivo custaria
          // qualidade a uma fotografia que não precisava de a perder.
          if (blob.size <= limite) return melhor;
          i += 1;
          return degrau();
        });
      }

      return degrau().then(function (blob) {
        // A imagem descodificada fica em memória até o browser a libertar, e
        // quinze ao mesmo tempo é a diferença entre um lote que passa e um
        // separador que morre.
        if (origem.fonte && typeof origem.fonte.close === 'function') {
          origem.fonte.close();
        }
        return blob || ficheiro;
      });
    });
  }

  function substituirFicheiros(input, ficheiros) {
    // O `DataTransfer` é a única forma de escrever no `files` de um input: o
    // atributo é de leitura para o script. Onde não existe — Safari antigo — a
    // resposta é `false` e quem chama diz o que fazer, em vez de deixar passar um
    // pedido que a plataforma recusa sem explicar.
    if (typeof DataTransfer !== 'function') return false;
    try {
      var transporte = new DataTransfer();
      ficheiros.forEach(function (f) {
        transporte.items.add(f);
      });
      input.files = transporte.files;
      // A atribuição pode ser aceite em silêncio e não escrever nada, por isso
      // o que se confirma é o que ficou no input, e não o que se mandou fazer.
      return input.files.length === ficheiros.length;
    } catch (erro) {
      return false;
    }
  }

  function criarEstado(input) {
    // A mensagem nasce depois do texto de ajuda e não antes: o `aria-describedby`
    // do input aponta para a ajuda, e o campo não deve anunciar o estado do
    // envio antes de dizer o que é o campo.
    var alvo = input;
    var descritos = (input.getAttribute('aria-describedby') || '').split(/\s+/);
    for (var i = 0; i < descritos.length; i += 1) {
      var apontado = document.getElementById(descritos[i]);
      if (apontado) {
        alvo = apontado;
        break;
      }
    }
    var p = document.createElement('p');
    p.hidden = true;
    alvo.parentNode.insertBefore(p, alvo.nextSibling);

    return {
      preparar: function () {
        p.textContent = '';
        p.hidden = true;
        p.removeAttribute('role');
        p.className = 'field__help';
      },
      // O progresso não entra num papel que o leitor de ecrã anuncie: «3 de 12»,
      // «4 de 12», «5 de 12» lidos em voz alta são três interrupções para repetir
      // o que o número já mostra no ecrã.
      dizer: function (texto) {
        p.textContent = texto;
        p.className = 'field__help';
        p.hidden = false;
      },
      terminar: function (texto, urgente) {
        p.textContent = texto;
        p.className = urgente ? 'field__error' : 'field__help';
        p.setAttribute('role', urgente ? 'alert' : 'status');
        p.hidden = false;
      }
    };
  }

  function setupCompressaoLote() {
    var formularios = document.querySelectorAll('form[data-orcamento-mb]');
    if (!formularios.length) return;

    Array.prototype.forEach.call(formularios, function (form) {
      // O `name` vem do formulário porque este redutor não é só das fotografias
      // dos imóveis: o retrato do perfil reduz-se com a mesma política, e o campo
      // chama-se `photo`. Um nome escrito aqui transformava o retrato num
      // ficheiro que entrava sem ser reduzido — que é um 413 à espera, e um 413
      // que o formulário aceitaria porque o limite de validação é outro.
      var nomeInput = form.getAttribute('data-input-ficheiros') || 'images';
      var input = form.querySelector('input[type="file"][name="' + nomeInput + '"]');
      if (!input) return;
      var orcamento = mbParaBytes(parseFloat(form.getAttribute('data-orcamento-mb')) || 0);
      var ladoMaximo = parseInt(form.getAttribute('data-lado-maximo'), 10) || 0;
      if (!orcamento) return;
      // Um ficheiro só diz-se ao singular. «As 1 fotografias ainda somam» é uma
      // frase que se lê como defeito, e é a que a pessoa que vai ver a fotografia
      // de perfil do primeiro perfil do sítio vai ler.
      var plural = form.getAttribute('data-nome-plural') || 'fotografias';
      var singular = form.getAttribute('data-nome-singular')
        || plural.replace(/s$/, '');
      var aTratar = false;

      form.addEventListener('submit', function (evento) {
        // A guarda é o que impede o ciclo: o `form.submit()` de baixo não
        // dispara este evento, mas o browser a validar no primeiro toque devolve
        // o formulário ao mesmo caminho, e sem isto o que resolve o peso do lote
        // voltava a entrar nele.
        if (aTratar) return;
        var ficheiros = Array.prototype.slice.call(input.files || []);
        if (!ficheiros.length) return;

        var total = ficheiros.reduce(function (soma, f) {
          return soma + f.size;
        }, 0);
        // Cabe no pedido: não há peso a corrigir e nada a dizer à equipa.
        if (total <= orcamento) return;

        evento.preventDefault();
        aTratar = true;
        var botao = form.querySelector('button[type="submit"]');
        if (botao) botao.disabled = true;
        var estado = criarEstado(input);
        estado.preparar();

        var porFicheiro = Math.floor(orcamento / ficheiros.length);
        var usados = {};
        var reduzidos = [];
        var indice = 0;
        var umSo = ficheiros.length === 1;
        var nomeFicheiro = umSo ? singular : plural;

        function proximo() {
          if (indice >= ficheiros.length) return Promise.resolve();
          var original = ficheiros[indice];
          indice += 1;
          estado.dizer(umSo
            ? 'A reduzir a ' + nomeFicheiro + '…'
            : 'A reduzir as ' + nomeFicheiro + ': ' + indice + ' de '
              + ficheiros.length + '.');
          return reencodar(original, porFicheiro, ladoMaximo).then(function (saida) {
            reduzidos.push(saida === original
              ? original
              : new File([saida], nomeJpeg(original.name, usados), { type: 'image/jpeg' }));
          }).catch(function () {
            // Um ficheiro que este browser não conseguiu abrir segue como estava.
            // Quem decide se uma fotografia entra é o servidor, e a recusa dele
            // diz qual é o problema: um ficheiro mau não leva o lote consigo, e
            // esta fotografia não entra nessa regra só por ter falhado aqui.
            reduzidos.push(original);
          }).then(proximo);
        }

        proximo().then(function () {
          var final = reduzidos.reduce(function (soma, f) {
            return soma + f.size;
          }, 0);
          aTratar = false;
          if (final > orcamento) {
            if (botao) botao.disabled = false;
            estado.terminar(umSo
              ? 'A ' + nomeFicheiro + ' ainda tem ' + mbLegivel(final)
                + ' e o envio aceita ' + mbLegivel(orcamento)
                + '. Escolha uma imagem mais leve.'
              : 'As ' + reduzidos.length + ' ' + nomeFicheiro + ' ainda somam '
                + mbLegivel(final) + ' e o envio aceita ' + mbLegivel(orcamento)
                + '. Escolha menos fotografias, ou menos de cada vez.', true);
            return;
          }
          if (!substituirFicheiros(input, reduzidos)) {
            if (botao) botao.disabled = false;
            estado.terminar('Este navegador não deixa trocar as '
              + nomeFicheiro + ' escolhidas. Carregue menos de cada vez.', true);
            return;
          }
          estado.terminar(umSo
            ? nomeFicheiro.charAt(0).toUpperCase() + nomeFicheiro.slice(1)
              + ' reduzida de ' + mbLegivel(total) + ' para ' + mbLegivel(final)
              + '. A enviar…'
            : nomeFicheiro.charAt(0).toUpperCase() + nomeFicheiro.slice(1)
              + ' reduzidas de ' + mbLegivel(total) + ' para ' + mbLegivel(final)
              + '. A enviar…');
          form.submit();
        });
      });
    });
  }

  function ready() {
    setupBurger();
    setupScopeTabs();
    setupGallery();
    setupChatSuggestions();
    setupChat();
    setupFieldToggles();
    setupMunicipalityOptions();
    setupReveal();
    setupAreaSearch();
    setupPinPicker();
    setupConfirmacoes();
    setupCardCarousels();
    setupCompressaoLote();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }

  // Os swaps do HTMX trocam listas inteiras: o que entra tem de ser observado.
  document.body.addEventListener('htmx:afterSwap', setupReveal);
  document.body.addEventListener('htmx:afterSettle', setupReveal);
  document.body.addEventListener('htmx:afterSwap', onChatSwap);
  document.body.addEventListener('htmx:beforeRequest', function (event) {
    setBusy(event, true);
  });
  ['htmx:afterSettle', 'htmx:responseError', 'htmx:sendError'].forEach(function (name) {
    document.body.addEventListener(name, function (event) {
      setBusy(event, false);
    });
  });
})();
