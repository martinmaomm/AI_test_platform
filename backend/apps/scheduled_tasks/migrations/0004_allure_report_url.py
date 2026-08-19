# 多任务 Allure 物理隔离：按 execution_id 存储报告相对路径

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduled_tasks", "0003_step_log_and_report_path"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskexecutionlog",
            name="allure_report_url",
            field=models.CharField(blank=True, max_length=500, null=True, verbose_name="Allure报告相对路径(供iframe)"),
        ),
    ]
