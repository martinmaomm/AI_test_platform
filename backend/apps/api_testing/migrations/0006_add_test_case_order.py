from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0005_add_pre_post_script'),
    ]

    operations = [
        migrations.AddField(
            model_name='apitestsuite',
            name='test_case_order',
            field=models.JSONField(blank=True, default=list, verbose_name='用例执行顺序'),
        ),
    ]
