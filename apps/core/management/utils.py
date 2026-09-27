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

#: Variável que dá a palavra-passe às contas de demonstração em produção.
NOME_DEMO_PASSWORD = "ECHILO_DEMO_PASSWORD"


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


def exige_permissao_explicita(*, debug: bool, permitido: bool, comando: str) -> None:
    """Deixa um comando escrever em produção, mas só quando o operador o pede.

    Há comandos que em produção são um erro (escrever em disco) e comandos que
    em produção são uma decisão (semear um catálogo de demonstração). Tratar os
    dois com o mesmo travão é o que obriga a desligar a protecção do primeiro
    para usar o segundo.

    A permissão é um argumento, e não a variável `DEBUG`: com `DEBUG` a deciding,
    quem protege a base de produção desliga-a com um `set` e o travão deixa de
    existir. Com um argumento, a decisão fica no comando que se executou, e quem
    a viu é a consola.
    """
    if debug or permitido:
        return
    raise CommandError(
        f"`{comando}` recusa-se a escrever em produção sem permissão explícita. "
        "Se o catálogo de demonstração é mesmo o que queres em produção, "
        f"passa `--permitir-producao`. Ver a nota em AGENTS.md sobre a Vercel: "
        "a base de dados é a mesma para os previews e para a produção."
    )
