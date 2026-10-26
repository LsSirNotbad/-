# -*- coding: utf-8 -*-
"""
步数提交小工具 v4（最终版）
- 定时提交 / 步数随机生成
- 日志按日期分文件夹：log/YYYYMMDD/step_tool.log（打包后写在 exe 同级目录）
- 支持独立运行和被主程序调用（双模式）
"""
import os
import sys
import json
import random
import logging
import threading
import tkinter as tk
from tkinter import messagebox
from datetime import datetime, timedelta


# ---------------- 输出安全包装（与其他模块一致，防止 Windows 控制台编码报错） ----------------
class _SafeStream:
    def __init__(self, stream):
        self._stream = stream

    def write(self, data):
        try:
            self._stream.write(data)
        except (UnicodeEncodeError, UnicodeError):
            self._stream.write(str(data).encode("ascii", "replace").decode("ascii"))

    def flush(self):
        self._stream.flush()


def _make_output_safe(stream):
    if stream is None:
        return None
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(errors="replace")
            return stream
        except Exception:
            pass
    try:
        stream.write("")
    except UnicodeError:
        return _SafeStream(stream)
    return stream


sys.stdout = _make_output_safe(sys.stdout)
sys.stderr = _make_output_safe(sys.stderr)


def _base_dir():
    """基准目录：打包后取 exe 所在目录，开发时取脚本所在目录"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


class _DailyFileHandler(logging.Handler):
    """按天滚动的文件 Handler：每次写日志前检查日期，跨天自动创建 log/新日期/ 目录并切换到新文件"""

    def __init__(self, module_name):
        super().__init__()
        self._module_name = module_name
        self._current_date = None
        self._handler = None
        self._rollover()

    def _rollover(self):
        today = datetime.now().strftime("%Y%m%d")
        if today == self._current_date:
            return
        if self._handler is not None:
            self._handler.close()
        log_dir = os.path.join(_base_dir(), "log", today)
        os.makedirs(log_dir, exist_ok=True)
        self._handler = logging.FileHandler(
            os.path.join(log_dir, f"{self._module_name}.log"), encoding="utf-8")
        self._handler.setFormatter(self.formatter)
        self._current_date = today

    def setFormatter(self, fmt):
        # 修复：构造 _DailyFileHandler 时 formatter 还未设置，需同步给内部 FileHandler，否则日志文件里没有时间
        super().setFormatter(fmt)
        if self._handler is not None:
            self._handler.setFormatter(fmt)

    def emit(self, record):
        self._rollover()
        self._handler.emit(record)

    def close(self):
        if self._handler is not None:
            self._handler.close()
        super().close()


def _setup_logger():
    """标准化日志：写入 log/YYYYMMDD/step_tool.log，同时输出到控制台"""
    global logger
    if "logger" in globals() and logger is not None:
        return logger
    logger = logging.getLogger("step_tool")
    logger.setLevel(logging.INFO)
    log_path = None
    if not logger.handlers:
        stamp = datetime.now().strftime("%Y%m%d")
        log_dir = os.path.join(_base_dir(), "log", stamp)
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "step_tool.log")
        fh = _DailyFileHandler("step_tool")  # 按天自动滚动，跨天写入新日期目录
        fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        logger.propagate = False
        logger.info(f"日志文件路径: {log_path}")
    return logger


logger = _setup_logger()

# ---------------- 固定配置（不再使用 json 配置文件） ----------------
FIXED_USERNAME = "1497388243@qq.com"
FIXED_PASSWORD = "5454294!Ljx"
FIXED_SUBMIT_TIME = "21:30"   # 每天定时提交时间（固定）

# ---------------- 界面配色（与主程序一致的浅色风格） ----------------
C_BG = "#F5F7FB"        # 主背景
C_CARD = "#FFFFFF"      # 卡片/头部底色
C_ACCENT = "#4169E1"    # 主按钮强调色
C_TITLE = "#1F2937"     # 标题深色
C_TEXT = "#333333"      # 正文
C_GRAY = "#6B7280"      # 次要说明
C_LINE = "#E5E7EB"      # 分隔线/次按钮按下色
C_OK = "#16a34a"
C_ERR = "#dc2626"

API_URL = "https://qxii.cn/bushu/api.php"


class StepTool:
    def __init__(self, parent=None):
        # 双模式：parent=None 独立运行（Tk + mainloop）；否则作为 Toplevel 嵌入主程序
        self._standalone = parent is None
        if self._standalone:
            self.root = tk.Tk()
        else:
            self.root = tk.Toplevel(parent)
        self.root.title("步数提交工具")
        self.root.geometry("580x460")
        self.root.resizable(False, False)
        self.root.configure(bg=C_BG)

        # 打开时自动生成随机步数；定时固定开启，时间固定 21:30
        # 账号密码默认显示固定值，界面上可临时修改（仅本次运行生效，不写文件）
        self.var_username = tk.StringVar(value=FIXED_USERNAME)
        self.var_password = tk.StringVar(value=FIXED_PASSWORD)
        self.var_show_pwd = tk.BooleanVar(value=False)
        self.var_steps = tk.StringVar(value=str(random.randint(10000, 40000)))
        self.var_submit_time = tk.StringVar(value=FIXED_SUBMIT_TIME)

        self._auto_thread_started = False
        self._stop_auto = False

        self._build_ui()
        self._start_auto_scheduler()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        logger.info("========== 程序启动 ==========")
        if self._standalone:
            self.root.mainloop()

    def _build_ui(self):
        # 白色卡片式头部 + 底部分隔线（替换原来的紫色头部）
        header = tk.Frame(self.root, bg=C_CARD, height=70)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="步数提交工具",
                 font=("微软雅黑", 15, "bold"),
                 bg=C_CARD, fg=C_TITLE).pack(anchor="w", padx=24, pady=(12, 0))
        tk.Label(header, text="定时提交 · 步数随机 · 日志按日归档",
                 font=("微软雅黑", 9),
                 bg=C_CARD, fg=C_GRAY).pack(anchor="w", padx=24)
        tk.Frame(self.root, bg=C_LINE, height=1).pack(fill="x")

        form = tk.Frame(self.root, bg=C_BG)
        form.pack(fill="x", padx=24, pady=12)

        tk.Label(form, text="账号：", font=("微软雅黑", 11),
                 bg=C_BG, fg=C_TITLE).grid(row=0, column=0, sticky="e", pady=6)
        tk.Entry(form, textvariable=self.var_username,
                 font=("微软雅黑", 11), width=32, relief="solid", bd=1
                 ).grid(row=0, column=1, columnspan=2, sticky="w", padx=6, pady=6)

        tk.Label(form, text="密码：", font=("微软雅黑", 11),
                 bg=C_BG, fg=C_TITLE).grid(row=1, column=0, sticky="e", pady=6)
        self.entry_pwd = tk.Entry(form, textvariable=self.var_password,
                                  font=("微软雅黑", 11), width=32,
                                  show="●", relief="solid", bd=1)
        self.entry_pwd.grid(row=1, column=1, sticky="w", padx=6, pady=6)

        def toggle_pwd():
            self.entry_pwd.config(show="" if self.var_show_pwd.get() else "●")
        tk.Checkbutton(form, text="显示", variable=self.var_show_pwd,
                       command=toggle_pwd, bg=C_BG,
                       font=("微软雅黑", 9)).grid(row=1, column=2, sticky="w")

        tk.Label(form, text="步数：", font=("微软雅黑", 11),
                 bg=C_BG, fg=C_TITLE).grid(row=2, column=0, sticky="e", pady=6)
        tk.Entry(form, textvariable=self.var_steps,
                 font=("微软雅黑", 11), width=32, relief="solid", bd=1
                 ).grid(row=2, column=1, columnspan=2, sticky="w", padx=6, pady=6)
        tk.Label(form, text="打开时已自动生成，可手动修改",
                 font=("微软雅黑", 9), bg=C_BG, fg=C_GRAY
                 ).grid(row=3, column=1, sticky="w", padx=6)

        timer_frame = tk.LabelFrame(self.root, text="定时自动提交",
                                    font=("微软雅黑", 10),
                                    bg=C_BG, fg=C_TITLE, bd=1, relief="solid")
        timer_frame.pack(fill="x", padx=24, pady=8)

        tk.Label(timer_frame, text=f"已开启：每天 {FIXED_SUBMIT_TIME} 自动提交（随机步数）",
                 font=("微软雅黑", 10), bg=C_BG, fg=C_TEXT
                 ).grid(row=0, column=0, sticky="w", padx=10, pady=8)

        self.var_next_time = tk.StringVar(value="")
        tk.Label(timer_frame, textvariable=self.var_next_time,
                 font=("微软雅黑", 9), bg=C_BG, fg=C_GRAY
                 ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))

        btn_frame = tk.Frame(self.root, bg=C_BG)
        btn_frame.pack(fill="x", padx=24, pady=6)

        self.btn_submit = tk.Button(btn_frame, text="立即提交",
                                    font=("微软雅黑", 11, "bold"),
                                    bg=C_ACCENT, fg="white",
                                    activebackground="#2F56C4",
                                    activeforeground="white",
                                    bd=0, padx=20, pady=8,
                                    cursor="hand2",
                                    command=self.submit_async)
        self.btn_submit.pack(side="left")

        tk.Button(btn_frame, text="随机步数",
                  font=("微软雅黑", 10),
                  bg=C_CARD, fg=C_TEXT,
                  activebackground=C_LINE,
                  bd=1, relief="solid", padx=12, pady=6, cursor="hand2",
                  command=self.randomize_steps).pack(side="left", padx=6)

        tk.Button(btn_frame, text="查看日志",
                  font=("微软雅黑", 10),
                  bg=C_CARD, fg=C_TEXT,
                  activebackground=C_LINE,
                  bd=1, relief="solid", padx=12, pady=6, cursor="hand2",
                  command=self.open_log_viewer).pack(side="left", padx=6)

        self.var_status = tk.StringVar(value="就绪")
        tk.Label(self.root, textvariable=self.var_status,
                 font=("微软雅黑", 10),
                 bg=C_BG, fg=C_GRAY, anchor="w"
                 ).pack(fill="x", padx=24, pady=(2, 4))

    def randomize_steps(self):
        n = random.randint(5000, 30000)
        self.var_steps.set(str(n))
        self.var_status.set(f"已生成随机步数：{n}")
        logger.info(f"生成随机步数：{n}")

    def open_log_viewer(self):
        # 用系统默认程序打开当天的日志文件
        log_path = os.path.join(_base_dir(), "log",
                                datetime.now().strftime("%Y%m%d"), "step_tool.log")
        if os.path.exists(log_path):
            try:
                os.startfile(log_path)
            except Exception as e:
                messagebox.showerror("错误", f"无法打开日志文件：{e}")
        else:
            messagebox.showwarning("提示", f"日志文件不存在：\n{log_path}")

    def submit_async(self):
        self.btn_submit.config(state="disabled", text="提交中…")
        self.var_status.set("正在提交…")
        threading.Thread(target=self._do_submit, daemon=True).start()

    def _do_submit(self, is_auto=False):
        import requests

        # 定时提交时随机生成步数；手动提交用界面上的值
        if is_auto:
            steps = str(random.randint(5000, 30000))
            self.root.after(0, lambda: self.var_steps.set(steps))
        else:
            steps = self.var_steps.get().strip()

        if not steps.isdigit():
            self.root.after(0, lambda: messagebox.showwarning("提示", "步数必须是数字"))
            self._reset_button()
            return

        params = {"username": self.var_username.get().strip(),
                  "password": self.var_password.get().strip(),
                  "steps": steps}

        if not params["username"] or not params["password"]:
            self.root.after(0, lambda: messagebox.showwarning("提示", "账号、密码不能为空"))
            self._reset_button()
            return
        prefix = "【定时】" if is_auto else ""
        logger.info(f"── {prefix}开始提交，步数={steps} ──")

        try:
            resp = requests.get(API_URL, params=params, timeout=15)
            data = resp.json()
            logger.info(f"{prefix}接口返回: {json.dumps(data, ensure_ascii=False)}")

            success = data.get("success", None)
            message = data.get("message", "")

            if success is True:
                logger.info(f"{prefix}成功：{message}")
                self.root.after(0, lambda: self.var_status.set(f"成功：{message}"))
                self.root.after(0, lambda: messagebox.showinfo(
                    "成功", f"{prefix}步数修改成功！\n\n步数：{data.get('step', steps)}\n时间：{data.get('time', '-')}"))
            elif success is False:
                logger.error(f"{prefix}失败：{message}")
                self.root.after(0, lambda: self.var_status.set(f"失败：{message}"))
                self.root.after(0, lambda: messagebox.showerror(
                    "失败", f"{prefix}提交失败\n\n原因：{message}"))
            else:
                logger.error(f"{prefix}返回中未找到 success 字段")
                self.root.after(0, lambda: messagebox.showwarning(
                    "提示", f"{prefix}接口返回异常，详情见日志文件"))

        except ValueError:
            logger.error(f"{prefix}接口返回非 JSON：{resp.text[:300]}")
            self.root.after(0, lambda: messagebox.showerror(
                "失败", f"{prefix}接口返回格式异常，详情见日志文件"))

        except requests.exceptions.Timeout:
            logger.error(f"{prefix}请求超时")
            self.root.after(0, lambda: self.var_status.set("请求超时"))
            self.root.after(0, lambda: messagebox.showerror("失败", f"{prefix}请求超时，请检查网络"))

        except Exception as e:
            logger.error(f"{prefix}提交异常：{e}")
            self.root.after(0, lambda: self.var_status.set("提交失败"))
            self.root.after(0, lambda: messagebox.showerror("失败", f"{prefix}提交异常：{e}"))

        finally:
            self._reset_button()

    def _reset_button(self):
        self.root.after(0, lambda: self.btn_submit.config(state="normal", text="立即提交"))

    def _start_auto_scheduler(self):
        if self._auto_thread_started:
            return
        self._auto_thread_started = True
        threading.Thread(target=self._auto_loop, daemon=True).start()

    def _auto_loop(self):
        last_run_date = None
        while not self._stop_auto:
            try:
                submit_time = FIXED_SUBMIT_TIME
                hh, mm = map(int, submit_time.split(":"))
                now = datetime.now()
                target_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                next_time = target_today if now < target_today else target_today + timedelta(days=1)
                self.root.after(0, lambda: self.var_next_time.set(
                    f"下次提交：{next_time.strftime('%Y-%m-%d %H:%M')}"))

                today_str = now.strftime("%Y-%m-%d")
                if now >= target_today and last_run_date != today_str:
                    logger.info(f"定时任务触发（{submit_time}）")
                    self._do_submit(is_auto=True)
                    last_run_date = today_str
            except Exception as e:
                logger.error(f"定时线程异常：{e}")

            import time
            for _ in range(30):
                if self._stop_auto:
                    return
                time.sleep(1)

    def _on_close(self):
        self._stop_auto = True
        logger.info("========== 程序退出 ==========")
        try:
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    StepTool()