"""
此管理命令已废弃。
involved_endpoints / involved_modules / request_data 字段已在迁移 0007/0008 中物理删除。
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = '【已废弃】involved_endpoints/modules 字段已删除，此命令不再执行任何操作'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING(
            '此命令已废弃：involved_endpoints / involved_modules 字段'
            '已在数据库迁移 0008 中物理删除。'
        ))
