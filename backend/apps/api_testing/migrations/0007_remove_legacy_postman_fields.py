"""
Migration 0007: 移除 APITestCase 表中遗留的 Postman 风格冗余字段

删除的字段（7个）：
  - pre_script       (TextField)
  - post_script      (TextField)
  - request_data     (JSONField)
  - expected_response(JSONField)
  - variables        (JSONField)
  - expected_status_code (IntegerField)
  - assertions       (JSONField)

保留：script_content 作为唯一执行逻辑数据源。
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0006_add_test_case_order'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='apitestcase',
            name='pre_script',
        ),
        migrations.RemoveField(
            model_name='apitestcase',
            name='post_script',
        ),
        migrations.RemoveField(
            model_name='apitestcase',
            name='request_data',
        ),
        migrations.RemoveField(
            model_name='apitestcase',
            name='expected_response',
        ),
        migrations.RemoveField(
            model_name='apitestcase',
            name='variables',
        ),
        migrations.RemoveField(
            model_name='apitestcase',
            name='expected_status_code',
        ),
        migrations.RemoveField(
            model_name='apitestcase',
            name='assertions',
        ),
    ]
