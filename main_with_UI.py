import os
import subprocess
import requests
import re
from pathlib import Path
import venv
import sys
import shutil
import time
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import threading
import json

# 尝试导入无头浏览器相关库
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


class WebSearch:
    """网络搜索类，用于从百度获取信息"""

    def __init__(self, ui_callback=None, max_results=5, timeout=10):
        self.ui_callback = ui_callback
        self.driver = None
        self.initialized = False
        self.max_results = max_results
        self.timeout = timeout

    def log(self, message):
        """输出日志"""
        print(message)
        if self.ui_callback:
            self.ui_callback(message + "\n")

    def initialize(self):
        """初始化WebDriver"""
        if not SELENIUM_AVAILABLE:
            self.log("❌ Selenium不可用，请安装相关库: pip install selenium webdriver-manager")
            return False

        try:
            self.log("初始化Chrome无头浏览器...")
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            self.initialized = True
            self.log("浏览器初始化成功")
            return True
        except Exception as e:
            self.log(f"❌ 浏览器初始化失败: {str(e)}")
            return False

    def search(self, keywords):
        """执行百度搜索并返回结果"""
        if not self.initialized and not self.initialize():
            return {"success": False, "error": "浏览器未初始化"}

        try:
            search_url = f"https://www.baidu.com/s?wd={keywords}"
            self.log(f"正在搜索: {keywords}")
            self.driver.get(search_url)

            # 等待搜索结果加载
            WebDriverWait(self.driver, self.timeout).until(
                EC.presence_of_element_located((By.CLASS_NAME, "result"))
            )

            # 获取搜索结果
            results = []
            elements = self.driver.find_elements(By.CLASS_NAME, "result")[:self.max_results]

            for elem in elements:
                try:
                    title_elem = elem.find_element(By.CSS_SELECTOR, "h3")
                    title = title_elem.text

                    link_elem = title_elem.find_element(By.TAG_NAME, "a")
                    link = link_elem.get_attribute("href")

                    abstract_elem = elem.find_element(By.CLASS_NAME, "c-abstract")
                    abstract = abstract_elem.text

                    results.append({
                        "title": title,
                        "link": link,
                        "abstract": abstract
                    })
                except Exception as e:
                    self.log(f"解析结果出错: {str(e)}")

            self.log(f"找到 {len(results)} 条搜索结果")
            return {"success": True, "results": results}
        except Exception as e:
            self.log(f"❌ 搜索失败: {str(e)}")
            return {"success": False, "error": str(e)}

    def close(self):
        """关闭浏览器"""
        if self.driver:
            self.driver.quit()
            self.initialized = False
            self.log("浏览器已关闭")


class AutoCoder:
    def __init__(self, task, notes="", workspace="safe_workspace", host="localhost", port=1234,
                 ui_callback=None, max_tokens=2000, expected_output=None, auto_expect=False,
                 max_attempts=5, command_timeout=30, api_timeout=120, search_results=5):
        """初始化代码生成器"""
        self.task = task
        self.notes = notes
        self.workspace = Path(workspace).absolute()
        self.venv_path = self.workspace / "venv"
        self.project_files = []
        self.error_log = []
        self.development_history = []
        self.host = host
        self.port = port
        self.ui_callback = ui_callback
        self.max_tokens = max_tokens
        self.expected_output = expected_output  # 用户指定的预期输出
        self.auto_expect = auto_expect  # 是否使用LLM生成的预期输出
        self.llm_expected_output = None  # LLM生成的预期输出
        self.original_task = task  # 保存原始任务
        self.next_steps = []  # 跟踪下一步需要实现的功能

        # 新增网络参数
        self.max_attempts = max_attempts
        self.command_timeout = command_timeout
        self.api_timeout = api_timeout
        self.search_results = search_results

        self.web_search = WebSearch(ui_callback, max_results=search_results, timeout=command_timeout)

        self.log("初始化工作目录: " + str(self.workspace))
        self.log(f"任务: {task}")
        if notes:
            self.log(f"任务注意事项: {notes}")
        if expected_output:
            self.log(f"用户指定的预期输出: {expected_output}")
        if auto_expect:
            self.log("启用自动预期验证: 将使用LLM生成的预期输出进行验证")

            # 初始化环境
        self._setup_workspace()
        self._setup_venv()

        # 创建任务跟踪文件
        self._initialize_task_tracking()

    def log(self, message):
        """输出日志信息，同时更新UI（如果有）"""
        print(message)
        if self.ui_callback:
            self.ui_callback(message + "\n")

    def _initialize_task_tracking(self):
        """初始化任务跟踪"""
        # 创建任务跟踪文件
        tracking_file = self.workspace / "task_tracking.json"
        tracking_data = {
            "original_task": self.original_task,
            "notes": self.notes,
            "expected_output": self.expected_output,
            "auto_expect": self.auto_expect,
            "current_step": "初始化环境",
            "next_steps": [],
            "progress": 0.0
        }

        with open(tracking_file, 'w', encoding='utf-8') as f:
            json.dump(tracking_data, f, ensure_ascii=False, indent=2)

        self.log("任务跟踪初始化完成")

    def _update_task_tracking(self, current_step, next_steps, progress):
        """更新任务跟踪"""
        tracking_file = self.workspace / "task_tracking.json"

        try:
            with open(tracking_file, 'r', encoding='utf-8') as f:
                tracking_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            tracking_data = {
                "original_task": self.original_task,
                "notes": self.notes,
                "expected_output": self.expected_output,
                "auto_expect": self.auto_expect
            }

        tracking_data["current_step"] = current_step
        tracking_data["next_steps"] = next_steps
        tracking_data["progress"] = progress

        # 如果有LLM生成的预期输出，也保存下来
        if self.llm_expected_output:
            tracking_data["llm_expected_output"] = self.llm_expected_output

        with open(tracking_file, 'w', encoding='utf-8') as f:
            json.dump(tracking_data, f, ensure_ascii=False, indent=2)

        self.next_steps = next_steps

    def _setup_workspace(self):
        """创建并清理工作目录"""
        if self.workspace.exists():
            self.log(f"清理工作目录: {self.workspace}")
            # 清理现有文件
            for item in self.workspace.glob('*'):
                if item.is_file():
                    try:
                        item.unlink()
                    except Exception as e:
                        self.log(f"无法删除文件 {item}: {e}")
                elif item.is_dir() and item.name != 'venv':  # 保留venv
                    try:
                        shutil.rmtree(item)
                    except Exception as e:
                        self.log(f"无法删除目录 {item}: {e}")
        else:
            self.log(f"创建工作目录: {self.workspace}")
            self.workspace.mkdir(parents=True, exist_ok=True)

    def _setup_venv(self):
        """创建虚拟环境"""
        if not self.venv_path.exists():
            self.log("创建虚拟环境...")
            try:
                venv.create(self.venv_path, with_pip=True)
                self.log("虚拟环境创建成功")
            except Exception as e:
                self.log(f"创建虚拟环境失败: {e}")
                self.error_log.append(f"创建虚拟环境失败: {e}")

    def _get_python_path(self):
        """获取虚拟环境中的Python解释器路径"""
        # 检查Windows路径
        win_path = self.venv_path / "Scripts" / "python.exe"
        if win_path.exists():
            return str(win_path)

            # 检查Unix路径
        unix_path = self.venv_path / "bin" / "python"
        if unix_path.exists():
            return str(unix_path)

            # 返回系统Python
        return "python"

    def _get_pip_path(self):
        """获取虚拟环境中的pip路径"""
        # 检查Windows路径
        win_path = self.venv_path / "Scripts" / "pip.exe"
        if win_path.exists():
            return str(win_path)

            # 检查Unix路径
        unix_path = self.venv_path / "bin" / "pip"
        if unix_path.exists():
            return str(unix_path)

            # 返回系统pip
        return "pip"

    def _call_llm(self, prompt):
        """调用LLM API"""
        try:
            self.log("请求LLM生成代码...")
            api_url = f"http://{self.host}:{self.port}/v1/chat/completions"

            messages = [
                {
                    "role": "system",
                    "content": "你是一个Python专家，请分析问题并生成代码解决方案。使用<think>标签记录你的思考过程。"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]

            payload = {
                "model": "local-model",
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": self.max_tokens
            }

            response = requests.post(api_url, json=payload, timeout=self.api_timeout)

            if response.status_code == 200:
                content = response.json()['choices'][0]['message']['content']
                self.log("LLM响应成功")
                return content.strip()
            else:
                error_msg = f"API调用失败: {response.status_code}"
                self.error_log.append(error_msg)
                self.log(error_msg)
                return None

        except Exception as e:
            error_msg = f"LLM调用错误: {str(e)}"
            self.error_log.append(error_msg)
            self.log(error_msg)
            return None

    def _generate_code(self, context):
        """生成代码的提示词构建"""
        # 组合任务和注意事项
        task_with_notes = self.task
        if self.notes:
            task_with_notes += f"\n\n[重要注意事项]\n{self.notes}"

        auto_expect_prompt = """同时，你需要准确预测代码的输出结果，并在响应中包含[EXPECTED OUTPUT]部分。这个部分应该包含运行代码后预期得到的精确输出，这将用于验证代码是否正确执行。""" if self.auto_expect else ""

        prompt = f"""请分析并完成以下任务：  

[原始任务需求]  
{task_with_notes}  

[当前执行环境]  
- 已生成文件: {', '.join(self.project_files[-3:]) if self.project_files else '无'}  
- 最近错误日志: {', '.join(self.error_log[-3:]) if self.error_log else '无'}  
- 当前进度: {context['current_step']} ({context['progress'] * 100:.0f}%)  
- 下一步需要解决的问题: {', '.join(context['next_steps']) if 'next_steps' in context else '无'}  

{'[用户指定的预期输出] ' + self.expected_output if self.expected_output else ''}  

作为Python开发专家，请：  
1. 在<think>标签中分析当前状况并规划解决方案  
2. 然后使用以下固定格式给出行动方案：  

[ACTION]  
(必须且只能选择以下之一)  
CODE - 生成代码文件  
COMMAND - 执行环境命令  
SEARCH - 搜索相关资料  

[CONTENT]  
根据ACTION类型，提供具体内容：  
- CODE时: 包含文件名和完整代码  
  # filename: xxx.py  
  代码内容...  

- COMMAND时: 提供命令  
  pip install xxx 或 python xxx.py  

- SEARCH时: 提供搜索关键词  
  keyword1 keyword2 ...  

{auto_expect_prompt}  

[EXPECTED OUTPUT]  
运行代码后的精确预期输出结果...  

[NEXT STEPS]  
- 列出下一步需要实现的功能或需要解决的问题  
- 每行一个步骤  

请确保每个响应包含且仅包含[ACTION]、[CONTENT]、{('[EXPECTED OUTPUT]' if self.auto_expect else '')}和[NEXT STEPS]部分。  
"""
        return self._call_llm(prompt)

    def _parse_response(self, response):
        """解析LLM的响应，适配DeepSeek模型的输出特点"""
        try:
            # 提取思考过程
            think_match = re.search(r'<think>(.*?)</think>', response, re.DOTALL)
            thinking = think_match.group(1).strip() if think_match else ""

            # 提取动作类型(支持多种格式)
            action = None
            content = None
            next_steps = []
            expected_output = None

            # 尝试提取标准格式的ACTION
            action_match = re.search(r'\[ACTION\]\s*(CODE|COMMAND|SEARCH)', response, re.IGNORECASE)
            if action_match:
                action = action_match.group(1).upper()

                # 如果没有明确的ACTION标记，尝试通过内容推断
            if not action:
                if "# filename:" in response:
                    action = "CODE"
                elif "pip install" in response or "python " in response:
                    action = "COMMAND"
                elif re.search(r'搜索|关键词|search', response, re.IGNORECASE):
                    action = "SEARCH"

                    # 提取预期输出
            expected_match = re.search(r'\[EXPECTED OUTPUT\](.*?)(?=\[|$)', response, re.DOTALL)
            if expected_match:
                expected_output = expected_match.group(1).strip()
                if expected_output:
                    self.log("提取到LLM生成的预期输出")
                    self.llm_expected_output = expected_output

                    # 提取内容
            if action == "CODE":
                # 提取代码块和文件名
                file_match = re.search(r'# filename:\s*(\S+)', response)
                code_block_match = re.search(r'```python\s*(.*?)\s*```', response, re.DOTALL)

                if file_match and code_block_match:
                    filename = file_match.group(1).strip()
                    code = code_block_match.group(1).strip()
                    content = f"# filename: {filename}\n{code}"
                else:
                    # 备用提取方法
                    code_section = response.split("# filename:", 1)
                    if len(code_section) > 1:
                        code_part = code_section[1].strip()
                        filename_match = re.search(r'^([\w\.]+)', code_part)
                        filename = filename_match.group(1) if filename_match else "main.py"
                        content = f"# filename: {filename}\n{code_part}"

            elif action == "COMMAND":
                # 提取命令
                command_match = re.search(r'(pip install\s+\S+|python\s+[\w\.]+)', response)
                if command_match:
                    content = command_match.group(1)
                else:
                    # 备用提取方法
                    for line in response.split('\n'):
                        if line.strip().startswith('pip ') or line.strip().startswith('python '):
                            content = line.strip()
                            break

            elif action == "SEARCH":
                # 提取搜索关键词
                search_match = re.search(r'\[CONTENT\]\s*(.*?)(?=\[|$)', response, re.DOTALL)
                if search_match:
                    content = search_match.group(1).strip()
                else:
                    lines = response.split('\n')
                    for i, line in enumerate(lines):
                        if "SEARCH" in line.upper() and i + 1 < len(lines):
                            content = lines[i + 1].strip()
                            break

                            # 提取下一步步骤
            next_steps_match = re.search(r'\[NEXT STEPS\](.*?)($|\[)', response, re.DOTALL)
            if next_steps_match:
                steps_text = next_steps_match.group(1).strip()
                next_steps = [step.strip().strip('-').strip() for step in steps_text.split('\n') if step.strip()]

                # 确保我们至少得到了一些内容
            if not content:
                self.log("警告: 无法提取有效内容，使用原始响应")
                content = response

                # 如果我们没有得到明确的动作类型，基于内容再次推断
            if not action:
                if "# filename:" in content or "```python" in content:
                    action = "CODE"
                elif "pip " in content or "python " in content:
                    action = "COMMAND"
                else:
                    action = "SEARCH"

            self.log(f"解析结果: 动作={action}")
            if expected_output:
                self.log(f"LLM预期输出: {expected_output}")
            if next_steps:
                self.log(f"下一步计划: {', '.join(next_steps)}")

                # 记录开发历史
            self.development_history.append({
                "thinking": thinking,
                "action": action,
                "content": content,
                "expected_output": expected_output,
                "next_steps": next_steps
            })

            return action, content, thinking, next_steps

        except Exception as e:
            error_msg = f"响应解析错误: {str(e)}"
            self.error_log.append(error_msg)
            self.log(error_msg)
            self.log(f"原始响应: {response[:100]}...")
            return "ERROR", response, "", []

    def _extract_code_from_response(self, content):
        """从响应中提取代码和文件名"""
        try:
            # 确保有文件名
            if "# filename:" not in content:
                # 尝试查找或推断文件名
                code_match = re.search(r'```python\s*(.*?)\s*```', content, re.DOTALL)
                if code_match:
                    code = code_match.group(1).strip()
                    return "main.py", code
                else:
                    return "main.py", content

                    # 提取文件名
            file_match = re.search(r'# filename:\s*(\S+)', content)
            filename = file_match.group(1) if file_match else "main.py"

            # 提取代码
            code_parts = []
            capture = False

            # 处理Markdown代码块
            if "```python" in content:
                code_match = re.search(r'```python\s*(.*?)\s*```', content, re.DOTALL)
                if code_match:
                    return filename, code_match.group(1).strip()

                    # 处理常规代码
            for line in content.split('\n'):
                if line.strip().startswith('# filename:'):
                    continue
                code_parts.append(line)

            code = '\n'.join(code_parts).strip()
            return filename, code

        except Exception as e:
            self.error_log.append(f"代码提取错误: {str(e)}")
            return "main.py", content

    def _execute_safe(self, code_block):
        """安全执行生成的代码"""
        try:
            # 提取文件名和代码
            filename, code = self._extract_code_from_response(code_block)

            self.log(f"保存代码到文件: {filename}")
            self.log("代码内容:")
            self.log("-" * 40)
            self.log(code)
            self.log("-" * 40)

            # 保存代码文件
            file_path = self.workspace / filename
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(code)

            self.project_files.append(str(file_path))

            # 在虚拟环境中执行
            python_path = self._get_python_path()
            self.log(f"使用Python解释器: {python_path}")
            self.log(f"执行代码: {filename}")

            cmd = [python_path, str(file_path)]
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.command_timeout,
                    cwd=str(self.workspace)
                )

                execution_result = {
                    "success": result.returncode == 0,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode
                }

                self.log(f"执行结果: {'成功' if result.returncode == 0 else '失败'}")
                self.log(f"标准输出: {result.stdout}")

                if result.stderr:
                    self.log(f"错误输出: {result.stderr}")

                return execution_result

            except subprocess.TimeoutExpired:
                self.error_log.append(f"执行超时: {filename}")
                return {"success": False, "error": "执行超时"}
            except Exception as e:
                self.error_log.append(f"执行异常: {str(e)}")
                return {"success": False, "error": str(e)}

        except Exception as e:
            error_msg = f"代码执行准备失败: {str(e)}"
            self.error_log.append(error_msg)
            self.log(error_msg)
            return {"success": False, "error": str(e)}

    def _run_safe_command(self, command):
        """安全执行命令"""
        self.log(f"执行命令: {command}")

        # 只允许安全命令
        if command.startswith('pip install'):
            package = command.split('pip install')[1].strip()
            pip_path = self._get_pip_path()

            self.log(f"使用pip安装包: {package}")
            try:
                result = subprocess.run(
                    [pip_path, 'install', package],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False
                )

                if result.returncode == 0:
                    msg = f"包安装成功: {package}"
                    self.log(msg)
                    return {"success": True, "message": msg, "stdout": result.stdout}
                else:
                    msg = f"包安装失败: {result.stderr}"
                    self.error_log.append(msg)
                    self.log(msg)
                    return {"success": False, "error": msg}

            except Exception as e:
                msg = f"包安装异常: {str(e)}"
                self.error_log.append(msg)
                self.log(msg)
                return {"success": False, "error": str(e)}

        elif command.startswith('python '):
            script = command.split('python ')[1].strip()
            python_path = self._get_python_path()
            script_path = self.workspace / script

            if not script_path.exists():
                msg = f"脚本不存在: {script}"
                self.error_log.append(msg)
                self.log(msg)
                return {"success": False, "error": msg}

            self.log(f"执行Python脚本: {script}")
            try:
                result = subprocess.run(
                    [python_path, script],
                    capture_output=True,
                    text=True,
                    timeout=self.command_timeout,
                    cwd=str(self.workspace),
                    check=False
                )

                self.log(f"脚本执行结果: {'成功' if result.returncode == 0 else '失败'}")
                self.log(f"标准输出: {result.stdout}")

                if result.stderr:
                    self.log(f"错误输出: {result.stderr}")

                return {
                    "success": result.returncode == 0,
                    "stdout": result.stdout,
                    "stderr": result.stderr
                }

            except Exception as e:
                msg = f"脚本执行异常: {str(e)}"
                self.error_log.append(msg)
                self.log(msg)
                return {"success": False, "error": str(e)}
        else:
            msg = f"不支持的命令: {command}"
            self.error_log.append(msg)
            self.log(msg)
            return {"success": False, "error": msg}

    def _perform_web_search(self, keywords):
        """执行网络搜索"""
        return self.web_search.search(keywords)

    def validate_result(self, result):
        """验证执行结果"""
        if not isinstance(result, dict):
            return False

        if not result.get("success", False):
            return False

        stdout = result.get("stdout", "").strip()

        # 如果自动预期验证已启用且有LLM生成的预期输出，使用它进行验证
        if self.auto_expect and self.llm_expected_output:
            expected = self.llm_expected_output.strip()
            self.log("使用LLM生成的预期输出进行验证...")
            # 否则使用用户指定的预期输出
        elif self.expected_output:
            expected = self.expected_output.strip()
            self.log("使用用户指定的预期输出进行验证...")
        else:
            # 没有预期输出，只验证程序执行成功
            self.log("没有预期输出，仅验证程序执行成功")
            return True

            # 精确匹配
        if stdout == expected:
            self.log("✅ 输出与预期完全匹配")
            return True

            # 包含匹配
        if expected in stdout:
            self.log("✅ 输出包含预期内容")
            return True

            # 模糊匹配：去除空白字符后比较
        stdout_normalized = re.sub(r'\s+', '', stdout)
        expected_normalized = re.sub(r'\s+', '', expected)
        if stdout_normalized == expected_normalized:
            self.log("✅ 输出与预期基本匹配（忽略空白字符）")
            return True

            # 经典的Hello World检查
        if "Hello, World!" in stdout:
            self.log("✅ 检测到Hello World输出")
            return True

        self.log("❌ 输出与预期不匹配")
        return False

    def development_cycle(self):
        """开发主循环"""
        context = {
            "current_step": "初始化开发环境",
            "progress": 0.0,
            "next_steps": ["分析任务需求", "编写初始代码"]
        }

        # 更新任务跟踪
        self._update_task_tracking(context["current_step"], context["next_steps"], context["progress"])

        for step in range(self.max_attempts):
            self.log(f"\n{'=' * 20} 开发周期 {step + 1}/{self.max_attempts} {'=' * 20}")

            # 生成代码
            llm_response = self._generate_code(context)
            if not llm_response:
                self.log("LLM响应失败，重试...")
                time.sleep(1)
                continue

                # 解析响应
            action, content, thinking, next_steps = self._parse_response(llm_response)

            # 显示思考过程
            if thinking:
                self.log("\n思考过程:")
                self.log("-" * 40)
                self.log(thinking[:500] + "..." if len(thinking) > 500 else thinking)
                self.log("-" * 40)

                # 如果解析失败，尝试进行修复
            if action == "ERROR":
                self.log("响应解析失败，尝试简单解析...")
                # 尝试简单启发式解析
                if "# filename:" in llm_response:
                    action = "CODE"
                    content = llm_response
                elif "pip install" in llm_response or "python " in llm_response:
                    action = "COMMAND"
                    # 提取第一个看起来像命令的行
                    for line in llm_response.split('\n'):
                        if "pip install" in line or "python " in line:
                            content = line.strip()
                            break
                    if not content:
                        content = llm_response
                else:
                    self.log("无法解析内容，跳过此周期")
                    context["current_step"] = "修复解析错误"
                    context["progress"] = min(1.0, (step + 1) / self.max_attempts)
                    self._update_task_tracking(context["current_step"], next_steps or context.get("next_steps", []),
                                               context["progress"])
                    continue

            self.log(f"执行动作: {action}")

            # 执行对应操作
            if action == "CODE":
                result = self._execute_safe(content)
                validation_result = self.validate_result(result)
                if validation_result:
                    self.log("\n✅ 代码执行成功!")
                    self.log(f"输出: {result.get('stdout', '')}")
                    return True
                else:
                    error_msg = result.get("stderr", result.get("error", "未知错误"))
                    if not error_msg and result.get("success", False):
                        # 执行成功但验证失败，可能是输出格式不匹配
                        error_msg = f"输出不符合预期: {result.get('stdout', '')}"
                    self.log(f"\n❌ 代码验证失败: {error_msg}")
                    self.error_log.append(f"验证失败: {error_msg}")
                    context["current_step"] = "修复执行错误"

            elif action == "COMMAND":
                result = self._run_safe_command(content)
                if result.get("success", False):
                    self.log(f"\n✅ 命令执行成功: {result.get('message', '')}")
                    if "stdout" in result:
                        self.log(f"输出: {result['stdout']}")
                else:
                    error_msg = result.get("error", "未知错误")
                    self.log(f"\n❌ 命令执行失败: {error_msg}")
                    self.error_log.append(f"命令失败: {error_msg}")
                context["current_step"] = "执行环境配置"

            elif action == "SEARCH":
                self.log(f"\n🔍 搜索关键词: {content}")
                search_result = self._perform_web_search(content)
                if search_result.get("success", False):
                    results = search_result.get("results", [])
                    self.log(f"找到 {len(results)} 条搜索结果:")
                    for i, result in enumerate(results):
                        self.log(f"\n结果 {i + 1}: {result['title']}")
                        self.log(f"链接: {result['link']}")
                        self.log(f"摘要: {result['abstract'][:200]}...")
                else:
                    error_msg = search_result.get("error", "搜索失败")
                    self.log(f"❌ 搜索失败: {error_msg}")
                    self.error_log.append(f"搜索失败: {error_msg}")
                context["current_step"] = "搜索相关资料"

                # 更新进度
            context["progress"] = min(1.0, (step + 1) / self.max_attempts)
            # 更新下一步计划
            context["next_steps"] = next_steps if next_steps else context.get("next_steps", [])
            # 更新任务跟踪
            self._update_task_tracking(context["current_step"], context["next_steps"], context["progress"])

        self.log("\n❌ 达到最大重试次数，开发失败")
        return False

    def get_summary(self):
        """获取开发摘要"""
        summary = "\n" + "=" * 50 + "\n"

        if self.project_files:
            summary += "✅ 开发成功!\n\n"
            summary += "生成的文件:\n"
            for file in self.project_files:
                summary += f"- {file}\n"

                # 显示最终文件内容
            latest_file = self.project_files[-1]
            summary += f"\n最终文件内容 ({latest_file}):\n"
            summary += "-" * 40 + "\n"
            try:
                with open(latest_file, 'r', encoding='utf-8') as f:
                    summary += f.read() + "\n"
            except Exception as e:
                summary += f"无法读取文件: {e}\n"
            summary += "-" * 40 + "\n"
        else:
            summary += "❌ 开发失败\n\n"
            summary += "错误日志:\n"
            for error in self.error_log[-10:]:  # 仅显示最近10条错误
                summary += f"- {error}\n"

        summary += "\n开发历史总结:\n"
        for i, entry in enumerate(self.development_history):
            summary += f"\n周期 {i + 1}:\n"
            summary += f"- 动作: {entry['action']}\n"
            if entry.get('thinking'):
                thinking_summary = entry['thinking'][:100] + "..." if len(entry['thinking']) > 100 else entry[
                    'thinking']
                summary += f"- 思考摘要: {thinking_summary}\n"
            if entry.get('expected_output'):
                summary += f"- 预期输出: {entry['expected_output']}\n"
            if entry.get('next_steps'):
                summary += f"- 下一步计划: {', '.join(entry['next_steps'])}\n"

        return summary


class AutoCoderGUI:
    def __init__(self, root):
        self.root = root
        root.title("AutoCoder - AI代码生成器")
        root.geometry("900x750")  # 略微增加高度以适应新控件
        root.minsize(800, 650)

        # 全局字体和颜色设置
        self.title_font = ("Arial", 14, "bold")
        self.normal_font = ("Arial", 10)
        self.code_font = ("Courier New", 10)
        self.bg_color = "#f5f5f5"
        self.header_color = "#e0e0e0"

        # 创建主框架
        self.main_frame = tk.Frame(root, bg=self.bg_color)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 创建顶部输入区域
        self.setup_input_area()

        # 创建日志输出区域
        self.setup_log_area()

        # 创建底部状态栏
        self.status_var = tk.StringVar()
        self.status_var.set("就绪")
        self.status_bar = tk.Label(
            root,
            textvariable=self.status_var,
            bd=1,
            relief=tk.SUNKEN,
            anchor=tk.W
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # 运行状态
        self.running = False
        self.auto_coder = None

    def setup_input_area(self):
        """设置输入区域"""
        input_frame = tk.LabelFrame(self.main_frame, text="任务输入", font=self.title_font, bg=self.bg_color)
        input_frame.pack(fill=tk.X, pady=(0, 10))

        # 任务描述文本框
        task_label = tk.Label(
            input_frame,
            text="任务描述:",
            font=self.normal_font,
            bg=self.bg_color,
            anchor=tk.W
        )
        task_label.pack(fill=tk.X, padx=10, pady=(10, 0))

        self.task_text = scrolledtext.ScrolledText(
            input_frame,
            height=5,
            font=self.normal_font,
            wrap=tk.WORD
        )
        self.task_text.pack(fill=tk.X, padx=10, pady=(0, 10))

        # 添加一些默认任务示例
        default_task = """创建一个Python程序，打印杨辉三角形的前4行  
要求：  
1. 使用math.comb函数计算组合数  
2. 每行元素之间用空格分隔  
3. 输出应为4行，分别显示杨辉三角形的前4行"""
        self.task_text.insert(tk.END, default_task)

        # 预期输出和自动验证区域
        expected_frame = tk.Frame(input_frame, bg=self.bg_color)
        expected_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        # 添加自动预期验证复选框
        self.auto_expect_var = tk.IntVar(value=1)  # 默认启用
        self.auto_expect_check = tk.Checkbutton(
            expected_frame,
            text="自动预期验证 (使用LLM生成的预期输出进行验证)",
            variable=self.auto_expect_var,
            command=self.toggle_expected_input,
            font=self.normal_font,
            bg=self.bg_color
        )
        self.auto_expect_check.pack(anchor=tk.W)

        # 预期输出文本框
        expected_label = tk.Label(
            input_frame,
            text="预期输出 (用于验证，自动预期验证启用时可选):",
            font=self.normal_font,
            bg=self.bg_color,
            anchor=tk.W
        )
        expected_label.pack(fill=tk.X, padx=10, pady=(0, 0))

        self.expected_text = scrolledtext.ScrolledText(
            input_frame,
            height=3,
            font=self.normal_font,
            wrap=tk.WORD
        )
        self.expected_text.pack(fill=tk.X, padx=10, pady=(0, 10))

        # 添加默认预期输出
        default_expected = """1  
1 1  
1 2 1  
1 3 3 1"""
        self.expected_text.insert(tk.END, default_expected)

        # 设置预期输出文本框的初始状态
        self.toggle_expected_input()

        # 任务注意事项文本框
        notes_label = tk.Label(
            input_frame,
            text="任务注意事项 (适用于所有任务):",
            font=self.normal_font,
            bg=self.bg_color,
            anchor=tk.W
        )
        notes_label.pack(fill=tk.X, padx=10, pady=(0, 0))

        self.notes_text = scrolledtext.ScrolledText(
            input_frame,
            height=3,
            font=self.normal_font,
            wrap=tk.WORD
        )
        self.notes_text.pack(fill=tk.X, padx=10, pady=(0, 10))

        # 添加默认注意事项
        default_notes = """1. 请勿使用input()函数或任何需要用户输入的代码，这将导致测试超时  
2. 所有输入数据应该在程序中预设或从文件读取  
3. 所有代码必须是自包含的，不要依赖外部资源  
4. 避免使用需要安装的第三方库，除非明确要求"""
        self.notes_text.insert(tk.END, default_notes)

        # 高级设置区域（折叠式）
        self.advanced_var = tk.IntVar()
        self.advanced_check = tk.Checkbutton(
            input_frame,
            text="显示高级设置",
            variable=self.advanced_var,
            command=self.toggle_advanced_settings,
            font=self.normal_font,
            bg=self.bg_color
        )
        self.advanced_check.pack(anchor=tk.W, padx=10)

        # 高级设置框架
        self.advanced_frame = tk.Frame(input_frame, bg=self.bg_color)
        self.advanced_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.advanced_frame.pack_forget()  # 默认隐藏

        # 服务器设置
        settings_frame = tk.Frame(self.advanced_frame, bg=self.bg_color)
        settings_frame.pack(fill=tk.X, pady=(5, 5))

        # 主机设置
        host_label = tk.Label(
            settings_frame,
            text="主机:",
            font=self.normal_font,
            bg=self.bg_color
        )
        host_label.pack(side=tk.LEFT, padx=(0, 5))

        self.host_entry = tk.Entry(settings_frame, width=15, font=self.normal_font)
        self.host_entry.insert(0, "localhost")
        self.host_entry.pack(side=tk.LEFT, padx=(0, 20))

        # 端口设置
        port_label = tk.Label(
            settings_frame,
            text="端口:",
            font=self.normal_font,
            bg=self.bg_color
        )
        port_label.pack(side=tk.LEFT, padx=(0, 5))

        self.port_entry = tk.Entry(settings_frame, width=6, font=self.normal_font)
        self.port_entry.insert(0, "1234")
        self.port_entry.pack(side=tk.LEFT, padx=(0, 20))

        # 工作目录设置
        workspace_label = tk.Label(
            settings_frame,
            text="工作目录:",
            font=self.normal_font,
            bg=self.bg_color
        )
        workspace_label.pack(side=tk.LEFT, padx=(0, 5))

        self.workspace_entry = tk.Entry(settings_frame, width=20, font=self.normal_font)
        self.workspace_entry.insert(0, "./auto_coder_workspace")
        self.workspace_entry.pack(side=tk.LEFT, padx=(0, 20))

        # 参数设置框架
        params_frame = tk.Frame(self.advanced_frame, bg=self.bg_color)
        params_frame.pack(fill=tk.X, pady=(0, 5))

        # 最大令牌限制
        tokens_label = tk.Label(
            params_frame,
            text="最大文本长度:",
            font=self.normal_font,
            bg=self.bg_color
        )
        tokens_label.pack(side=tk.LEFT, padx=(0, 5))

        self.tokens_var = tk.StringVar(value="2000")
        self.tokens_entry = tk.Entry(
            params_frame,
            textvariable=self.tokens_var,
            width=6,
            font=self.normal_font
        )
        self.tokens_entry.pack(side=tk.LEFT, padx=(0, 20))

        # 最大尝试次数
        attempts_label = tk.Label(
            params_frame,
            text="最大尝试次数:",
            font=self.normal_font,
            bg=self.bg_color
        )
        attempts_label.pack(side=tk.LEFT, padx=(0, 5))

        self.attempts_var = tk.StringVar(value="5")
        self.attempts_entry = tk.Entry(
            params_frame,
            textvariable=self.attempts_var,
            width=3,
            font=self.normal_font
        )
        self.attempts_entry.pack(side=tk.LEFT, padx=(0, 20))

        # 网络参数设置
        net_frame = tk.Frame(self.advanced_frame, bg=self.bg_color)
        net_frame.pack(fill=tk.X, pady=(0, 5))

        # 命令执行超时
        cmd_timeout_label = tk.Label(
            net_frame,
            text="命令超时(秒):",
            font=self.normal_font,
            bg=self.bg_color
        )
        cmd_timeout_label.pack(side=tk.LEFT, padx=(0, 5))

        self.cmd_timeout_var = tk.StringVar(value="30")
        self.cmd_timeout_entry = tk.Entry(
            net_frame,
            textvariable=self.cmd_timeout_var,
            width=4,
            font=self.normal_font
        )
        self.cmd_timeout_entry.pack(side=tk.LEFT, padx=(0, 20))

        # API超时
        api_timeout_label = tk.Label(
            net_frame,
            text="API超时(秒):",
            font=self.normal_font,
            bg=self.bg_color
        )
        api_timeout_label.pack(side=tk.LEFT, padx=(0, 5))

        self.api_timeout_var = tk.StringVar(value="120")
        self.api_timeout_entry = tk.Entry(
            net_frame,
            textvariable=self.api_timeout_var,
            width=4,
            font=self.normal_font
        )
        self.api_timeout_entry.pack(side=tk.LEFT, padx=(0, 20))

        # 搜索结果数量
        search_results_label = tk.Label(
            net_frame,
            text="搜索结果数:",
            font=self.normal_font,
            bg=self.bg_color
        )
        search_results_label.pack(side=tk.LEFT, padx=(0, 5))

        self.search_results_var = tk.StringVar(value="5")
        self.search_results_entry = tk.Entry(
            net_frame,
            textvariable=self.search_results_var,
            width=3,
            font=self.normal_font
        )
        self.search_results_entry.pack(side=tk.LEFT)

        # 按钮区域
        button_frame = tk.Frame(input_frame, bg=self.bg_color)
        button_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        self.start_button = tk.Button(
            button_frame,
            text="开始生成",
            font=self.normal_font,
            command=self.start_code_generation,
            bg="#4CAF50",
            fg="white",
            padx=10
        )
        self.start_button.pack(side=tk.LEFT, padx=(0, 10))

        self.stop_button = tk.Button(
            button_frame,
            text="停止",
            font=self.normal_font,
            command=self.stop_code_generation,
            bg="#f44336",
            fg="white",
            padx=10,
            state=tk.DISABLED
        )
        self.stop_button.pack(side=tk.LEFT)

        self.clear_button = tk.Button(
            button_frame,
            text="清空日志",
            font=self.normal_font,
            command=self.clear_log,
            bg="#2196F3",
            fg="white",
            padx=10
        )
        self.clear_button.pack(side=tk.RIGHT)

    def toggle_expected_input(self):
        """根据自动预期验证复选框状态切换预期输出文本框状态"""
        if self.auto_expect_var.get():
            # 自动预期验证启用，预期输出为可选
            self.expected_text.config(bg="#f0f0f0")  # 略微变灰表示可选
        else:
            # 自动预期验证禁用，预期输出为必填
            self.expected_text.config(bg="white")

    def toggle_advanced_settings(self):
        """切换高级设置显示"""
        if self.advanced_var.get():
            self.advanced_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        else:
            self.advanced_frame.pack_forget()

    def setup_log_area(self):
        """设置日志显示区域"""
        log_frame = tk.LabelFrame(
            self.main_frame,
            text="执行日志",
            font=self.title_font,
            bg=self.bg_color
        )
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=20,
            font=self.code_font,
            wrap=tk.WORD,
            bg="#1E1E1E",
            fg="#DCDCDC"
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.log_text.config(state=tk.DISABLED)

    def update_log(self, message):
        """更新日志区域"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.root.update()

    def clear_log(self):
        """清空日志区域"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

    def start_code_generation(self):
        """开始代码生成过程"""
        if self.running:
            return

            # 获取输入参数
        task = self.task_text.get(1.0, tk.END).strip()
        notes = self.notes_text.get(1.0, tk.END).strip()
        expected_output = self.expected_text.get(1.0, tk.END).strip()
        auto_expect = bool(self.auto_expect_var.get())
        host = self.host_entry.get().strip()
        port = self.port_entry.get().strip()
        workspace = self.workspace_entry.get().strip()

        # 获取高级参数
        try:
            max_tokens = int(self.tokens_var.get().strip())
            max_attempts = int(self.attempts_var.get().strip())
            command_timeout = int(self.cmd_timeout_var.get().strip())
            api_timeout = int(self.api_timeout_var.get().strip())
            search_results = int(self.search_results_var.get().strip())
        except ValueError as e:
            messagebox.showerror("参数错误", f"请确保所有数值参数都是有效的整数: {str(e)}")
            return

            # 验证输入
        if not task:
            messagebox.showerror("错误", "请输入任务描述")
            return

        if not auto_expect and not expected_output:
            messagebox.showerror("错误", "请输入预期输出或启用自动预期验证")
            return

        try:
            port = int(port)
        except ValueError:
            messagebox.showerror("错误", "端口必须是数字")
            return

            # 设置UI状态
        self.running = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.status_var.set("代码生成中...")
        self.clear_log()

        # 创建并启动自动编码器
        try:
            # 防止阻塞UI，使用线程执行生成过程
            self.auto_coder = AutoCoder(
                task=task,
                notes=notes,
                workspace=workspace,
                host=host,
                port=port,
                ui_callback=self.update_log,
                max_tokens=max_tokens,
                expected_output=expected_output,
                auto_expect=auto_expect,
                max_attempts=max_attempts,
                command_timeout=command_timeout,
                api_timeout=api_timeout,
                search_results=search_results
            )

            # 使用线程执行长时间任务
            self.generation_thread = threading.Thread(
                target=self.run_generation_process
            )
            self.generation_thread.daemon = True
            self.generation_thread.start()

        except Exception as e:
            self.update_log(f"\n❌ 错误: {str(e)}\n")
            self.reset_ui()

    def run_generation_process(self):
        """在线程中运行生成过程"""
        try:
            success = self.auto_coder.development_cycle()

            # 生成摘要
            summary = self.auto_coder.get_summary()
            self.update_log(summary)

            # 更新状态
            if success:
                self.status_var.set("代码生成成功")
            else:
                self.status_var.set("代码生成失败")

        except Exception as e:
            self.update_log(f"\n❌ 执行异常: {str(e)}\n")
            self.status_var.set("执行出错")
        finally:
            # 重置UI状态
            self.reset_ui()

    def stop_code_generation(self):
        """停止代码生成过程"""
        if not self.running:
            return

        self.update_log("\n⚠️ 用户中断操作，正在停止...\n")

        # 关闭相关资源
        if self.auto_coder:
            try:
                # 关闭网络搜索组件
                if hasattr(self.auto_coder, 'web_search'):
                    self.auto_coder.web_search.close()
            except Exception as e:
                self.update_log(f"关闭资源时出错: {str(e)}\n")

                # 由于线程是守护线程，不需要显式终止
        self.reset_ui()
        self.status_var.set("操作已中断")

    def reset_ui(self):
        """重置UI状态"""
        self.running = False
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)

    def on_closing(self):
        """窗口关闭时的处理"""
        if self.running:
            if messagebox.askokcancel("退出", "代码生成正在进行中，确定要退出吗？"):
                self.stop_code_generation()
                self.root.destroy()
        else:
            self.root.destroy()

def main():
        """主程序入口"""
        root = tk.Tk()
        app = AutoCoderGUI(root)

        # 设置窗口关闭处理
        root.protocol("WM_DELETE_WINDOW", app.on_closing)

        # 启动GUI主循环
        root.mainloop()

if __name__ == "__main__":
        main()