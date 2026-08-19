# RESTful API 设计规范

## Web UI测试用例管理 API

### 基础URL
```
/api/v1/web-testing/test-cases/
```

### 资源操作

#### 1. 获取测试用例列表
- **方法**: `GET`
- **URL**: `/api/v1/web-testing/test-cases/`
- **描述**: 获取当前用户的测试用例列表，支持分页和过滤
- **查询参数**:
  - `page`: 页码 (默认: 1)
  - `page_size`: 每页数量 (默认: 20)
  - `project_id`: 项目ID过滤
  - `priority`: 优先级过滤 (high/medium/low)
  - `category`: 类别过滤 (functional/negative/boundary/security/performance/ui/integration)
  - `search`: 标题搜索

**响应示例**:
```json
{
  "success": true,
  "data": {
    "test_cases": [...],
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total_count": 100,
      "total_pages": 5
    }
  },
  "message": "获取到20个测试用例"
}
```

#### 2. 创建测试用例
- **方法**: `POST`
- **URL**: `/api/v1/web-testing/test-cases/`
- **描述**: 创建新的测试用例
- **请求体**: 测试用例数据

**响应示例**:
```json
{
  "success": true,
  "data": {
    "id": 1,
    "title": "登录功能测试",
    "description": "测试用户登录功能",
    ...
  },
  "message": "测试用例创建成功"
}
```

#### 3. 获取测试用例详情
- **方法**: `GET`
- **URL**: `/api/v1/web-testing/test-cases/{id}/`
- **描述**: 获取指定测试用例的详细信息

**响应示例**:
```json
{
  "success": true,
  "data": {
    "id": 1,
    "title": "登录功能测试",
    "description": "测试用户登录功能",
    "url": "https://example.com/login",
    "priority": "high",
    "category": "functional",
    "preconditions": ["用户已注册", "浏览器已打开"],
    "steps": [...],
    "expected_result": "登录成功",
    "test_data": {...},
    "yaml_script_content": "...",
    "js_script_content": "...",
    "python_script_content": "...",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  },
  "message": "获取测试用例详情成功"
}
```

#### 4. 更新测试用例 (完整更新)
- **方法**: `PUT`
- **URL**: `/api/v1/web-testing/test-cases/{id}/`
- **描述**: 完整更新测试用例的所有字段
- **请求体**: 完整的测试用例数据

**响应示例**:
```json
{
  "success": true,
  "data": {
    "id": 1,
    "title": "更新后的登录功能测试",
    ...
  },
  "message": "测试用例更新成功"
}
```

#### 5. 部分更新测试用例
- **方法**: `PATCH`
- **URL**: `/api/v1/web-testing/test-cases/{id}/`
- **描述**: 部分更新测试用例的指定字段
- **请求体**: 需要更新的字段数据

**响应示例**:
```json
{
  "success": true,
  "data": {
    "id": 1,
    "title": "部分更新的测试用例",
    ...
  },
  "message": "测试用例更新成功"
}
```

#### 6. 删除测试用例
- **方法**: `DELETE`
- **URL**: `/api/v1/web-testing/test-cases/{id}/`
- **描述**: 删除指定的测试用例

**响应示例**:
```json
{
  "success": true,
  "message": "测试用例 '登录功能测试' 删除成功"
}
```

## RESTful 设计原则

### 1. 资源命名
- 使用复数形式: `test-cases`
- 使用连字符分隔: `test-cases` 而不是 `testCases`
- 保持一致性: 所有资源都遵循相同的命名规范

### 2. HTTP 方法
- `GET`: 获取资源
- `POST`: 创建资源
- `PUT`: 完整更新资源
- `PATCH`: 部分更新资源
- `DELETE`: 删除资源

### 3. URL 设计
- 使用名词而不是动词
- 使用层级结构表示资源关系
- 使用查询参数进行过滤和分页

### 4. 状态码
- `200 OK`: 成功获取资源
- `201 Created`: 成功创建资源
- `204 No Content`: 成功删除资源
- `400 Bad Request`: 请求参数错误
- `401 Unauthorized`: 未认证
- `403 Forbidden`: 无权限
- `404 Not Found`: 资源不存在
- `500 Internal Server Error`: 服务器内部错误

### 5. 响应格式
- 统一的响应结构
- 包含成功状态、数据和消息
- 错误时提供详细的错误信息

### 6. 版本控制
- 在URL中包含版本号: `/api/v1/`
- 保持向后兼容性
- 新版本通过URL版本号区分

## 优势

1. **标准化**: 遵循RESTful设计原则，易于理解和维护
2. **可扩展**: 支持多种HTTP方法，满足不同操作需求
3. **缓存友好**: GET请求可以被缓存
4. **无状态**: 每个请求都是独立的
5. **统一接口**: 所有资源都遵循相同的操作模式
6. **易于测试**: 标准的HTTP方法便于API测试
