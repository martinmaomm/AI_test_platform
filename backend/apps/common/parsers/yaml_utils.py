"""
YAML处理工具
提供YAML提取、验证、修复等功能
"""
import re
import logging
import yaml

logger = logging.getLogger(__name__)


def extract_yaml_from_output(raw_output: str) -> str:
    """
    从输出中提取YAML脚本
    
    Args:
        raw_output: 原始输出文本
        
    Returns:
        提取的YAML代码，如果提取失败返回None
    """
    try:
        if not raw_output or not isinstance(raw_output, str):
            logger.warning("输出为空或格式不正确")
            return None
        
        # 清理输出内容
        cleaned_output = raw_output.strip()
        
        # 策略1: 查找YAML代码块 (```yaml ... ```)
        yaml_patterns = [
            r'```yaml\s*\n(.*?)\n```',
            r'```yml\s*\n(.*?)\n```',
            r'```\s*yaml\s*\n(.*?)\n```',
            r'```\s*yml\s*\n(.*?)\n```'
        ]
        
        for pattern in yaml_patterns:
            match = re.search(pattern, cleaned_output, re.DOTALL | re.IGNORECASE)
            if match:
                yaml_content = match.group(1).strip()
                if validate_yaml_content(yaml_content):
                    logger.info(f"成功从输出中提取YAML脚本 (策略: 代码块)")
                    return yaml_content
        
        # 策略2: 查找以web:或android:开头的YAML内容
        yaml_start_patterns = [
            r'(web:\s*\n.*?)(?=\n\s*\n|\n\s*$|$)',
            r'(android:\s*\n.*?)(?=\n\s*\n|\n\s*$|$)',
            r'(tasks:\s*\n.*?)(?=\n\s*\n|\n\s*$|$)'
        ]
        
        for pattern in yaml_start_patterns:
            match = re.search(pattern, cleaned_output, re.DOTALL | re.IGNORECASE)
            if match:
                yaml_content = match.group(1).strip()
                if validate_yaml_content(yaml_content):
                    logger.info(f"成功从输出中提取YAML脚本 (策略: 直接匹配)")
                    return yaml_content
        
        # 策略3: 查找包含关键YAML结构的文本
        full_yaml_pattern = r'(web:\s*\n.*?tasks:\s*\n.*?)(?=\n\s*\n|\n\s*$|$)'
        match = re.search(full_yaml_pattern, cleaned_output, re.DOTALL | re.IGNORECASE)
        if match:
            yaml_content = match.group(1).strip()
            if validate_yaml_content(yaml_content):
                logger.info(f"成功从输出中提取YAML脚本 (策略: 完整结构)")
                return yaml_content
        
        # 策略4: 如果输出本身就是有效的YAML格式
        if validate_yaml_content(cleaned_output):
            logger.info(f"成功从输出中提取YAML脚本 (策略: 直接输出)")
            return cleaned_output
        
        # 策略5: 尝试从输出中提取所有可能的YAML片段
        lines = cleaned_output.split('\n')
        yaml_lines = []
        in_yaml_section = False
        
        for line in lines:
            stripped_line = line.strip()
            # 检查是否是YAML的开始
            if any(stripped_line.startswith(keyword) for keyword in ['web:', 'android:', 'tasks:']):
                in_yaml_section = True
                yaml_lines.append(line)
            elif in_yaml_section:
                # 如果遇到空行且已经有内容，可能是YAML结束
                if not stripped_line and yaml_lines:
                    break
                yaml_lines.append(line)
        
        if yaml_lines:
            yaml_content = '\n'.join(yaml_lines).strip()
            if validate_yaml_content(yaml_content):
                logger.info(f"成功从输出中提取YAML脚本 (策略: 行解析)")
                return yaml_content
        
        logger.warning("无法从输出中提取有效的YAML脚本")
        logger.debug(f"输出内容: {raw_output[:500]}...")
        return None
        
    except Exception as e:
        logger.error(f"提取YAML脚本时发生错误: {e}")
        return None


def validate_yaml_content(yaml_content: str) -> bool:
    """
    验证YAML内容是否有效
    
    Args:
        yaml_content: 要验证的YAML内容
        
    Returns:
        如果YAML内容有效则返回True，否则返回False
    """
    try:
        if not yaml_content or not isinstance(yaml_content, str):
            return False
        
        # 基本检查：确保包含必要的YAML结构
        yaml_content_lower = yaml_content.lower()
        
        # 检查是否包含web或android配置
        has_platform_config = any(keyword in yaml_content_lower for keyword in ['web:', 'android:'])
        
        # 检查是否包含tasks
        has_tasks = 'tasks:' in yaml_content_lower
        
        # 检查是否包含基本的YAML语法
        has_yaml_syntax = any(keyword in yaml_content for keyword in ['- ', ':', '\n'])
        
        # 基本验证通过
        if has_yaml_syntax and (has_platform_config or has_tasks):
            # 尝试解析YAML语法
            try:
                yaml.safe_load(yaml_content)
                return True
            except yaml.YAMLError:
                # YAML语法错误，但内容看起来像YAML
                logger.warning("YAML语法验证失败，但内容结构看起来正确")
                return True
        
        return False
        
    except Exception as e:
        logger.error(f"验证YAML内容时发生错误: {e}")
        return False


def is_valid_yaml(script_content: str) -> bool:
    """
    检查YAML语法是否合法
    
    Args:
        script_content: YAML内容
        
    Returns:
        是否为有效的YAML
    """
    try:
        yaml.safe_load(script_content)
        return True
    except yaml.YAMLError:
        return False


def fix_yaml_syntax(script_content: str) -> str:
    """
    修复YAML语法问题（主要用于HttpRunner格式）
    
    Args:
        script_content: 需要修复的YAML内容
        
    Returns:
        修复后的YAML内容
    """
    # 修复所有验证规则中的引号问题（统一处理）
    validate_pattern = r'(\s*-\s*\w+:\s*)\[([^\]]+)\]'
    def fix_validate_rule(match):
        indent_validator = match.group(1)
        content = match.group(2)
        
        # 分割并清理内容
        parts = content.split(',')
        if len(parts) == 2:
            first_part = parts[0].strip()
            second_part = parts[1].strip()
            
            # 清理双重引号
            first_part = first_part.replace('""', '"').strip()
            second_part = second_part.replace('""', '"').strip()
            
            # 确保正确的引号（数字、布尔值、null不需要引号）
            if not first_part.startswith('"') and not re.match(r'^(true|false|null|\d+)$', first_part):
                first_part = f'"{first_part}"'
            
            if not second_part.startswith('"') and not re.match(r'^(true|false|null|\d+(?:\.\d+)?)$', second_part):
                second_part = f'"{second_part}"'
            
            return f'{indent_validator}[{first_part}, {second_part}]'
        
        return match.group(0)
    
    script_content = re.sub(validate_pattern, fix_validate_rule, script_content)
    
    # 修复URL参数类型问题（HttpRunner要求params中的值都是字符串）
    params_pattern = r'(\s*params:\s*\n(?:\s+[^\n]+\n)*)'
    def fix_params(match):
        params_block = match.group(1)
        param_lines = params_block.split('\n')
        fixed_lines = []
        
        for line in param_lines:
            if ':' in line and not line.strip().startswith('params:'):
                param_pattern = r'(\s*)(\w+):\s*(\d+(?:\.\d+)?)'
                def fix_param_value(match):
                    indent = match.group(1)
                    key = match.group(2)
                    value = match.group(3)
                    return f'{indent}{key}: "{value}"'
                
                fixed_line = re.sub(param_pattern, fix_param_value, line)
                fixed_lines.append(fixed_line)
            else:
                fixed_lines.append(line)
        
        return '\n'.join(fixed_lines)
    
    script_content = re.sub(params_pattern, fix_params, script_content)
    
    # 修复extract中的JSONPath表达式
    lines = script_content.split('\n')
    in_extract = False
    fixed_lines = []
    
    for line in lines:
        if 'extract:' in line:
            in_extract = True
            fixed_lines.append(line)
        elif line.startswith('  ') and in_extract and ':' in line and not line.strip().startswith('-'):
            extract_pattern = r'(\s*)(\w+):\s*([a-zA-Z_][a-zA-Z0-9_.\[\]]*)'
            def fix_extract_rule(match):
                indent = match.group(1)
                key = match.group(2)
                value = match.group(3).strip().replace('""', '"')
                return f'{indent}{key}: "{value}"'
            
            fixed_line = re.sub(extract_pattern, fix_extract_rule, line)
            fixed_lines.append(fixed_line)
        else:
            if not line.startswith(' ') and not line.startswith('-'):
                in_extract = False
            fixed_lines.append(line)
    
    return '\n'.join(fixed_lines)
