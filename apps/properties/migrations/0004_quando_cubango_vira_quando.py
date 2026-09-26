"""Leva `CUANDO_CUBANGO` a `CUANDO`, o código que deixou de existir.

A lista do projecto separa "Cuando" de "Cubango"; a fonte de mapas tem as duas
como uma divisão só. Quem tinha `CUANDO_CUBANGO` gravado fica em `CUANDO`: os
municípios de Menongue, Calai, Dirico, Mavinga, Nancova, Rivungo, Caiundo,
Cuangar, Cuchi e Cuito Cuanavale estão todos na lista de `CUANDO` ou na de
`CUBANGO`, e a equipa confirma a correcção no registo do imóvel. Escolher `CUANDO`
em vez de `CUBANGO` é a menos enganosa das duas: se o imóvel estiver do lado
errado, a diferença é um município de uma lista para outra, e não um imóvel
numa província que o filtro não oferece.

Isto não tem reverso. Desfazer exigiria adivinhar qual das duas províncias era,
e um ficheiro que volta a ter um código que o formulário não oferece é pior do
que um registo por corrigir.
"""

from django.db import migrations

CODIGO_ANTIGO = "CUANDO_CUBANGO"
CODIGO_NOVO = "CUANDO"


def para_cuando(apps, schema_editor):
    """Passa os imóveis e as contas do código antigo ao novo."""
    Property = apps.get_model("properties", "Property")
    Property.objects.filter(province_ref=CODIGO_ANTIGO).update(province_ref=CODIGO_NOVO)
    User = apps.get_model("accounts", "User")
    User.objects.filter(province=CODIGO_ANTIGO).update(province=CODIGO_NOVO)


def sem_reverso(apps, schema_editor):
    """Não há como adivinhar qual das duas metades o imóvel ocupava."""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_alter_user_province"),
        ("properties", "0003_alter_property_province_ref"),
    ]

    operations = [
        migrations.RunPython(para_cuando, sem_reverso),
    ]
