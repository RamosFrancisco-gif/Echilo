# Bibliotecas de terceiros

Nenhuma biblioteca vem de CDN. Todas estão vendorizadas aqui, com a versão
fixa, para que o deploy não dependa da disponibilidade de terceiros nem de um
build step.

Um CDN não é só um risco de indisponibilidade. A protecção contra tracking do
Edge bloqueia o armazenamento de scripts de terceiros e deixa avisos no
`console`; em definições mais restritivas, chega a não executar o script. Como
o HTMX é o que faz os filtros e a pesquisa por área funcionarem, perdê-lo
partia o catálogo.

| Biblioteca | Versão | Ficheiros | Licença |
| --- | --- | --- | --- |
| HTMX | 1.9.12 | `htmx/1.9.12/htmx.min.js` | BSD-2-Clause |
| Leaflet | 1.9.4 | `leaflet/1.9.4/` | BSD-2-Clause |
| Leaflet.draw | 1.0.4 | `leaflet-draw/1.0.4/` | MIT |

`geo/` não é uma biblioteca, mas vive aqui pela mesma razão: dados de terceiro,
com versão fixa, sem CDN e com a licença escrita.

| Ficheiro | Fonte | Licença |
| --- | --- | --- |
| `geo/angola-provincias.json` | geoBoundaries `gbOpen` ADM1, commit `9469f09` | CC BY 4.0 |
| `geo/angola-municipios.json` | geoBoundaries `gbOpen` ADM2, commit `9469f09` | CC BY 4.0 |

Não se editam à mão. `python manage.py build_admin_boundaries` volta a gerá-los a
partir da fonte, e `--check` falha se os ficheiros versionados já não baterem com
ela. As decisões que o comando toma — commit fixo, simplificação e atribuição de
município a província por ponto-em-polígono — estão explicadas no topo do próprio
comando. A simplificação não é a mesma nas duas camadas: 223 m nos municípios,
1,1 km nas províncias, porque uma província tem 200 km de lado e 1,1 km de desvio
não se vê. A atribuição usa 5,5 m, bem mais fino, porque aí um erro põe o
município na província errada.

### Ordem das coordenadas

O `geometry.coordinates` de cada `Feature` está em `[lon, lat]`, como manda a
RFC 7946 e como o `L.geoJSON` lê. O `properties.ponto` está ao contrário, em
`[lat, lon]`, porque é o `L.circleMarker` que o recebe. São dois contratos
diferentes no mesmo ficheiro, e cada um tem a sua regra: a geometria desenhada,
o ponto que ancora o nome. Trocar um deles faz o mapa desenhar Angola no
Atlântico a oeste, ou escrever os nomes no mar, e nenhum dos dois erros se
denuncia — o `path` conta, o DOM está lá, a consola cala-se. O comando converte
em `geometria()` e desfaz em `aneis_de()`; o JavaScript faz o mesmo em
`dentroDe()`.

### O que a fonte não dá

A lista de municípios do projecto tem 171 entradas e a camada municipal da fonte
tem 157 divisões. Não são a mesma coisa: 33 municípios da lista ficam sem
contorno e sem nome no mapa, entre eles `Catumbela`, `Quiculungo`, `Chicala-Choloanga`,
`Cacula`, todos os seis de `Icolo e Bengo`, os cinco de `Moxico Leste` e os sete
municípios urbanos de Luanda, que o geoBoundaries desenha como um só. É um
facto sobre a fonte, não uma escolha, e o comando imprime a lista completa de
cada vez que corre.

A camada provincial tem o mesmo furo, e maior: a lista do projecto tem vinte e
uma entradas e a fonte desenha dezoito divisões. `Icolo e Bengo` e `Moxico Leste`
não têm contorno provincial. `Cuando` e `Cubango` são duas entradas da lista do
projecto e uma só divisão da fonte, `Cuando Cubango`, desenhada uma vez sob o
código `CUANDO`; a correspondência está escrita à mão em
`PROVINCE_BOUNDARY_CODES`, porque não é dedutível e muda quando a fonte muda.

Nada disto é tapado com geometria inventada. Um contorno emprestado seria uma
linha a dizer que a província é outra, e o filtro a oferecer uma divisão que o
mapa nega. Onde a fonte cala, o mapa cala.

A CC BY 4.0 exige atribuição, e a atribuição que fica num README não cumpre a
licença: quem vê o mapa tem de a ver no mapa. Ela está no painel do catálogo,
em `templates/properties/property_list.html`, e um teste compara a licença escrita
com a que os ficheiros declaram.

`sha256` do HTMX 1.9.12, para confirmar que o ficheiro não foi trocado:

```
449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452  htmx.min.js
```

As folhas de estilo das duas bibliotecas do mapa referenciam as imagens por
caminho relativo (`images/spritesheet.png` na barra de desenho,
`images/layers.png` no controlo de camadas). As imagens estão incluídas pela
mesma razão: sem elas a barra de ferramentas de desenhar aparece sem símbolos.

### A directiva `sourceMappingURL` do Leaflet foi removida

O `leaflet.js` vendorizado vinha com uma directiva `sourceMappingURL` no fim do
ficheiro, e o `.map` que ela aponta **não** está vendorizado: pesa quase um
megabyte e só serve a quem está a depurar o Leaflet no devtools. A referência foi
retirada do ficheiro, e a decisão tem duas consequências que vale a pena saber:

- Um `.map` em falta é um 404 na consola do browser em **cada** página com mapa.
- Pior: em produção o `CompressedManifestStaticFilesStorage` não perdoa
  ficheiros em falta e o `collectstatic` **falha**, o que faz o build da Vercel
  falhar. O sintoma é um erro do `whitenoise` a falar de um
  `leaflet.js.map` que ninguém sabe porque está ali.

Portanto, ao actualizar a versão do Leaflet, a directiva volta a entrar e tem de
sair outra vez. `apps.core.test_deploy.BuildEstaticoTests` corre o
`collectstatic` com as settings de produção e apanha o caso se alguém se
esquecer.

## Ordem de carregamento

As bibliotecas entram no `head` de `templates/base.html`, **antes** de
`static/js/echilo.js`, e todas com `defer`. Com `defer`, o nosso `ready()`
dispara de imediato e uma biblioteca vinda depois já não estaria carregada: o
mapa não apareceria, sem erro na consola. `base_auth.html` tem um `head`
independente e não carrega HTMX de propósito: o login e o registo são formulários
normais, sem `hx-`.

## Actualizar uma versão

1. Substituir os ficheiros em `static/vendor/<biblioteca>/<versão>/`.
2. Actualizar o caminho no template que a carrega
   (`templates/base.html` para o HTMX,
   `templates/properties/property_list.html` para o Leaflet).
3. Actualizar a tabela acima e o `sha256`.
4. Apagar a versão antiga.

O `{% static %}` é obrigatório nos templates: em produção o
`CompressedManifestStaticFilesStorage` hasheia os nomes, e um caminho escrito à
mão não resolve.

## O que não é uma dependência

Os *tiles* não são vendorizados e não podem ser. O fornecedor vem das settings
(`ECHILO_MAP_TILE_URL`): em desenvolvimento serve a imagem aérea do Esri, sem
chave, mas nenhum basemap gratuito aguenta tráfego de produção. Depois de mudar
essa URL, `python manage.py verify_map_tiles` diz se o fornecedor devolve um mapa
ou um cartaz de "API KEY REQUIRED". Ver `AGENTS.md` §2.12.
