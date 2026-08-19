"""
API测试用例工具函数
"""
import re
from typing import List, Dict


def extract_endpoints_from_test_data(test_data: dict or list) -> List[Dict[str, str]]:
    """
    从测试数据中提取涉及的端点信息
    
    Args:
        test_data: 测试数据，可能是字典或列表
        
    Returns:
        端点列表，格式: [{"method": "GET", "path": "/api/user/login"}]
    """
    endpoints = []
    
    if not test_data:
        return endpoints
    
    # 如果test_data是字典且包含steps或teststeps（HttpRunner格式）
    if isinstance(test_data, dict):
        if 'steps' in test_data:
            steps = test_data['steps']
        elif 'teststeps' in test_data:
            steps = test_data['teststeps']  # HttpRunner格式
        else:
            return endpoints
    # 如果test_data本身就是列表
    elif isinstance(test_data, list):
        steps = test_data
    else:
        return endpoints
    
    # 遍历步骤，提取端点信息
    for step in steps:
        if not isinstance(step, dict):
            continue
            
        method = None
        path = None
        
        # 从request字段提取
        if 'request' in step:
            request = step['request']
            if isinstance(request, dict):
                method = request.get('method', 'GET').upper()
                url = request.get('url', '')
                path = _extract_path_from_url(url)
        
        # 或者直接从步骤字段提取
        if not method and 'method' in step:
            method = step['method'].upper()
        if not path and 'url' in step:
            path = _extract_path_from_url(step['url'])
        if not path and 'path' in step:
            path = step['path']
        
        # 如果提取到了端点信息，添加到列表
        if method and path:
            endpoints.append({
                'method': method,
                'path': path
            })
    
    # 去重
    unique_endpoints = []
    seen = set()
    for ep in endpoints:
        key = f"{ep['method']} {ep['path']}"
        if key not in seen:
            seen.add(key)
            unique_endpoints.append(ep)
    
    return unique_endpoints


def _extract_path_from_url(url: str) -> str:
    """
    从URL中提取路径
    
    Args:
        url: 完整的URL或路径
        
    Returns:
        提取的路径
    """
    if not url:
        return ''
    
    # 如果以http开头，提取路径部分
    if url.startswith('http://') or url.startswith('https://'):
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.path
        except:
            pass
    
    # 如果以/开头，去掉查询参数
    if url.startswith('/'):
        return url.split('?')[0]
    
    # 否则返回原始值
    return url


def extract_modules_from_endpoints(endpoints: List[Dict[str, str]]) -> List[str]:
    """
    从端点列表中提取模块信息
    
    Args:
        endpoints: 端点列表，格式: [{"method": "GET", "path": "/api/user/login"}]
        
    Returns:
        模块列表，格式: ["用户模块", "订单模块"]
    """
    modules = set()
    
    # 模块映射表
    module_map = {
        'user': '用户模块',
        'order': '订单模块',
        'product': '商品模块',
        'building': '楼栋模块',
        'community': '小区模块',
        'file': '文件模块',
        'owner': '业主模块',
        'payment': '支付模块',
        'api': 'API模块',
        'auth': '认证模块',
        'account': '账户模块',
        'customer': '客户模块',
        'shop': '商店模块',
        'cart': '购物车模块',
        'checkout': '结算模块',
        'inventory': '库存模块',
        'logistics': '物流模块',
        'finance': '财务模块',
        'report': '报表模块',
        'system': '系统模块',
        'config': '配置模块',
        'admin': '管理模块'
    }
    
    for endpoint in endpoints:
        path = endpoint.get('path', '')
        
        # 从路径中提取第一层路径作为模块名
        # 例如: /api/user/login -> user
        # /user/register -> user
        path_parts = path.split('/')
        path_parts = [p for p in path_parts if p and not p.startswith('{') and not re.match(r'^\d+$', p)]
        
        if path_parts:
            # 跳过api前缀
            module_key = path_parts[1] if path_parts[0].lower() == 'api' and len(path_parts) > 1 else path_parts[0]
            module_key = module_key.lower()
            
            # 映射到中文模块名
            module_name = module_map.get(module_key, f'{module_key.capitalize()}模块')
            modules.add(module_name)
    
    return sorted(list(modules))


def extract_modules_from_title(title: str) -> List[str]:
    """
    从标题中提取模块信息（备用方案）
    
    Args:
        title: 测试用例标题
        
    Returns:
        模块列表
    """
    modules = []
    
    # 模块匹配模式
    module_patterns = [
        (r'^(用户|会员|账号|账户)', '用户模块'),
        (r'^(订单|下单)', '订单模块'),
        (r'^(商品|产品|货物)', '商品模块'),
        (r'^(支付|付款|缴费)', '支付模块'),
        (r'^(楼栋|楼宇|建筑)', '楼栋模块'),
        (r'^(小区|社区|园区)', '小区模块'),
        (r'^(业主|住户|居民)', '业主模块'),
        (r'^(文件|附件|上传|下载)', '文件模块'),
        (r'^(登录|认证|授权)', '认证模块'),
        (r'^(购物|结算|购买)', '购物模块'),
    ]
    
    for pattern, module_name in module_patterns:
        if re.match(pattern, title):
            modules.append(module_name)
            break
    
    return modules
