"""
API规范解析服务
使用prance库解析OpenAPI规范，并提供完整的API规范解析功能
"""
import json
import yaml
import logging
import os
from typing import Dict, List, Any, Optional
from django.core.files.storage import default_storage

from prance import ResolvingParser, ValidationError
from openapi_spec_validator import validate_spec
from openapi_spec_validator.exceptions import OpenAPISpecValidatorError

logger = logging.getLogger(__name__)


class APIParserService:
    """API规范解析服务"""
    
    def __init__(self):
        self.parser = None
        self.parsed_spec = None  # 保存完整的解析后的规范，用于解析引用
    
    def _extract_structured_data(self, parsed_spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        从解析后的规范中提取结构化数据
        
        Args:
            parsed_spec: 解析后的OpenAPI规范
            
        Returns:
            结构化的API数据
        """
        # 支持 Swagger 2.0 的 definitions 和 OpenAPI 3.0 的 components/schemas
        schemas = {}
        if 'definitions' in parsed_spec:
            # Swagger 2.0
            schemas = parsed_spec.get('definitions', {})
        elif 'components' in parsed_spec:
            # OpenAPI 3.0
            schemas = parsed_spec.get('components', {}).get('schemas', {})
        
        structured_data = {
            'info': parsed_spec.get('info', {}),
            'servers': parsed_spec.get('servers', []),
            'endpoints': [],
            'schemas': schemas,
            'definitions': parsed_spec.get('definitions', {}),  # Swagger 2.0
            'security_schemes': parsed_spec.get('components', {}).get('securitySchemes', {}),
            'tags': parsed_spec.get('tags', [])
        }
        
        # 提取端点信息
        paths = parsed_spec.get('paths', {})
        for path, methods in paths.items():
            for method, details in methods.items():
                if method.upper() in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']:
                    endpoint_data = self._extract_endpoint_data(path, method, details)
                    structured_data['endpoints'].append(endpoint_data)
        
        return structured_data
    
    def _extract_endpoint_data(self, path: str, method: str, details: Dict[str, Any]) -> Dict[str, Any]:
        """
        提取单个端点的详细信息
        
        Args:
            path: 端点路径
            method: HTTP方法
            details: 端点详情
            
        Returns:
            端点结构化数据
        """
        endpoint_data = {
            'path': path,
            'method': method.upper(),
            'summary': details.get('summary', ''),
            'description': details.get('description', ''),
            'operation_id': details.get('operationId', ''),
            'tags': details.get('tags', []),
            'parameters': self._extract_parameters(details.get('parameters', [])),
            'request_body': self._extract_request_body(details.get('requestBody', {})),
            'responses': self._extract_responses(details.get('responses', {})),
            'security': details.get('security', []),
            'deprecated': details.get('deprecated', False)
        }
        
        return endpoint_data
    
    def _extract_parameters(self, parameters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        提取参数信息
        
        Args:
            parameters: 参数列表
            
        Returns:
            结构化的参数数据
        """
        extracted_params = []
        
        for param in parameters:
            param_in = param.get('in', '')
            
            # Swagger 2.0 中，请求体参数在 parameters 中，in='body'
            # 需要特殊处理，解析 schema 中的 $ref
            schema = param.get('schema', {})
            
            param_data = {
                'name': param.get('name', ''),
                'in': param_in,  # path, query, header, cookie, body
                'required': param.get('required', False),
                'description': param.get('description', ''),
                'schema': self._extract_schema(schema),
                'example': param.get('example'),
                'deprecated': param.get('deprecated', False)
            }
            
            # 如果是 body 参数，且包含 $ref，需要解析引用的字段
            if param_in == 'body' and schema and '$ref' in schema:
                resolved_schema = self._resolve_ref(schema['$ref'])
                if resolved_schema:
                    # 将解析后的 schema 信息合并到 param_data 中
                    param_data['schema'] = self._extract_schema(resolved_schema)
                    param_data['resolved_ref'] = True
            
            extracted_params.append(param_data)
        
        return extracted_params
    
    def _extract_request_body(self, request_body: Dict[str, Any]) -> Dict[str, Any]:
        """
        提取请求体信息
        
        Args:
            request_body: 请求体定义
            
        Returns:
            结构化的请求体数据
        """
        if not request_body:
            return {}
        
        body_data = {
            'required': request_body.get('required', False),
            'description': request_body.get('description', ''),
            'content': {}
        }
        
        # 提取不同内容类型
        content = request_body.get('content', {})
        for content_type, content_schema in content.items():
            body_data['content'][content_type] = {
                'schema': self._extract_schema(content_schema.get('schema', {})),
                'example': content_schema.get('example'),
                'examples': content_schema.get('examples', {})
            }
        
        return body_data
    
    def _extract_responses(self, responses: Dict[str, Any]) -> Dict[str, Any]:
        """
        提取响应信息（同时支持Swagger 2.0和OpenAPI 3.0格式）
        
        Args:
            responses: 响应定义
            
        Returns:
            结构化的响应数据
        """
        extracted_responses = {}
        
        for status_code, response in responses.items():
            response_data = {
                'description': response.get('description', ''),
                'headers': response.get('headers', {}),
                'content': {}
            }
            
            # OpenAPI 3.0 格式：使用 content 字段
            if 'content' in response:
                content = response.get('content', {})
                for content_type, content_schema in content.items():
                    response_data['content'][content_type] = {
                        'schema': self._extract_schema(content_schema.get('schema', {})),
                        'example': content_schema.get('example'),
                        'examples': content_schema.get('examples', {})
                    }
            
            # Swagger 2.0 格式：使用 schema 字段
            elif 'schema' in response:
                # Swagger 2.0 默认使用 application/json
                content_type = 'application/json'
                schema = response.get('schema', {})
                extracted_schema = self._extract_schema(schema)
                
                # 尝试从schema生成示例数据
                generated_example = self._generate_example_from_schema(extracted_schema)
                
                # 优先使用response中的examples，否则使用生成的示例
                existing_examples = response.get('examples', {})
                example_data = existing_examples.get(content_type) if isinstance(existing_examples, dict) else None
                
                response_data['content'][content_type] = {
                    'schema': extracted_schema,
                    'example': example_data or generated_example,  # 使用现有的或生成的示例
                    'examples': existing_examples
                }
            
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
    
    def _resolve_ref(self, ref_path: str) -> Optional[Dict[str, Any]]:
        """
        解析 $ref 引用
        
        Args:
            ref_path: 引用路径，如 "#/definitions/UserDto" 或 "#/components/schemas/UserDto"
            
        Returns:
            解析后的Schema定义，如果无法解析则返回None
        """
        if not ref_path or not ref_path.startswith('#'):
            return None
        
        if not self.parsed_spec:
            return None
        
        try:
            # 移除开头的 # 和 /，然后按 / 分割
            parts = ref_path.lstrip('#/').split('/')
            
            # 根据路径类型解析
            if parts[0] == 'definitions':
                # Swagger 2.0: #/definitions/UserDto
                definitions = self.parsed_spec.get('definitions', {})
                if len(parts) > 1:
                    return definitions.get(parts[1])
            elif parts[0] == 'components' and len(parts) > 2 and parts[1] == 'schemas':
                # OpenAPI 3.0: #/components/schemas/UserDto
                schemas = self.parsed_spec.get('components', {}).get('schemas', {})
                if len(parts) > 2:
                    return schemas.get(parts[2])
            
            return None
        except Exception as e:
            logger.warning(f"解析引用失败: {ref_path}, 错误: {str(e)}")
            return None
    
    def _extract_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        提取Schema信息，支持解析 $ref 引用
        
        Args:
            schema: Schema定义
            
        Returns:
            结构化的Schema数据
        """
        if not schema:
            return {}
        
        # 如果包含 $ref，先解析引用
        if '$ref' in schema:
            resolved_schema = self._resolve_ref(schema['$ref'])
            if resolved_schema:
                # 递归解析解析后的 schema
                resolved_data = self._extract_schema(resolved_schema)
                # 保留原始引用路径
                resolved_data['$ref'] = schema['$ref']
                resolved_data['resolved'] = True
                return resolved_data
            else:
                # 无法解析引用，只返回引用路径
                return {
                    '$ref': schema['$ref'],
                    'resolved': False
                }
        
        schema_data = {
            'type': schema.get('type', ''),
            'format': schema.get('format', ''),
            'description': schema.get('description', ''),
            'example': schema.get('example'),  # 提取example字段
            'nullable': schema.get('nullable', False),
            'deprecated': schema.get('deprecated', False)
        }
        
        # 处理不同类型的Schema
        schema_type = schema.get('type', '')
        
        if schema_type == 'object':
            # 递归处理 properties 中的每个字段
            properties = schema.get('properties', {})
            resolved_properties = {}
            for prop_name, prop_schema in properties.items():
                resolved_properties[prop_name] = self._extract_schema(prop_schema)
            
            schema_data.update({
                'properties': resolved_properties,
                'required': schema.get('required', []),
                'additional_properties': schema.get('additionalProperties', True)
            })
        elif schema_type == 'array':
            items_schema = schema.get('items', {})
            schema_data.update({
                'items': self._extract_schema(items_schema),
                'min_items': schema.get('minItems'),
                'max_items': schema.get('maxItems'),
                'unique_items': schema.get('uniqueItems', False)
            })
        elif schema_type in ['string', 'number', 'integer', 'boolean']:
            # 处理基本类型的约束
            if schema_type == 'string':
                schema_data.update({
                    'min_length': schema.get('minLength'),
                    'max_length': schema.get('maxLength'),
                    'pattern': schema.get('pattern'),
                    'enum': schema.get('enum', [])
                })
            elif schema_type in ['number', 'integer']:
                schema_data.update({
                    'minimum': schema.get('minimum'),
                    'maximum': schema.get('maximum'),
                    'exclusive_minimum': schema.get('exclusiveMinimum'),
                    'exclusive_maximum': schema.get('exclusiveMaximum'),
                    'multiple_of': schema.get('multipleOf')
                })
        
        return schema_data
    
    def _generate_example_from_schema(self, schema: Dict[str, Any]) -> Any:
        """
        根据Schema生成示例数据
        
        Args:
            schema: Schema定义
            
        Returns:
            生成的示例数据
        """
        if not schema:
            return None
        
        # 如果schema本身有example，直接返回
        if 'example' in schema and schema['example'] is not None:
            return schema['example']
        
        schema_type = schema.get('type', '')
        
        if schema_type == 'object':
            # 为对象类型生成示例
            example_obj = {}
            properties = schema.get('properties', {})
            for prop_name, prop_schema in properties.items():
                prop_example = self._generate_example_from_schema(prop_schema)
                if prop_example is not None:
                    example_obj[prop_name] = prop_example
            return example_obj if example_obj else None
        
        elif schema_type == 'array':
            # 为数组类型生成示例
            items_schema = schema.get('items', {})
            item_example = self._generate_example_from_schema(items_schema)
            return [item_example] if item_example is not None else None
        
        elif schema_type == 'string':
            # 字符串类型的默认值
            enum = schema.get('enum')
            if enum and len(enum) > 0:
                return enum[0]
            return schema.get('example', 'string')
        
        elif schema_type == 'integer':
            return schema.get('example', 0)
        
        elif schema_type == 'number':
            return schema.get('example', 0.0)
        
        elif schema_type == 'boolean':
            return schema.get('example', False)
        
        return None
    
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
            
            # 验证OpenAPI/Swagger格式并校验规范合规性
            if spec.spec_type == 'swagger':
                self._validate_swagger_spec(content)
            
            # 使用prance解析器解析规范（用于解析引用）
            try:
                # 将内容转换为字符串以便prance解析
                if file_extension in ['.yaml', '.yml']:
                    spec_content_str = yaml.dump(content, default_flow_style=False)
                else:
                    spec_content_str = json.dumps(content, ensure_ascii=False)
                
                self.parser = ResolvingParser(spec_string=spec_content_str)
                parsed_spec = self.parser.specification
                self.parsed_spec = parsed_spec  # 保存完整规范用于解析引用
                
                # 提取结构化信息（包含解析后的引用）
                structured_data = self._extract_structured_data(parsed_spec)
            except Exception as e:
                logger.warning(f"使用prance解析器解析失败，使用原始内容: {str(e)}")
                # 如果prance解析失败，使用原始内容
                self.parsed_spec = content
                structured_data = None
            
            # 解析并创建API端点（使用解析后的结构化数据）
            if structured_data:
                endpoints_created = self._create_api_endpoints_from_structured_data(spec, structured_data)
            else:
                # 降级使用原始方法
                endpoints_created = self._create_api_endpoints(spec, content)
            
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
    
    def _convert_body_param_to_request_body(self, parameters: List[Dict[str, Any]], 
                                           existing_request_body: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        将 Swagger 2.0 中 in: body 的参数转换为 request_body 格式
        
        Args:
            parameters: 参数列表
            existing_request_body: 已存在的 request_body（如果有）
            
        Returns:
            转换后的 request_body
        """
        if existing_request_body:
            return existing_request_body
        
        # 查找 body 参数
        body_param = None
        for param in parameters:
            if param.get('in') == 'body':
                body_param = param
                break
        
        # 如果有 body 参数，将其转换为 request_body 格式
        if body_param:
            # 尝试解析其中的 $ref
            schema = body_param.get('schema', {})
            if schema and '$ref' in schema:
                ref_path = schema['$ref']
                resolved_schema = self._resolve_ref(ref_path)
                if resolved_schema:
                    schema = resolved_schema
                    schema['$ref'] = ref_path  # 保留原始引用
            
            return {
                'required': body_param.get('required', False),
                'description': body_param.get('description', ''),
                'content': {
                    'application/json': {
                        'schema': schema
                    }
                }
            }
        
        return {}
    
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
        
        try:
            parameters = endpoint_info.get('parameters', [])
            request_body = endpoint_info.get('request_body', endpoint_info.get('requestBody', {}))
            
            # 处理 Swagger 2.0 中 in: body 的参数
            request_body = self._convert_body_param_to_request_body(parameters, request_body)
            
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
        except Exception as e:
            method = endpoint_info.get('method', 'UNKNOWN')
            path = endpoint_info.get('path', 'UNKNOWN')
            logger.error(f"创建端点失败: {method} {path}, 错误: {str(e)}")
            return False
    
    def _create_api_endpoints_from_structured_data(self, spec, structured_data: Dict[str, Any]) -> int:
        """
        从结构化数据创建API端点（包含解析后的引用）
        
        Args:
            spec: APISpecification 实例
            structured_data: 解析后的结构化数据
            
        Returns:
            创建的端点数量
        """
        endpoints_created = 0
        endpoints = structured_data.get('endpoints', [])
        
        for endpoint_data in endpoints:
            if self._create_single_endpoint(spec, endpoint_data):
                endpoints_created += 1
        
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
        endpoints_created = 0
        
        if 'paths' in content:
            for path, methods in content['paths'].items():
                for method, details in methods.items():
                    if method.upper() in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']:
                        endpoint_info = {
                            'path': path,
                            'method': method,
                            'summary': details.get('summary', ''),
                            'description': details.get('description', ''),
                            'parameters': details.get('parameters', []),
                            'requestBody': details.get('requestBody', {}),
                            'responses': details.get('responses', {}),
                            'tags': details.get('tags', []),
                            'operationId': details.get('operationId', '')
                        }
                        if self._create_single_endpoint(spec, endpoint_info):
                            endpoints_created += 1
        
        logger.info(f"成功创建了 {endpoints_created} 个API端点")
        return endpoints_created
