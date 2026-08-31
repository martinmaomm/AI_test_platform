# 定时任务中心使用说明

## 功能概述

定时任务中心是一个统一的定时任务管理模块，支持对Web、App、API测试套件进行定时执行。通过Celery + Celery Beat实现周期性调度，任务执行由Celery worker异步完成。

## 主要功能

### 1. 任务管理
- **创建任务**: 支持为Web、App、API测试套件创建定时任务
- **编辑任务**: 修改任务的执行时间、环境、通知配置等
- **任务状态**: 支持启用、暂停、禁用任务状态
- **手动执行**: 可以手动触发任务立即执行

### 2. 调度配置
- **Cron表达式**: 支持标准的Cron表达式配置执行时间
- **执行环境**: 选择测试执行的环境
- **通知配置**: 支持邮件和Webhook通知

### 3. 执行监控
- **执行日志**: 查看任务的执行历史和详细日志
- **执行统计**: 显示成功率、执行次数等统计信息
- **实时状态**: 监控任务的执行状态

## 使用方式

### 1. 从测试套件页面创建定时任务

#### Web测试套件
1. 进入"Web测试" → "测试套件管理"
2. 在套件列表中找到目标套件
3. 点击"定时任务"按钮
4. 系统会自动跳转到定时任务中心并预填充套件信息

#### API测试套件
1. 进入"API测试" → "测试套件管理"
2. 在套件列表中找到目标套件
3. 点击"定时任务"按钮
4. 系统会自动跳转到定时任务中心并预填充套件信息

### 2. 直接创建定时任务

1. 进入"定时任务中心" → "任务管理"
2. 点击"创建任务"按钮
3. 填写任务信息：
   - 任务名称
   - 测试类型（Web/API/App）
   - 测试套件
   - Cron表达式
   - 执行环境
   - 通知配置

### 3. Cron表达式示例

```
0 9 * * 1-5     # 工作日9点执行
0 0 * * 0       # 每周日0点执行
0 0 1 * *       # 每月1号0点执行
*/30 * * * *    # 每30分钟执行一次
0 0 1 1 *       # 每年1月1号0点执行
```

## 技术架构

### 后端技术栈
- **Django**: Web框架
- **Django REST Framework**: API框架
- **Celery**: 异步任务队列
- **Celery Beat**: 定时任务调度器
- **django-celery-beat**: 数据库存储定时任务配置
- **Redis**: 消息代理和结果存储

### 前端技术栈
- **Vue 3**: 前端框架
- **Element Plus**: UI组件库
- **Vue Router**: 路由管理

### 数据模型

#### ScheduledTask (定时任务)
- 基本信息：名称、描述、测试类型、套件ID
- 调度配置：Cron表达式、执行环境
- 状态管理：启用/暂停/禁用状态
- 时间记录：上次执行时间、下次执行时间

#### TaskExecutionLog (执行日志)
- 执行信息：开始时间、结束时间、执行状态
- 执行结果：日志内容、错误信息
- 统计信息：总用例数、通过数、失败数、跳过数
- 报告链接：测试报告URL

#### TaskNotification (通知配置)
- 通知方式：邮件/Webhook/不通知
- 通知条件：成功时通知、失败时通知
- 接收者配置：邮件地址、Webhook URL

## API接口

### 任务管理
- `GET /api/v1/scheduled-tasks/tasks/` - 获取任务列表
- `POST /api/v1/scheduled-tasks/tasks/` - 创建任务
- `PUT /api/v1/scheduled-tasks/tasks/{id}/` - 更新任务
- `DELETE /api/v1/scheduled-tasks/tasks/{id}/` - 删除任务
- `POST /api/v1/scheduled-tasks/tasks/{id}/run/` - 手动执行任务
- `PATCH /api/v1/scheduled-tasks/tasks/{id}/status/` - 更新任务状态

### 执行日志
- `GET /api/v1/scheduled-tasks/execution-logs/` - 获取执行日志列表
- `GET /api/v1/scheduled-tasks/execution-logs/{id}/` - 获取执行日志详情
- `GET /api/v1/scheduled-tasks/tasks/{id}/execution-logs/` - 获取指定任务的执行日志

### 辅助接口
- `GET /api/v1/scheduled-tasks/suite-choices/` - 获取测试套件选择列表
- `GET /api/v1/scheduled-tasks/statistics/` - 获取统计信息

## 部署说明

### 1. 安装依赖
```bash
pip install django-celery-beat==2.5.0
```

### 2. 数据库迁移
```bash
python manage.py makemigrations scheduled_tasks
python manage.py migrate
```

### 3. 启动服务
```bash
# 启动Django服务
python manage.py runserver

# 启动Celery Worker
celery -A aits_backend worker --loglevel=info

# 启动Celery Beat调度器
celery -A aits_backend beat --loglevel=info
```

### 4. 配置说明

#### Celery配置
- 使用Redis作为消息代理
- 使用django-celery-beat作为调度器
- 时区设置为Asia/Shanghai

#### 任务执行
- 任务执行通过统一的`run_scheduled_task`函数
- 根据任务类型调用相应的测试执行逻辑
- 支持Web、API、App三种测试类型

## 注意事项

1. **权限控制**: 只有任务创建者和项目成员可以管理任务
2. **环境依赖**: 确保Redis服务正常运行
3. **任务执行**: 确保Celery Worker正常运行
4. **通知配置**: 邮件通知需要配置SMTP服务
5. **Cron表达式**: 使用标准的5位Cron表达式格式

## 扩展功能

### 计划中的功能
- App测试套件支持
- 更多通知方式（钉钉、企业微信等）
- 任务执行历史分析
- 任务执行性能监控
- 批量任务操作
















