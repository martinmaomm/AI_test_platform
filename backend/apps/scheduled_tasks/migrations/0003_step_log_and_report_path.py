# Generated manually for step_log and report_path

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduled_tasks", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskexecutionlog",
            name="step_log",
            field=models.TextField(blank=True, null=True, verbose_name="用例步骤与错误堆栈"),
        ),
        migrations.AddField(
            model_name="taskexecutionlog",
            name="report_path",
            field=models.CharField(blank=True, max_length=500, null=True, verbose_name="Allure报告静态路径"),
        ),
        migrations.AlterField(
            model_name="taskexecutionlog",
            name="result_log",
            field=models.TextField(blank=True, null=True, verbose_name="执行日志(启动JSON)"),
        ),
    ]
