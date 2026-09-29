"""Datas e horas escritas para o cliente ler, e não para a base de dados guardar.

O steering diz (§1) que o fuso é `Africa/Luanda` e que as horas se dizem como se
dizem em Angola. O Django sabe formatar um `datetime` com a locale activa, e o
que ele escreve é «28 de setembro de 2026 às 15:00»: certo, completo, e mais
comprido do que alguém escreveria numa conversa.

O lembrete de 24 h antes é o caso onde a diferença se nota. «A sua visita está
confirmada para 28 de setembro de 2026 às 15:00» é uma data; «amanhã às 15:00» é
uma instrução. E a segunda só pode ser escrita se a comparação for feita por dia
de calendário e não por mais 24 horas: às 23:00 de hoje, a visita de amanhã às
10:00 está a onze horas e as vinte e quatro ainda não passaram.
"""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone

DIAS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)

# A partir de quantos dias à frente deixa de ser «próximo» e passa a ser a data.
LIMITE_PROXIMO_DIAS = 7


def quando_para_o_cliente(momento: datetime, *, agora: datetime | None = None) -> str:
    """Escreve um instante da forma mais curta que ainda se percebe.

    Hoje, amanhã e o resto da semana dizem o dia; mais longe já diz a data. O que
    já passou nunca recebe «ontem»: um registo de que a visita ficou para trás tem
    de se ler à mesma luz em Fevereiro e em Setembro, e «ontem» muda de
    significado com a memória de quem lê.
    """
    if momento is None:
        return ""
    local = timezone.localtime(momento)
    hoje = timezone.localtime(agora) if agora is not None else timezone.localtime()

    dias = (local.date() - hoje.date()).days
    hora = f"às {local:%H:%M}"

    if dias == 0:
        return f"hoje {hora}"
    if dias == 1:
        return f"amanhã {hora}"
    if dias < 0:
        return f"{local:%d/%m/%Y} {hora}"
    if dias <= LIMITE_PROXIMO_DIAS:
        return f"{DIAS[local.weekday()]}, {local:%d/%m} {hora}"
    return f"{local:%d/%m/%Y} {hora}"
