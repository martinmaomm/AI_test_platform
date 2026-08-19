"""
JavaScript处理工具
提供JavaScript代码提取、验证等功能
"""
import re
import logging

logger = logging.getLogger(__name__)


def extract_javascript_from_output(raw_output: str) -> str:
    """
    从输出中提取JavaScript脚本
    
    Args:
        raw_output: 原始输出文本
        
    Returns:
        提取的JavaScript代码，如果提取失败返回空字符串
    """
    if not raw_output:
        return ""
    
    try:
        # 策略1: 查找代码块标记
        code_block_patterns = [
            r'```javascript\s*\n(.*?)\n```',
            r'```js\s*\n(.*?)\n```',
            r'```\s*\n(.*?)\n```',
            r'```javascript(.*?)```',
            r'```js(.*?)```',
            r'```(.*?)```'
        ]
        
        for pattern in code_block_patterns:
            matches = re.findall(pattern, raw_output, re.DOTALL | re.IGNORECASE)
            if matches:
                script = matches[0].strip()
                if validate_javascript_content(script):
                    logger.info(f"通过代码块模式提取JavaScript脚本成功，长度: {len(script)}")
                    return script
        
        # 策略2: 查找import语句开始的JavaScript代码
        import_pattern = r'(import\s+.*?from\s+.*?;.*?)(?=\n\n|\Z)'
        matches = re.findall(import_pattern, raw_output, re.DOTALL)
        if matches:
            script = matches[0].strip()
            if validate_javascript_content(script):
                logger.info(f"通过import模式提取JavaScript脚本成功，长度: {len(script)}")
                return script
        
        # 策略3: 查找完整的JavaScript函数或类定义
        function_patterns = [
            r'(async\s+function\s+.*?)(?=\n\n|\Z)',
            r'(function\s+.*?)(?=\n\n|\Z)',
            r'(const\s+.*?=.*?;.*?)(?=\n\n|\Z)',
            r'(let\s+.*?=.*?;.*?)(?=\n\n|\Z)',
            r'(var\s+.*?=.*?;.*?)(?=\n\n|\Z)'
        ]
        
        for pattern in function_patterns:
            matches = re.findall(pattern, raw_output, re.DOTALL)
            if matches:
                script = matches[0].strip()
                if validate_javascript_content(script):
                    logger.info(f"通过函数模式提取JavaScript脚本成功，长度: {len(script)}")
                    return script
        
        # 策略4: 如果输出看起来像完整的JavaScript代码，直接返回
        if validate_javascript_content(raw_output.strip()):
            logger.info(f"直接使用原始输出作为JavaScript脚本，长度: {len(raw_output)}")
            return raw_output.strip()
        
        # 策略5: 按行查找JavaScript代码
        lines = raw_output.split('\n')
        js_lines = []
        in_code_block = False
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # 检查是否是代码块开始
            if line.startswith('```javascript') or line.startswith('```js') or line.startswith('```'):
                in_code_block = True
                continue
            
            # 检查是否是代码块结束
            if in_code_block and line == '```':
                break
            
            # 如果在代码块中或者是JavaScript代码
            if in_code_block or is_javascript_line(line):
                js_lines.append(line)
        
        if js_lines:
            script = '\n'.join(js_lines)
            if validate_javascript_content(script):
                logger.info(f"通过行解析提取JavaScript脚本成功，长度: {len(script)}")
                return script
        
        logger.warning("无法从输出中提取有效的JavaScript脚本")
        return ""
        
    except Exception as e:
        logger.error(f"提取JavaScript脚本时发生错误: {e}")
        return ""


def validate_javascript_content(content: str) -> bool:
    """
    验证内容是否为有效的JavaScript代码
    
    Args:
        content: 要验证的内容
        
    Returns:
        是否为有效的JavaScript代码
    """
    if not content or len(content.strip()) < 10:
        return False
    
    # 检查是否包含JavaScript关键字或语法
    js_indicators = [
        'import', 'from', 'export', 'const', 'let', 'var',
        'function', 'async', 'await', 'return', 'if', 'else',
        'for', 'while', 'try', 'catch', 'throw', 'new',
        'class', 'extends', 'this', '=>', '{}', '[]', '()'
    ]
    
    content_lower = content.lower()
    indicator_count = sum(1 for indicator in js_indicators if indicator in content_lower)
    
    # 至少包含3个JavaScript指示器
    return indicator_count >= 3


def is_javascript_line(line: str) -> bool:
    """
    判断一行是否为JavaScript代码
    
    Args:
        line: 要判断的行
        
    Returns:
        是否为JavaScript代码
    """
    if not line:
        return False
    
    # JavaScript关键字
    js_keywords = [
        'import', 'from', 'export', 'const', 'let', 'var',
        'function', 'async', 'await', 'return', 'if', 'else',
        'for', 'while', 'try', 'catch', 'throw', 'new',
        'class', 'extends', 'this', 'console.log', 'console.error'
    ]
    
    line_lower = line.lower()
    return any(keyword in line_lower for keyword in js_keywords)
