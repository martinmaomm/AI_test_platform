# Generated manually for the additive generation workspace field.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('web_testing', '0015_simplify_script_assets')]

    operations = [
        migrations.AddField(
            model_name='webuiscriptgeneration',
            name='workspace',
            field=models.JSONField(blank=True, default=dict, verbose_name='编辑与调试工作区'),
        ),
    ]
