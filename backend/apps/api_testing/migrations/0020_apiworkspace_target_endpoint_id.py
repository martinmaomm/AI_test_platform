from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('api_testing', '0019_apispecification_source_selection_key_and_more')]

    operations = [
        migrations.AddField(
            model_name='apiworkspace',
            name='target_endpoint_id',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
