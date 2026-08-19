# AI场景智能体生成错误API路径的问题修复

## 🔴 问题描述

**现象**：
AI生成的场景测试用例中，API路径不正确。

**示例**：
- 实际API端点：`POST /user/register`
- AI生成的路径：
  - ❌ `/api/v1/users/register`
  - ❌ `/api/register`
  - ❌ `/api/users/register`

**根本原因**：
LLM在生成测试脚本时，**没有严格使用API规范中提供的exact path**，而是根据自己的"知识"进行了"优化"或"猜测"。

---

## 🔧 修复方案

### 修复1：增强Prompt约束（已实施）

**位置**：`backend/apps/ai_core/api_scenario_agent.py` - `_generate_scenario_script` 方法

**修改内容**：

#### 在Prompt中明确添加了7条严格规则：

```python
⚠️ 严格使用API规范的规则（必须遵守）：
1. 你必须严格使用上面 API 规范 中提供的端点路径（path）、方法（method）和参数（parameters）
2. 不要修改、猜测或创造任何未在API规范中明确定义的路径或参数
3. 如果API规范中某个端点的path是 "/user/register"，你必须使用 "/user/register"，
   不能改成 "/api/users/register" 或 "/api/v1/users/register"
4. 如果API规范中某个端点的method是 "POST"，你必须使用 "POST"，不能改成其他方法
5. 如果API规范中某个端点的参数是 "username"，你必须使用 "username"，
   不能改成 "user_name" 或 "userName"
6. 每个步骤的 request.url 必须精确匹配API规范中的某个端点的 path 字段
7. 每个步骤的 request.method 必须精确匹配API规范中对应端点的 method 字段
```

**关键改进**：
- ✅ 使用了"必须"、"不能"等强制性语言
- ✅ 提供了具体的错误示例（不能改成...）
- ✅ 强调"精确匹配"（exact match）

### 修复2：添加具体示例（已实施）

**在Prompt中添加了完整的示例**，展示如何正确使用API规范：

```
假设API规范中有以下端点：
- POST /user/register - 用户注册
- POST /user/login - 用户登录
- GET /user/profile - 获取用户信息

那么你生成的测试脚本必须使用这些exact路径：
{
  "teststeps": [
    {
      "name": "用户注册",
      "request": {
        "method": "POST",
        "url": "/user/register",  ← 精确使用API规范中的路径
        ...
      }
    }
  ]
}

⚠️ 注意：如果API规范中定义的是 "/api/users/register"，
那么你就必须使用 "/api/users/register"，不能自己改成其他格式！
```

**关键改进**：
- ✅ 提供了完整的正确示例
- ✅ 明确指出路径来源于API规范
- ✅ 强调不能自行修改

### 修复3：添加调试日志（已实施）

**在代码中添加了详细的日志输出**，记录传递给LLM的API规范信息：

```python
logger.info(f"传递给LLM的API规范数量: {len(api_specs_data)}")
for idx, spec in enumerate(api_specs_data[:3]):
    logger.info(f"API规范 {idx+1}: {spec.get('name')}, 端点数量: {len(spec.get('endpoints'))}")
    for ep_idx, endpoint in enumerate(spec.get('endpoints', [])[:5]):
        logger.info(f"  端点 {ep_idx+1}: {endpoint.get('method')} {endpoint.get('path')} - {endpoint.get('summary')}")
```

**用途**：
- ✅ 验证API规范是否正确传递给LLM
- ✅ 检查端点信息是否完整（method、path、summary）
- ✅ 快速诊断匹配问题

---

## 🧪 验证步骤

### 第1步：检查API规范端点信息

**目的**：确认数据库中存储的端点路径是否正确

**执行**：在 Anaconda Prompt 中运行

```bash
cd /d d:\dev\proj\aits-system\backend
conda activate aits-backend
python check_api_specs_endpoints.py
```

**预期输出**：

```
================================================================================
项目: 智慧物业项目 (ID: 1)
================================================================================

共有 1 个已完成的API规范

--------------------------------------------------------------------------------
API规范: 智慧物业-api-docs.json
描述: N/A
类型: swagger
状态: completed

端点数量: 20

端点列表（前20个）:
序号   方法      路径                                      摘要
----------------------------------------------------------------------------------------------------
1      POST     /user/register                            用户注册
2      POST     /user/login                               用户登录
3      GET      /user/profile                             获取用户信息
...
```

**检查项**：
- [ ] 是否有"用户注册"相关的端点？
- [ ] 端点的路径是否是 `/user/register`（而不是 `/api/users/register`）？
- [ ] 端点的方法是否正确（POST）？
- [ ] 端点的摘要是否清晰（如"用户注册"）？

**如果端点路径不对**：
- 说明Swagger文件中定义的路径就是错误的
- 需要更新Swagger文件或在数据库中手动修正

### 第2步：重启Django服务器

**重要**：修改了代码后必须重启

```bash
# 在Django服务器终端中
# 按 Ctrl+C 停止服务器
# 然后重新启动
python manage.py runserver
```

### 第3步：使用AI生成测试用例

1. **访问**：`http://localhost:5173/api-testing/scenario-generator`

2. **输入场景描述**：
   ```
   请针对系统管理用户的注册、登录流程设计测试用例
   ```

3. **点击发送**，等待AI生成

### 第4步：查看Django日志

**在Django服务器终端中查看日志输出**：

```
[INFO] 开始映射用户请求到API接口
[INFO] API规范 '智慧物业-api-docs.json' 匹配度: 0.45 (匹配3个关键词)
[INFO] 成功映射 1 个API接口
[INFO] 成功获取 1 个API规范详细信息

[INFO] 开始生成测试脚本（JSON格式）
[INFO] 传递给LLM的API规范数量: 1
[INFO] API规范 1: 智慧物业-api-docs.json, 端点数量: 20
[INFO]   端点 1: POST /user/register - 用户注册  ← 检查这里的路径
[INFO]   端点 2: POST /user/login - 用户登录
[INFO]   端点 3: GET /user/profile - 获取用户信息
...
```

**关键检查项**：
- [ ] "传递给LLM的API规范数量" > 0
- [ ] 端点列表中显示的路径是否正确（如 `/user/register`）
- [ ] 端点摘要是否清晰

### 第5步：检查生成的测试用例

1. **访问**：`http://localhost:5173/api-testing/test-cases/scenario`

2. **查看最新生成的测试用例**

3. **点击"编辑"**，查看完整的测试脚本

4. **检查 teststeps 中的 url 字段**：

**期望结果**：
```json
{
  "config": {
    "name": "用户注册登录流程测试",
    ...
  },
  "teststeps": [
    {
      "name": "用户注册",
      "request": {
        "method": "POST",
        "url": "/user/register",  ← 应该精确匹配API规范中的路径
        ...
      }
    },
    {
      "name": "用户登录",
      "request": {
        "method": "POST",
        "url": "/user/login",  ← 应该精确匹配
        ...
      }
    }
  ]
}
```

**如果路径仍然不对**：
- 检查Django日志中"端点列表"显示的路径是否正确
- 如果日志中的路径是错的，说明数据库中存储的就是错的，需要修正Swagger文件
- 如果日志中的路径是对的，但生成的还是错的，说明LLM仍然没有遵守规则

---

## 🔍 问题诊断流程

### 场景A：API规范中没有注册端点

**现象**：诊断脚本输出
```
❌ 未找到包含'注册'或'register'的端点
```

**原因**：Swagger文件中没有定义用户注册端点

**解决方案**：
1. 检查Swagger文件，确认是否包含注册端点
2. 如果没有，手动添加或重新生成Swagger文件
3. 重新上传到"API规范管理"

### 场景B：端点路径在数据库中就是错的

**现象**：诊断脚本输出
```
✓ 找到匹配端点:
  方法: POST
  路径: /api/v1/users/register  ← 这个路径本身就是错的
  摘要: 用户注册
```

**原因**：Swagger文件中定义的路径就是 `/api/v1/users/register`，但实际API是 `/user/register`

**解决方案**：

**方法1：修正Swagger文件（推荐）**
1. 打开Swagger文件（`智慧物业-api-docs.json`）
2. 找到注册端点的定义：
   ```json
   "paths": {
     "/api/v1/users/register": {  ← 修改这里
       "post": {
         "summary": "用户注册",
         ...
       }
     }
   }
   ```
3. 修改为正确的路径：
   ```json
   "paths": {
     "/user/register": {  ← 修改为实际的API路径
       "post": {
         "summary": "用户注册",
         ...
       }
     }
   }
   ```
4. 删除旧的API规范
5. 重新上传修正后的Swagger文件

**方法2：直接修改数据库（临时方案）**
```python
# 在Django shell中执行
from api_testing.models import APIEndpoint
ep = APIEndpoint.objects.get(path='/api/v1/users/register')
ep.path = '/user/register'
ep.save()
```

### 场景C：端点信息正确，但LLM仍然生成错误路径

**现象**：
- 诊断脚本显示路径正确：`/user/register`
- Django日志显示传递给LLM的路径正确：`/user/register`
- 但生成的测试用例中路径仍然是：`/api/users/register`

**原因**：LLM仍然在"优化"路径，没有严格遵守规则

**解决方案**：

**方法1：进一步增强Prompt约束**

在Prompt的开头添加更强硬的约束：

```python
prompt = ChatPromptTemplate.from_template("""
🚨 严重警告：你必须100%精确地使用API规范中的路径，任何修改都会导致测试失败！

你是一名资深测试开发工程师，负责根据 用户需求、业务上下文 和 API 规范 自动生成...

[后续Prompt保持不变]
""")
```

**方法2：使用更强的LLM模型**

某些LLM模型（如GPT-4、Claude-3.5）遵守指令的能力更强，可以尝试切换模型。

**方法3：后处理验证**

在生成脚本后，添加验证逻辑：

```python
# 在 _generate_scenario_script 方法的末尾添加
def _validate_generated_paths(self, generated_script, api_specs):
    """验证生成的路径是否匹配API规范"""
    # 提取所有合法的API路径
    valid_paths = set()
    for spec in api_specs:
        for endpoint in spec.get('endpoints', []):
            valid_paths.add((endpoint.get('method'), endpoint.get('path')))
    
    # 检查生成的脚本中的每个步骤
    test_data = json.loads(generated_script)
    for step in test_data.get('teststeps', []):
        method = step['request']['method']
        path = step['request']['url']
        
        if (method, path) not in valid_paths:
            logger.warning(f"生成的路径 {method} {path} 不在API规范中！")
            # 尝试找到最相似的路径
            for valid_method, valid_path in valid_paths:
                if valid_method == method and (
                    path in valid_path or valid_path in path
                ):
                    logger.info(f"自动修正: {path} -> {valid_path}")
                    step['request']['url'] = valid_path
                    break
    
    return json.dumps(test_data, ensure_ascii=False)
```

---

## 📋 修复清单

请按顺序执行以下步骤：

- [ ] **第1步**：运行诊断脚本
  ```bash
  cd /d d:\dev\proj\aits-system\backend
  conda activate aits-backend
  python check_api_specs_endpoints.py
  ```

- [ ] **第2步**：检查诊断结果
  - 是否找到了"用户注册"端点？
  - 端点的路径是否正确（`/user/register`）？
  - 端点的摘要是否清晰？

- [ ] **第3步**：如果端点路径不对
  - 修正Swagger文件
  - 重新上传到"API规范管理"
  - 再次运行诊断脚本验证

- [ ] **第4步**：重启Django服务器
  ```bash
  python manage.py runserver
  ```

- [ ] **第5步**：使用AI生成测试用例
  - 访问场景生成器页面
  - 输入场景描述
  - 等待生成完成

- [ ] **第6步**：查看Django日志
  - 检查"传递给LLM的API规范数量" > 0
  - 检查端点列表中的路径是否正确

- [ ] **第7步**：检查生成的测试用例
  - 查看生成的测试脚本
  - 验证 `request.url` 是否精确匹配API规范

- [ ] **第8步**：如果仍然不对
  - 截图Django日志中的端点列表
  - 截图生成的测试脚本中的url字段
  - 把两者对比，找出差异
  - 反馈给开发者进一步优化

---

## 📊 预期效果对比

### 修复前（错误）

**API规范中的端点**：
```
POST /user/register - 用户注册
```

**AI生成的测试用例**：
```json
{
  "request": {
    "method": "POST",
    "url": "/api/users/register"  ← 错误！LLM自己"优化"的路径
  }
}
```

### 修复后（正确）

**API规范中的端点**：
```
POST /user/register - 用户注册
```

**AI生成的测试用例**：
```json
{
  "request": {
    "method": "POST",
    "url": "/user/register"  ← 正确！精确匹配API规范
  }
}
```

---

## 🎯 核心改进总结

1. **Prompt约束更严格**
   - 明确要求"必须精确匹配"
   - 提供错误示例（不能改成...）
   - 使用强制性语言（必须、不能、严格）

2. **示例更具体**
   - 完整的正确示例
   - 明确指出路径来源
   - 强调不能自行修改

3. **调试能力增强**
   - 记录传递给LLM的API规范信息
   - 可以快速诊断匹配问题
   - 验证数据传递的完整性

4. **诊断工具完善**
   - 检查API规范端点脚本
   - 快速定位问题所在
   - 提供修正建议

---

## ❓ 常见问题

### Q1：修改了Prompt后，为什么还是生成错误的路径？

**A**：可能的原因：
1. 没有重启Django服务器
2. API规范本身就是错的（数据库中存储的路径不对）
3. LLM模型的指令遵守能力较弱
4. Prompt虽然严格，但LLM仍然在"创造性"地生成

**解决**：
- 先运行诊断脚本，确认API规范是否正确
- 确保重启了Django服务器
- 考虑使用更强的LLM模型（如GPT-4、Claude-3.5）

### Q2：如何确认LLM收到了正确的API规范信息？

**A**：查看Django服务器日志，找到类似以下的输出：

```
[INFO] 传递给LLM的API规范数量: 1
[INFO] API规范 1: 智慧物业-api-docs.json, 端点数量: 20
[INFO]   端点 1: POST /user/register - 用户注册
```

如果这里显示的路径是对的，说明数据传递正确。

### Q3：Swagger文件中的路径格式应该是什么样的？

**A**：路径应该与实际的API完全一致：

- 如果实际API是 `POST http://localhost:8000/user/register`
- 那么Swagger中的路径应该是 `/user/register`（不包含host）

**常见错误**：
- ❌ `/api/v1/users/register`（加了版本号）
- ❌ `/api/users/register`（加了/api前缀）
- ❌ `http://localhost:8000/user/register`（包含了host）

**正确格式**：
- ✅ `/user/register`（精确匹配实际路径）

---

## 📂 相关文件

- **修改的文件**：`backend/apps/ai_core/api_scenario_agent.py`
- **诊断脚本**：`backend/check_api_specs_endpoints.py`
- **文档**：`backend/docs/AI场景智能体生成错误API路径的问题修复.md`（本文件）

---

**现在请执行验证步骤，告诉我结果如何！** 🚀
