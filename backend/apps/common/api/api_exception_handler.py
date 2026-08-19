# apps/util/exceptions_handler.py
import logging
from typing import Dict, Any
from rest_framework.views import exception_handler
from rest_framework.exceptions import (
    APIException, ValidationError, PermissionDenied,
    NotFound, AuthenticationFailed, NotAuthenticated,
    Throttled, MethodNotAllowed
)
from .api_response import response
from django.http import HttpRequest

logger = logging.getLogger(__name__)

def custom_exception_handler(exc: Exception, context: Dict[str, Any]):
    """
    全局异常处理器（使用统一的 response 函数）
    """
    request: HttpRequest = context.get('request')

    # --- 业务异常 ---
    if isinstance(exc, Exception) and hasattr(exc, "status_code") and hasattr(exc, "code"):
        # 如果是自定义 BusinessException 子类
        return response(
            kind="error",
            message=getattr(exc, "message", str(exc)),
            status_code=getattr(exc, "status_code", 400),
            errors=getattr(exc, "details", None)
        )

    # --- DRF 默认异常处理 ---
    drf_response = exception_handler(exc, context)
    if drf_response is not None:
        # 如果 DRF 已经处理了异常，我们将其转换为统一格式
        if hasattr(exc, 'detail'):
            if isinstance(exc.detail, dict):
                return response(
                    kind="validation_error",
                    errors=exc.detail,
                    message="数据验证失败"
                )
            else:
                return response(
                    kind="error",
                    message=str(exc.detail),
                    status_code=drf_response.status_code
                )
        else:
            return response(
                kind="error",
                message=str(exc),
                status_code=drf_response.status_code
            )

    # --- 常见 DRF 异常类型 ---
    if isinstance(exc, ValidationError):
        detail = getattr(exc, "detail", {})
        if isinstance(detail, dict):
            return response(
                kind="validation_error",
                errors=detail,
                message="数据验证失败"
            )
        else:
            return response(
                kind="error",
                message=str(detail),
                status_code=400
            )
            
    if isinstance(exc, PermissionDenied):
        return response(
            kind="permission_denied",
            message=str(exc) or "权限不足"
        )
        
    if isinstance(exc, NotFound):
        return response(
            kind="not_found",
            message=str(exc) or "资源不存在"
        )
        
    if isinstance(exc, AuthenticationFailed):
        return response(
            kind="error",
            message=str(exc) or "认证失败",
            status_code=401
        )
        
    if isinstance(exc, NotAuthenticated):
        return response(
            kind="error",
            message=str(exc) or "请先登录",
            status_code=401
        )
        
    if isinstance(exc, Throttled):
        return response(
            kind="rate_limited",
            message=str(exc) or "请求过于频繁，请稍后再试"
        )
        
    if isinstance(exc, MethodNotAllowed):
        return response(
            kind="error",
            message=f"不支持 {exc.method} 方法",
            status_code=405
        )
        
    if isinstance(exc, APIException):
        return response(
            kind="error",
            message=str(exc),
            status_code=getattr(exc, 'status_code', 500)
        )

    # --- 未知异常：返回 400 而非 500，便于前端展示，避免用户看到 500 ---
    logger.error(f"未处理异常: {exc}", exc_info=True)
    if request:
        logger.error(f"请求信息: {request.method} {request.path}")
        logger.error(f"用户: {getattr(request, 'user', 'anonymous')}")

    return response(
        kind="error",
        message=str(exc) or "服务器内部错误，请稍后重试",
        status_code=400
    )
