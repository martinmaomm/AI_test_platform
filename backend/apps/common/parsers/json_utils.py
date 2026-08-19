"""
JSON处理工具
提供JSON提取、修复等功能
强制使用 json-repair 容错解析，可自动补全缺失括号、修复多余逗号等
"""
import json_repair
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)


def extract_json_from_output(raw_output: str) -> Optional[str]:
    """
    从输出中提取JSON内容
    
    Args:
        raw_output: 原始输出文本
        
    Returns:
        提取的JSON文本，如果提取失败返回None
    """
    if not raw_output:
        return None
    
    try:
        # 尝试提取markdown代码块中的JSON
        if "```json" in raw_output:
            json_text = raw_output.split("```json")[1].split("```")[0].strip()
            logger.info("从```json标记中提取JSON")
            return json_text
        elif "```" in raw_output:
            json_text = raw_output.split("```")[1].split("```")[0].strip()
            logger.info("从```标记中提取JSON")
            return json_text
        else:
            # 尝试直接查找JSON对象
            response_text = raw_output.strip()
            # 查找第一个 { 和最后一个 }
            start_idx = response_text.find('{')
            if start_idx != -1:
                # 从后往前找最后一个 }
                end_idx = response_text.rfind('}')
                if end_idx != -1 and end_idx > start_idx:
                    json_text = response_text[start_idx:end_idx+1]
                    logger.info("从响应文本中直接提取JSON")
                    return json_text
        return None
    except Exception as e:
        logger.error(f"提取JSON失败: {e}")
        return None


def parse_json_robust(raw_json: str) -> Any:
    """
    强制使用 json_repair 容错解析，直接修复并解析 AI 生成的残缺/超长 JSON。
    不再 fallback 到标准 json。
    """
    try:
        return json_repair.loads(raw_json)
    except Exception as e:
        raise ValueError(f"彻底修复失败，无法解析 JSON: {e}") from e


def fix_json_format(json_text: str) -> str:
    """
    修复常见的JSON格式问题
    
    Args:
        json_text: 需要修复的JSON文本
        
    Returns:
        修复后的JSON文本
    """
    try:
        # 移除可能的BOM标记
        json_text = json_text.lstrip('\ufeff')
        # 移除首尾空白字符
        json_text = json_text.strip()
        # 尝试修复单引号（简单处理，不完美）
        # 注意：这里只做简单修复，复杂情况可能需要更强大的解析器
        return json_text
    except Exception as e:
        logger.warning(f"JSON格式修复失败: {e}")
        return json_text
