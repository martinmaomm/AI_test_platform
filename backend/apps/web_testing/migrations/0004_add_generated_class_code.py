# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("web_testing", "0003_webpage_webelement"),
    ]

    operations = [
        migrations.AddField(
            model_name="webpage",
            name="generated_class_code",
            field=models.TextField(blank=True, null=True, verbose_name="生成的 Page 类代码（Page 库）"),
        ),
        migrations.AddField(
            model_name="webpage",
            name="page_class_name",
            field=models.CharField(blank=True, max_length=100, null=True, verbose_name="Page 类名"),
        ),
    ]
