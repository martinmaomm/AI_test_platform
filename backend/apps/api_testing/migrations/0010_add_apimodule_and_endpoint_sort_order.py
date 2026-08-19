# Generated manually for APIModule model and APIEndpoint sort_order

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0001_initial'),
        ('api_testing', '0009_add_sort_order_to_apitestcase'),
    ]

    operations = [
        # 创建 APIModule 表（用于端点测试用例页面的模块分组排序）
        migrations.CreateModel(
            name='APIModule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='module name')),
                ('sort_order', models.IntegerField(db_index=True, default=0, verbose_name='sort order')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='api_modules', to='projects.project', verbose_name='project')),
            ],
            options={
                'verbose_name': 'API module',
                'verbose_name_plural': 'API modules',
                'db_table': 'api_modules',
                'ordering': ['sort_order', '-created_at'],
                'unique_together': {('project', 'name')},
            },
        ),
        # 为 APIEndpoint 添加 sort_order 字段（若已存在则跳过）
        migrations.AddField(
            model_name='apiendpoint',
            name='sort_order',
            field=models.IntegerField(db_index=True, default=0, verbose_name='sort order'),
        ),
    ]
