"""
Django management command: 清洗历史 APIEndpoint 数据，为 module 为空的端点补全模块关联

复刻前端 getModuleName 的分类算法，根据 tags 或 path 推导模块名，
并通过 APIModule.get_or_create + bulk_update 批量更新。
"""
import re
from django.core.management.base import BaseCommand
from api_testing.models import APIEndpoint, APIModule


# 与前端 EndpointTestCases.vue getModuleName 完全一致的映射
PATH_TO_MODULE_MAP = {
    'user': '用户',
    'order': '订单',
    'product': '商品',
    'auth': '认证',
    'payment': '支付',
    'building': '楼栋',
    'community': '小区',
    'owner': '业主',
    'file': '文件',
    'api': 'API',
}


def resolve_module_name(endpoint: APIEndpoint) -> str:
    """
    根据 endpoint 的 tags 或 path 推导模块名，与前端 getModuleName 逻辑一致。

    优先级：
    1. tags 存在且有值 -> tags[0]
    2. path 存在 -> 按 / 分割，过滤后取首词，查 MAP 或兜底
    3. 兜底 -> '其他'
    """
    # 优先：tags
    tags = endpoint.tags
    if tags and len(tags) > 0:
        return str(tags[0])

    # 降级：path
    path = endpoint.path or ''
    if path:
        parts = [
            p for p in path.split('/')
            if p and not p.startswith('{') and not re.fullmatch(r'\d+', p)
        ]
        if parts:
            k = parts[0].lower()
            if k in PATH_TO_MODULE_MAP:
                return f"{PATH_TO_MODULE_MAP[k]}相关操作"
            return f"{parts[0]} 模块"

    return '其他'


class Command(BaseCommand):
    help = '清洗历史 APIEndpoint 数据：为 module 为空的端点补全模块关联（复刻前端分类算法）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='仅统计待处理数量，不实际更新',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='bulk_update 每批数量（默认 500）',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        qs = APIEndpoint.objects.filter(module__isnull=True).select_related('spec')
        total = qs.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS('没有需要清洗的端点，module 均已关联。'))
            return

        self.stdout.write(f'找到 {total} 个 module 为空的端点。')
        if dry_run:
            self.stdout.write(self.style.WARNING('--dry-run 模式，不执行更新。'))
            return

        module_cache = {}  # (project_id, name) -> module
        total_updated = 0
        offset = 0

        while True:
            batch = list(qs[offset : offset + batch_size])
            if not batch:
                break

            for endpoint in batch:
                project_id = endpoint.spec.project_id
                module_name = resolve_module_name(endpoint)
                cache_key = (project_id, module_name)
                if cache_key not in module_cache:
                    module, _ = APIModule.objects.get_or_create(
                        project_id=project_id,
                        name=module_name,
                        defaults={'sort_order': 0},
                    )
                    module_cache[cache_key] = module
                endpoint.module = module_cache[cache_key]

            APIEndpoint.objects.bulk_update(batch, ['module'])
            total_updated += len(batch)
            self.stdout.write(f'Successfully migrated {total_updated} endpoints...')

            offset += batch_size
            if len(batch) < batch_size:
                break

        self.stdout.write(self.style.SUCCESS(f'完成。共清洗 {total_updated} 个端点的 module 关联。'))
