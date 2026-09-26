"""Passa a lista de províncias do projecto de 18 para 21 entradas.

Não altera dados: `0004` trata do código que deixou de existir.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('properties', '0002_property_prop_lat_lon_idx'),
    ]

    operations = [
        migrations.AlterField(
            model_name='property',
            name='province_ref',
            field=models.CharField(choices=[('BENGO', 'Bengo'), ('BENGUELA', 'Benguela'), ('BIE', 'Bié'), ('CABINDA', 'Cabinda'), ('CUANDO', 'Cuando'), ('CUBANGO', 'Cubango'), ('CUANZA_NORTE', 'Cuanza Norte'), ('CUANZA_SUL', 'Cuanza Sul'), ('CUNENE', 'Cunene'), ('HUAMBO', 'Huambo'), ('HUILA', 'Huíla'), ('ICOLO_E_BENGO', 'Icolo e Bengo'), ('LUANDA', 'Luanda'), ('LUNDA_NORTE', 'Lunda Norte'), ('LUNDA_SUL', 'Lunda Sul'), ('MALANJE', 'Malanje'), ('MOXICO', 'Moxico'), ('MOXICO_LESTE', 'Moxico Leste'), ('NAMIBE', 'Namibe'), ('UIGE', 'Uíge'), ('ZAIRE', 'Zaire')], db_index=True, max_length=20, verbose_name='província'),
        ),
    ]
