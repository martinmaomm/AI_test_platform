# Generated manually - POM 库级持久化

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("web_testing", "0004_add_generated_class_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="webpage",
            name="pom_code",
            field=models.TextField(blank=True, null=True, verbose_name="POM 页面类代码（库级持久化）"),
        ),
    ]
