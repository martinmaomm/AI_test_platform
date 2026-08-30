import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('projects', '0001_initial'),
        ('web_testing', '0010_webuitestexecution_error_message'),
    ]

    operations = [
        migrations.CreateModel(
            name='WebUIScriptGeneration',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_mode', models.CharField(choices=[('manual_prompt', 'AI 脚本实验室'), ('test_case', '测试用例')], default='manual_prompt', max_length=30, verbose_name='生成入口')),
                ('celery_task_id', models.CharField(blank=True, max_length=100, null=True, unique=True, verbose_name='Celery 任务 ID')),
                ('status', models.CharField(choices=[('created', '已创建'), ('normalizing', '理解需求中'), ('preflighting', '安全预检中'), ('exploring', '探索页面中'), ('generating', '生成脚本中'), ('validating', '检查脚本中'), ('repairing', '修复脚本中'), ('needs_input', '需要补充输入'), ('needs_confirmation', '需要确认'), ('needs_credentials', '需要登录信息'), ('needs_review', '需要人工检查'), ('ready', '生成完成'), ('ready_with_warnings', '生成完成（有警告）'), ('cancelled', '已取消'), ('failed', '生成失败')], default='created', max_length=30, verbose_name='生成状态')),
                ('current_stage', models.CharField(choices=[('created', '已创建'), ('normalizing', '理解需求'), ('preflighting', '安全预检'), ('exploring', '探索页面'), ('generating', '生成脚本'), ('validating', '检查脚本'), ('repairing', '修复脚本'), ('completed', '已完成')], default='created', max_length=30, verbose_name='当前阶段')),
                ('progress', models.PositiveSmallIntegerField(default=0, verbose_name='进度百分比')),
                ('start_path', models.CharField(default='/', max_length=500, verbose_name='环境内起始路径')),
                ('target_url_safe', models.TextField(blank=True, default='', verbose_name='脱敏目标地址')),
                ('description_safe', models.TextField(blank=True, default='', verbose_name='脱敏场景描述')),
                ('scenario_spec', models.JSONField(blank=True, default=dict, verbose_name='规范化场景')),
                ('exploration_snapshot', models.JSONField(blank=True, default=dict, verbose_name='探索证据')),
                ('script_draft', models.TextField(blank=True, default='', verbose_name='脚本草稿')),
                ('quality_report', models.JSONField(blank=True, default=dict, verbose_name='质量报告')),
                ('warnings', models.JSONField(blank=True, default=list, verbose_name='用户可见警告')),
                ('model_info', models.JSONField(blank=True, default=dict, verbose_name='模型信息')),
                ('tool_stats', models.JSONField(blank=True, default=dict, verbose_name='工具统计')),
                ('repair_count', models.PositiveSmallIntegerField(default=0, verbose_name='已修复次数')),
                ('credentials_required', models.BooleanField(default=False, verbose_name='是否需要登录信息')),
                ('credentials_provided', models.BooleanField(default=False, verbose_name='是否已提供临时登录信息')),
                ('credentials_expired', models.BooleanField(default=False, verbose_name='临时登录信息是否已失效')),
                ('error_code', models.CharField(blank=True, default='', max_length=64, verbose_name='稳定错误码')),
                ('error_message', models.TextField(blank=True, default='', verbose_name='用户可读错误信息')),
                ('cancel_requested_at', models.DateTimeField(blank=True, null=True, verbose_name='取消请求时间')),
                ('started_at', models.DateTimeField(blank=True, null=True, verbose_name='开始时间')),
                ('completed_at', models.DateTimeField(blank=True, null=True, verbose_name='完成时间')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('environment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='webui_script_generations', to='projects.environment', verbose_name='WebUI 环境')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='webui_script_generations', to='projects.project', verbose_name='所属项目')),
                ('test_case', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='script_generations', to='web_testing.webuitestcase', verbose_name='关联测试用例')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='webui_script_generations', to=settings.AUTH_USER_MODEL, verbose_name='发起用户')),
            ],
            options={
                'verbose_name': 'WebUI 脚本生成记录',
                'verbose_name_plural': 'WebUI 脚本生成记录',
                'db_table': 'webui_script_generations',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='webuiscriptgeneration',
            index=models.Index(fields=['project', '-created_at'], name='webui_gen_project_created'),
        ),
        migrations.AddIndex(
            model_name='webuiscriptgeneration',
            index=models.Index(fields=['user', 'status'], name='webui_gen_user_status'),
        ),
        migrations.AddIndex(
            model_name='webuiscriptgeneration',
            index=models.Index(fields=['status', 'updated_at'], name='webui_gen_status_updated'),
        ),
    ]
