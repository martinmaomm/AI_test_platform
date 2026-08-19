"""
此管理命令已废弃。
involved_endpoints / involved_modules / request_data 字段已在迁移 0007/0008 中物理删除。
端点和模块信息现在从 script_content.teststeps 动态解析，无需存储。
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = '【已废弃】involved_endpoints/modules 字段已删除，此命令不再执行任何操作'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING(
            '此命令已废弃：involved_endpoints / involved_modules / request_data 字段'
            '已在数据库迁移 0007/0008 中物理删除。端点信息现从 script_content 动态解析。'
        ))
