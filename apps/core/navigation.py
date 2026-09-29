"""A árvore de menus, montada a partir do perfil de quem entra (§3).

A navegação é a única parte da interface que responde a "o que é que esta pessoa
pode fazer", e a resposta estava escondida em dez `{% if %}` que ninguém conta.
O resultado é o que a equipa descreveu: a mesma barra para toda a gente, sem a
entrada de curadoria e sem a de contas, e um endereço que existe mas não tem por
onde se chegar.

Por isso a árvore vive aqui e não no template. Cada entrada declara com que
perfil aparece, lida do mesmo sítio que a view usa, para o menu e a página não
poderem discordar sobre o que uma pessoa pode fazer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class MenuLink:
    """Uma folha da navegação: um destino e o texto que o nomeia."""

    label: str
    url_name: str
    # Filtros do catálogo. Vão colados ao URL porque a entrada é o mesmo
    # destino com outra pergunta, e o menu tem de mostrar a pergunta.
    query: str = ""
    # Nome da view que marca a entrada como "a página onde estou". Vazio quando o
    # destino não é uma página com nome próprio.
    match: str = ""


@dataclass(frozen=True)
class MenuGroup:
    """Um ramo da navegação que existe para ter subentradas."""

    label: str
    links: list[MenuLink] = field(default_factory=list)
    # Prefixo comum aos `match` das filhas, para o ramo saber quando está
    # sobre a página actual sem olhar para o URL.
    match_prefix: str = ""


# A navegação pública. É igual para toda a gente, e é igual para quem está
# dentro: um cliente e um administrador procuram o mesmo imóvel pelas mesmas
# três portas. O que muda é o que vem a seguir.
PUBLICO: tuple[MenuLink, ...] = (
    MenuLink("Início", "properties:home", match="home"),
    MenuLink("Arrendar", "properties:property_list", match="property_list", query="?purpose=RENT"),
    MenuLink("Comprar", "properties:property_list", match="property_list", query="?purpose=SALE"),
    MenuLink("Terrenos", "properties:property_list", match="property_list", query="?type=LAND"),
    # A âncora fica na home, e por isso o `match` é vazio: duas entradas com
    # `aria-current` na mesma página lê-se como dois "está aqui".
    MenuLink("Como funciona", "properties:home", query="#como-funciona"),
    MenuLink("Assistente", "assistant:chat", match="chat"),
)


def arvore(user: object) -> list[MenuLink | MenuGroup]:
    """Devolve a navegação que o utilizador pode usar, por ordem de leitura."""
    entradas: list[MenuLink | MenuGroup] = list(PUBLICO)

    if getattr(user, "can_curate", False):
        # A curadoria é de quem escreve o imóvel, e por isso entra por
        # `can_curate` e não por "está na equipa". Um cliente registado que
        # chegue a esta barra não vê o ramo.
        entradas.append(
            MenuGroup(
                "Curadoria",
                links=[
                    MenuLink("Imóveis", "properties:curator_dashboard", match="curator_dashboard"),
                    MenuLink("Novo imóvel", "properties:curator_create", match="curator_create"),
                ],
                match_prefix="curator_",
            )
        )

    if getattr(user, "can_validate", False):
        # O atendimento é de quem decide sobre pedidos de clientes — visitas,
        # propostas, conversas e contactos. Entra por `can_validate` (AGENT,
        # ADMIN) e não por "está na equipa": o CURATOR escreve imóveis e não
        # decide pedidos, e vê Curadoria mas não isto. As três últimas entradas
        # ainda não têm vista e por isso não aparecem — um destino que não
        # existe é uma entrada que não se desenha, em vez de um 500 no
        # cabeçalho. Os nomes `conversation_queue`, `visit_queue` e
        # `offer_list` são o contrato com a Etapa 2: é com estes nomes que as
        # vistas têm de nascer, e há teste a travá-lo.
        entradas.append(
            MenuGroup(
                "Atendimento",
                links=[
                    MenuLink("Conversas", "concierge:conversation_queue", match="conversation_queue"),
                    MenuLink("Contactos", "concierge:lead_queue", match="lead_queue"),
                    MenuLink("Visitas", "concierge:visit_queue", match="visit_queue"),
                    MenuLink("Propostas", "concierge:offer_list", match="offer_list"),
                ],
                match_prefix="",
            )
        )

    if getattr(user, "can_manage_users", False):
        entradas.append(
            MenuGroup(
                "Equipa e clientes",
                links=[
                    MenuLink("Membros da equipa", "accounts:team_list", match="team_list"),
                    MenuLink("Clientes", "accounts:client_list", match="client_list"),
                ],
                match_prefix="",
            )
        )

    return entradas


def para_template(user: object, url_atual: str = "", nome_atual: str = "") -> list[dict]:
    """Achata a árvore em dicionários prontos a desenhar, já com o URL e o estado.

    Resolve os destinos aqui para o template não fazer trabalho: um `{% url %}`
    por entrada repetiria o trabalho da mesma página em cada linha do menu, e um
    destino que não exista daria `NoReverseMatch` a meio do cabeçalho de todas as
    páginas. Uma entrada sem destino é uma entrada que não aparece — o erro de
    programa vê-se na ausência, que se pergunta, e não num 500 no topo.
    """
    ramos: list[dict] = []
    for entrada in arvore(user):
        if isinstance(entrada, MenuGroup):
            filhas = [folha for folha in (_render(link, nome_atual) for link in entrada.links) if folha]
            if not filhas:
                continue
            ramos.append(
                {
                    "label": entrada.label,
                    "links": filhas,
                    "is_group": True,
                    "is_current": any(folha["is_current"] for folha in filhas)
                    or bool(entrada.match_prefix and entrada.match_prefix in (nome_atual or "")),
                }
            )
        else:
            folha = _render(entrada, nome_atual)
            if folha:
                ramos.append({**folha, "is_group": False})
    return ramos


def _render(link: MenuLink, nome_atual: str) -> dict | None:
    """Resolve uma folha. `None` quando o destino não existe."""
    try:
        url = reverse(link.url_name) + link.query
    except NoReverseMatch:
        return None
    return {
        "label": link.label,
        "url": url,
        "is_current": bool(link.match) and link.match == nome_atual,
    }
