"""
Python处理工具
提供Python代码提取等功能
"""
import re
import logging

logger = logging.getLogger(__name__)


def extract_python_from_output(raw_output: str) -> str:
    """
    从输出中提取Python脚本
    
    Args:
        raw_output: 原始输出文本
        
    Returns:
        提取的Python代码，如果提取失败返回空字符串
    """
    if not raw_output:
        return ""
    
    try:
        logger.info("开始从输出中提取Python脚本")
        
        # 策略1: 查找```python代码块
        pattern = r'```python\s*\n(.*?)\n```'
        matches = re.findall(pattern, raw_output, re.DOTALL)
        
        if matches:
            python_code = matches[-1].strip()
            logger.info(f"找到Python代码块，长度: {len(python_code)}")
            return python_code
        
        # 策略2: 查找```代码块
        pattern = r'```\s*\n(.*?)\n```'
        matches = re.findall(pattern, raw_output, re.DOTALL)
        
        if matches:
            # 检查是否包含Python关键字
            for match in matches:
                if any(keyword in match.lower() for keyword in ['import', 'def', 'class', 'from', 'async', 'await', 'playwright', 'pytest', 'test_']):
                    logger.info(f"找到可能的Python代码块，长度: {len(match)}")
                    return match.strip()
        
        # 策略3: 查找包含Python关键字的行
        lines = raw_output.split('\n')
        python_lines = []
        in_code_block = False
        
        for line in lines:
            # 检查是否进入代码块
            if '```' in line and not in_code_block:
                in_code_block = True
                continue
            elif '```' in line and in_code_block:
                in_code_block = False
                break
            
            # 如果在代码块中，收集行
            if in_code_block:
                python_lines.append(line)
            # 如果不在代码块中，但包含Python关键字
            elif any(keyword in line.lower() for keyword in ['import', 'def', 'class', 'from', 'async', 'await', 'playwright', 'pytest', 'test_']):
                python_lines.append(line)
        
        if python_lines:
            python_code = '\n'.join(python_lines).strip()
            logger.info(f"通过关键字匹配找到Python代码，长度: {len(python_code)}")
            return python_code
        
        # 策略4: 查找包含Playwright相关导入的行
        playwright_lines = []
        for line in lines:
            if any(keyword in line.lower() for keyword in ['from playwright', 'import playwright', 'playwright.sync_api', 'playwright.async_api']):
                playwright_lines.append(line)
        
        if playwright_lines:
            # 尝试找到完整的代码块
            start_idx = None
            end_idx = None
            
            for i, line in enumerate(lines):
                if any(keyword in line.lower() for keyword in ['from playwright', 'import playwright']):
                    start_idx = i
                    break
            
            if start_idx is not None:
                # 查找代码块的结束位置
                for i in range(start_idx, len(lines)):
                    if lines[i].strip() == '' and i > start_idx + 5:  # 空行且已经有一些内容
                        end_idx = i
                        break
                
                if end_idx is None:
                    end_idx = len(lines)
                
                python_code = '\n'.join(lines[start_idx:end_idx]).strip()
                logger.info(f"通过Playwright导入找到Python代码，长度: {len(python_code)}")
                return python_code
        
        logger.warning("未找到Python代码块")
        return ""
        
    except Exception as e:
        logger.error(f"提取Python脚本失败: {e}")
        return ""
