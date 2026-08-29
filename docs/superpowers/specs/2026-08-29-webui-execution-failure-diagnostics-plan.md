# WebUI 执行失败诊断与截图计划

## 文档状态

- 状态：已实施，待用户页面验收
- 设计确认日期：2026-08-29
- 当前验证平台：macOS、zsh
- 项目路径：`/Users/maoyijiu/Documents/tg-work/aits_v2`
- 变更级别：中高风险，多文件联动但不调整数据库结构

## 背景

当前 WebUI 自动化脚本失败后，用户在用例列表和执行详情中看到的主要是 Playwright/Pytest 原始异常，例如：

```text
playwright._impl._errors.TimeoutError: Locator.click: Timeout 30000ms exceeded.
```

这类信息可以供开发人员排查，但普通用户无法直接判断失败发生在哪个操作、哪个页面元素以及等待了多长时间。用例列表的失败提示还混入并重复展示预期结果，进一步降低了可读性。

执行日志中的 `baseurl: http://localhost:8000` 也与实际测试环境不一致。当前根因已经确认：`PlaywrightRunner._create_pytest_config()` 在生成临时 `pytest.ini` 时写死了该地址。测试脚本实际通过 `ExecutionConfig.base_url` 和 `PLAYWRIGHT_BASE_URL` 使用所选环境地址，因此这里主要是日志展示错误，但仍会误导排查人员。

此外，数据模型已经预留以下字段，但当前执行器没有真正产生失败截图：

- `WebUITestCaseExecutionDetail.screenshot_path`
- `WebUITestSuiteCaseExecution.screenshot_path`

执行器临时工作目录在未生成 Allure 报告时会被删除，因此截图不能只保存在临时目录中。

## 目标

1. 将常见 Playwright 执行错误转换为用户可理解的中文失败摘要。
2. 尽可能展示失败动作、目标元素、等待时间和可能原因，但不根据不完整日志臆测元素。
3. 保留完整原始输出，供开发人员在“技术日志”中继续排查。
4. 日志和执行详情中的测试地址与本次选中的环境一致，不再显示写死的 localhost。
5. 脚本失败时，在浏览器关闭前保存当前可视区域截图。
6. 单用例和测试套件中的失败子用例都能独立关联错误摘要与截图。
7. 不新增数据库字段，不改变已保存 Python Playwright 脚本的业务契约。

## 非目标

- 不使用大语言模型翻译异常。
- 不在本轮引入 Playwright Trace、视频录制或新的 Allure 报告体系。
- 不修改测试脚本生成逻辑、定位器生成策略或断言逻辑。
- 不承诺所有第三方异常都能识别；未知异常保留安全兜底和原始日志。
- 不在第一版实现定时清理任务；删除执行记录时同步清理关联截图即可。

## 方案对比

| 方案 | 实现方式 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- | --- |
| A：仅前端翻译 | 前端对 `error_message` 执行正则匹配 | 改动最少 | 无法稳定取得 Pytest 调用日志，截图和套件子用例难以关联 | 不采用 |
| B：后端诊断、执行器截图、前端分层展示 | 后端解析原始输出并生成摘要，执行器捕获失败截图，前端区分摘要与技术日志 | 信息来源可靠，可测试，可覆盖单用例和套件 | 涉及执行器、任务、序列化和前端组件 | **采用** |
| C：完整 Trace/视频/报告中心 | 每次执行保存 Trace、视频和全部附件 | 诊断信息最丰富 | 存储、权限、清理和页面改造范围过大 | 后续单独评估 |

## 总体设计

```text
用户启动执行
    ↓
Celery 读取测试环境和执行记录
    ↓
PlaywrightRunner 生成 pytest 文件和真实 pytest 配置
    ↓
脚本执行失败 ──→ 关闭浏览器前截取当前可视区域
    ↓
Pytest stdout/stderr ──→ 规则化错误诊断器
    ↓                         ↓
原始技术日志              中文失败摘要
    ↓                         ↓
执行详情日志              error_message / last_error_message
    ↓
前端展示摘要、真实环境、失败截图；技术日志默认折叠
```

## 详细设计

### 1. 失败诊断对象

新增独立的后端诊断模块，使用确定性规则解析 Pytest/Playwright 输出，不依赖 AI。建议定义不可变的数据对象 `FailureDiagnostic`：

| 字段 | 说明 | 示例 |
| --- | --- | --- |
| `category` | 机器可识别的失败分类 | `action_timeout` |
| `title` | 面向用户的短标题 | `点击元素超时` |
| `action` | 失败动作 | `点击` |
| `target` | 从调用日志确认的目标元素 | `按钮“登录”` |
| `timeout_ms` | 日志中确认的超时时间 | `30000` |
| `suggestion` | 与分类对应的排查建议 | `请确认按钮已显示且未被遮挡` |
| `technical_message` | 首条原始技术异常 | `Locator.click: Timeout...` |

第一版将用户可读内容格式化为多行文本，继续写入现有 `error_message` 和 `last_error_message` 字段：

```text
点击元素超时
操作对象：按钮“登录”
等待时间：30 秒
建议：请确认按钮已显示、未被遮挡且当前页面正确
```

完整 `stdout` 和 `stderr` 继续写入现有日志字段。这样不需要数据库迁移，也不会丢失技术证据。

### 2. 第一版错误分类

| Playwright/Pytest 特征 | 用户提示 | 需要提取的信息 |
| --- | --- | --- |
| `Locator.click` + `TimeoutError` | 点击元素超时 | 定位器、超时时间 |
| `Locator.fill` + `TimeoutError` | 输入内容超时 | 输入框定位器、超时时间 |
| `Locator.check` / `select_option` / `press` / `hover` | 对应操作超时 | 动作、定位器、超时时间 |
| `expect(...).to_be_visible` 等等待断言 | 页面校验未通过 | 目标元素、预期状态 |
| `strict mode violation` | 找到多个匹配元素 | 定位器、匹配数量（日志可确认时） |
| `Page.goto` + `net::ERR_*` | 页面访问失败 | 目标地址、浏览器网络错误 |
| `TargetClosedError` | 页面或浏览器已提前关闭 | 原始异常摘要 |
| 脚本语法错误、导入错误 | 测试脚本无法启动 | 文件、行号、异常类型 |
| 未识别异常 | 测试执行失败，请查看技术日志 | 首条可信异常 |

定位器仅在日志明确提供时进行中文化：

- `get_by_role("button", name="登录")` → `按钮“登录”`
- `get_by_text("保存")` → `文本“保存”`
- `get_by_label("用户名")` → `标签为“用户名”的输入项`
- `get_by_placeholder("请输入账号")` → `占位提示为“请输入账号”的输入项`
- `locator("#submit")` → `定位器“#submit”`

若无法从日志确认目标元素，只显示动作和超时时间，不生成虚假的按钮名称。

### 3. Base URL 修复

将 `_create_pytest_config(work_dir)` 调整为接收当前 `ExecutionConfig`，生成配置时使用本次执行环境的 `base_url`，并删除写死的 `http://localhost:8000`。

约束如下：

- 对地址去除回车和换行，避免破坏 `pytest.ini` 格式。
- Pytest 配置中的地址只用于日志和插件上下文展示；浏览器上下文仍由 `script_contract.materialize_script()` 统一设置。
- 单用例和套件执行必须走同一实现，避免其中一条链路再次出现 localhost。
- 环境未配置地址时，执行前继续失败并提示“测试环境缺少基础 URL”，不回退到 localhost。

### 4. 失败截图捕获

在 `materialize_script()` 生成的托管浏览器包装器中加入异常捕获：

1. 调用 `run(page)` 发生异常。
2. 在 `context.close()` 和 `browser.close()` 之前调用 `page.screenshot()`。
3. 默认使用 `full_page=False`，只保存当前可视区域，减少无关业务数据暴露。
4. 截图失败不得覆盖原始测试异常；记录截图失败日志后重新抛出原异常。
5. 浏览器启动失败、上下文创建失败或页面已经关闭时可能无法截图，此时错误摘要仍必须正常保存。

截图文件名由执行器生成，不接受测试脚本或用户输入直接拼接路径：

```text
webui_failure_screenshots/
└── execution_<execution_id>/
    ├── case_<test_case_id>.png
    └── single_case.png
```

Celery 在启动 Pytest 前创建目标目录并把绝对截图路径传给执行器。截图直接写入持久化目录，避免临时工作目录清理导致文件丢失。

### 5. 截图访问与权限

失败截图可能包含页面业务数据，不建议把文件路径直接作为公开静态地址展示。

第一版增加受现有登录和项目权限控制的截图读取接口：

- 根据执行记录和用例明细定位截图。
- 校验当前用户是否有权查看对应项目。
- 只允许读取数据库中已关联的 PNG 文件，不接受任意文件路径。
- 返回 `FileResponse`，并设置正确的 `Content-Type`。
- 前端通过现有认证请求获取 Blob，再生成临时对象 URL 供 `el-image` 预览。
- 对象 URL 在组件销毁或切换执行记录时调用 `URL.revokeObjectURL()`。

数据库中的 `screenshot_path` 保存相对受控路径，不保存客户端可构造的绝对文件系统路径。

### 6. 执行结果持久化

单用例失败时：

- `WebUITestExecution.error_message`：保存用户可读摘要。
- `WebUITestCaseExecutionDetail.error_message`：保存同一摘要。
- `WebUITestCase.last_error_message`：保存适合列表展示的摘要，截断时从开头保留，不能像当前实现一样从字符串末尾截取。
- `WebUITestCaseExecutionDetail.log/stdout/stderr`：继续保存原始技术输出。
- `WebUITestCaseExecutionDetail.screenshot_path`：保存截图相对路径。

套件失败时：

- 按 `test_case_<id>.py` 对 Pytest 失败段落进行归属。
- 每个 `WebUITestSuiteCaseExecution` 保存自己的摘要、日志片段和截图路径。
- 套件顶层 `WebUITestExecution.error_message` 只汇总失败数量和第一个失败原因，不把所有堆栈拼接到顶层。
- 单个子用例解析失败时使用通用摘要，不影响其他子用例的状态和截图关联。

### 7. 前端展示

#### 用例列表

失败提示调整为简洁结构：

```text
执行失败
点击元素超时
操作对象：按钮“登录”
等待时间：30 秒
```

- 不再在失败提示中重复展示“校验规则/预期结果”。
- 列表只展示摘要，不展示 Python 包名、堆栈或临时文件路径。
- 保留进入执行详情的入口。

#### 单用例执行详情

- 顶部增加“失败摘要”卡片。
- 执行环境显示环境名称和真实 Base URL。
- 有截图时显示“失败现场”，支持点击放大。
- 原始 `stdout/stderr` 放入默认折叠的“技术日志”。
- 页面内本次触及的英文标题改为中文，避免同一详情页中英文混用。

#### 套件执行详情

- 顶层显示通过、失败和跳过数量。
- 失败子用例展开后分别显示失败摘要和截图。
- 技术日志仍按套件保留，并默认折叠。

### 8. 文件清理

- 用例通过时不创建失败截图。
- 同一执行记录的文件名固定，重试新建执行记录，不覆盖历史记录。
- 删除执行记录时同步删除其 `execution_<id>` 截图目录。
- 文件删除失败只记录警告，不阻止数据库记录删除。
- 第一版不增加定时清理任务；后续如截图增长明显，再增加按保留天数清理策略。

## 预计修改文件

| 文件 | 计划改动 |
| --- | --- |
| `backend/apps/web_testing/execution_diagnostics.py` | 新增确定性错误分类、定位器中文化和摘要格式化 |
| `backend/apps/web_testing/script_contract.py` | 在托管浏览器包装器中捕获异常并保存失败截图 |
| `backend/apps/web_testing/playwright_python_runner.py` | 传递真实 Base URL、截图路径，解析单用例和套件错误 |
| `backend/apps/web_testing/tasks.py` | 创建持久化目录，保存摘要、技术日志和截图关联 |
| `backend/apps/web_testing/serializers.py` | 返回截图可用状态和受控读取地址，不暴露文件系统路径 |
| `backend/apps/web_testing/views.py` | 增加带项目权限校验的截图读取及删除清理逻辑 |
| `backend/apps/web_testing/urls.py` | 注册截图读取路由 |
| `backend/apps/web_testing/test_execution_diagnostics.py` | 新增错误分类和摘要单元测试 |
| `backend/apps/web_testing/test_script_contract.py` | 增加异常截图、截图失败不掩盖原异常、真实 Base URL 测试 |
| `backend/apps/web_testing/test_webui_metadata.py` | 增加任务持久化、权限、套件子用例和清理测试 |
| `frontend/src/views/web-testing/TestCases.vue` | 精简失败悬浮提示，移除重复预期结果 |
| `frontend/src/components/WebUITestCaseExecutionDetail.vue` | 展示摘要、真实环境、失败截图和折叠技术日志 |
| `frontend/src/components/WebUITestSuiteExecutionDetail.vue` | 展示失败子用例摘要、截图和折叠技术日志 |

实际实施前应再次通过 `rg` 确认路由文件和详情组件的当前组成关系；若已有公共媒体读取或错误卡片组件，应优先复用，避免重复实现。

## 实施步骤

### 阶段一：后端诊断基础

1. 先为错误分类器编写失败测试。
2. 实现 `FailureDiagnostic`、常见错误规则和安全兜底。
3. 替换现有 `extract_execution_error()` 的页面展示用途，同时保留兼容入口，避免其他调用方一次性失效。

### 阶段二：真实环境地址

1. 修改 Pytest 配置生成函数签名。
2. 单用例、标准套件和数据库套件工作区三条调用链统一传入 `ExecutionConfig`。
3. 增加地址为空、地址包含换行和真实地址展示测试。

### 阶段三：失败截图

1. 扩展脚本物化参数，传入受控截图绝对路径。
2. 在 `run(page)` 异常路径截图并重新抛出原异常。
3. 单用例和套件为每个用例生成独立截图文件名。
4. 验证临时目录清理后持久化截图仍存在。

### 阶段四：持久化和权限接口

1. 任务层保存友好摘要、原始日志和相对截图路径。
2. 实现截图权限读取接口。
3. 删除执行记录时清理关联截图。
4. 增加跨项目访问拒绝和路径穿越防护测试。

### 阶段五：前端交互

1. 调整用例列表失败提示。
2. 重构单用例执行详情的信息层级。
3. 补充套件失败子用例展示。
4. 实现认证截图加载、预览和对象 URL 回收。

### 阶段六：回归与手工验收

1. 执行后端聚焦测试和系统检查。
2. 执行前端生产构建。
3. 使用一个必然点击超时的脚本完成真实 Chrome 失败冒烟测试。
4. 使用一个正常脚本确认通过路径不受影响。
5. 使用包含一个通过、一个失败用例的套件确认子用例归属正确。

## 自动验证

后续实施完成后至少执行：

```bash
cd /Users/maoyijiu/Documents/tg-work/aits_v2/backend
source .venv/bin/activate
python manage.py test web_testing.test_execution_diagnostics
python manage.py test web_testing.test_script_contract web_testing.test_webui_metadata
python manage.py check
python manage.py makemigrations --check --dry-run

cd /Users/maoyijiu/Documents/tg-work/aits_v2/frontend
npm run build

cd /Users/maoyijiu/Documents/tg-work/aits_v2
git diff --check
```

若聚焦测试通过，还应运行当前 WebUI 自动化模块的完整测试集。

## 手工验收场景

### 场景 A：按钮点击超时

脚本定位一个不存在的按钮并点击，预期：

- 用例列表显示“点击元素超时”。
- 能确认定位器时显示按钮名称；不能确认时不虚构名称。
- 显示 30 秒等实际等待时间。
- 详情展示当前可视区域截图。
- 技术日志展开后仍能看到原始 `Locator.click` 异常。

### 场景 B：页面访问失败

使用无法连接的测试环境，预期：

- 提示“页面访问失败”，显示实际环境地址和浏览器网络错误。
- 不显示 `http://localhost:8000`。
- 浏览器未成功创建页面时允许没有截图，并明确显示“未能生成失败截图”。

### 场景 C：断言未通过

等待一个不存在的文本，预期：

- 提示“页面校验未通过”。
- 展示可确认的目标文本和等待时间。
- 截图显示断言失败时的页面状态。

### 场景 D：测试套件部分失败

执行一个包含一个通过用例和一个失败用例的套件，预期：

- 套件统计为一通过、一失败。
- 失败摘要和截图只关联失败用例。
- 通过用例不产生失败截图。

### 场景 E：访问权限

使用另一个无项目权限的账号请求失败截图，预期返回 `403` 或 `404`，不能读取文件。

## 验收标准

- [ ] 用例列表不再直接展示 Python/Playwright 原始堆栈。
- [ ] 常见点击、输入、断言、定位冲突和页面访问错误有清晰中文摘要。
- [ ] 日志中的 Base URL 与所选测试环境一致。
- [ ] 单用例失败时能查看当前可视区域截图。
- [ ] 套件失败时每个失败子用例有独立摘要和截图。
- [ ] 通过用例不生成失败截图。
- [ ] 截图失败不会覆盖或改变原始测试失败。
- [ ] 原始技术日志完整保留且默认折叠。
- [ ] 截图读取经过登录和项目权限校验。
- [ ] 删除执行记录后关联截图被清理。
- [ ] 不产生数据库迁移。
- [ ] 后端测试、系统检查、前端构建和 `git diff --check` 通过。

## 风险与控制

| 风险 | 控制措施 |
| --- | --- |
| Playwright 不同版本的错误格式存在差异 | 使用多条真实格式测试；未命中时安全回退到通用摘要 |
| 解析器误判目标元素 | 只使用日志明确出现的定位器；不完整时省略目标名称 |
| 截图包含业务数据 | 默认只截当前可视区域；通过权限接口读取；不记录密码输入值 |
| 截图操作自身失败 | 独立捕获并记录警告，始终重新抛出原测试异常 |
| 套件错误归属错误 | 按稳定的 `test_case_<id>.py` 文件名映射，并增加混合通过/失败测试 |
| 截图文件持续增长 | 删除执行记录时同步删除；定时保留策略作为后续独立需求 |
| 修改执行器影响已有脚本 | 保持 `async def run(page)` 契约不变，并运行脚本契约完整回归 |

## 回滚方案

- 后端修改通过独立提交交付；回滚该提交即可恢复原执行和展示逻辑。
- 不涉及数据库迁移，回滚时无需处理 Schema。
- 已生成的失败截图目录可以保留，不影响旧代码；确认无须保留后再按明确目录清理。
- 前端详情组件和列表提示可与后端提交一起回滚，避免新旧接口字段不一致。

## 后续可选增强

以下内容不纳入第一版：

1. Playwright Trace 在线查看。
2. 失败步骤前后多张截图。
3. 自动录屏。
4. 失败原因聚合统计和趋势图。
5. 可配置截图保留天数。
6. 根据失败分类给出可点击的定位器修复建议。

## 换账号后的实施交接

新任务开始时应先完成以下检查：

1. 阅读本计划书并确认需求没有变化。
2. 执行 `git status --short`，保留用户现有改动和浏览器压缩包。
3. 重新核对 `playwright_python_runner.py`、`script_contract.py`、`tasks.py` 和前端详情组件，防止代码已发生漂移。
4. 按“实施步骤”逐阶段修改，每个阶段先测试再进入下一阶段。
5. 不顺手修改 UI 自动化模块中与本计划无关的问题。
6. 完成后单独汇报已验证、未验证和依赖用户环境验证的内容，再由用户决定是否提交。

## 2026-08-29 实施结果

本计划已完成代码实施，且未新增数据库迁移。实现范围包括：

- 确定性 Playwright/Pytest 错误分类和中文失败摘要；
- 单用例及套件子用例失败截图；
- 真实测试环境 Base URL；
- 受项目报告权限保护的截图读取接口；
- 删除执行记录时清理截图；
- 用例列表精简提示，以及详情页摘要、截图和折叠技术日志。

已验证：

- `python manage.py test web_testing --keepdb`：61 个测试全部通过；
- `python manage.py check`：通过；
- `python manage.py makemigrations --check --dry-run`：无迁移变化；
- `npm run build`：通过；
- `git diff --check`：通过；
- 真实 Chrome 单用例失败冒烟：摘要、真实 Base URL 和截图均正确；
- 真实 Chrome 套件冒烟：一通过、一失败，截图仅关联失败子用例，日志未串线。

待用户在实际页面完成最终交互验收，确认视觉效果及历史执行记录兼容展示。
