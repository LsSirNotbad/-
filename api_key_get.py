# -*- coding: utf-8 -*-
"""
密钥管理工具：获取 / 更新接口密钥

- 支持独立单文件运行：python api_key_get.py
- 也可被主程序调用：update_apikey(parent)
- 获取 / 更新过程写入 log/<年月日>/api_key_get.log（UTF-8），日期文件夹不存在时自动创建。
"""
import os
import sys
import logging
import tkinter as tk
from tkinter import messagebox
from io import BytesIO
from datetime import datetime
# ★ 懒加载 requests
import importlib


class _LazyModule:
    def __init__(self, module_name):
        self._module_name = module_name
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = importlib.import_module(self._module_name)
        return self._module

    def __getattr__(self, name):
        return getattr(self._load(), name)


requests = _LazyModule("requests")


# ---------- 日志 ----------
class _SafeStream:
    """文本流兜底包装：写入无法编码的字符时用替换字符代替，避免抛异常。"""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        try:
            return self._stream.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self._stream, "encoding", None) or "utf-8"
            safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            return self._stream.write(safe_text)

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        # 其余属性（encoding、buffer、fileno 等）透传给原始流
        return getattr(self._stream, name)


def _make_output_safe(stream):
    """修复 Windows 控制台/IDE 使用非 UTF-8 编码（如 cp1252）输出中文时的报错。"""
    if stream is None:
        return None
    try:
        stream.reconfigure(errors="replace")
        return stream
    except Exception:
        return _SafeStream(stream)


sys.stdout = _make_output_safe(sys.stdout)
sys.stderr = _make_output_safe(sys.stderr)


class _DailyFileHandler(logging.Handler):
    """按天滚动的文件 Handler：每次写日志前检查日期，跨天自动创建 log/新日期/ 目录并切换到新文件"""

    def __init__(self, module_name):
        super().__init__()
        self._module_name = module_name
        self._current_date = None
        self._handler = None
        self._rollover()

    @staticmethod
    def _base_dir():
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    def _rollover(self):
        today = datetime.now().strftime("%Y%m%d")
        if today == self._current_date:
            return
        if self._handler is not None:
            self._handler.close()
        log_dir = os.path.join(self._base_dir(), "log", today)
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
    """配置日志：写入 UTF-8 日志文件，同时安全输出到控制台（单文件运行 / 被主程序调用均可用）。"""
    log = logging.getLogger("api_key_get")
    if log.handlers:
        return log

    log.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S")

    try:
        stamp = datetime.now().strftime("%Y%m%d")
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        log_dir = os.path.join(base_dir, "log", stamp)
        os.makedirs(log_dir, exist_ok=True)
        file_handler = _DailyFileHandler("api_key_get")  # 按天自动滚动，跨天写入新日期目录
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)
    except Exception:
        pass

    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        log.addHandler(stream_handler)

    # 独立维护日志，避免与主程序的日志配置相互干扰
    log.propagate = False
    return log


api_logger = _setup_logger()

# 兼容两种调用方式：
# 1) 主程序 import 本模块（或 from api_key_get import ...）：本模块命名空间里没有 logger，
#    则使用本模块专属的 api_logger，日志写入自己的日志文件；
# 2) 主程序把本文件 exec 进自己的命名空间：主程序里已有 logger，
#    则保留主程序的 logger 不覆盖，日志仍进主程序的日志文件。
if "logger" not in globals():
    logger = api_logger

logger.info("密钥管理模块已加载（可在主程序中直接调用）")


# 密钥文件路径
KEY_FILE = os.path.join(os.getcwd(), 'api_key.txt')


# 加载密钥（兼容 exec 注入：主程序里已有 load_key，则不重复定义，避免覆盖）
if "load_key" not in globals():
    def load_key():
        logger.info(f"读取密钥文件：{KEY_FILE}")
        if os.path.exists(KEY_FILE):
            with open(KEY_FILE, 'r') as file:
                key = file.read().strip()
            logger.info(f"已加载密钥：{key}")
            return key
        logger.warning("密钥文件不存在，使用默认密钥 96f163cda80b")
        return "96f163cda80b"


# 保存密钥（兼容 exec 注入：主程序里已有 save_key，则不重复定义，避免覆盖）
if "save_key" not in globals():
    def save_key(key):
        logger.info(f"保存密钥到：{KEY_FILE}")
        with open(KEY_FILE, 'w') as file:
            file.write(key)
        logger.info(f"密钥已更新：{key}")


# 更新接口密钥
def update_apikey(parent=None):
    logger.info("打开密钥更新窗口")
    current_key = load_key()

    def update_key():
        new_key = entry.get()
        logger.info(f"用户输入的新密钥：{new_key}")
        if new_key:
            save_key(new_key)
            logger.info("密钥更新成功")
            messagebox.showinfo("更新成功", "密钥更新成功！")
        else:
            logger.warning("未输入有效的密钥，更新被拒绝")
            messagebox.showerror("错误", "请输入有效的密钥！")

    # 独立运行：自建 root 并进入主循环；被主程序调用：挂到父窗口，不启动独立主循环
    if parent is None:
        own_root = tk.Tk()
        win = own_root
    else:
        own_root = None
        win = tk.Toplevel(parent)

    win.title("密钥更新")
    entry = tk.Entry(win, width=30)
    entry.insert(0, current_key)
    entry.pack(pady=10)
    button = tk.Button(win, text="更新密钥", command=update_key)
    button.pack(pady=10)

    if own_root is not None:
        own_root.mainloop()


# 显示获取密钥方式图片
def show_image(parent=None):
    logger.info("打开获取密钥方式窗口")
    try:
        from PIL import Image, ImageTk
    except ImportError:
        logger.error("缺少 Pillow 库，无法显示二维码图片")
        messagebox.showerror("错误", "缺少 Pillow 库（pip install pillow），无法显示图片")
        return

    try:
        image_url = "https://apis.whyta.cn/assets/qrcode.png"
        logger.info(f"获取二维码图片：{image_url}")
        response = requests.get(image_url)
        logger.info(f"二维码图片响应状态码：{response.status_code}")
        if response.status_code == 200:
            image_data = BytesIO(response.content)
            image = Image.open(image_data)
            photo = ImageTk.PhotoImage(image)

            # 被主程序调用时挂到父窗口；独立调用时用默认根窗口，没有则新建
            if parent is None:
                parent = getattr(tk, "_default_root", None)
            image_window = tk.Toplevel(parent) if parent is not None else tk.Tk()
            image_window.title("扫码关注")
            image_window.resizable(False, False)

            image_label = tk.Label(image_window, image=photo)
            image_label.image = photo  # 保持引用，避免被回收
            image_label.pack()
            note_label = tk.Label(image_window, text="扫码关注公众号发送“4”获取APIKEY")
            note_label.pack()
            logger.info("获取密钥方式窗口已显示")
        else:
            logger.error(f"获取二维码图片失败，状态码：{response.status_code}")
            messagebox.showerror("错误", f"无法获取图片，状态码: {response.status_code}")
    except Exception as e:
        logger.error(f"获取密钥方式异常：{e}")
        messagebox.showerror("错误", f"发生错误: {e}")


if __name__ == "__main__":
    update_apikey()
