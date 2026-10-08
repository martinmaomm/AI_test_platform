from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('performance_testing', '0009_performanceanalysis_types')]
    operations = [
        migrations.AddField(model_name='performanceplan', name='acceptance_targets', field=models.JSONField(default=dict, blank=True)),
        migrations.AddField(model_name='performancerun', name='acceptance_targets', field=models.JSONField(default=dict, blank=True, editable=False)),
    ]
