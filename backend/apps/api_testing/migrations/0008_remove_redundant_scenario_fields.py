"""
Migration 0008: 移除 APITestCase 表中冗余的场景元数据字段

删除的字段（4个）：
  - mapped_apis          (JSONField) — AI 映射结果，可从 script_content 动态解析
  - api_specifications   (JSONField) — API 规范元数据，属于接口层，不应冗余在用例表
  - involved_endpoints   (JSONField) — 涉及端点，可从 script_content.teststeps 提取
  - involved_modules     (JSONField) — 涉及模块，可从端点路径动态推导

保留：script_content 作为唯一执行逻辑与结构数据源。
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0007_remove_legacy_postman_fields'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='apitestcase',
            name='mapped_apis',
        ),
        migrations.RemoveField(
            model_name='apitestcase',
            name='api_specifications',
        ),
        migrations.RemoveField(
            model_name='apitestcase',
            name='involved_endpoints',
        ),
        migrations.RemoveField(
            model_name='apitestcase',
            name='involved_modules',
        ),
    ]
