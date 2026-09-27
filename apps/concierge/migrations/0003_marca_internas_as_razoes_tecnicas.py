"""Marca como internas as razões técnicas que já foram mostradas ao cliente.

O `0002` dá à `Message` a distinção entre o que a equipa lê e o que o cliente
lê. Até à data, o motivo técnico da escalação era escrito como uma bolha normal:
uma conversa degradada punha "Erro técnico no assistente." dentro do ecrã do
cliente, e a equipa — que é quem tem de agir com essa informação — não o via em
lado nenhum. As conversas que já existiam em produção ficam com o texto onde
está; esta migration limita-se a marcá-lo como interno, que é o que a nova
leitura do histórico faz.

São duas frases, e são exactamente as duas que `apps.assistant.services` escreve
quando o nível 1 não consegue responder. A lista é escrita aqui à mão de
propósito: uma migration que importa o código de aplicação depende do estado
dele seis meses depois, e o que ela tem de reparar é precisamente o que esse
código mudou. O caminho de volta volta a marcá-las como visíveis, para que a
migration seja reversível.
"""

from __future__ import annotations

from django.db import migrations

RAZOES_TECNICAS = (
    "Assistente temporariamente indisponível.",
    "Erro técnico no assistente.",
)


def marca_internas(apps: object, schema_editor: object) -> None:
    """Passa a internas as bolhas de escalação que eram diagnóstico da equipa."""
    message = apps.get_model("concierge", "Message")  # type: ignore[attr-defined]
    message.objects.filter(  # type: ignore[attr-defined]
        is_escalation_notice=True,
        is_internal=False,
        body__in=RAZOES_TECNICAS,
    ).update(is_internal=True)


def desmarca_internas(apps: object, schema_editor: object) -> None:
    """Devolve as mesmas bolhas à leitura do cliente."""
    message = apps.get_model("concierge", "Message")  # type: ignore[attr-defined]
    message.objects.filter(  # type: ignore[attr-defined]
        body__in=RAZOES_TECNICAS,
    ).update(is_internal=False)


class Migration(migrations.Migration):

    dependencies = [
        ("concierge", "0002_message_is_internal"),
    ]

    operations = [
        migrations.RunPython(marca_internas, desmarca_internas),
    ]
