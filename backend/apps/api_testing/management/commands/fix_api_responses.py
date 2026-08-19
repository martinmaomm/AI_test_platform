"""
Django management command: 重新解析API规范，修复响应信息
"""
from django.core.management.base import BaseCommand
from api_testing.api_parser_service import APIParserService
from api_testing.models import APISpecification, APIEndpoint
import json


class Command(BaseCommand):
    help = '重新解析API规范，修复响应信息（支持Swagger 2.0格式）'

    def add_arguments(self, parser):
        parser.add_argument('spec_id', type=int, help='API规范ID')

    def handle(self, *args, **options):
        spec_id = options['spec_id']
        
        try:
            # 获取API规范
            spec = APISpecification.objects.get(id=spec_id)
            self.stdout.write(f"处理API规范: {spec.spec_name}")
            
            # 创建解析服务
            parser = APIParserService()
            
            # 从metadata中获取完整的Swagger JSON
            if not spec.metadata:
                self.stdout.write(self.style.ERROR("metadata为空，无法处理"))
                return
            
            # metadata字段是JSONField，Django自动反序列化为dict
            metadata = spec.metadata
            if isinstance(metadata, str):
                # 如果是字符串，需要解析
                metadata = json.loads(metadata)
            
            self.stdout.write(f"成功加载metadata，包含 {len(metadata.get('paths', {}))} 个路径")
            
            # 设置parsed_spec
            parser.parsed_spec = metadata
            
            # 获取所有端点
            endpoints = APIEndpoint.objects.filter(spec_id=spec_id)
            self.stdout.write(f"找到 {len(endpoints)} 个端点")
            
            updated_count = 0
            
            # 遍历所有端点，更新响应信息
            for endpoint in endpoints:
                # 从metadata中找到对应的端点定义
                path_item = metadata.get('paths', {}).get(endpoint.path)
                if not path_item:
                    continue
                
                operation = path_item.get(endpoint.method.lower())
                if not operation:
                    continue
                
                # 提取响应信息
                responses = operation.get('responses', {})
                if responses:
                    # 使用更新后的_extract_responses方法
                    extracted_responses = parser._extract_responses(responses)
                    
                    # 更新数据库
                    endpoint.responses = extracted_responses
                    endpoint.save(update_fields=['responses'])
                    
                    updated_count += 1
                    
                    # 打印前3个示例
                    if updated_count <= 3:
                        self.stdout.write(f"\n更新端点: {endpoint.method} {endpoint.path}")
                        response_preview = json.dumps(extracted_responses, indent=2, ensure_ascii=False)[:300]
                        self.stdout.write(f"响应信息: {response_preview}...")
            
            self.stdout.write(self.style.SUCCESS(f"\n成功更新 {updated_count} 个端点的响应信息"))
            
        except APISpecification.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"未找到spec_id={spec_id}的API规范"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"错误: {e}"))
            import traceback
            traceback.print_exc()
