# Generated manually for sort_order field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0008_remove_redundant_scenario_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='apitestcase',
            name='sort_order',
            field=models.IntegerField(db_index=True, default=0, verbose_name='sort order'),
        ),
    ]
