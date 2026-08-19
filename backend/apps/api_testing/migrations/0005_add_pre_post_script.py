from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0004_add_involved_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='apitestcase',
            name='pre_script',
            field=models.TextField(blank=True, verbose_name='pre script'),
        ),
        migrations.AddField(
            model_name='apitestcase',
            name='post_script',
            field=models.TextField(blank=True, verbose_name='post script'),
        ),
    ]
