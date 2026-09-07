from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('api_testing', '0017_apiworkspace_spec_generation')]

    operations = [
        migrations.AddField(
            model_name='apiworkspace', name='parent',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.CASCADE, related_name='scenarios', to='api_testing.apiworkspace'),
        ),
        migrations.AddField(model_name='apiworkspace', name='scenario_order', field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name='apiworkspace', name='scenario_description', field=models.TextField(blank=True)),
    ]
