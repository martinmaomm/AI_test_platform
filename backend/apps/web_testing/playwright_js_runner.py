"""
Playwright JavaScript脚本执行器

该模块负责执行保存到数据库中的Playwright JavaScript测试脚本
"""

import os
import tempfile
import subprocess
import json
import logging
import fnmatch
from typing import Dict, Any, Optional, List
from pathlib import Path
import asyncio
import shutil

logger = logging.getLogger(__name__)


class PlaywrightRunner:
    """Playwright JavaScript脚本执行器"""
    
    def __init__(self, project_id: int, script_id: str):
        """
        初始化Playwright执行器
        
        Args:
            project_id: 项目ID
            script_id: 脚本ID
        """
        self.project_id = project_id
        self.script_id = script_id
        self.temp_dir = None
        self.package_json_path = None
        self.playwright_config_path = None
        
        # 设置项目根目录
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.playwright_workspace = os.path.join(self.project_root, "playwright_workspace")
        
    def __enter__(self):
        """上下文管理器入口"""
        self._setup_temp_environment()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self._cleanup_temp_environment()
        
    def _setup_temp_environment(self):
        """设置临时执行环境"""
        try:
            # 确保playwright工作空间目录存在
            os.makedirs(self.playwright_workspace, exist_ok=True)
            
            # 在项目目录下创建临时目录
            self.temp_dir = os.path.join(self.playwright_workspace, f"playwright_{self.script_id}")
            os.makedirs(self.temp_dir, exist_ok=True)
            logger.info(f"创建临时目录: {self.temp_dir}")
            
            # 创建package.json
            self._create_package_json()
            
            # 创建playwright.config.js
            self._create_playwright_config()
            
            # 创建.env文件
            self._create_env_file()
            
        except Exception as e:
            logger.error(f"设置临时环境失败: {e}")
            raise
            
    def _cleanup_temp_environment(self, keep_reports: bool = True):
        """清理临时环境"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                if keep_reports:
                    # 保留报告文件，只清理其他文件
                    self._cleanup_except_reports()
                    logger.info(f"保留报告文件，清理其他临时文件: {self.temp_dir}")
                else:
                    # 完全清理目录
                    shutil.rmtree(self.temp_dir)
                    logger.info(f"完全清理临时目录: {self.temp_dir}")
            except Exception as e:
                logger.warning(f"清理临时目录失败: {e}")
    
    def _cleanup_except_reports(self):
        """清理除报告文件外的其他文件"""
        if not self.temp_dir or not os.path.exists(self.temp_dir):
            return
            
        # 保留的报告相关目录和文件
        keep_patterns = [
            'playwright-report',
            'test-results',
            '*.html',
            '*.json',
            '*.png',
            '*.mp4'
        ]
        
        for root, dirs, files in os.walk(self.temp_dir, topdown=False):
            # 检查是否应该保留此目录
            should_keep = False
            for pattern in keep_patterns:
                if pattern in root or any(fnmatch.fnmatch(f, pattern) for f in files):
                    should_keep = True
                    break
            
            if not should_keep:
                # 删除不需要的文件
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        logger.warning(f"删除文件失败 {file_path}: {e}")
                
                # 删除空目录
                try:
                    if not os.listdir(root):
                        os.rmdir(root)
                except Exception as e:
                    logger.warning(f"删除目录失败 {root}: {e}")
                
    def _create_package_json(self):
        """创建package.json文件"""
        package_json = {
            "name": f"playwright-test-{self.script_id}",
            "version": "1.0.0",
            "type": "module",
            "scripts": {
                "test": "playwright test",
                "test:headed": "playwright test --headed",
                "test:debug": "playwright test --debug"
            },
            "devDependencies": {
                "@playwright/test": "^1.40.0"
            }
        }
        
        self.package_json_path = os.path.join(self.temp_dir, "package.json")
        with open(self.package_json_path, 'w', encoding='utf-8') as f:
            json.dump(package_json, f, indent=2, ensure_ascii=False)
            
        logger.info(f"创建package.json: {self.package_json_path}")
        
    def _create_playwright_config(self):
        """创建playwright.config.js文件"""
        playwright_config = """import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['html', { open: 'never' }]],
  use: {
    // baseURL: process.env.BASE_URL || 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],
  // webServer: {
  //   command: 'echo "No web server configured"',
  //   port: 3000,
  //   reuseExistingServer: !process.env.CI,
  // },
});
"""
        
        self.playwright_config_path = os.path.join(self.temp_dir, "playwright.config.js")
        with open(self.playwright_config_path, 'w', encoding='utf-8') as f:
            f.write(playwright_config)
            
        logger.info(f"创建playwright.config.js: {self.playwright_config_path}")
        
    def _create_env_file(self):
        """创建.env文件"""
        env_content = """# Playwright测试环境变量
BASE_URL=http://localhost:3000
HEADLESS=true
BROWSER=chromium
PLAYWRIGHT_HTML_REPORT_OPEN=never
"""
        
        env_path = os.path.join(self.temp_dir, ".env")
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(env_content)
            
        logger.info(f"创建.env文件: {env_path}")
        
    def _get_environment_variables(self) -> Dict[str, str]:
        """获取环境变量"""
        from django.conf import settings
        
        env_vars = os.environ.copy()
        
        # 添加Django设置中的环境变量
        if hasattr(settings, 'OPENAI_API_KEY'):
            env_vars['OPENAI_API_KEY'] = settings.OPENAI_API_KEY
        if hasattr(settings, 'OPENAI_BASE_URL'):
            env_vars['OPENAI_BASE_URL'] = settings.OPENAI_BASE_URL
        if hasattr(settings, 'DEEPSEEK_API_KEY'):
            env_vars['DEEPSEEK_API_KEY'] = settings.DEEPSEEK_API_KEY
        if hasattr(settings, 'DEEPSEEK_BASE_URL'):
            env_vars['DEEPSEEK_BASE_URL'] = settings.DEEPSEEK_BASE_URL
        
        # 添加Playwright相关环境变量，确保不会自动打开浏览器
        env_vars['PLAYWRIGHT_HTML_REPORT_OPEN'] = 'never'
        env_vars['HEADLESS'] = 'true'
        env_vars['CI'] = 'true'  # 设置为CI环境，禁用交互式功能
            
        return env_vars
        
    def _run_command(self, command: str, cwd: str = None, env: Dict[str, str] = None) -> subprocess.CompletedProcess:
        """运行命令"""
        try:
            logger.info(f"执行命令: {command}")
            logger.info(f"工作目录: {cwd or self.temp_dir}")
            
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd or self.temp_dir,
                env=env or self._get_environment_variables(),
                capture_output=True,
                text=True,
                timeout=300  # 5分钟超时
            )
            
            logger.info(f"命令执行完成，返回码: {result.returncode}")
            if result.stdout:
                logger.info(f"标准输出: {result.stdout}")
            if result.stderr:
                logger.warning(f"标准错误: {result.stderr}")
                
            return result
            
        except subprocess.TimeoutExpired as e:
            logger.error(f"命令执行超时: {e}")
            raise
        except Exception as e:
            logger.error(f"命令执行失败: {e}")
            raise
            
    def install_dependencies(self) -> bool:
        """安装依赖"""
        try:
            logger.info("开始安装依赖...")
            
            # 安装npm依赖
            result = self._run_command("npm install")
            if result.returncode != 0:
                logger.error(f"npm install失败: {result.stderr}")
                return False
                
            # 安装Playwright浏览器
            result = self._run_command("npx playwright install")
            if result.returncode != 0:
                logger.error(f"Playwright浏览器安装失败: {result.stderr}")
                return False
                
            logger.info("依赖安装成功")
            return True
            
        except Exception as e:
            logger.error(f"安装依赖失败: {e}")
            return False
            
    def create_test_file(self, script_content: str) -> str:
        """创建测试文件"""
        try:
            # 创建tests目录
            tests_dir = os.path.join(self.temp_dir, "tests")
            os.makedirs(tests_dir, exist_ok=True)
            
            # 创建测试文件
            test_file_path = os.path.join(tests_dir, f"test_{self.script_id}.spec.js")
            
            # 确保脚本内容以正确的格式保存
            if not script_content.strip().startswith("import"):
                # 如果脚本没有import语句，添加默认的import
                script_content = f"""import {{ test, expect }} from '@playwright/test';

{script_content}"""
            
            # 确保脚本内容以正确的格式保存
            if not script_content.strip().endswith("}"):
                # 如果脚本没有正确的结束，添加默认的结束
                script_content = f"""{script_content}

// 测试完成"""
            
            with open(test_file_path, 'w', encoding='utf-8') as f:
                f.write(script_content)
                
            logger.info(f"创建测试文件: {test_file_path}")
            logger.info(f"测试文件内容预览: {script_content[:200]}...")
            return test_file_path
            
        except Exception as e:
            logger.error(f"创建测试文件失败: {e}")
            raise
            
    def run_tests(self, test_file_path: str, options: Dict[str, Any] = None) -> Dict[str, Any]:
        """运行测试"""
        try:
            logger.info(f"开始运行测试: {test_file_path}")
            
            # 构建测试命令
            test_command = f"npx playwright test {os.path.basename(test_file_path)}"
            
            # 添加选项
            if options:
                if options.get('headed', False):
                    test_command += " --headed"
                if options.get('debug', False):
                    test_command += " --debug"
                if options.get('browser'):
                    test_command += f" --project {options['browser']}"
            
            # 确保不会自动打开浏览器显示报告
            test_command += " --reporter=html"
                    
            # 运行测试
            result = self._run_command(test_command)
            
            # 解析结果
            test_result = {
                'success': result.returncode == 0,
                'return_code': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'test_file': test_file_path
            }
            
            # 尝试读取测试报告
            report_dir = os.path.join(self.temp_dir, "playwright-report")
            if os.path.exists(report_dir):
                test_result['report_dir'] = report_dir
                
            # 如果测试失败，提供更详细的错误信息
            if not test_result['success']:
                logger.error(f"测试失败，返回码: {result.returncode}")
                logger.error(f"标准输出: {result.stdout}")
                logger.error(f"标准错误: {result.stderr}")
                
                # 尝试提供更友好的错误信息
                if "Process from config.webServer exited early" in result.stderr:
                    test_result['error'] = "Web服务器配置错误，请检查测试脚本中的URL配置"
                elif "No tests found" in result.stdout:
                    test_result['error'] = "未找到测试用例，请检查测试脚本格式"
                else:
                    test_result['error'] = result.stderr or result.stdout or "测试执行失败"
                
            logger.info(f"测试运行完成，成功: {test_result['success']}")
            return test_result
            
        except Exception as e:
            logger.error(f"运行测试失败: {e}")
            return {
                'success': False,
                'error': str(e),
                'test_file': test_file_path
            }
            
    def get_test_results(self) -> Dict[str, Any]:
        """获取测试结果"""
        try:
            # 查找测试结果文件
            results_dir = os.path.join(self.temp_dir, "test-results")
            if not os.path.exists(results_dir):
                return {'error': '测试结果目录不存在'}
                
            results = {}
            for root, dirs, files in os.walk(results_dir):
                for file in files:
                    if file.endswith(('.json', '.html', '.png', '.mp4')):
                        file_path = os.path.join(root, file)
                        relative_path = os.path.relpath(file_path, results_dir)
                        results[relative_path] = file_path
                        
            return results
            
        except Exception as e:
            logger.error(f"获取测试结果失败: {e}")
            return {'error': str(e)}


def playwright_runner(script_id: str, script_content: str, project_id: int, 
                     options: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    执行Playwright JavaScript脚本
    
    Args:
        script_id: 脚本ID
        script_content: 脚本内容
        project_id: 项目ID
        options: 执行选项
        
    Returns:
        执行结果字典
    """
    if options is None:
        options = {}
        
    try:
        logger.info(f"开始执行Playwright脚本: {script_id}")
        
        runner = PlaywrightRunner(project_id, script_id)
        try:
            # 设置临时环境
            runner._setup_temp_environment()
            
            # 安装依赖
            if not runner.install_dependencies():
                return {
                    'success': False,
                    'error': '依赖安装失败',
                    'script_id': script_id
                }
                
            # 创建测试文件
            test_file_path = runner.create_test_file(script_content)
            
            # 运行测试
            test_result = runner.run_tests(test_file_path, options)
            
            # 获取测试结果
            test_results = runner.get_test_results()
            test_result['test_results'] = test_results
            
            # 添加报告目录信息
            if test_result.get('success') and 'report_dir' in test_result:
                test_result['result'] = {
                    'status': 'passed' if test_result['success'] else 'failed',
                    'script_id': script_id,
                    'stdout': test_result.get('stdout', ''),
                    'stderr': test_result.get('stderr', ''),
                    'test_file': test_result.get('test_file', ''),
                    'return_code': test_result.get('return_code', 0),
                    'report_dir': test_result.get('report_dir')
                }
            
            # 清理临时环境，但保留报告文件
            #runner._cleanup_temp_environment(keep_reports=True)
            
            return test_result
            
        except Exception as e:
            # 发生异常时也清理环境
            #runner._cleanup_temp_environment(keep_reports=True)
            raise
            
    except Exception as e:
        logger.error(f"执行Playwright脚本失败: {e}")
        return {
            'success': False,
            'error': str(e),
            'script_id': script_id
        }


async def playwright_runner_async(script_id: str, script_content: str, project_id: int,
                                 options: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    异步执行Playwright JavaScript脚本
    
    Args:
        script_id: 脚本ID
        script_content: 脚本内容
        project_id: 项目ID
        options: 执行选项
        
    Returns:
        执行结果字典
    """
    # 在线程池中运行同步版本
    import concurrent.futures
    
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(playwright_runner, script_id, script_content, project_id, options)
        return future.result(timeout=600)  # 10分钟超时
