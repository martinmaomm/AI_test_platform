# 清理冗余 TestModule，将大模型字段合并至 WebUITestModule

from django.db import migrations, models
import django.db.models.deletion


def migrate_pages_module_to_webuitestmodule(apps, schema_editor):
    """将 WebPage 的 module 从 TestModule 迁移到 WebUITestModule（按 project+name 匹配）"""
    WebPage = apps.get_model("web_testing", "WebPage")
    TestModule = apps.get_model("web_testing", "TestModule")
    WebUITestModule = apps.get_model("web_testing", "WebUITestModule")

    for page in WebPage.objects.filter(module_id__isnull=False):
        try:
            old_module = TestModule.objects.get(id=page.module_id)
        except TestModule.DoesNotExist:
            continue
        new_module, _ = WebUITestModule.objects.get_or_create(
            project_id=old_module.project_id,
            name=old_module.name,
            defaults={
                "description": getattr(old_module, "description", "") or "",
                "business_rules": getattr(old_module, "business_rules", None) or [],
            },
        )
        page.module_new = new_module
        page.save(update_fields=["module_new"])


def reverse_migrate(apps, schema_editor):
    """回滚时无需恢复 TestModule 数据"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("web_testing", "0007_add_test_module_and_webpage_module"),
    ]

    operations = [
        # 1. 给 WebUITestModule 增加 description、business_rules
        migrations.AddField(
            model_name="webuitestmodule",
            name="description",
            field=models.TextField(blank=True, null=True, verbose_name="模块描述"),
        ),
        migrations.AddField(
            model_name="webuitestmodule",
            name="business_rules",
            field=models.JSONField(
                blank=True, default=list, null=True, verbose_name="业务约束规则"
            ),
        ),
        # 2. 添加临时 FK 字段 module_new 指向 WebUITestModule
        migrations.AddField(
            model_name="webpage",
            name="module_new",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pages_new",
                to="web_testing.webuitestmodule",
                verbose_name="所属模块(新)",
            ),
        ),
        # 3. 数据迁移：TestModule -> WebUITestModule
        migrations.RunPython(migrate_pages_module_to_webuitestmodule, reverse_migrate),
        # 4. 移除旧 module 字段
        migrations.RemoveField(
            model_name="webpage",
            name="module",
        ),
        # 5. 删除 TestModule
        migrations.DeleteModel(
            name="TestModule",
        ),
        # 6. 将 module_new 重命名为 module
        migrations.RenameField(
            model_name="webpage",
            old_name="module_new",
            new_name="module",
        ),
        # 7. 修正 related_name
        migrations.AlterField(
            model_name="webpage",
            name="module",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pages",
                to="web_testing.webuitestmodule",
                verbose_name="所属模块",
            ),
        ),
    ]
