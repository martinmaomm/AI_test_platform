from django.db import migrations, models
from django.db.models import F


def copy_provider_to_provider_name(apps, schema_editor):
    LLMConfiguration = apps.get_model('ai_core', 'LLMConfiguration')
    LLMConfiguration.objects.update(provider_name=F('provider'))


class Migration(migrations.Migration):

    dependencies = [
        ('ai_core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='llmconfiguration',
            name='provider_name',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='模型提供商'),
        ),
        migrations.RunPython(copy_provider_to_provider_name, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='llmconfiguration',
            name='provider',
            field=models.CharField(max_length=20, verbose_name='模型接口类型'),
        ),
    ]
