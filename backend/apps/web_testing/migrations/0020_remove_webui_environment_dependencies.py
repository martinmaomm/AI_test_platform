# Generated manually for the environment-independent WebUI workflow.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web_testing', '0019_deferred_assertion_execution_state'),
    ]

    operations = [
        migrations.RenameField(
            model_name='webuiscriptgeneration',
            old_name='target_url_safe',
            new_name='target_url',
        ),
        migrations.AlterField(
            model_name='webuiscriptgeneration',
            name='target_url',
            field=models.TextField(blank=True, default='', verbose_name='目标网址'),
        ),
        migrations.AlterField(
            model_name='webuiscriptgeneration',
            name='status',
            field=models.CharField(
                choices=[
                    ('created', '已创建'), ('normalizing', '理解需求中'),
                    ('preflighting', '安全预检中'), ('exploring', '探索页面中'),
                    ('generating', '生成脚本中'), ('validating', '检查脚本中'),
                    ('repairing', '修复脚本中'), ('needs_input', '需要补充输入'),
                    ('needs_confirmation', '需要确认'), ('needs_review', '需要人工检查'),
                    ('ready', '生成完成'), ('ready_with_warnings', '生成完成（有警告）'),
                    ('cancelled', '已取消'), ('failed', '生成失败'),
                ],
                default='created', max_length=30, verbose_name='生成状态',
            ),
        ),
        migrations.RemoveField(
            model_name='webuiscriptgeneration',
            name='environment',
        ),
        migrations.RemoveField(
            model_name='webuiscriptgeneration',
            name='start_path',
        ),
        migrations.RemoveField(
            model_name='webuiscriptgeneration',
            name='credentials_required',
        ),
        migrations.RemoveField(
            model_name='webuiscriptgeneration',
            name='credentials_provided',
        ),
        migrations.RemoveField(
            model_name='webuiscriptgeneration',
            name='credentials_expired',
        ),
        migrations.RemoveField(
            model_name='webuitestexecution',
            name='environment',
        ),
    ]
