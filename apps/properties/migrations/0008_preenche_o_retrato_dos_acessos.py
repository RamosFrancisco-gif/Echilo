"""Preenche o retrato dos acessos a documentos já registados.

O `0007` dá ao `DocumentAccessLog` as três colunas do retrato, mas nasce-as
vazias. Os registos que já existem em produção — e que são a razão de ser do
modelo — ficariam sem o nome do ficheiro e sem a referência do imóvel até ao
próximo acesso ao documento, que nunca mais acontece para a maioria deles. É o
mesmo estrago de não fazer a migration: a prova existe, mas já não diz o quê.

O retrato escreve-se com um `UPDATE` por linha e não pelo `save()` do modelo, que
não está disponível aqui. Imediatamente depois de aplicada, o que interessa é que
`property_reference` deixe de estar vazia, e é isso que a migration garante.
"""

from __future__ import annotations

from django.db import migrations


def preenche_retrato(apps: object, schema_editor: object) -> None:
    """Copia imóvel, tipo e ficheiro para os acessos que ainda os não têm."""
    log = apps.get_model("properties", "DocumentAccessLog")  # type: ignore[attr-defined]
    for acesso in log.objects.filter(property_reference="").select_related(  # type: ignore[attr-defined]
        "document", "document__property"
    ):
        doc = acesso.document
        if doc is None:
            continue
        acesso.property_reference = doc.property.reference
        acesso.document_type = doc.document_type
        acesso.file_name = str(doc.file.name or "")
        acesso.save(update_fields=["property_reference", "document_type", "file_name"])


def desfaz_retrato(apps: object, schema_editor: object) -> None:
    """Esvazia o retrato; as colunas caem na migration anterior."""
    log = apps.get_model("properties", "DocumentAccessLog")  # type: ignore[attr-defined]
    log.objects.update(property_reference="", document_type="", file_name="")  # type: ignore[attr-defined]


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0007_documentaccesslog_document_type_and_more"),
    ]

    operations = [
        migrations.RunPython(preenche_retrato, desfaz_retrato),
    ]
