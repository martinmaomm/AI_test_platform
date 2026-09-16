"""Retire App options without deleting any historical rows or tables."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("projects", "0005_alter_project_project_type")]

    operations = [
        migrations.AlterField(
            model_name="project",
            name="project_type",
            field=models.CharField(
                choices=[
                    ("api", "API Testing"),
                    ("web", "Web Testing"),
                    ("perf", "Performance Testing"),
                ],
                default="api",
                max_length=20,
                verbose_name="project type",
            ),
        ),
        migrations.AlterField(
            model_name="environment",
            name="category",
            field=models.CharField(
                choices=[("api", "API Testing"), ("web", "WebUI Testing")],
                max_length=20,
                verbose_name="environment category",
            ),
        ),
    ]
