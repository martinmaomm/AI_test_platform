# Miniconda 环境变量配置指南

## 问题说明

双击运行 `.bat` 文件时提示"未找到conda"，是因为 `conda` 命令没有添加到系统 PATH 环境变量中。

Anaconda Prompt 之所以能用，是因为它启动时会自动初始化 conda 环境。

## 解决方案

### 方案A：通过 Anaconda Prompt 运行（推荐，无需配置）

#### 步骤1: 打开 Anaconda Prompt

方法1：开始菜单搜索 "Anaconda Prompt (miniconda3)"
方法2：开始菜单 → Anaconda3/Miniconda3 → Anaconda Prompt

#### 步骤2: 运行脚本

在 Anaconda Prompt 中输入：

```bash
cd /d d:\dev\proj\aits-system\backend
修复场景数据-AnacondaPrompt版.bat
```

或者直接执行命令：

```bash
cd /d d:\dev\proj\aits-system\backend
conda activate aits-backend
python manage.py fix_scenario_fields
```

---

### 方案B：配置系统环境变量（一劳永逸）

#### 步骤1: 找到 Miniconda 安装路径

常见路径：
- `C:\Users\你的用户名\miniconda3`
- `C:\Users\你的用户名\anaconda3`
- `C:\ProgramData\miniconda3`
- `%LOCALAPPDATA%\miniconda3`

**如何查找**：
在 Anaconda Prompt 中输入：
```bash
where conda
```
会显示类似：`C:\Users\Administrator\miniconda3\Scripts\conda.exe`

#### 步骤2: 添加到系统环境变量

1. 右键"此电脑" → 属性 → 高级系统设置 → 环境变量
2. 在"系统变量"中找到 `Path`，点击"编辑"
3. 点击"新建"，添加以下三个路径（假设 miniconda 安装在 `C:\Users\Administrator\miniconda3`）：

```
C:\Users\Administrator\miniconda3
C:\Users\Administrator\miniconda3\Scripts
C:\Users\Administrator\miniconda3\Library\bin
```

4. 点击"确定"保存
5. **重启所有命令行窗口**

#### 步骤3: 初始化 conda

打开**新的** CMD 或 PowerShell，执行：

```bash
conda init cmd.exe
conda init powershell
```

#### 步骤4: 验证配置

重新打开 CMD，输入：
```bash
conda --version
```

应该显示版本号，如 `conda 23.x.x`

现在可以直接双击 `.bat` 文件运行了！

---

### 方案C：使用绝对路径的脚本（临时方案）

我已经创建了 `一键修复场景数据-兼容版.bat`，它会自动尝试找到 conda 的安装路径。

**使用方法**：
双击运行 `一键修复场景数据-兼容版.bat`

如果还是失败，会提示你手动在 Anaconda Prompt 中执行命令。

---

## 推荐方案总结

### 🥇 最简单：方案A（无需配置）

1. 打开 Anaconda Prompt
2. 输入：
   ```bash
   cd /d d:\dev\proj\aits-system\backend
   conda activate aits-backend
   python manage.py fix_scenario_fields
   ```

### 🥈 最方便：方案B（一次配置，终身受益）

配置环境变量后，可以：
- 在任何 CMD/PowerShell 中使用 conda
- 双击运行 `.bat` 文件
- VS Code 等编辑器直接识别 conda 环境

### 🥉 备用方案：方案C

使用 `一键修复场景数据-兼容版.bat`，它会自动尝试找到 conda

---

## 快速执行命令（复制粘贴即用）

**在 Anaconda Prompt 中执行**：

```bash
# 切换目录
cd /d d:\dev\proj\aits-system\backend

# 激活环境
conda activate aits-backend

# 执行修复
python manage.py fix_scenario_fields
```

完成后：
1. 重启 Django 服务器
2. 刷新浏览器页面
3. 查看效果

---

## 常见问题

### Q: 为什么 Anaconda Prompt 能用，CMD 不能用？

A: Anaconda Prompt 启动时会运行初始化脚本，自动设置 PATH 和激活 base 环境。普通 CMD 不会。

### Q: 配置环境变量后还是不行？

A: 确保：
1. 已重启所有命令行窗口
2. 已执行 `conda init cmd.exe`
3. 路径中没有空格或特殊字符

### Q: 不想配置环境变量，有其他办法吗？

A: 使用方案A，直接在 Anaconda Prompt 中运行命令即可。
