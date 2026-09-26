"""Passa a lista de províncias do registo de 18 para 21 entradas.

A `accounts` não tem dados com `CUANDO_CUBANGO` para migrar: a província de
residência é opcional e nunca foi usada como chave de nenhuma tabela.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0002_user_date_of_birth_user_gender_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='province',
            field=models.CharField(blank=True, choices=[('BENGO', 'Bengo'), ('BENGUELA', 'Benguela'), ('BIE', 'Bié'), ('CABINDA', 'Cabinda'), ('CUANDO', 'Cuando'), ('CUBANGO', 'Cubango'), ('CUANZA_NORTE', 'Cuanza Norte'), ('CUANZA_SUL', 'Cuanza Sul'), ('CUNENE', 'Cunene'), ('HUAMBO', 'Huambo'), ('HUILA', 'Huíla'), ('ICOLO_E_BENGO', 'Icolo e Bengo'), ('LUANDA', 'Luanda'), ('LUNDA_NORTE', 'Lunda Norte'), ('LUNDA_SUL', 'Lunda Sul'), ('MALANJE', 'Malanje'), ('MOXICO', 'Moxico'), ('MOXICO_LESTE', 'Moxico Leste'), ('NAMIBE', 'Namibe'), ('UIGE', 'Uíge'), ('ZAIRE', 'Zaire')], default='', max_length=20, verbose_name='província de residência'),
        ),
    ]
