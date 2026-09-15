from django.db import migrations, models


def ensure_at_most_one_mcp_configuration(apps, schema_editor):
    MCPConfiguration = apps.get_model('ai_core', 'MCPConfiguration')
    count = MCPConfiguration.objects.count()
    if count > 1:
        raise RuntimeError(
            '无法启用全局Playwright MCP单例约束：当前存在多条MCP配置；'
            '迁移不会自动删除数据，请先人工核对并保留至多一条。'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('ai_core', '0003_mcpconfiguration_tool_discovery_status'),
    ]

    operations = [
        migrations.RunPython(
            ensure_at_most_one_mcp_configuration,
            migrations.RunPython.noop,
        ),
        migrations.AddField(
            model_name='mcpconfiguration',
            name='singleton_key',
            field=models.PositiveSmallIntegerField(
                default=1,
                editable=False,
                unique=True,
                verbose_name='全局配置槽位',
            ),
        ),
        migrations.AddConstraint(
            model_name='mcpconfiguration',
            constraint=models.CheckConstraint(
                check=models.Q(singleton_key=1),
                name='mcp_configuration_singleton_key_is_one',
            ),
        ),
    ]
