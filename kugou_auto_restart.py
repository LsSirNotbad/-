# -*- coding: utf-8 -*-
"""
酷狗音乐守护 + 定时重启工具
- 守护：酷狗退出后自动拉起
- 定时：每隔 N 分钟关闭酷狗并立即重启
- 日志：log/YYYYMMDD/kugou_auto_restart.log（打包后写在 exe 同级目录）
- 支持独立运行和被主程序调用（双模式）
"""
import os
import sys
import logging
import subprocess
import threading
import time
import re
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime


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
        self._rollover()  # 写每条日志前检查是否跨天
        self._handler.emit(record)

    def close(self):
        if self._handler is not None:
            self._handler.close()
        super().close()


def _setup_logger():
    """标准化日志：写入 log/YYYYMMDD/kugou_auto_restart.log（跨天自动切换新日期文件），同时输出到控制台"""
    global logger
    if "logger" in globals() and logger is not None:
        return logger
    logger = logging.getLogger("kugou_auto_restart")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        fh = _DailyFileHandler("kugou_auto_restart")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        logger.propagate = False
        logger.info(f"日志文件路径: {os.path.join(_base_dir(), 'log', datetime.now().strftime('%Y%m%d'), 'kugou_auto_restart.log')}")
    return logger


logger = _setup_logger()

# ===================== 【默认酷狗路径，界面上可修改并保存】 =====================
KUGOU_PATH = r"C:\Program Files\KuGou\KGMusic\KuGou.exe"
# =============================================================================

import json

CONFIG_FILE = os.path.join(_base_dir(), "kugou_config.json")
CONFIG_KEY_PATH = "kugou_path"
CONFIG_KEY_AUTO_PLAY = "auto_play_on_start"


def _load_config():
    """读取整个配置字典，返回 {}；不存在/损坏时返回 {}"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception as e:
            logger.error(f"读取酷狗配置失败：{e}")
    return {}


def _save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_kugou_path():
    """读取已保存的酷狗路径，没有则用代码里的默认路径"""
    path = _load_config().get(CONFIG_KEY_PATH, "").strip()
    return path or KUGOU_PATH


def save_kugou_path(path):
    cfg = _load_config()
    cfg[CONFIG_KEY_PATH] = path
    _save_config(cfg)


def load_auto_play_on_start():
    """读取'启动后自动播放'开关，默认 True"""
    cfg = _load_config()
    return bool(cfg.get(CONFIG_KEY_AUTO_PLAY, True))


def save_auto_play_on_start(enabled):
    cfg = _load_config()
    cfg[CONFIG_KEY_AUTO_PLAY] = bool(enabled)
    _save_config(cfg)


# 当前生效的酷狗路径（启动时从配置文件加载，界面修改保存后更新）
kugou_path = load_kugou_path()

# 全局状态
monitor_running = False
timer_running = False
_root = None       # 当前窗口引用（用于线程安全地刷新界面日志）
_log_text = None   # 界面日志框引用


def log(msg):
    """同时写界面日志框和日志文件（后台线程通过 after 安全刷新 UI）"""
    logger.info(msg)
    if _root is not None and _log_text is not None:
        line = f"{time.strftime('%H:%M:%S')} {msg}\n"
        _root.after(0, lambda: (_log_text.insert(tk.END, line), _log_text.see(tk.END)))


# 判断酷狗是否正在运行
def is_kugou_running():
    res = subprocess.run('tasklist /FI "IMAGENAME eq KuGou.exe"',
                         capture_output=True, text=True, encoding="gbk")
    return "KuGou.exe" in res.stdout


# 获取酷狗进程 PID
def get_kugou_pid():
    res = subprocess.run('tasklist /FI "IMAGENAME eq KuGou.exe" /FO CSV /NH',
                         capture_output=True, text=True, encoding="gbk")
    m = re.search(r'"KuGou.exe","(\d+)"', res.stdout)
    return int(m.group(1)) if m else None


# 关闭酷狗
def kill_kugou():
    subprocess.run("taskkill /F /IM KuGou.exe", shell=True, capture_output=True)


# 启动酷狗
# 自动播放开关（启动酷狗后是否自动发送播放键）
auto_play_on_start = load_auto_play_on_start()


def start_kugou():
    try:
        subprocess.Popen(kugou_path)
        # 启动后异步确认酷狗在播放（解决有时重启后停在暂停状态不自动播放的问题）
        if auto_play_on_start:
            threading.Thread(target=ensure_kugou_playing, daemon=True).start()
        else:
            log("ℹ️ 自动播放已关闭，酷狗启动后需手动点击播放")
    except FileNotFoundError:
        log(f"❌ 找不到 KuGou.exe：{kugou_path}")


# ============ 启动酷狗后触发播放 ============
# 不再做音频峰值检测（WASAPI 在酷狗某些版本/静音状态下读不到峰值，
# 反复按键会把正在播放的音乐暂停）。改为：启动后只发送一次空格键（酷狗播放/暂停快捷键）。

_user32 = ctypes.windll.user32


def find_kugou_window():
    """根据酷狗 PID 找到其可见主窗口句柄"""
    pid = get_kugou_pid()
    if not pid:
        return None
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        lpdw = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(lpdw))
        if lpdw.value == pid and _user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    _user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def kugou_play():
    """向酷狗主窗口发送一次空格键（酷狗播放/暂停快捷键），触发播放。
    只按一次，避免把正在播放的音乐暂停。"""
    hwnd = find_kugou_window()
    if not hwnd:
        log("⚠️ 未找到酷狗窗口，无法触发播放，请手动点击播放")
        return False
    WM_KEYDOWN, WM_KEYUP, VK_SPACE = 0x0100, 0x0101, 0x20
    try:
        _user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        _user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.3)
    _user32.PostMessageW(hwnd, WM_KEYDOWN, VK_SPACE, 0)
    time.sleep(0.05)
    _user32.PostMessageW(hwnd, WM_KEYUP, VK_SPACE, 0)
    log("🎵 已发送播放指令（空格键）")
    return True


def ensure_kugou_playing():
    """启动酷狗后调用：等待进程与窗口就绪，发送一次播放指令。"""
    # 等进程起来（最多 12 秒）
    for _ in range(24):
        if get_kugou_pid():
            break
        time.sleep(0.5)
    else:
        log("⚠️ 酷狗进程未成功启动，无法触发播放")
        return
    # 等窗口建立（最多 15 秒）
    for _ in range(30):
        if find_kugou_window():
            break
        time.sleep(0.5)
    kugou_play()


# 守护线程：检测酷狗退出就自动拉起
def monitor_loop():
    while monitor_running:
        if not is_kugou_running():
            start_kugou()
            log("【守护触发】酷狗已关闭，自动启动")
        time.sleep(2)


# 定时线程：先等 N 分钟，到点后关闭 + 立即重启
def timer_loop(interval_min):
    interval_sec = interval_min * 60
    while timer_running:
        # 先等待 N 分钟
        waited = 0.0
        while waited < interval_sec:
            if not timer_running:
                return
            time.sleep(0.5)
            waited += 0.5

        if not timer_running:
            return

        # 到点：关闭 + 立即重启
        log("【定时任务】到达时间，准备重启酷狗")
        kill_kugou()
        time.sleep(1)
        start_kugou()
        log("【定时任务】重启完成")


# 开启守护监控
def start_monitor():
    global monitor_running
    if monitor_running:
        messagebox.showinfo("提示", "守护监控已开启")
        return
    monitor_running = True
    threading.Thread(target=monitor_loop, daemon=True).start()
    log("✅守护监控已开启")


# 停止守护监控
def stop_monitor():
    global monitor_running
    monitor_running = False
    log("🛑守护监控已停止")


# 开启定时循环重启
def start_timer(entry_interval):
    global timer_running
    if timer_running:
        messagebox.showinfo("提示", "定时任务正在运行")
        return
    txt = entry_interval.get().strip()
    try:
        minute = float(txt)
        if minute <= 0:
            raise ValueError
    except ValueError:
        messagebox.showerror("错误", "请输入有效的数字（单位分钟）")
        return
    timer_running = True
    threading.Thread(target=timer_loop, args=(minute,), daemon=True).start()
    log(f"⏱️定时重启已开启，间隔 {minute} 分钟")
    log(f"   （{minute} 分钟后将关闭酷狗并立即重启）")


# 停止定时任务
def stop_timer():
    global timer_running
    timer_running = False
    log("⏹️定时重启任务已停止")


# 手动一键关闭酷狗
def manual_kill():
    kill_kugou()
    log("🔴手动关闭酷狗")


# 手动一键启动酷狗
def manual_start():
    start_kugou()
    log("🟢手动启动酷狗")


def create_kugou_tool(parent=None):
    """创建工具窗口：parent=None 独立运行（Tk + mainloop）；否则作为 Toplevel 嵌入主程序"""
    global _root, _log_text
    standalone = parent is None
    root = tk.Tk() if standalone else tk.Toplevel(parent)
    _root = root
    root.title("酷狗音乐守护+定时重启工具")
    root.geometry("720x520")

    # 顶部区域：酷狗路径配置
    frame_path = ttk.LabelFrame(root, text="酷狗路径配置")
    frame_path.pack(padx=10, pady=4, fill="x")
    var_path = tk.StringVar(value=kugou_path)
    entry_path = ttk.Entry(frame_path, textvariable=var_path, width=62)
    entry_path.grid(row=0, column=0, padx=6, pady=6)

    def browse_path():
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选择 KuGou.exe",
            filetypes=[("酷狗主程序", "KuGou.exe"), ("可执行文件", "*.exe")])
        if path:
            var_path.set(path)

    def apply_path():
        global kugou_path
        path = var_path.get().strip()
        if not path:
            messagebox.showerror("错误", "路径不能为空")
            return
        if not os.path.exists(path):
            messagebox.showerror("错误", f"文件不存在：\n{path}")
            return
        try:
            save_kugou_path(path)
        except Exception as e:
            messagebox.showerror("错误", f"保存配置失败：{e}")
            return
        kugou_path = path
        log(f"⚙️酷狗路径已更新并保存：{path}")
        messagebox.showinfo("成功", "酷狗路径已保存，下次启动仍生效")

    ttk.Button(frame_path, text="浏览…", command=browse_path).grid(row=0, column=1, padx=4, pady=6)
    ttk.Button(frame_path, text="保存", command=apply_path).grid(row=0, column=2, padx=4, pady=6)

    # 第一区域：守护监控
    frame1 = ttk.LabelFrame(root, text="进程守护（关闭酷狗自动拉起）")
    frame1.pack(padx=10, pady=4, fill="x")
    ttk.Button(frame1, text="开启守护", command=start_monitor).grid(row=0, column=0, padx=6, pady=6)
    ttk.Button(frame1, text="停止守护", command=stop_monitor).grid(row=0, column=1, padx=6, pady=6)

    # 第二区域：定时循环重启
    frame2 = ttk.LabelFrame(root, text="定时循环重启【到点关闭酷狗，立刻重新打开】")
    frame2.pack(padx=10, pady=4, fill="x")
    ttk.Label(frame2, text="循环间隔(分钟)：").grid(row=0, column=0, padx=5, pady=6)
    entry_interval = ttk.Entry(frame2, width=12)
    entry_interval.insert(0, "30")
    entry_interval.grid(row=0, column=1, padx=5, pady=6)
    ttk.Button(frame2, text="开启定时重启",
               command=lambda: start_timer(entry_interval)).grid(row=0, column=2, padx=6, pady=6)
    ttk.Button(frame2, text="停止定时重启", command=stop_timer).grid(row=0, column=3, padx=6, pady=6)

    # 第三区域：手动操作
    frame3 = ttk.LabelFrame(root, text="手动操作")
    frame3.pack(padx=10, pady=4, fill="x")
    ttk.Button(frame3, text="🔴手动关闭酷狗", command=manual_kill).grid(row=0, column=0, padx=6, pady=6)
    ttk.Button(frame3, text="🟢手动启动酷狗", command=manual_start).grid(row=0, column=1, padx=6, pady=6)
    ttk.Button(frame3, text="🎵手动播放",
               command=lambda: (
                   log("⚠️ 酷狗未运行，先点'🟢手动启动酷狗'再播放") if not get_kugou_pid()
                   else (threading.Thread(target=ensure_kugou_playing, daemon=True).start(), log("🎵手动触发播放指令已发出"))[1]
               )).grid(row=0, column=2, padx=6, pady=6)

    # 第四区域：自动播放设置
    frame4 = ttk.LabelFrame(root, text="启动后自动播放")
    frame4.pack(padx=10, pady=4, fill="x")
    var_auto_play = tk.BooleanVar(value=auto_play_on_start)

    def on_auto_play_toggle():
        global auto_play_on_start
        auto_play_on_start = var_auto_play.get()
        save_auto_play_on_start(auto_play_on_start)
        if auto_play_on_start:
            log("✅ 已开启：启动酷狗后自动发送播放指令")
        else:
            log("❌ 已关闭：启动酷狗后不会自动播放")

    ttk.Checkbutton(frame4, text="打开酷狗后自动点击播放（酷狗播放/暂停快捷键=空格键）",
                    variable=var_auto_play, command=on_auto_play_toggle).grid(row=0, column=0, padx=6, pady=6, sticky="w")

    # 日志区域（界面展示，同时写入日志文件）
    frame_log = ttk.LabelFrame(root, text="运行日志")
    frame_log.pack(padx=10, pady=5, fill="both", expand=True)
    log_text = tk.Text(frame_log, height=12)
    log_text.pack(fill="both", expand=True)
    _log_text = log_text
    log_text.insert(tk.END, "使用说明：\n1. 顶部可配置酷狗 KuGou.exe 路径（保存后下次启动仍生效）\n"
                            "2. 守护：手动退出酷狗自动重启\n3. 定时：每隔设定分钟自动关闭酷狗并立刻打开\n"
                            "4. 日志同时保存到 log/日期/kugou_auto_restart.log\n")

    def on_close():
        global monitor_running, timer_running, _root, _log_text
        monitor_running = False
        timer_running = False
        logger.info("========== 程序退出 ==========")
        _root = None
        _log_text = None
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    logger.info("========== 程序启动 ==========")
    if standalone:
        root.mainloop()


if __name__ == "__main__":
    create_kugou_tool()
