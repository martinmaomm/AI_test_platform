from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('performance_testing', '0002_execution_control_plane'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='performancenode',
            name='labels',
        ),
        migrations.AddField(
            model_name='performancenode',
            name='deleted_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
