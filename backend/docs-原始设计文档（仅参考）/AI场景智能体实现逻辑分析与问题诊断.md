# AI场景智能体实现逻辑分析与问题诊断

## 📋 功能概述

**页面**: `http://localhost:5173/api-testing/scenario-generator`

**核心功能**: 用户输入业务场景描述（如"用户注册、登录流程"），AI自动生成可执行的HttpRunner格式测试脚本。

---

## 🔄 完整执行流程

### 1️⃣ 前端流程（ScenarioGenerator.vue）

```
用户输入场景描述
    ↓
发送WebSocket请求到后端
    ↓
实时接收AI执行步骤和生成内容
    ↓
显示生成的测试用例
```

**关键代码位置**: `frontend/src/views/api-testing/ScenarioGenerator.vue`

### 2️⃣ 后端流程（ScenarioGenerationAgent）

```
Step 1: requirement_parsing (需求解析)
  └─ 使用RAG检索项目知识库，获取业务上下文
     
Step 2: map_to_apis (映射API)
  └─ 根据需求关键词匹配相关的API规范
     
Step 3: fetch_api_specifications (获取API规范)
  └─ 获取匹配的API规范的详细信息（端点、方法、参数等）
     
Step 4: generate_scenario_script (生成测试脚本)
  └─ 使用LLM+Pydantic结构化输出生成HttpRunner JSON格式脚本
     
Step 5: save_test_case (保存测试用例)
  └─ 将生成的脚本保存到数据库
```

**关键代码位置**: `backend/apps/ai_core/api_scenario_agent.py`

---

## 🔍 问题分析：为什么生成的接口地址和参数不对？

### 🎯 核心原因

AI生成的测试用例接口地址和参数不对，**主要是因为LLM没有获取到准确的API规范信息**。

### 📊 详细原因分析

#### 原因1：API规范未导入或不完整 ⭐⭐⭐⭐⭐（最可能）

**问题**：
- 数据库中没有导入项目的API规范文件（OpenAPI/Swagger）
- 或者导入的API规范信息不完整（缺少端点、参数、描述等）

**影响**：
- Step 2（map_to_apis）：无法找到匹配的API规范
- Step 3（fetch_api_specifications）：获取到空的或不完整的API信息
- Step 4（generate_scenario_script）：LLM只能基于**自身知识**"猜测"API的路径和参数

**LLM的行为**：
```
当LLM没有准确的API规范时，它会：
1. 根据通用RESTful API设计规范猜测路径
   例如：/api/users/register, /api/users/login
   
2. 根据常见API设计猜测参数
   例如：username, password, email
   
3. 这些猜测可能与你的实际API不一致！
```

**验证方法**：
1. 查看Django日志中的输出：
   ```
   成功映射 X 个API接口
   成功获取 X 个API规范详细信息
   ```
   如果X=0或很小，说明没有匹配到API规范

2. 查看数据库中是否有API规范：
   - 访问：`http://localhost:5173/api-testing/api-specs`
   - 查看是否有导入的API规范文件

#### 原因2：API规范匹配算法简单 ⭐⭐⭐

**问题**：
当前的API映射算法（`_calculate_relevance`）使用简单的关键词匹配：

```python
def _calculate_relevance(self, api_spec: APISpecification, search_text: str) -> float:
    """计算API与搜索文本的相关性分数（不使用LLM）"""
    search_keywords = search_text.lower().split()
    api_text = f"{api_spec.spec_name or api_spec.file_name} {api_spec.description or ''}".lower()
    
    # 简单的相关性计算：匹配的关键词数量 / 总关键词数
    matches = sum(1 for keyword in search_keywords if len(keyword) > 2 and keyword in api_text)
    return matches / len(search_keywords) if search_keywords else 0.0
```

**缺陷**：
- 只匹配API规范的名称和描述，不检查端点路径
- 不使用语义相似度，只是简单的字符串匹配
- 如果API规范名称不包含相关关键词，可能匹配不到

**示例**：
```
用户输入："用户注册、登录流程"
API规范名称："智慧物业平台API v1.0"  ← 不包含"用户"或"登录"关键词
结果：匹配失败，相关性为0
```

#### 原因3：LLM Prompt设计不够严格 ⭐⭐

**问题**：
当前Prompt（第305-407行）虽然详细，但LLM在**没有API规范**或**API规范不完整**时，仍然会"创造性地"生成测试脚本。

**Prompt的当前行为**：
```python
prompt = ChatPromptTemplate.from_template("""
你是一名资深测试开发工程师，负责根据 用户需求、业务上下文 和 API 规范 自动生成...

API 规范（API Specifications）
{api_specifications}  # ← 这里可能是空数组 [] 或不完整的信息

请根据这三段信息自动推理完整测试流程...
""")
```

**当 api_specifications = [] 时**：
- LLM会说："好的，虽然没有API规范，但我可以根据常见的RESTful设计生成脚本..."
- 结果：生成的API路径和参数都是"猜测"的，可能与实际API不一致

#### 原因4：基础URL配置问题 ⭐

**问题**：
生成的测试脚本中的 `base_url` 字段：

```json
{
  "config": {
    "name": "场景名称",
    "base_url": "http://example.com",  # ← 这是默认值
    ...
  }
}
```

**影响**：
- 如果没有配置项目的实际API基础URL，LLM会使用占位符
- 导致生成的测试用例无法直接执行

---

## ✅ 解决方案

### 方案1：导入完整的API规范（推荐⭐⭐⭐⭐⭐）

**适用情况**：你有OpenAPI/Swagger规范文件

**步骤**：

#### 1.1 准备API规范文件

确保你的API规范文件包含：
- ✅ 所有端点的路径（path）
- ✅ 所有端点的HTTP方法（method）
- ✅ 所有端点的摘要（summary）和描述（description）
- ✅ 所有端点的参数（parameters）：query、path、header、body等
- ✅ 请求体和响应体的schema

**示例（OpenAPI 3.0格式）**：

```yaml
openapi: 3.0.0
info:
  title: 智慧物业平台API
  version: 1.0.0
  description: 智慧物业管理系统的后端API接口文档

servers:
  - url: http://localhost:8000
    description: 本地开发服务器

paths:
  /api/users/register:
    post:
      summary: 用户注册
      description: 新用户注册接口，创建用户账户
      tags:
        - 用户管理
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - username
                - password
                - email
              properties:
                username:
                  type: string
                  description: 用户名
                  example: testuser
                password:
                  type: string
                  format: password
                  description: 密码
                  example: password123
                email:
                  type: string
                  format: email
                  description: 邮箱
                  example: test@example.com
      responses:
        '201':
          description: 注册成功
          content:
            application/json:
              schema:
                type: object
                properties:
                  success:
                    type: boolean
                    example: true
                  data:
                    type: object
                    properties:
                      user_id:
                        type: string
                        example: "12345"
                      username:
                        type: string
                        example: "testuser"
                      
  /api/users/login:
    post:
      summary: 用户登录
      description: 用户登录接口，返回访问令牌
      tags:
        - 用户管理
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - username
                - password
              properties:
                username:
                  type: string
                  description: 用户名
                  example: testuser
                password:
                  type: string
                  format: password
                  description: 密码
                  example: password123
      responses:
        '200':
          description: 登录成功
          content:
            application/json:
              schema:
                type: object
                properties:
                  success:
                    type: boolean
                    example: true
                  data:
                    type: object
                    properties:
                      token:
                        type: string
                        example: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
                      user_id:
                        type: string
                        example: "12345"
```

#### 1.2 导入API规范

1. 访问：`http://localhost:5173/api-testing/api-specs`
2. 点击"上传规范"或"创建规范"按钮
3. 选择你的OpenAPI/Swagger文件（支持JSON或YAML格式）
4. 填写规范名称和描述
5. 点击"保存"

#### 1.3 验证导入结果

1. 在API规范列表中查看是否有你刚导入的规范
2. 点击"查看详情"，检查是否正确解析了所有端点
3. 确认每个端点都有：
   - ✅ 路径（path）
   - ✅ 方法（method）
   - ✅ 摘要（summary）
   - ✅ 参数（parameters）

#### 1.4 重新使用AI生成

1. 访问：`http://localhost:5173/api-testing/scenario-generator`
2. 输入相同的场景描述
3. 查看Django日志，确认：
   ```
   成功映射 X 个API接口  ← X 应该 > 0
   成功获取 X 个API规范详细信息  ← X 应该 > 0
   ```
4. 生成的测试用例应该使用正确的API路径和参数

---

### 方案2：优化API映射算法（技术改进⭐⭐⭐）

**适用情况**：已经导入API规范，但匹配效果不好

**改进方向**：

#### 2.1 增强关键词匹配

修改 `_calculate_relevance` 方法，不仅匹配API规范名称，还匹配端点路径和摘要：

```python
def _calculate_relevance(self, api_spec: APISpecification, search_text: str) -> float:
    """计算API与搜索文本的相关性分数（增强版）"""
    search_keywords = search_text.lower().split()
    
    # 构建搜索文本：包含规范名称、描述、所有端点的路径和摘要
    api_text_parts = [
        api_spec.spec_name or api_spec.file_name,
        api_spec.description or ''
    ]
    
    # 添加所有端点的路径和摘要
    endpoints = APIEndpoint.objects.filter(spec=api_spec)
    for endpoint in endpoints:
        api_text_parts.append(endpoint.path or '')
        api_text_parts.append(endpoint.summary or '')
        api_text_parts.append(endpoint.description or '')
    
    api_text = ' '.join(api_text_parts).lower()
    
    # 计算匹配度
    matches = sum(1 for keyword in search_keywords if len(keyword) > 2 and keyword in api_text)
    return matches / len(search_keywords) if search_keywords else 0.0
```

#### 2.2 使用语义相似度（可选）

如果有向量数据库（如Chroma），可以使用语义搜索：

```python
def _map_to_apis_semantic(self, state: ScenarioAgentState) -> ScenarioAgentState:
    """使用语义相似度映射API（需要RAG）"""
    user_request = state["user_request"]
    
    # 使用RAG检索相似的API规范
    similar_specs = self.rag_manager.search_api_specifications(
        query=user_request,
        project_id=self.project_id,
        top_k=10
    )
    
    mapped_apis = [
        {
            "api_spec_id": spec.id,
            "api_name": spec.spec_name,
            "relevance_score": score
        }
        for spec, score in similar_specs
    ]
    
    state["mapped_apis"] = mapped_apis
    return state
```

---

### 方案3：改进LLM Prompt（防止"创造性"生成⭐⭐⭐⭐）

**修改Prompt，明确要求LLM只使用提供的API规范**：

```python
prompt = ChatPromptTemplate.from_template("""
你是一名资深测试开发工程师，负责根据 用户需求、业务上下文 和 API 规范 自动生成可直接运行的 HttpRunner 测试脚本（JSON格式）。

⚠️ 重要规则：
1. 你必须严格使用下面提供的 API 规范 中的端点路径、方法和参数
2. 不要"猜测"或"创造"任何未在API规范中明确定义的端点或参数
3. 如果API规范中没有某个端点，你应该在生成的脚本中添加注释说明缺失的API
4. 所有的URL路径、HTTP方法、参数名称都必须来自API规范

📌 输入内容（模型使用三段信息）

用户需求（User Request）
{user_request}

业务上下文（Business Context）
{business_context}

API 规范（API Specifications）
{api_specifications}

⚠️ 如果 API 规范为空或不完整，请在生成的脚本中添加注释说明：
  "注意：由于缺少完整的API规范，部分端点路径和参数可能需要手动调整。"

请根据这三段信息，严格按照API规范生成测试脚本...

[后续Prompt保持不变]
""")
```

**优点**：
- 明确告诉LLM不要"猜测"
- 当API规范不完整时，LLM会添加警告信息
- 减少生成错误的API路径和参数

---

### 方案4：配置项目基础URL（环境配置⭐⭐）

**目的**：让生成的测试脚本使用正确的基础URL

**方法1：在项目设置中配置**

1. 修改 `projects` 应用的 `Project` 模型，添加 `base_url` 字段：

```python
# backend/apps/projects/models.py
class Project(models.Model):
    name = models.CharField(max_length=255)
    base_url = models.CharField(max_length=255, default='http://localhost:8000', help_text='API基础URL')
    # ... 其他字段
```

2. 在生成测试脚本时使用项目的 `base_url`：

```python
# 在 _generate_scenario_script 方法中
project = Project.objects.get(id=self.project_id)
base_url = project.base_url or "http://localhost:8000"

# 在Prompt中添加
prompt = ChatPromptTemplate.from_template("""
...
config 配置信息
   - name: 测试场景名称（字符串）
   - base_url: 基础URL（字符串，使用 "{base_url}"）
...
""")

messages = prompt.format_messages(
    base_url=base_url,
    user_request=self.user_request,
    ...
)
```

**方法2：从API规范中提取**

如果API规范（OpenAPI）中有 `servers` 字段，可以提取基础URL：

```python
# 在 _fetch_api_specifications 方法中
for spec in api_specs:
    if spec.parsed_content and 'servers' in spec.parsed_content:
        servers = spec.parsed_content['servers']
        if servers:
            base_url = servers[0].get('url', 'http://localhost:8000')
            # 存储base_url到state中
```

---

## 🔧 诊断步骤

### 第1步：检查API规范是否存在

访问：`http://localhost:5173/api-testing/api-specs`

**检查项**：
- [ ] 是否有导入的API规范文件？
- [ ] API规范的端点数量是否完整？
- [ ] 每个端点是否有完整的信息（路径、方法、参数、摘要）？

**如果没有或不完整** → 执行 **解决方案1**（导入API规范）

### 第2步：查看AI生成日志

1. 打开Django服务器终端
2. 使用AI生成一个测试用例
3. 查看日志输出：

```
[INFO] 开始执行场景生成Agent工作流
[INFO] 开始检索业务上下文，查询: [用户需求]
[INFO] 检索到 X 条业务上下文
[INFO] 成功映射 Y 个API接口  ← 重点关注这一行
[INFO] 成功获取 Z 个API规范详细信息  ← 重点关注这一行
[INFO] 开始生成测试脚本（JSON格式）
```

**分析**：
- 如果 `Y = 0` 或 `Z = 0`，说明没有匹配到API规范 → 执行 **解决方案1** 或 **解决方案2**
- 如果 `Y > 0` 且 `Z > 0`，但生成的API仍然不对 → 执行 **解决方案3**（改进Prompt）

### 第3步：手动检查生成的测试用例

1. 访问：`http://localhost:5173/api-testing/test-cases/scenario`
2. 查看最新生成的测试用例
3. 点击"编辑"，查看完整的测试脚本

**检查项**：
- [ ] `config.base_url` 是否正确？
- [ ] `teststeps` 中的 `request.url` 路径是否与实际API一致？
- [ ] `request.json` 中的参数名称是否与实际API一致？
- [ ] `validate` 中的断言是否合理？

---

## 📊 快速诊断表

| 症状 | 可能原因 | 解决方案 |
|------|---------|---------|
| 生成的API路径完全不对（如 `/api/users/register` vs 实际 `/user/register`） | 数据库中没有API规范，LLM在"猜测" | ⭐⭐⭐⭐⭐ 导入完整的API规范（方案1） |
| 生成的API参数名称不对（如 `username` vs 实际 `user_name`） | API规范不完整，缺少参数定义 | ⭐⭐⭐⭐⭐ 补充API规范中的参数定义 |
| 日志显示"成功映射 0 个API接口" | API规范的名称或描述不包含关键词 | ⭐⭐⭐ 优化API映射算法（方案2） |
| 生成的 `base_url` 是 `http://example.com` | 没有配置项目的基础URL | ⭐⭐ 配置项目基础URL（方案4） |
| API规范已导入，但仍然生成错误的API | Prompt不够严格，LLM仍在"创造" | ⭐⭐⭐⭐ 改进LLM Prompt（方案3） |

---

## 🎯 推荐执行顺序

### 阶段1：基础修复（必做）

1. **导入完整的API规范**（解决方案1）
   - 准备OpenAPI/Swagger文件
   - 确保包含所有端点和参数
   - 导入到系统中

2. **验证导入结果**
   - 查看API规范管理页面
   - 确认端点数量和信息完整

3. **测试AI生成**
   - 输入简单的场景描述
   - 查看生成的测试用例
   - 对比实际API是否一致

### 阶段2：优化改进（可选）

4. **优化API映射算法**（解决方案2）
   - 如果匹配效果不好，增强关键词匹配
   - 考虑使用语义相似度

5. **改进LLM Prompt**（解决方案3）
   - 明确要求LLM只使用提供的API规范
   - 添加警告机制

6. **配置基础URL**（解决方案4）
   - 在项目设置中配置API基础URL
   - 或从API规范中自动提取

---

## 💡 最佳实践

### 1. 维护完整的API规范

- ✅ 每次API变更后及时更新规范文件
- ✅ 使用自动化工具生成API文档（如Django REST Framework的schema功能）
- ✅ 确保每个端点都有清晰的摘要和描述
- ✅ 参数定义要完整（类型、是否必需、示例值）

### 2. 优化场景描述

输入更详细的场景描述，帮助AI更好地匹配API：

**不好的描述**：
```
用户登录
```

**好的描述**：
```
一个新注册的用户（用户名：testuser，密码：password123），
通过登录接口（POST /api/users/login）进行身份验证，
获取访问令牌（token），然后使用该令牌访问用户信息接口
（GET /api/users/profile）查看个人信息。
```

### 3. 分步验证

不要一次生成复杂的多步骤场景，先从简单的单步骤开始：

```
Step 1: 先生成"用户注册"单步骤场景 → 验证API路径和参数
Step 2: 再生成"用户登录"单步骤场景 → 验证API路径和参数
Step 3: 最后生成"注册+登录"组合场景 → 验证数据传递
```

---

## 📂 相关文件清单

### 前端文件
- `frontend/src/views/api-testing/ScenarioGenerator.vue` - AI场景生成器页面

### 后端文件
- `backend/apps/ai_core/api_scenario_agent.py` - AI场景生成Agent核心逻辑
- `backend/apps/ai_core/views.py` - API端点（scenario_generation_agent）
- `backend/apps/api_testing/models.py` - APISpecification、APIEndpoint模型

### 配置文件
- `backend/apps/ai_core/model_manager.py` - LLM管理器
- `backend/apps/ai_core/rag_service.py` - RAG检索服务

---

## ❓ 常见问题

### Q1：为什么AI能生成测试用例，但API路径都不对？

**A**：因为数据库中没有或只有不完整的API规范。LLM会基于通用的RESTful API设计"猜测"路径，这些路径可能与你的实际API不一致。

**解决**：导入完整的API规范文件（OpenAPI/Swagger）。

### Q2：我已经导入了API规范，为什么还是匹配不到？

**A**：可能是因为：
1. API规范的名称或描述不包含你输入的关键词
2. 当前的关键词匹配算法太简单

**解决**：
- 方法1：在场景描述中使用更明确的关键词（如API规范的名称）
- 方法2：优化API映射算法，增加端点路径和摘要的匹配

### Q3：生成的测试用例能执行吗？

**A**：如果API路径和参数都是"猜测"的，大概率无法执行。需要：
1. 导入正确的API规范
2. 配置正确的基础URL
3. 手动调整生成的测试用例（如果必要）

---

## 🚀 立即行动

**现在请执行以下步骤**：

1. **访问API规范管理页面**
   ```
   http://localhost:5173/api-testing/api-specs
   ```

2. **检查是否有API规范**
   - 如果没有 → 准备并导入OpenAPI/Swagger文件
   - 如果有 → 查看端点数量和信息是否完整

3. **重新使用AI生成测试用例**
   - 输入简单的场景描述
   - 查看Django日志中的"成功映射 X 个API接口"
   - 对比生成的API路径和参数是否正确

4. **把结果告诉我**
   - 是否找到了API规范？
   - 日志中映射了多少个API？
   - 生成的API路径是否正确？

我会根据你的反馈给出更具体的解决方案！ 🎯
