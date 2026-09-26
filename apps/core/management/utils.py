"""Travões partilhados pelos comandos de desenvolvimento.

Há comandos que só fazem sentido a escrever no disco de quem está a trabalhar no
projecto: semear o catálogo, gerar as camadas de divisões administrativas. Numa
plataforma serverless o disco é efémero e, pior, a base de dados é a mesma para
todos os previews e para a produção.

Um `seed_demo` a correr em produção deixava imóveis de exemplo publicados à frente
dos imóveis verdadeiros, e o catálogo ficava errado sem nenhum erro. Um
`build_admin_boundaries` a correr no build da Vercel falhava a meio com um erro de
permissão do sistema de ficheiros, que não diz nada sobre a causa.

Por isso a verificação é uma função, e não um `if` dentro do `handle`: assim
testa-se sem base de dados, sem rede e sem disco.
"""

from __future__ import annotations

from django.core.management.base import CommandError


def exige_desenvolvimento(*, debug: bool, comando: str) -> None:
    """Recusa o comando quando não está em desenvolvimento.

    Fica fora do `handle` para que o travão se teste sozinho: um teste que tenha
    de abrir uma base de dados para verificar que uma função levanta uma excepção
    deixa de ser um teste.
    """
    if debug:
        return
    raise CommandError(
        f"`{comando}` só corre em desenvolvimento (DEBUG activo). "
        "Este comando escreve em disco e mexe na base de dados, e nenhum dos "
        "dois é seguro em produção. Ver a nota em AGENTS.md sobre a Vercel."
    )
