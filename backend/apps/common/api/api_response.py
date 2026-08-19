from typing import Any, Dict, List, Optional, Type
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import QuerySet
from rest_framework.response import Response
from rest_framework.exceptions import APIException
import logging

logger = logging.getLogger(__name__)


class ApiResponse:
    """
    统一API响应类（优化版）
    - success / error / paginated / CRUD / exception
    - 安全、日志、类型提示、分页友好
    """

    def __init__(
        self,
        success: bool,
        status_code: int,
        data: Any = None,
        message: Optional[str] = None,
        error: Optional[Dict[str, Any]] = None,
    ):
        self.success = success
        self.status_code = status_code
        self.data = data
        self.message = message
        self.error = error

    def to_response(self, request=None) -> Response:
        """
        转换为 DRF Response 对象，保持统一结构
        """
        response_data: Dict[str, Any] = {"success": self.success}
        if self.message:
            response_data["message"] = self.message
        if self.success and self.data is not None:
            response_data["data"] = self.data
        elif not self.success and self.error:
            response_data["error"] = self.error
        
        # 记录请求信息到日志（如果需要的话）
        if request:
            logger.debug(f"响应请求: {request.method} {request.path} -> {self.status_code}")
        
        return Response(response_data, status=self.status_code)

    # ------------------- 成功/错误 -------------------

    @classmethod
    def success(
        cls,
        data: Any = None,
        message: str = "请求成功",
        status_code: int = 200,
    ) -> 'ApiResponse':
        return cls(success=True, data=data, message=message, status_code=status_code)

    @classmethod
    def error(
        cls,
        error_code: str = "unknown_error",
        error_message: str = "未知错误",
        status_code: int = 400,
        details: Optional[Any] = None,
    ) -> 'ApiResponse':
        error_payload: Dict[str, Any] = {"code": error_code, "message": error_message}
        if details:
            error_payload["details"] = details
        return cls(success=False, message=error_message, error=error_payload, status_code=status_code)

    # ------------------- 分页 -------------------

    @classmethod
    def paginated(
        cls,
        items: List[Any],
        total: int,
        page: int,
        page_size: int,
        message: str = "请求成功",
        status_code: int = 200,
    ) -> 'ApiResponse':
        total_pages = (total + page_size - 1) // page_size if total else 0
        data = {
            "items": items,
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": page * page_size < total,
                "has_previous": page > 1,
            },
        }
        return cls.success(data=data, message=message, status_code=status_code)

    @classmethod
    def paginated_from_queryset(
        cls,
        queryset: QuerySet,
        page: int,
        page_size: int,
        serializer_class: Optional[Type] = None,
        message: str = "请求成功",
        status_code: int = 200,
    ) -> 'ApiResponse':
        page = max(1, page)  # 保证页码 >=1
        paginator = Paginator(queryset, page_size)
        try:
            page_obj = paginator.page(page)
            items = list(page_obj.object_list)
            if serializer_class:
                items = serializer_class(items, many=True).data
            return cls.paginated(
                items=items,
                total=paginator.count,
                page=page_obj.number,
                page_size=paginator.per_page,
                message=message,
                status_code=status_code,
            )
        except (EmptyPage, PageNotAnInteger):
            # 空页返回空列表，非404
            return cls.paginated(
                items=[],
                total=paginator.count,
                page=page,
                page_size=page_size,
                message=message,
                status_code=status_code,
            )
        except Exception as e:
            logger.exception(f"分页处理失败: {e}")
            return cls.server_error(message="分页处理时发生内部错误")

    # ------------------- CRUD 快捷 -------------------

    @classmethod
    def list(cls, items: List[Any], total: Optional[int] = None, message: str = "请求成功", status_code: int = 200) -> 'ApiResponse':
        data = {"items": items}
        if total is not None:
            data["total"] = total
        return cls.success(data=data, message=message, status_code=status_code)

    @classmethod
    def detail(cls, item: Any, message: str = "请求成功", status_code: int = 200) -> 'ApiResponse':
        return cls.success(data=item, message=message, status_code=status_code)

    @classmethod
    def created(cls, data: Any = None, message: str = "创建成功", status_code: int = 201) -> 'ApiResponse':
        return cls.success(data=data, message=message, status_code=status_code)

    @classmethod
    def updated(cls, data: Any = None, message: str = "更新成功", status_code: int = 200) -> 'ApiResponse':
        return cls.success(data=data, message=message, status_code=status_code)

    @classmethod
    def deleted(cls, data: Any = None, message: str = "删除成功", status_code: int = 200) -> 'ApiResponse':
        return cls.success(data=data, message=message, status_code=status_code)

    # ------------------- 异常处理 -------------------

    @classmethod
    def from_exception(cls, exc: Exception, status_code: Optional[int] = None) -> 'ApiResponse':
        if isinstance(exc, APIException):
            code = getattr(exc, "default_code", "api_error")
            msg = str(exc.detail if isinstance(exc.detail, str) else exc)
            status = exc.status_code
            details = exc.detail if not isinstance(exc.detail, str) else None
        else:
            code = "server_error"
            msg = "服务器内部错误"
            logger.exception(exc)  # 日志记录完整异常
            status = 500
            details = None
        final_status = status_code if status_code is not None else status
        return cls.error(error_code=code, error_message=msg, status_code=final_status, details=details)

    @classmethod
    def validation_error(cls, errors: Dict[str, Any], message: str = "数据验证失败", status_code: int = 400) -> 'ApiResponse':
        return cls.error("validation_error", message, status_code, details=errors)

    @classmethod
    def permission_denied(cls, message: str = "权限不足", status_code: int = 403) -> 'ApiResponse':
        return cls.error("permission_denied", message, status_code)

    @classmethod
    def not_found(cls, resource: str = "资源", message: Optional[str] = None, status_code: int = 404) -> 'ApiResponse':
        msg = message or f"{resource}不存在"
        return cls.error("not_found", msg, status_code, details={"resource": resource})

    @classmethod
    def server_error(cls, message: str = "服务器内部错误", status_code: int = 500) -> 'ApiResponse':
        return cls.error("server_error", message, status_code)

    @classmethod
    def rate_limited(cls, message: str = "请求过于频繁，请稍后再试", status_code: int = 429) -> 'ApiResponse':
        return cls.error("rate_limited", message, status_code)

# ------------------- 统一响应函数 -------------------

def response(
    kind: str,
    data: Any = None,
    items: Optional[List[Any]] = None,
    total: Optional[int] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    serializer_class: Optional[Type] = None,
    message: str = "请求成功",
    status_code: Optional[int] = None,
    errors: Optional[Dict[str, Any]] = None,
    resource: Optional[str] = None,
    exc: Optional[Exception] = None,
    request=None,
) -> Response:
    """
    统一返回函数
    kind: success | error | validation_error | permission_denied | not_found |
          server_error | rate_limited | exception |
          created | updated | deleted | detail | list | paginated | paginated_queryset
    """
    try:
        if kind == "success":
            return ApiResponse.success(data=data, message=message, status_code=status_code or 200).to_response(request)
        elif kind == "error":
            return ApiResponse.error(error_message=message, error_code="error", status_code=status_code or 400).to_response(request)
        elif kind == "validation_error":
            return ApiResponse.validation_error(errors=errors or {}, message=message, status_code=status_code or 400).to_response(request)
        elif kind == "permission_denied":
            return ApiResponse.permission_denied(message=message, status_code=status_code or 403).to_response(request)
        elif kind == "not_found":
            return ApiResponse.not_found(resource=resource or "资源", message=message, status_code=status_code or 404).to_response(request)
        elif kind == "server_error":
            return ApiResponse.server_error(message=message, status_code=status_code or 500).to_response(request)
        elif kind == "rate_limited":
            return ApiResponse.rate_limited(message=message, status_code=status_code or 429).to_response(request)
        elif kind == "exception":
            return ApiResponse.from_exception(exc, status_code=status_code).to_response(request)
        elif kind in ("created", "updated", "deleted", "detail", "list"):
            method = getattr(ApiResponse, kind)
            if kind in ("list",):
                return method(items=items, total=total, message=message, status_code=status_code or 200).to_response(request)
            return method(data=data if kind != "detail" else items, message=message, status_code=status_code).to_response(request)
        elif kind == "paginated":
            return ApiResponse.paginated(items=items or [], total=total or 0, page=page or 1, page_size=page_size or 10, message=message, status_code=status_code or 200).to_response(request)
        elif kind == "paginated_queryset":
            return ApiResponse.paginated_from_queryset(queryset=data, page=page or 1, page_size=page_size or 10, serializer_class=serializer_class, message=message, status_code=status_code or 200).to_response(request)
        else:
            logger.warning(f"未知响应类型: {kind}")
            return ApiResponse.error(error_code="unknown_kind", error_message=f"未知响应类型: {kind}", status_code=500).to_response(request)
    except Exception as e:
        logger.exception(f"response 函数处理失败: {e}")
        return ApiResponse.server_error(message="服务器内部错误").to_response(request)
