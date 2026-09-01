from django.db import migrations


def clear_pre_v3_script_generations(apps, schema_editor):
    """Development reset: v3 intentionally does not read legacy generation JSON."""
    generation = apps.get_model('web_testing', 'WebUIScriptGeneration')
    generation.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('web_testing', '0017_webuiscriptgeneration_exploration_timeout'),
    ]

    operations = [
        migrations.RunPython(clear_pre_v3_script_generations, migrations.RunPython.noop),
    ]
