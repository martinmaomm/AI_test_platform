"""
API规范解析服务
提供有界本地引用解析和 API 契约提取。
"""
import copy
import json
import logging
import os
from collections.abc import Mapping
from typing import Any, Dict, List, Optional

import yaml
from django.core.files.storage import default_storage
from django.db import transaction

from openapi_spec_validator import validate_spec
from openapi_spec_validator.exceptions import OpenAPISpecValidatorError

logger = logging.getLogger(__name__)


class APIParserService:
    """API规范解析服务"""

    HTTP_METHODS = {'GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS', 'TRACE'}
    PARAMETER_METADATA_KEYS = {
        '$ref', 'name', 'in', 'description', 'required', 'deprecated', 'allowEmptyValue',
        'style', 'explode', 'allowReserved', 'example', 'examples', 'content',
    }
    SCHEMA_MAPPING_KEYS = {
        'additionalProperties', 'contains', 'contentSchema', 'else', 'if', 'items', 'not',
        'propertyNames', 'then', 'unevaluatedItems', 'unevaluatedProperties',
    }
    SCHEMA_LIST_KEYS = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}
    SCHEMA_MAP_KEYS = {'$defs', 'dependentSchemas', 'patternProperties', 'properties'}

    def __init__(self):
        self.parser = None
        self.parsed_spec = None

    def _extract_structured_data(self, parsed_spec: Dict[str, Any]) -> Dict[str, Any]:
        """将原始规范投影为端点数据，而不丢失可执行契约信息。"""
        if not isinstance(parsed_spec, Mapping):
            return {'endpoints': []}

        components = parsed_spec.get('components', {})
        components = components if isinstance(components, Mapping) else {}
        schemas = parsed_spec.get('definitions', {}) or components.get('schemas', {}) or {}
        structured_data = {
            'info': parsed_spec.get('info', {}),
            'servers': parsed_spec.get('servers', []),
            'endpoints': [],
            'schemas': schemas,
            'definitions': parsed_spec.get('definitions', {}),
            'security_schemes': components.get('securitySchemes', parsed_spec.get('securityDefinitions', {})),
            'tags': parsed_spec.get('tags', []),
            'consumes': copy.deepcopy(parsed_spec.get('consumes', [])),
            'produces': copy.deepcopy(parsed_spec.get('produces', [])),
            'security': copy.deepcopy(parsed_spec.get('security', [])),
        }

        paths = parsed_spec.get('paths', {})
        if not isinstance(paths, Mapping):
            return structured_data
        for path, raw_path_item in paths.items():
            path_item = self._resolve_reference_object(raw_path_item)
            if not isinstance(path_item, Mapping):
                continue
            inherited_parameters = path_item.get('parameters', [])
            for method, raw_operation in path_item.items():
                if str(method).upper() not in self.HTTP_METHODS:
                    continue
                operation = self._resolve_reference_object(raw_operation)
                if not isinstance(operation, Mapping):
                    continue
                structured_data['endpoints'].append(
                    self._extract_endpoint_data(
                        str(path), str(method), operation, inherited_parameters, parsed_spec,
                    )
                )
        return structured_data

    def _extract_endpoint_data(
        self,
        path: str,
        method: str,
        details: Dict[str, Any],
        inherited_parameters: Any = None,
        root_spec: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """提取一个操作，并应用 Path Item 参数覆盖规则。"""
        root_spec = root_spec if isinstance(root_spec, Mapping) else {}
        raw_parameters = self._merge_parameters(inherited_parameters, details.get('parameters', []))
        parameters = self._extract_parameters(raw_parameters)
        consumes = self._operation_value(details, root_spec, 'consumes')
        produces = self._operation_value(details, root_spec, 'produces')
        security = self._operation_value(details, root_spec, 'security')
        request_body = self._extract_request_body(details.get('requestBody', {}))
        request_body = self._convert_body_param_to_request_body(parameters, request_body, consumes)
        operation_metadata = {
            'consumes': consumes,
            'produces': produces,
            'security': security,
        }
        # APIEndpoint 没有独立 operation 元数据列；采用 JSON 扩展避免污染参数或媒体类型。
        request_body['x-aits-operation'] = operation_metadata
        endpoint_data = {
            'path': path,
            'method': method.upper(),
            'summary': details.get('summary', ''),
            'description': details.get('description', ''),
            'operation_id': details.get('operationId', ''),
            'tags': details.get('tags', []),
            'parameters': parameters,
            'request_body': request_body,
            'responses': self._extract_responses(details.get('responses', {}), produces),
            'consumes': consumes,
            'produces': produces,
            'security': security,
            'deprecated': details.get('deprecated', False),
        }
        return endpoint_data

    def _operation_value(self, operation: Mapping[str, Any], root: Mapping[str, Any], key: str) -> Any:
        """操作字段存在时（包含空数组）覆盖根级字段。"""
        if key in operation:
            return copy.deepcopy(operation[key])
        return copy.deepcopy(root.get(key, []))

    def _merge_parameters(self, inherited: Any, operation_parameters: Any) -> List[Any]:
        """按 OpenAPI 的 ``in + name`` 规则合并 Path Item 与操作参数。"""
        merged: List[Any] = []
        positions: Dict[tuple[str, str], int] = {}
        for parameter in list(inherited or []) + list(operation_parameters or []):
            resolved = self._resolve_reference_object(parameter)
            identity = self._parameter_identity(resolved)
            if identity is not None and identity in positions:
                merged[positions[identity]] = parameter
            else:
                if identity is not None:
                    positions[identity] = len(merged)
                merged.append(parameter)
        return merged

    @staticmethod
    def _parameter_identity(parameter: Any) -> Optional[tuple[str, str]]:
        if not isinstance(parameter, Mapping):
            return None
        location, name = parameter.get('in'), parameter.get('name')
        if isinstance(location, str) and isinstance(name, str):
            return location, name
        return None

    def _extract_parameters(self, parameters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """保留 Swagger 2 顶层类型约束和 OpenAPI 3 参数语义。"""
        extracted_params = []
        for raw_parameter in parameters:
            parameter = self._resolve_reference_object(raw_parameter)
            if not isinstance(parameter, Mapping):
                continue
            param_data = copy.deepcopy(dict(parameter))
            schema = parameter.get('schema')
            if isinstance(schema, Mapping):
                param_data['schema'] = self._extract_schema(schema)
            elif 'content' not in parameter:
                # Swagger 2.0 的非 body 参数把 type/format/enum/minimum 等放在顶层。
                param_schema = {
                    key: copy.deepcopy(value)
                    for key, value in parameter.items()
                    if key not in self.PARAMETER_METADATA_KEYS and not key.startswith('x-')
                }
                param_data['schema'] = self._extract_schema(param_schema)
            if isinstance(parameter.get('content'), Mapping):
                param_data['content'] = {
                    content_type: self._extract_media_type(media)
                    for content_type, media in parameter['content'].items()
                    if isinstance(media, Mapping)
                }
            extracted_params.append(param_data)
        return extracted_params

    def _extract_request_body(self, request_body: Dict[str, Any]) -> Dict[str, Any]:
        """提取 OpenAPI 3 Request Body，保留每种媒体类型而不强制 JSON。"""
        if not request_body:
            return {}
        request_body = self._resolve_reference_object(request_body)
        if not isinstance(request_body, Mapping):
            return {}
        body_data = copy.deepcopy(dict(request_body))
        body_data['content'] = {}
        content = request_body.get('content', {})
        if isinstance(content, Mapping):
            for content_type, media in content.items():
                if isinstance(media, Mapping):
                    body_data['content'][content_type] = self._extract_media_type(media)
        return body_data

    def _extract_media_type(self, media: Mapping[str, Any]) -> Dict[str, Any]:
        """复制 Media Type Object，明确示例即使为假值也原样保留。"""
        extracted = copy.deepcopy(dict(media))
        schema = media.get('schema')
        if isinstance(schema, Mapping):
            extracted['schema'] = self._extract_schema(schema)
        return extracted

    def _extract_responses(self, responses: Dict[str, Any], produces: Any = None) -> Dict[str, Any]:
        """提取 Swagger 2 / OpenAPI 3 响应，不合成可被当作 oracle 的示例。"""
        extracted_responses = {}
        if not isinstance(responses, Mapping):
            return extracted_responses
        media_types = [item for item in (produces or []) if isinstance(item, str)] or ['application/json']
        for status_code, raw_response in responses.items():
            response = self._resolve_reference_object(raw_response)
            if not isinstance(response, Mapping):
                continue
            response_data = copy.deepcopy(dict(response))
            response_data['content'] = {}
            if isinstance(response.get('content'), Mapping):
                response_data['content'] = {
                    content_type: self._extract_media_type(media)
                    for content_type, media in response['content'].items()
                    if isinstance(media, Mapping)
                }
            elif 'schema' in response or isinstance(response.get('examples'), Mapping):
                explicit_examples = response.get('examples', {})
                for content_type in media_types:
                    media: Dict[str, Any] = {}
                    if isinstance(response.get('schema'), Mapping):
                        media['schema'] = self._extract_schema(response['schema'])
                    # Swagger 2 examples is keyed by MIME type; ``in`` preserves null/false/0 values.
                    if isinstance(explicit_examples, Mapping) and content_type in explicit_examples:
                        media['example'] = copy.deepcopy(explicit_examples[content_type])
                    if media:
                        response_data['content'][content_type] = media
            extracted_responses[status_code] = response_data
        return extracted_responses
    
    def _validate_swagger_spec(self, content: Dict[str, Any]) -> None:
        """
        验证并校验 Swagger/OpenAPI 规范
        
        Args:
            content: 规范内容字典
            
        Raises:
            Exception: 如果规范无效或验证失败
        """
        if not isinstance(content, dict):
            raise Exception('OpenAPI/Swagger文件必须是有效的JSON/YAML对象')
        
        # 检查必要的字段
        if 'openapi' not in content and 'swagger' not in content:
            raise Exception('文件不是有效的OpenAPI/Swagger规范')
        
        # 检查是否有paths字段
        if 'paths' not in content:
            raise Exception('OpenAPI/Swagger文件缺少paths字段')

        # 使用openapi-spec-validator验证规范合规性
        try:
            validate_spec(content)
            logger.info("OpenAPI规范验证通过")
        except OpenAPISpecValidatorError as e:
            error_msg = f"OpenAPI规范验证失败: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)

    def _reject_external_refs(self, value: Any) -> None:
        """拒绝所有外部或非 JSON Pointer 的 $ref，保证入口不会触发外部解析。"""
        external_refs: List[str] = []

        def visit(item: Any) -> None:
            if isinstance(item, Mapping):
                reference = item.get('$ref')
                if isinstance(reference, str) and reference != '#' and not reference.startswith('#/'):
                    external_refs.append(reference)
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        if external_refs:
            sample = ', '.join(repr(reference) for reference in external_refs[:3])
            suffix = ' 等' if len(external_refs) > 3 else ''
            raise Exception(
                f'OpenAPI/Swagger规范包含不支持的外部 $ref: {sample}{suffix}；'
                '仅支持当前文件内的 # 或 #/... JSON Pointer 引用。'
            )
    
    def _resolve_ref(self, ref_path: str) -> Optional[Dict[str, Any]]:
        """仅解析当前规范内的 JSON Pointer；外部引用永不读取网络或文件系统。"""
        if not isinstance(ref_path, str) or not ref_path.startswith('#') or not isinstance(self.parsed_spec, Mapping):
            return None
        if ref_path != '#' and not ref_path.startswith('#/'):
            return None
        current: Any = self.parsed_spec
        try:
            for raw_part in ref_path[2:].split('/') if ref_path.startswith('#/') else []:
                part = raw_part.replace('~1', '/').replace('~0', '~')
                if isinstance(current, Mapping):
                    current = current[part]
                elif isinstance(current, list):
                    current = current[int(part)]
                else:
                    return None
            return copy.deepcopy(current) if isinstance(current, Mapping) else None
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def _resolve_reference_object(self, value: Any, resolving: frozenset[str] = frozenset()) -> Any:
        """解析 Parameter/Response/RequestBody/PathItem 等本地引用并保留来源。"""
        if not isinstance(value, Mapping):
            return value
        ref = value.get('$ref')
        if not isinstance(ref, str):
            return copy.deepcopy(dict(value))
        if ref in resolving:
            result = copy.deepcopy(dict(value))
            result['x-aits-ref-status'] = 'cyclic'
            return result
        target = self._resolve_ref(ref)
        if not isinstance(target, Mapping):
            result = copy.deepcopy(dict(value))
            result['x-aits-ref-status'] = 'unresolved'
            return result
        resolved = self._resolve_reference_object(target, resolving | {ref})
        result = copy.deepcopy(dict(resolved)) if isinstance(resolved, Mapping) else {}
        result.update(copy.deepcopy(dict(value)))
        result['x-aits-ref-status'] = 'resolved'
        return result

    def _extract_schema(
        self,
        schema: Dict[str, Any],
        resolving: frozenset[str] = frozenset(),
    ) -> Dict[str, Any]:
        """保留 JSON Schema/OpenAPI 字段，并有界展开本地 ``$ref``。"""
        if not isinstance(schema, Mapping):
            return {}

        schema_data = copy.deepcopy(dict(schema))
        for key in self.SCHEMA_MAPPING_KEYS:
            child = schema.get(key)
            if isinstance(child, Mapping):
                schema_data[key] = self._extract_schema(child, resolving)
        for key in self.SCHEMA_LIST_KEYS:
            child = schema.get(key)
            if isinstance(child, list):
                schema_data[key] = [
                    self._extract_schema(item, resolving) if isinstance(item, Mapping) else copy.deepcopy(item)
                    for item in child
                ]
        for key in self.SCHEMA_MAP_KEYS:
            child = schema.get(key)
            if isinstance(child, Mapping):
                schema_data[key] = {
                    name: self._extract_schema(item, resolving) if isinstance(item, Mapping) else copy.deepcopy(item)
                    for name, item in child.items()
                }

        ref = schema.get('$ref')
        if not isinstance(ref, str):
            return schema_data
        if ref in resolving:
            schema_data['x-aits-ref-status'] = 'cyclic'
            return schema_data
        target = self._resolve_ref(ref)
        if not isinstance(target, Mapping):
            schema_data['x-aits-ref-status'] = 'unresolved'
            return schema_data
        resolved = self._extract_schema(target, resolving | {ref})
        # 保留 $ref 和所有同级字段，同时使本地消费者可读取已解析 properties/constraints。
        resolved.update(schema_data)
        resolved['x-aits-ref-status'] = 'resolved'
        return resolved
    
    def parse_api_specification_from_file(self, spec, uploaded_file) -> Dict[str, Any]:
        """
        从上传的文件解析API规范
        
        Args:
            spec: APISpecification 实例
            uploaded_file: UploadedFile 实例
            
        Returns:
            解析结果
        """
        try:
            # 检查文件是否存在
            if not uploaded_file or not uploaded_file.file_exists:
                raise Exception('上传的文件不存在或无法访问')
            
            # 从存储中读取文件
            file_path = uploaded_file.file.name if uploaded_file.file else None
            if not file_path:
                raise Exception('文件路径不存在')
            
            # 从文件名提取扩展名
            file_extension = os.path.splitext(uploaded_file.original_name)[1].lower()
            
            # 读取文件内容
            content = self._read_file_content(file_path, file_extension)
            
            # 验证内容格式
            if not content:
                raise Exception('文件内容为空')

            # 所有完整入口均只支持当前文件的 JSON Pointer，且必须早于官方 validator。
            self._reject_external_refs(content)
            
            # 验证OpenAPI/Swagger格式并校验规范合规性
            if spec.spec_type == 'swagger':
                self._validate_swagger_spec(content)
            
            # 只从上传的文档解析本地 JSON Pointer；不委托解析器读取外部 $ref。
            # 正常和降级路径共用同一投影逻辑，避免其中一条遗漏参数继承或媒体类型。
            self.parsed_spec = content
            structured_data = self._extract_structured_data(content)
            endpoints_created = self._create_api_endpoints_from_structured_data(spec, structured_data)
            
            # 更新文件状态为已处理
            uploaded_file.upload_status = 'uploaded'  # 使用正确的字段名
            uploaded_file.save()
            
            logger.info(f"API规范解析成功: {spec.file_name}, 创建了 {endpoints_created} 个端点")
            
            return {
                'success': True,
                'parsed_structure': content,
                'endpoints_created': endpoints_created
            }
            
        except Exception as e:
            # 解析失败，记录详细错误
            error_msg = f'API规范解析失败: {str(e)}'
            logger.error(f"错误详情: {error_msg}")
            
            # 更新文件状态为处理失败
            if uploaded_file:
                uploaded_file.upload_status = 'failed'
                uploaded_file.save()
            
            return {
                'success': False,
                'error': error_msg
            }
    
    def _read_file_content(self, file_path: str, file_extension: str) -> Dict[str, Any]:
        """
        读取文件内容的健壮方法
        
        Args:
            file_path: 文件路径
            file_extension: 文件扩展名
            
        Returns:
            文件内容
        """
        try:
            # 对于YAML文件，使用二进制模式读取并手动解码为UTF-8
            if file_extension in ['.yaml', '.yml']:
                with default_storage.open(file_path, 'rb') as f:
                    content_bytes = f.read()
                    content_str = content_bytes.decode('utf-8')
                    return yaml.safe_load(content_str)
            else:
                # 对于JSON文件，尝试多种读取方式
                content = None
                
                # 方法1: 尝试UTF-8编码读取（二进制模式+手动解码）
                try:
                    with default_storage.open(file_path, 'rb') as f:
                        content_bytes = f.read()
                        content_str = content_bytes.decode('utf-8')
                        content = json.loads(content_str)
                        logger.info("使用UTF-8编码成功读取文件")
                        return content
                except (UnicodeDecodeError, json.JSONDecodeError) as e1:
                    logger.info(f"UTF-8编码读取失败: {e1}, 尝试其他编码")
                    
                    # 方法2: 尝试其他编码
                    try:
                        with default_storage.open(file_path, 'rb') as f:
                            raw_content = f.read()
                            # 尝试不同的编码
                            for encoding in ['utf-8-sig', 'gbk', 'latin-1', 'cp1252']:
                                try:
                                    decoded_content = raw_content.decode(encoding)
                                    content = json.loads(decoded_content)
                                    logger.info(f"使用编码 {encoding} 成功读取文件")
                                    return content
                                except (UnicodeDecodeError, json.JSONDecodeError):
                                    continue
                            
                            raise Exception('无法使用任何编码解析文件')
                    except Exception as e2:
                        logger.error(f"所有编码尝试都失败: {e2}")
                        raise Exception(f'无法读取文件内容: {str(e2)}')
                
        except Exception as e:
            raise Exception(f'文件读取失败: {str(e)}')
    
    def _convert_body_param_to_request_body(
        self,
        parameters: List[Dict[str, Any]],
        existing_request_body: Optional[Dict[str, Any]] = None,
        consumes: Any = None,
    ) -> Dict[str, Any]:
        """
        将 Swagger 2.0 中 in: body 的参数转换为 request_body 格式
        
        Args:
            parameters: 参数列表
            existing_request_body: 已存在的 request_body（如果有）
            
        Returns:
            转换后的 request_body
        """
        body = copy.deepcopy(existing_request_body) if isinstance(existing_request_body, Mapping) else {}
        if body:
            return body

        media_types = [item for item in (consumes or []) if isinstance(item, str)] or ['application/json']
        body_param = next((item for item in parameters if item.get('in') == 'body'), None)
        if isinstance(body_param, Mapping):
            media = {'schema': copy.deepcopy(body_param.get('schema', {}))}
            if 'example' in body_param:
                media['example'] = copy.deepcopy(body_param['example'])
            return {
                'required': bool(body_param.get('required', False)),
                'description': body_param.get('description', ''),
                'content': {content_type: copy.deepcopy(media) for content_type in media_types},
            }

        form_parameters = [item for item in parameters if item.get('in') == 'formData']
        if not form_parameters:
            return {}
        form_media_types = [
            content_type for content_type in media_types
            if content_type in {'application/x-www-form-urlencoded', 'multipart/form-data'}
        ]
        if not form_media_types:
            form_media_types = ['multipart/form-data' if any(
                item.get('type') == 'file' or item.get('schema', {}).get('type') == 'file'
                for item in form_parameters
            ) else 'application/x-www-form-urlencoded']
        properties = {
            item.get('name', ''): copy.deepcopy(item.get('schema', {}))
            for item in form_parameters if item.get('name')
        }
        required = [item['name'] for item in form_parameters if item.get('required') and item.get('name')]
        schema: Dict[str, Any] = {'type': 'object', 'properties': properties}
        if required:
            schema['required'] = required
        return {
            'required': bool(required),
            'content': {content_type: {'schema': copy.deepcopy(schema)} for content_type in form_media_types},
        }
    
    def _create_single_endpoint(self, spec, endpoint_info: Dict[str, Any]) -> bool:
        """
        创建单个API端点
        
        Args:
            spec: APISpecification 实例
            endpoint_info: 端点信息字典，包含 path, method, summary 等字段
            
        Returns:
            是否创建成功
        """
        from .models import APIEndpoint, APIModule
        
        parameters = endpoint_info.get('parameters', [])
        request_body = endpoint_info.get('request_body', endpoint_info.get('requestBody', {}))

        # 处理 Swagger 2.0 中 in: body 的参数
        request_body = self._convert_body_param_to_request_body(
            parameters, request_body, endpoint_info.get('consumes'),
        )
        if 'x-aits-operation' not in request_body:
            request_body['x-aits-operation'] = {
                'consumes': copy.deepcopy(endpoint_info.get('consumes', [])),
                'produces': copy.deepcopy(endpoint_info.get('produces', [])),
                'security': copy.deepcopy(endpoint_info.get('security', [])),
            }

        # 根据 tags 获取或创建模块
        tags = endpoint_info.get('tags', [])
        tag_name = tags[0] if tags else '未分类'
        module, _ = APIModule.objects.get_or_create(
            project_id=spec.project_id,
            name=tag_name,
            defaults={'sort_order': 0}
        )

        APIEndpoint.objects.create(
            spec=spec,
            module=module,
            path=endpoint_info.get('path', ''),
            method=endpoint_info.get('method', '').upper(),
            summary=endpoint_info.get('summary', ''),
            description=endpoint_info.get('description', ''),
            parameters=parameters,
            request_body=request_body,
            responses=endpoint_info.get('responses', {}),
            tags=tags,
            operation_id=endpoint_info.get('operation_id', endpoint_info.get('operationId', ''))
        )
        return True
    
    def _create_api_endpoints_from_structured_data(self, spec, structured_data: Dict[str, Any]) -> int:
        """
        从结构化数据创建API端点（包含解析后的引用）
        
        Args:
            spec: APISpecification 实例
            structured_data: 解析后的结构化数据
            
        Returns:
            创建的端点数量
        """
        endpoints = structured_data.get('endpoints', [])
        with transaction.atomic():
            for endpoint_data in endpoints:
                self._create_single_endpoint(spec, endpoint_data)

        endpoints_created = len(endpoints)
        
        logger.info(f"成功创建了 {endpoints_created} 个API端点")
        return endpoints_created
    
    def _create_api_endpoints(self, spec, content: Dict[str, Any]) -> int:
        """
        创建API端点（原始方法，用于降级处理）
        
        Args:
            spec: APISpecification 实例
            content: 解析后的API规范内容
            
        Returns:
            创建的端点数量
        """
        self.parsed_spec = content
        return self._create_api_endpoints_from_structured_data(
            spec, self._extract_structured_data(content),
        )
