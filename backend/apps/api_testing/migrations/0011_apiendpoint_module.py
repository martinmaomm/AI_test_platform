# Generated manually for APIEndpoint.module FK

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0010_add_apimodule_and_endpoint_sort_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='apiendpoint',
            name='module',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='endpoints',
                to='api_testing.apimodule',
                verbose_name='所属模块'
            ),
        ),
    ]
