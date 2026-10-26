# -*- coding: utf-8 -*-
"""
API 接口调用模块（供主程序按钮直接调用，本身不是独立窗口程序）

- 主程序可以 import 本模块后调用其中的函数（如 Moments_copywriting），
  也可以在 exec 注入的方式下调用：本模块只补全缺失的依赖，不会覆盖主程序已有的
  logger / load_key。
- 每次接口调用的过程都写入 log/<年月日>/api_interface_call.log（UTF-8），
  日期文件夹不存在时自动创建。
"""
import os
import sys
import random
import logging
import tkinter as tk
from tkinter import messagebox
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
    """配置本模块专属日志：写入 UTF-8 日志文件，同时安全输出到控制台。"""
    log = logging.getLogger("api_interface_call")
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
        file_handler = _DailyFileHandler("api_interface_call")  # 按天自动滚动，跨天写入新日期目录
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
# 1) 主程序 import 本模块（或 from api_interface_call import ...）：
#    本模块命名空间里没有 logger，则使用本模块专属的 api_logger，日志写入自己的日志文件；
# 2) 主程序把本文件 exec 进自己的命名空间：主程序里已有 logger，
#    则保留主程序的 logger 不覆盖，日志仍进主程序的日志文件。
if "logger" not in globals():
    logger = api_logger

# 兼容 exec 注入：主程序里已有 load_key，则不重复定义，避免覆盖
if "load_key" not in globals():
    api_key_file = os.path.join(os.getcwd(), 'api_key.txt')

    def load_key():
        if os.path.exists(api_key_file):
            with open(api_key_file, 'r') as file:
                return file.read().strip()
        return "96f163cda80b"


logger.info("API接口调用模块已加载（可在主程序中直接调用）")


def Moments_copywriting():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/pyqwenan'
    apiKey = load_key()
    logger.info('朋友圈文案地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****朋友圈文案查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"朋友圈文案接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            source = responseResult['result']['source']
            copywriting = content + "\n" + source
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '朋友圈文案'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('朋友圈文案')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Moments_copywriting)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, copywriting)
                logger.error(f"{copywriting}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('朋友圈文案')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Moments_copywriting)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, copywriting)
                logger.error(f"{copywriting}")
            logger.error('********************朋友圈文案接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"朋友圈文案接口返回400：{message}")
            messagebox.showinfo("朋友圈文案", message)
        else:
            logger.error(f"朋友圈文案接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"朋友圈文案查询异常：{e}")
        return str(e)


# 励志古言
def Inspirational_old_sayings():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/lzmy'
    apiKey = load_key()
    logger.info('励志古言地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****励志古言查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"励志古言接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            saying = responseResult['result']['saying']
            transl = responseResult['result']['transl']
            source = responseResult['result']['source']
            Inspirational = '名言：' + saying + "\n" + '译文：' + transl + "\n" + '来源：' + source
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '励志古言'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('励志古言')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Inspirational_old_sayings)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, Inspirational)
                logger.error(f"{Inspirational}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('励志古言')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Inspirational_old_sayings)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, Inspirational)
                logger.error(f"{Inspirational}")
            logger.error('********************励志古言接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"励志古言接口返回400：{message}")
            messagebox.showinfo("励志古言", message)
        else:
            logger.error(f"励志古言接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"励志古言查询异常：{e}")
        return str(e)


# 渣男语录
def Scumbag_quotes():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/zhanan'
    apiKey = load_key()
    logger.info('渣男语录地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****渣男语录查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"渣男语录接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '渣男语录'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('渣男语录')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Scumbag_quotes)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('渣男语录')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Scumbag_quotes)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            logger.error('********************渣男语录接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"渣男语录接口返回400：{message}")
            messagebox.showinfo("渣男语录", message)
        else:
            logger.error(f"渣男语录接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"渣男语录查询异常：{e}")
        return str(e)


# 每日毒鸡汤
def Poisonous_chicken_soup():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/dujitang'
    apiKey = load_key()
    logger.info('毒鸡汤地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****毒鸡汤查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"毒鸡汤接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '毒鸡汤'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('毒鸡汤')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Poisonous_chicken_soup)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('毒鸡汤')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Poisonous_chicken_soup)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            logger.error('********************毒鸡汤接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"毒鸡汤接口返回400：{message}")
            messagebox.showinfo("毒鸡汤", message)
        else:
            logger.error(f"毒鸡汤接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"毒鸡汤查询异常：{e}")
        return str(e)


# 舔狗日记
def Licking_Dog_Diary():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/tiangou'
    apiKey = load_key()
    logger.info('舔狗日记地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****舔狗日记查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"舔狗日记接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '舔狗日记'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('舔狗日记')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Licking_Dog_Diary)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('舔狗日记')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Licking_Dog_Diary)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            logger.error('********************舔狗日记接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"舔狗日记接口返回400：{message}")
            messagebox.showinfo("舔狗日记", message)
        else:
            logger.error(f"舔狗日记接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"舔狗日记查询异常：{e}")
        return str(e)


# 土味情话
def Earthy_love_words():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/saylove'
    apiKey = load_key()
    logger.info('土味情话地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****土味情话查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"土味情话接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '土味情话'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('土味情话')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Earthy_love_words)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('土味情话')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Earthy_love_words)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            logger.error('********************土味情话接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"土味情话接口返回400：{message}")
            messagebox.showinfo("土味情话", message)
        else:
            logger.error(f"土味情话接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"土味情话查询异常：{e}")
        return str(e)


# 彩虹屁
def Rainbow_fart():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/caihongpi'
    apiKey = load_key()
    logger.info('彩虹屁地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****彩虹屁查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"彩虹屁接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '彩虹屁'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('彩虹屁')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Rainbow_fart)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('彩虹屁')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Rainbow_fart)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            logger.error('********************彩虹屁接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"彩虹屁接口返回400：{message}")
            messagebox.showinfo("彩虹屁", message)
        else:
            logger.error(f"彩虹屁接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"彩虹屁查询异常：{e}")
        return str(e)


# 失恋分手句子
def Broken_up():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/hsjz'
    apiKey = load_key()
    logger.info('失恋分手地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****失恋分手查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"失恋分手接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '失恋分手'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('失恋分手')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Broken_up)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('失恋分手')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Broken_up)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            logger.error('********************失恋分手接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"失恋分手接口返回400：{message}")
            messagebox.showinfo("失恋分手", message)
        else:
            logger.error(f"失恋分手接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"失恋分手查询异常：{e}")
        return str(e)


# 神回复
def God_replied():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/godreply'
    apiKey = load_key()
    logger.info('神回复地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****神回复查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"神回复接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['list']
            title = ""
            content_item = ""
            for item in content:
                title = item['title']
                content_item = item['content']
            contents = '标题:' + title + '\n' + '内容:' + content_item
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '神回复'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('神回复')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=God_replied)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('神回复')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=God_replied)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            logger.error('********************神回复接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"神回复接口返回400：{message}")
            messagebox.showinfo("神回复", message)
        else:
            logger.error(f"神回复接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"神回复查询异常：{e}")
        print(e)
        return str(e)


# 每日一句
def One_sentence_a_day():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/one'
    apiKey = load_key()
    logger.info('每日一句地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****每日一句查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"每日一句接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            word = responseResult['result']['word']
            wordfrom = responseResult['result']['wordfrom']
            date = responseResult['result']['date']
            contents = '句子:' + word + '\n' + '句子来源:' + wordfrom + '\n' + '时间:' + date
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '每日一句'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('每日一句')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=One_sentence_a_day)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('每日一句')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=One_sentence_a_day)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            logger.error('********************每日一句接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"每日一句接口返回400：{message}")
            messagebox.showinfo("每日一句", message)
        else:
            logger.error(f"每日一句接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"每日一句查询异常：{e}")
        return str(e)


# 每日英语
def English_daily():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/everyday'
    apiKey = load_key()
    logger.info('每日英语地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****每日英语查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"每日英语接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            note = responseResult['result']['note']
            date = responseResult['result']['date']
            contents = '句子内容:' + content + '\n' + '释义:' + note + '\n' + '时间:' + date
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '每日英语'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('每日英语')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=English_daily)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('每日英语')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=English_daily)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            logger.error('********************每日英语接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"每日英语接口返回400：{message}")
            messagebox.showinfo("每日英语", message)
        else:
            logger.error(f"每日英语接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"每日英语查询异常：{e}")
        return str(e)


# 菜谱查询
def Recipe_inquiry():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/caipu'
    apiKey = load_key()
    logger.info('菜谱查询地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****菜谱查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"菜谱查询接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['list']
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '菜谱查询'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('菜谱查询')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Recipe_inquiry)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('菜谱查询')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Recipe_inquiry)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            logger.error('********************菜谱查询接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"菜谱查询接口返回400：{message}")
            messagebox.showinfo("菜谱查询", message)
        else:
            logger.error(f"菜谱查询接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"菜谱查询查询异常：{e}")
        return str(e)


# 早安心语
def Say_goodbye_early():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/zaoan'
    apiKey = load_key()
    logger.info('早安心语地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****早安心语查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"早安心语接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '早安心语'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('早安心语')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Say_goodbye_early)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('早安心语')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Say_goodbye_early)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            logger.error('********************早安心语接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"早安心语接口返回400：{message}")
            messagebox.showinfo("早安心语", message)
        else:
            logger.error(f"早安心语接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"早安心语查询异常：{e}")
        return str(e)


# 晚安心语
def Evening_peace_of_mind():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/wanan'
    apiKey = load_key()
    logger.info('晚安心语地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****晚安心语查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"晚安心语接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '晚安心语'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('晚安心语')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Evening_peace_of_mind)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('晚安心语')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Evening_peace_of_mind)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            logger.error('********************晚安心语接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"晚安心语接口返回400：{message}")
            messagebox.showinfo("晚安心语", message)
        else:
            logger.error(f"晚安心语接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"晚安心语查询异常：{e}")
        return str(e)


# 历史上的今天
def Today_in_istory():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/lishi'
    apiKey = load_key()
    # 格式化当前日期（月、日各两位数），接口的 date 参数需要
    formatted_time = datetime.now().strftime("%m%d")
    logger.info('历史上的今天地址：' + f'apiUrl:{apiUrl}?Key={apiKey}&date={formatted_time}，' + '*****历史上的今天查询开始*****')
    requestParams = {'key': apiKey, 'date': formatted_time}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"历史上的今天接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['list']
            extracted_info = []
            for item in content:
                title = item['title']
                lsdate = item['lsdate']
                contents = lsdate + title
                extracted_info.append(contents)
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '历史上的今天'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('历史上的今天')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Today_in_istory)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, '\n'.join(extracted_info))
                logger.info(f"{extracted_info}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('历史上的今天')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Today_in_istory)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, '\n'.join(extracted_info))
                logger.info(f"{extracted_info}")
            logger.info('********************历史上的今天接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"历史上的今天接口返回400：{message}")
            messagebox.showinfo("历史上的今天", message)
        else:
            logger.error(f"历史上的今天接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"历史上的今天查询异常：{e}")
        return str(e)


# 周公解梦
def interprets_dreams():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/hsjz'
    apiKey = load_key()
    logger.info('周公解梦地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****周公解梦查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"周公解梦接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '周公解梦'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('周公解梦')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=interprets_dreams)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('周公解梦')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=interprets_dreams)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, content)
                logger.error(f"{content}")
            logger.error('********************周公解梦接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"周公解梦接口返回400：{message}")
            messagebox.showinfo("周公解梦", message)
        else:
            logger.error(f"周公解梦接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"周公解梦查询异常：{e}")
        return str(e)


# 奇葩对话
def Strange_dialogue():
    global root, label
    apiUrl = 'https://api.52vmy.cn/api/wl/yan/qpdh'
    cookies = {
        'PHPSESSID': 'r4s8i4bin8ng6os3sem61dpacj',
        '__51uvsct__3HICYcUhpS1iTWST': '1',
        '__51vcke__3HICYcUhpS1iTWST': 'b009aad0-1300-595e-83cb-347415d0ac55',
        '__51vuft__3HICYcUhpS1iTWST': '1731601686062',
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    logger.info('奇葩对话地址：' + f'apiUrl:{apiUrl},' + '*****奇葩对话查询开始*****')
    response = requests.get(apiUrl, cookies=cookies, headers=headers)
    responseResult = response.json()['content']
    try:
        need_rebuild = (
            'root' not in globals() or root is None
            or not root.winfo_exists()
            or root.title() != '奇葩对话'
        )
        if need_rebuild:
            if 'root' in globals() and root is not None:
                try:
                    if root.winfo_exists():
                        root.destroy()
                except Exception:
                    pass
            try:
                root = tk.Toplevel()
            except Exception:
                root = tk.Tk()
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
            root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
            root.title('奇葩对话')
            label = tk.Text(root, height=14, width=500)
            label.pack()
            shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                        activebackground=root.cget('bg'),
                                        command=Strange_dialogue)
            shortcut_button.place(x=420, y=190)
        label.delete('1.0', tk.END)
        label.insert(tk.END, responseResult)
        logger.error(f"{responseResult}")
    except tk.TclError as e:
        root = tk.Toplevel()
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
        root.title('奇葩对话')
        label = tk.Text(root, height=14, width=500)
        label.pack()
        shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                    activebackground=root.cget('bg'),
                                    command=Strange_dialogue)
        shortcut_button.place(x=420, y=190)
        label.delete('1.0', tk.END)
        label.insert(tk.END, responseResult)
        logger.error(f"{responseResult}")
    logger.error('********************奇葩对话接口返回数据结束********************')


# 雷人笑话
def Thunder_man_jokes():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/joke'
    apiKey = load_key()
    logger.info('雷人笑话地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****雷人笑话查询开始*****')
    requestParams = {'key': apiKey, 'num': 3}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"雷人笑话接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['list']
            extracted_info = []
            for item in content:
                title = item['title']
                lsdate = item['content']
                extracted_info.append((title, lsdate))
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '雷人笑话'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('雷人笑话')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Thunder_man_jokes)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                for title, lsdate in extracted_info:
                    label.insert(tk.END, f"标题：{title}\n内容：{lsdate}\n\n")
                    logger.error(f"标题：{title}\n内容：{lsdate}\n\n")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('雷人笑话')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Thunder_man_jokes)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                for title, lsdate in extracted_info:
                    label.insert(tk.END, f"标题：{title}\n内容：{lsdate}\n\n")
                    logger.error(f"标题：{title}\n内容：{lsdate}\n\n")
            logger.error('********************雷人笑话接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"雷人笑话接口返回400：{message}")
            messagebox.showinfo("雷人笑话", message)
        else:
            logger.error(f"雷人笑话接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"雷人笑话查询异常：{e}")
        return str(e)


# 诗词问答
def Poetry_QA():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/scwd'
    apiKey = load_key()
    logger.info('诗词问答地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****诗词问答查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"诗词问答接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            question = responseResult['result']['question']
            answer_a = responseResult['result']['answer_a']
            answer_b = responseResult['result']['answer_b']
            answer_c = responseResult['result']['answer_c']
            answer = responseResult['result']['answer']
            analytic = responseResult['result']['analytic']
            contents = ('问题:' + question + '\n' + '     A:' + answer_a + '\n' + '     B:' + answer_b +
                        '\n' + '     C:' + answer_c + '\n\n\n\n\n' + '正确答案:' + answer + '\n' + '答案解析:' + analytic)
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '诗词问答'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('诗词问答')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Poetry_QA)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('诗词问答')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Poetry_QA)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            logger.error('********************诗词问答接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"诗词问答接口返回400：{message}")
            messagebox.showinfo("诗词问答", message)
        else:
            logger.error(f"诗词问答接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"诗词问答查询异常：{e}")
        return str(e)


# 优美诗句
def Beautiful_verses():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/verse'
    apiKey = load_key()
    logger.info('优美诗句地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****优美诗句查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"优美诗句接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['content']
            source = responseResult['result']['source']
            author = responseResult['result']['author']
            contents = ('诗句：' + content + '\n' + '来源：' + source + '\n' + '作者：' + author)
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '优美诗句'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('优美诗句')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Beautiful_verses)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('优美诗句')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Beautiful_verses)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            logger.error('********************优美诗句接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"优美诗句接口返回400：{message}")
            messagebox.showinfo("优美诗句", message)
        else:
            logger.error(f"优美诗句接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"优美诗句查询异常：{e}")
        return str(e)


# 唐诗大全
def Tang_Poems():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/poetries'
    apiKey = load_key()
    logger.info('唐诗大全地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****唐诗大全查询开始*****')
    page = random.randint(1, 100)
    requestParams = {'key': apiKey, 'num': 3, 'page': page}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"唐诗大全接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['list']
            extracted_info = []
            for item in content:
                title = item['title']
                lsdate = item['content']
                author = item['author']
                extracted_info.append((title, lsdate, author))
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '唐诗大全'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('唐诗大全')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Tang_Poems)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                for title, lsdate, author in extracted_info:
                    label.insert(tk.END, f"标题：{title}\n内容：{lsdate}\n作者：{author}\n\n")
                    logger.error(f"标题：{title}\n内容：{lsdate}\n作者：{author}\n\n")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('唐诗大全')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Tang_Poems)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                for title, lsdate, author in extracted_info:
                    label.insert(tk.END, f"标题：{title}\n内容：{lsdate}\n作者：{author}\n\n")
                    logger.error(f"标题：{title}\n内容：{lsdate}\n作者：{author}\n\n")
            logger.error('********************唐诗大全接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"唐诗大全接口返回400：{message}")
            messagebox.showinfo("唐诗大全", message)
        else:
            logger.error(f"唐诗大全接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"唐诗大全查询异常：{e}")
        return str(e)


# 百科题库
def Encyclopedia_question_bank():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/baiketiku'
    apiKey = load_key()
    logger.info('百科题库地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****百科题库查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"百科题库接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            title = responseResult['result']['title']
            answerA = responseResult['result']['answerA']
            answerB = responseResult['result']['answerB']
            answerC = responseResult['result']['answerC']
            answerD = responseResult['result']['answerD']
            answer = responseResult['result']['answer']
            analytic = responseResult['result']['analytic']
            contents = ('问题:' + title + '\n' + '     A:' + answerA + '\n' + '     B:' + answerB +
                        '\n' + '     C:' + answerC + '\n' + '     D:' + answerD + '\n\n\n\n' + '正确答案:' + answer + '\n' + '答案解析:' + analytic)
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '百科题库'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('百科题库')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Encyclopedia_question_bank)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('百科题库')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Encyclopedia_question_bank)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                label.insert(tk.END, contents)
                logger.error(f"{contents}")
            logger.error('********************百科题库接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"百科题库接口返回400：{message}")
            messagebox.showinfo("百科题库", message)
        else:
            logger.error(f"百科题库接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"百科题库查询异常：{e}")
        return str(e)


# 脑筋急转弯
def Brain_teasers():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/naowan'
    apiKey = load_key()
    logger.info('脑筋急转弯地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****脑筋急转弯查询开始*****')
    requestParams = {'key': apiKey, 'num': 3}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"脑筋急转弯接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['list']
            extracted_info = []
            for item in content:
                quest = item['quest']
                result = item['result']
                extracted_info.append((quest, result))
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '脑筋急转弯'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('脑筋急转弯')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=Brain_teasers)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                for quest, result in extracted_info:
                    label.insert(tk.END, f"问题：{quest}\n\n答案：{result}\n\n\n")
                    logger.error(f"问题：{quest}\n\n答案：{result}\n\n\n")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('脑筋急转弯')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=Brain_teasers)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                for quest, result in extracted_info:
                    label.insert(tk.END, f"问题：{quest}\n\n答案：{result}\n\n\n")
                    logger.error(f"问题：{quest}\n\n答案：{result}\n\n\n")
            logger.error('********************脑筋急转弯接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"脑筋急转弯接口返回400：{message}")
            messagebox.showinfo("脑筋急转弯", message)
        else:
            logger.error(f"脑筋急转弯接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"脑筋急转弯查询异常：{e}")
        return str(e)


# 十万为什么
def whys():
    global root, label
    apiUrl = 'https://whyta.cn/api/tx/tenwhy'
    apiKey = load_key()
    logger.info('十万为什么地址：' + f'apiUrl:{apiUrl}?Key={apiKey}，' + '*****十万为什么查询开始*****')
    requestParams = {'key': apiKey}
    try:
        response = requests.get(apiUrl, params=requestParams)
        logger.info(f"十万为什么接口响应状态码：{response.status_code}")
        if response.status_code == 200:
            responseResult = response.json()
            content = responseResult['result']['list']
            extracted_info = []
            for item in content:
                title = item['title']
                content_item = item['content'].replace('\u3000', '')
                extracted_info.append((title, content_item))
            try:
                need_rebuild = (
                    'root' not in globals() or root is None
                    or not root.winfo_exists()
                    or root.title() != '十万为什么'
                )
                if need_rebuild:
                    if 'root' in globals() and root is not None:
                        try:
                            if root.winfo_exists():
                                root.destroy()
                        except Exception:
                            pass
                    try:
                        root = tk.Toplevel()
                    except Exception:
                        root = tk.Tk()
                    screen_width = root.winfo_screenwidth()
                    screen_height = root.winfo_screenheight()
                    root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                    root.title('十万为什么')
                    label = tk.Text(root, height=14, width=500)
                    label.pack()
                    shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                                activebackground=root.cget('bg'),
                                                command=whys)
                    shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                for t, c in extracted_info:
                    label.insert(tk.END, f"标题：{t}\n\n内容：{c}\n\n\n")
                    logger.error(f"标题：{t}\n\n内容：{c}\n\n\n")
            except tk.TclError as e:
                root = tk.Toplevel()
                screen_width = root.winfo_screenwidth()
                screen_height = root.winfo_screenheight()
                root.geometry("500x220+%d+%d" % (screen_width / 2 - 250, screen_height - 950))
                root.title('十万为什么')
                label = tk.Text(root, height=14, width=500)
                label.pack()
                shortcut_button = tk.Button(root, text='下一条', bd=0, bg=root.cget('bg'),
                                            activebackground=root.cget('bg'),
                                            command=whys)
                shortcut_button.place(x=420, y=190)
                label.delete('1.0', tk.END)
                for t, c in extracted_info:
                    label.insert(tk.END, f"标题：{t}\n\n内容：{c}\n\n\n")
                    logger.error(f"标题：{t}\n\n内容：{c}\n\n\n")
            logger.error('********************十万为什么接口返回数据结束********************')
        elif response.status_code == 400:
            responseResult = response.json()
            message = responseResult['message']
            logger.warning(f"十万为什么接口返回400：{message}")
            messagebox.showinfo("十万为什么", message)
        else:
            logger.error(f"十万为什么接口返回异常状态码：{response.status_code}")
            return "请求数据错误，联系开发人员"
    except Exception as e:
        logger.error(f"十万为什么查询异常：{e}")
        return str(e)
