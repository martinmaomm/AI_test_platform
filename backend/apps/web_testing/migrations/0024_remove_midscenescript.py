from django.db import migrations


def require_empty_legacy_table(apps, schema_editor):
    legacy = apps.get_model('web_testing', 'MidSceneScript')
    if legacy.objects.using(schema_editor.connection.alias).exists():
        raise RuntimeError('旧脚本表仍有数据，已拒绝删除；请先核对并处理历史数据。')


class Migration(migrations.Migration):
    dependencies = [('web_testing', '0023_webuitestcase_creation_source')]

    operations = [
        migrations.RunPython(require_empty_legacy_table, migrations.RunPython.noop),
        migrations.DeleteModel(name='MidSceneScript'),
    ]
