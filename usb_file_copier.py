import os
import sys
import ctypes
import shutil
import time
import logging
import threading
from queue import Queue, Empty
from datetime import datetime

import tkinter as tk
import tkinter.ttk as ttk
from tkinter import filedialog, messagebox


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
    log = logging.getLogger("usb_file_copier")
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
        file_handler = _DailyFileHandler("usb_file_copier")  # 按天自动滚动，跨天写入新日期目录
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


logger = _setup_logger()


def _get_removable_drives():
    """通过 Windows API 获取所有可移动驱动器盘符（使用标准库 ctypes，无需 pywin32）。"""
    DRIVE_REMOVABLE = 2
    kernel32 = ctypes.windll.kernel32
    # 显式声明参数与返回类型，保证 64 位 Python 下调用正确
    kernel32.GetLogicalDriveStringsW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
    kernel32.GetLogicalDriveStringsW.restype = ctypes.c_uint
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint

    buf = ctypes.create_unicode_buffer(1024)
    length = kernel32.GetLogicalDriveStringsW(len(buf) - 1, buf)
    if not length:
        return []

    # 返回的是以 '\x00' 分隔、并以 '\x00\x00' 结尾的多个盘符字符串。
    # 注意不能用 buf.value —— 它会在第一个 '\x00' 处截断，导致只能看到 C:\
    # 必须按返回的长度读取完整缓冲区内容后再按 '\x00' 分割。
    raw = ''.join(buf[:length])
    drives = [d for d in raw.split('\x00') if d]
    return [d for d in drives if kernel32.GetDriveTypeW(d) == DRIVE_REMOVABLE]


def run_usb_file_copier(parent=None):
    """
    运行 U盘文件复制工具

    - 单文件独立运行：不传参（parent=None），自动创建主窗口并进入主循环；
    - 被主程序调用：传入主窗口对象，工具以 Toplevel 子窗口形式弹出，由主程序的主循环驱动。

    :param parent: 主程序窗口对象，独立运行时为 None
    :return: USBDriveCopier 应用实例，方便外部控制
    """
    logger.info("启动U盘文件复制工具")
    # 视频格式列表
    VIDEO_FORMATS = {
        'mp4': 'MP4 - 通用视频格式',
        'avi': 'AVI - 标准视频格式',
        'mkv': 'MKV - 高清视频格式',
        'wmv': 'WMV - Windows媒体视频',
        'mov': 'MOV - Apple视频格式',
        'flv': 'FLV - 流媒体格式',
        'webm': 'WebM - 网页视频格式',
        'mpeg': 'MPEG - 标准视频格式',
        'mpg': 'MPG - 标准视频格式',
        'hevc': 'HEVC - 高效视频编码'
    }

    # 图片格式列表
    IMAGE_FORMATS = {
        'jpg': 'JPG - 照片格式',
        'jpeg': 'JPEG - 照片格式',
        'png': 'PNG - 支持透明的图片格式',
        'gif': 'GIF - 动图格式',
        'bmp': 'BMP - 无损位图格式',
        'webp': 'WebP - 高效网页图片格式',
        'svg': 'SVG - 矢量图形格式',
        'tiff': 'TIFF - 专业印刷格式',
        'ico': 'ICO - 图标格式'
    }

    class USBDriveCopier:
        def __init__(self, root):
            self.root = root
            self.root.title("U盘文件复制工具")
            self.root.geometry("950x720")

            # 初始化状态变量（界面回调与工作线程都会用到，需在创建界面之前完成）
            self.file_queue = Queue(maxsize=1000)
            self.copy_threads = []
            self.is_running = False
            self.worker_count = 4          # 复制工作线程数量
            self.lock = threading.Lock()   # 保护下面的共享计数
            self.scan_total = 0            # 本次扫描线程总数
            self.scan_done = 0             # 已结束的扫描线程数
            self.worker_done = 0           # 已结束的复制线程数
            self.copied_count = 0          # 已复制文件数
            self.found_files = []          # 选择模式下扫描到的文件 [(源, 目标)]
            self.tree_items = {}           # 文件列表项 {item_id: {source, target, checked}}
            self.stopped = False           # 是否被用户手动停止

            # 后台运行（静默监控 U 盘插入并自动复制）相关状态
            self._bg_running = False        # 后台监控是否运行中
            self._bg_monitor_stop = False   # 后台监控线程退出信号
            self._bg_drives_last = set()    # 上次检测到的盘符集合
            self._bg_drives_done = set()    # 后台模式下已处理过的盘符（避免重复触发）

            # 窗口关闭协议：确保后台监控/复制在窗口关闭前干净停止
            self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

            # 创建界面
            self.create_widgets()

        def create_widgets(self):
            # 创建顶部框架 - 目标目录选择
            top_frame = ttk.Frame(self.root, padding=10)
            top_frame.pack(fill=tk.X)

            ttk.Label(top_frame, text="目标目录:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)

            # 明确指定StringVar的父窗口
            self.target_dir_var = tk.StringVar(self.root, value=r"E:\U盘数据")
            entry = ttk.Entry(top_frame, textvariable=self.target_dir_var, width=50)
            entry.grid(row=0, column=1, padx=5, pady=5)

            # 绑定事件，使输入框内容变化时更新显示
            entry.bind("<KeyRelease>", self.update_path_display)

            ttk.Button(top_frame, text="浏览...", command=self.browse_directory).grid(row=0, column=2, padx=5, pady=5)

            # 显示选中的目录路径，明确指定StringVar的父窗口
            self.path_display_var = tk.StringVar(self.root)
            self.path_display_var.set(f"当前选择: {self.target_dir_var.get()}")
            ttk.Label(top_frame, textvariable=self.path_display_var, foreground="blue").grid(row=1, column=1, padx=5,
                                                                                             pady=2, sticky=tk.W)

            # 状态显示
            ttk.Label(top_frame, text="状态:").grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)
            self.status_var = tk.StringVar(self.root, value="就绪")
            ttk.Label(top_frame, textvariable=self.status_var).grid(row=2, column=1, padx=5, pady=5, sticky=tk.W)

            # 创建格式选择框架
            format_frame = ttk.LabelFrame(self.root, text="选择要复制的文件格式", padding=10)
            format_frame.pack(fill=tk.X, padx=10, pady=5)

            # 全选控制
            select_all_frame = ttk.Frame(format_frame, padding=5)
            select_all_frame.pack(fill=tk.X)

            self.select_all_videos_var = tk.BooleanVar(self.root, value=True)
            self.select_all_images_var = tk.BooleanVar(self.root, value=True)

            ttk.Checkbutton(select_all_frame, text="全选视频格式",
                            variable=self.select_all_videos_var,
                            command=self.update_video_checkboxes).pack(side=tk.LEFT, padx=20)

            ttk.Checkbutton(select_all_frame, text="全选图片格式",
                            variable=self.select_all_images_var,
                            command=self.update_image_checkboxes).pack(side=tk.LEFT, padx=20)

            # 视频格式选择 - 每行显示2个
            video_frame = ttk.LabelFrame(format_frame, text="视频格式", padding=5)
            video_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.video_vars = {}
            row = 0
            col = 0
            for ext, desc in VIDEO_FORMATS.items():
                var = tk.BooleanVar(self.root, value=True)  # 默认全选
                self.video_vars[ext] = var
                ttk.Checkbutton(video_frame, text=f"{ext} ({desc})", variable=var,
                                command=self.on_video_checkbox_changed).grid(
                    row=row, column=col, sticky=tk.W, padx=5, pady=2)
                col += 1
                if col > 1:  # 每行显示2个
                    col = 0
                    row += 1

            # 图片格式选择 - 每行显示2个
            image_frame = ttk.LabelFrame(format_frame, text="图片格式", padding=5)
            image_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.image_vars = {}
            row = 0
            col = 0
            for ext, desc in IMAGE_FORMATS.items():
                var = tk.BooleanVar(self.root, value=True)  # 默认全选
                self.image_vars[ext] = var
                ttk.Checkbutton(image_frame, text=f"{ext} ({desc})", variable=var,
                                command=self.on_image_checkbox_changed).grid(
                    row=row, column=col, sticky=tk.W, padx=5, pady=2)
                col += 1
                if col > 1:  # 每行显示2个
                    col = 0
                    row += 1

            # 复制模式选择
            mode_frame = ttk.LabelFrame(self.root, text="复制模式", padding=8)
            mode_frame.pack(fill=tk.X, padx=10, pady=5)

            self.mode_var = tk.StringVar(self.root, value="direct")
            ttk.Radiobutton(mode_frame, text="扫描后直接复制", value="direct",
                            variable=self.mode_var, command=self.on_mode_changed).pack(side=tk.LEFT, padx=15)
            ttk.Radiobutton(mode_frame, text="先扫描，再选择复制", value="select",
                            variable=self.mode_var, command=self.on_mode_changed).pack(side=tk.LEFT, padx=15)

            # 主要按钮
            button_frame = ttk.Frame(self.root, padding=10)
            button_frame.pack(fill=tk.X)

            self.start_button = ttk.Button(button_frame, text="开始复制", command=self.start_copying)
            self.start_button.pack(side=tk.LEFT, padx=5)

            self.stop_button = ttk.Button(button_frame, text="停止", command=self.stop_copying, state=tk.DISABLED)
            self.stop_button.pack(side=tk.LEFT, padx=5)

            # 后台运行：最小化窗口 + 监控 U 盘插入 + 静默复制（无弹窗）
            self.bg_start_button = ttk.Button(button_frame, text="🔕后台运行",
                                              command=self.start_background)
            self.bg_start_button.pack(side=tk.LEFT, padx=5)

            self.bg_stop_button = ttk.Button(button_frame, text="停止后台",
                                             command=self.stop_background,
                                             state=tk.DISABLED)
            self.bg_stop_button.pack(side=tk.LEFT, padx=5)

            # 以下按钮仅在“先扫描，再选择复制”模式下显示
            self.copy_selected_button = ttk.Button(button_frame, text="复制选中", command=self.copy_selected,
                                                   state=tk.DISABLED)
            self.select_all_button = ttk.Button(button_frame, text="全选",
                                                command=lambda: self.select_all_files(True))
            self.select_none_button = ttk.Button(button_frame, text="全不选",
                                                 command=lambda: self.select_all_files(False))

            # 扫描结果列表（仅在“先扫描，再选择复制”模式下显示）
            self.file_frame = ttk.LabelFrame(self.root, text="扫描到的文件（点击行可勾选/取消）", padding=5)
            self.file_tree = ttk.Treeview(self.file_frame, columns=("check", "name", "source"),
                                          show="headings", height=8)
            self.file_tree.heading("check", text="选择")
            self.file_tree.heading("name", text="文件名")
            self.file_tree.heading("source", text="来源路径")
            self.file_tree.column("check", width=50, anchor=tk.CENTER, stretch=False)
            self.file_tree.column("name", width=260, anchor=tk.W, stretch=False)
            self.file_tree.column("source", width=560, anchor=tk.W, stretch=True)
            self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            file_scrollbar = ttk.Scrollbar(self.file_frame, command=self.file_tree.yview)
            file_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.file_tree.config(yscrollcommand=file_scrollbar.set)
            self.file_tree.bind("<Button-1>", self.toggle_item)

            # 统计信息
            self.stats_frame = ttk.Frame(self.root, padding=10)
            self.stats_frame.pack(fill=tk.X)

            self.stats_var = tk.StringVar(self.root, value="准备就绪")
            ttk.Label(self.stats_frame, textvariable=self.stats_var).pack(anchor=tk.W)

            # 日志显示
            ttk.Label(self.root, text="操作日志:").pack(anchor=tk.W, padx=10, pady=5)

            self.log_text = tk.Text(self.root, height=15, width=110)
            self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

            scrollbar = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.log_text.config(yscrollcommand=scrollbar.set)

        def browse_directory(self):
            directory = filedialog.askdirectory(parent=self.root)  # 指定父窗口
            if directory:
                self.target_dir_var.set(directory)
                self.path_display_var.set(f"当前选择: {directory}")  # 更新显示的路径

        def update_path_display(self, event=None):
            """更新路径显示"""
            self.path_display_var.set(f"当前选择: {self.target_dir_var.get()}")

        def log(self, message, level=logging.INFO):
            """向日志窗口添加消息，同时写入日志文件与控制台"""
            logger.log(level, message)
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
            self.root.update_idletasks()

        def get_removable_drives(self):
            """获取所有可移动磁盘"""
            return _get_removable_drives()

        def get_selected_formats(self):
            """获取用户选择的文件格式"""
            selected_formats = set()

            # 添加选中的视频格式
            for ext, var in self.video_vars.items():
                if var.get():
                    selected_formats.add(ext)

            # 添加选中的图片格式
            for ext, var in self.image_vars.items():
                if var.get():
                    selected_formats.add(ext)

            return selected_formats

        def copy_file_task(self):
            """文件复制工作线程"""
            try:
                while self.is_running:
                    try:
                        item = self.file_queue.get(timeout=1)
                    except Empty:
                        continue

                    source, destination = item
                    if source is None:  # 退出信号
                        self.file_queue.task_done()
                        break

                    # 检查是否已停止
                    if not self.is_running:
                        self.file_queue.task_done()
                        break

                    try:
                        # 创建目标目录（如果不存在）
                        os.makedirs(os.path.dirname(destination), exist_ok=True)

                        # 使用shutil.copy2保留文件元数据
                        shutil.copy2(source, destination)
                        with self.lock:
                            self.copied_count += 1
                            copied = self.copied_count
                        self.log(f"成功复制: {source} -> {destination}")
                        self.stats_var.set(f"已复制: {copied} 个文件")
                    except Exception as e:
                        self.log(f"复制失败: {source} -> {destination}，错误：{e}", logging.WARNING)
                    finally:
                        self.file_queue.task_done()
            finally:
                # 线程结束，交给主线程统计是否全部完成
                self._on_worker_finished()

        def scan_drive(self, drive):
            """扫描驱动器：直接复制模式下把匹配文件放入复制队列；选择模式下收集到文件列表"""
            selected_formats = self.get_selected_formats()

            if not selected_formats:
                self.log("未选择任何文件格式，跳过扫描")
                self._on_scan_finished()
                return

            direct_mode = (self.mode_var.get() == "direct")
            target_dir = self.target_dir_var.get()

            self.log(f"开始扫描驱动器: {drive}")
            self.log(f"搜索的文件格式: {', '.join(sorted(selected_formats))}")

            total_files = 0
            matching_files = 0

            for root, _, files in os.walk(drive):
                # 检查是否已停止
                if not self.is_running:
                    break

                for file in files:
                    # 快速检查文件扩展名
                    ext = file.split('.')[-1].lower()
                    total_files += 1
                    if ext in selected_formats:
                        source_path = os.path.join(root, file)
                        target_path = os.path.join(target_dir, file)

                        # 避免覆盖已有文件
                        if os.path.exists(target_path):
                            base, ext = os.path.splitext(file)
                            counter = 1
                            while os.path.exists(os.path.join(target_dir, f"{base}_{counter}{ext}")):
                                counter += 1
                            target_path = os.path.join(target_dir, f"{base}_{counter}{ext}")

                        if direct_mode:
                            self.file_queue.put((source_path, target_path))
                        else:
                            with self.lock:
                                self.found_files.append((source_path, target_path))
                        matching_files += 1

                        # 更新统计信息
                        if total_files % 100 == 0:  # 每100个文件更新一次
                            self.stats_var.set(f"正在扫描: 已找到 {matching_files} 个匹配文件，共 {total_files} 个文件")

            self.log(f"扫描完成: {drive} - 共 {total_files} 个文件，找到 {matching_files} 个匹配文件")
            self._on_scan_finished()

        def _on_scan_finished(self):
            """扫描线程结束时调用：全部扫描完成后通知复制线程收尾，或刷新文件列表"""
            mode = self.mode_var.get()
            with self.lock:
                self.scan_done += 1
                all_scanned = self.scan_done >= self.scan_total
                running = self.is_running

            if all_scanned and running and mode == "direct":
                # 向复制线程发送退出信号，使其处理完队列内文件后结束
                for _ in range(self.worker_count):
                    self.file_queue.put((None, None))
            elif all_scanned and running and mode == "select":
                # 回到主线程刷新界面
                self.root.after(0, self._on_scan_complete_select)

        def _on_worker_finished(self):
            """复制线程结束时调用：所有复制线程结束后回到主线程复位界面"""
            with self.lock:
                self.worker_done += 1
                all_done = self.worker_done >= self.worker_count
            if all_done:
                self.root.after(0, self._on_all_done)

        def _on_all_done(self):
            """复制全部结束（在主线程执行）：复位按钮与状态"""
            with self.lock:
                copied = self.copied_count
            self.is_running = False
            self.status_var.set("就绪")
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            if self.mode_var.get() == "select" and self.tree_items:
                self.copy_selected_button.config(state=tk.NORMAL)
            if self.stopped:
                self.stats_var.set("操作已停止")
            else:
                self.stats_var.set(f"复制完成：共复制 {copied} 个文件")
                self.log(f"复制完成：共复制 {copied} 个文件")

        def _on_scan_complete_select(self):
            """选择模式下扫描完成（在主线程执行）：展示文件列表，等待用户勾选"""
            self.is_running = False
            self.status_var.set("就绪")
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            self.populate_file_list()
            count = len(self.tree_items)
            if count:
                self.copy_selected_button.config(state=tk.NORMAL)
                self.stats_var.set(f"扫描完成：共 {count} 个匹配文件，请勾选后点击“复制选中”")
            else:
                self.stats_var.set("扫描完成：未找到匹配文件")

        def populate_file_list(self):
            """把扫描结果填充到文件列表（默认全部勾选）"""
            self.clear_file_list()
            with self.lock:
                files = list(self.found_files)
            for source, target in files:
                item = self.file_tree.insert("", tk.END, values=("☑", os.path.basename(source), source))
                self.tree_items[item] = {"source": source, "target": target, "checked": True}
            self.log(f"已列出 {len(files)} 个匹配文件，请勾选需要复制的文件")

        def clear_file_list(self):
            """清空文件列表"""
            for item in self.file_tree.get_children():
                self.file_tree.delete(item)
            self.tree_items = {}

        def toggle_item(self, event):
            """点击列表行时切换勾选状态"""
            item = self.file_tree.identify_row(event.y)
            info = self.tree_items.get(item)
            if info is None:
                return
            info["checked"] = not info["checked"]
            self.file_tree.set(item, "check", "☑" if info["checked"] else "☐")

        def select_all_files(self, checked):
            """全选 / 全不选"""
            for item, info in self.tree_items.items():
                info["checked"] = checked
                self.file_tree.set(item, "check", "☑" if checked else "☐")

        def on_mode_changed(self):
            """切换复制模式：直连复制不显示文件列表，选择复制才显示"""
            if self.is_running:
                return
            if self.mode_var.get() == "select":
                self.file_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5, before=self.stats_frame)
                self.copy_selected_button.pack(side=tk.LEFT, padx=5)
                self.select_all_button.pack(side=tk.LEFT, padx=5)
                self.select_none_button.pack(side=tk.LEFT, padx=5)
                self.start_button.config(text="开始扫描")
                self.root.geometry("950x900")
                logger.info("复制模式切换为：先扫描，再选择复制")
            else:
                self.file_frame.pack_forget()
                self.copy_selected_button.pack_forget()
                self.select_all_button.pack_forget()
                self.select_none_button.pack_forget()
                self.start_button.config(text="开始复制")
                self.root.geometry("950x720")
                logger.info("复制模式切换为：扫描后直接复制")

        def copy_selected(self):
            """复制列表中已勾选的文件"""
            if self.is_running:
                return

            items = [(info["source"], info["target"]) for info in self.tree_items.values() if info["checked"]]
            if not items:
                messagebox.showwarning("提示", "请先勾选要复制的文件", parent=self.root)
                return

            target_dir = self.target_dir_var.get()
            if not target_dir:
                messagebox.showerror("错误", "请设置目标目录", parent=self.root)
                return
            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception as e:
                messagebox.showerror("错误", f"无法创建目标目录: {str(e)}", parent=self.root)
                return

            self.stopped = False
            self.reset_queue_and_threads()
            with self.lock:
                self.worker_done = 0
                self.copied_count = 0

            self.is_running = True
            self.status_var.set("正在复制...")
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
            self.copy_selected_button.config(state=tk.DISABLED)

            for source, target in items:
                self.file_queue.put((source, target))
            for _ in range(self.worker_count):
                self.file_queue.put((None, None))
            for _ in range(self.worker_count):
                t = threading.Thread(target=self.copy_file_task)
                t.daemon = True
                t.start()
                self.copy_threads.append(t)

            self.log(f"开始复制勾选的 {len(items)} 个文件")

        def start_copying(self):
            """开始扫描/复制过程"""
            if self.is_running:
                return

            # 检查目标目录
            target_dir = self.target_dir_var.get()
            if not target_dir:
                messagebox.showerror("错误", "请设置目标目录", parent=self.root)  # 指定父窗口
                return

            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception as e:
                messagebox.showerror("错误", f"无法创建目标目录: {str(e)}", parent=self.root)  # 指定父窗口
                return

            # 检查是否选择了格式
            selected_formats = self.get_selected_formats()
            if not selected_formats:
                messagebox.showerror("错误", "请至少选择一种文件格式", parent=self.root)  # 指定父窗口
                return

            # 获取可移动驱动器
            drives = self.get_removable_drives()
            if not drives:
                messagebox.showwarning("警告", "未检测到可移动驱动器", parent=self.root)  # 指定父窗口
                return

            mode = self.mode_var.get()

            # 清空日志
            self.log_text.delete(1.0, tk.END)
            self.log(f"检测到以下可移动驱动器: {', '.join(drives)}")

            # 重置队列、线程与计数器
            self.reset_queue_and_threads()
            with self.lock:
                self.found_files = []
                self.scan_total = len(drives)
                self.scan_done = 0
                self.worker_done = 0
                self.copied_count = 0
            self.stopped = False
            if mode == "select":
                self.clear_file_list()

            # 开始扫描
            self.is_running = True
            self.status_var.set("正在扫描...")
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
            if mode == "select":
                self.copy_selected_button.config(state=tk.DISABLED)
                self.stats_var.set("正在扫描，请稍候...")

            # 直接复制模式：立即启动复制线程，边扫描边复制
            if mode == "direct":
                for _ in range(self.worker_count):
                    t = threading.Thread(target=self.copy_file_task)
                    t.daemon = True
                    t.start()
                    self.copy_threads.append(t)

            # 为每个驱动器创建扫描线程
            for drive in drives:
                t = threading.Thread(target=self.scan_drive, args=(drive,))
                t.daemon = True
                t.start()

        def _on_window_close(self):
            """窗口关闭前的清理：停后台监控 + 停复制，然后销毁窗口"""
            # 1. 如果后台监控在跑，先优雅停止
            if self._bg_running:
                try:
                    self.stop_background()
                except Exception:
                    pass
            # 2. 如果复制在跑，停掉
            if self.is_running:
                try:
                    self.stopped = True
                    self.stop_copying()
                except Exception:
                    pass
            # 3. 销毁窗口（Toplevel 用 destroy()；独立 Tk 也用 destroy()）
            try:
                self.root.destroy()
            except tk.TclError:
                pass

        def reset_queue_and_threads(self):
            """重置队列和线程"""
            # 清空队列
            while not self.file_queue.empty():
                try:
                    self.file_queue.get_nowait()
                    self.file_queue.task_done()
                except:
                    pass

            # 重置线程列表
            self.copy_threads = []

        def stop_copying(self):
            """停止复制过程"""
            if not self.is_running:
                return

            self.stopped = True
            self.is_running = False
            self.status_var.set("正在停止...")

            # 清空队列
            self.reset_queue_and_threads()

            # 添加退出信号
            for _ in range(self.worker_count):  # 添加足够的退出信号
                self.file_queue.put((None, None))

            self.status_var.set("已停止")
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            self.stats_var.set("操作已停止")
            self.log("复制过程已停止")

        # ============ 后台运行（静默监控 U 盘插入 + 自动复制） ============
        def start_background(self):
            """启动后台监控：最小化窗口，自动检测 U 盘插入并静默复制（全程无弹窗）"""
            if self._bg_running:
                return
            # 校验：目标目录必须有、至少选了一种格式
            target_dir = self.target_dir_var.get()
            if not target_dir:
                messagebox.showerror("错误", "请先设置目标目录")
                return
            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception as e:
                messagebox.showerror("错误", f"无法创建目标目录: {e}")
                return
            selected_formats = self.get_selected_formats()
            if not selected_formats:
                messagebox.showerror("错误", "请至少选择一种文件格式")
                return

            # 记录当前已知盘符（排除当前已存在的 U 盘，避免立即触发复制）
            self._bg_drives_last = set(_get_removable_drives())
            self._bg_drives_done = set()
            self._bg_running = True
            self._bg_monitor_stop = False

            # 禁用操作按钮
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.DISABLED)
            self.bg_start_button.config(state=tk.DISABLED)
            self.bg_stop_button.config(state=tk.NORMAL)

            self.status_var.set("后台监控中（窗口已最小化）")
            self.log(f"🔕 后台运行已启动，目标目录：{target_dir}")
            self.log(f"   当前检测到的可移动驱动器：{', '.join(sorted(self._bg_drives_last)) or '(无)'}")
            self.log("   窗口将最小化，新 U 盘插入后自动复制，全程不弹窗")

            # 隐藏/最小化窗口
            # - 独立运行（Tk）：iconify() → 最小化到任务栏
            # - 被主程序调用（Toplevel）：iconify() 无效，用 withdraw() 完全隐藏
            try:
                if isinstance(self.root, tk.Toplevel):
                    self.root.withdraw()
                    self.log("   （子窗口已完全隐藏，关闭主程序或点'停止后台'可恢复）")
                else:
                    self.root.iconify()
            except tk.TclError:
                pass

            # 后台监控线程
            threading.Thread(target=self._background_monitor_loop, daemon=True).start()

        def stop_background(self):
            """停止后台监控，恢复窗口"""
            if not self._bg_running:
                return
            self._bg_monitor_stop = True
            self._bg_running = False

            # 恢复窗口
            try:
                self.root.deiconify()
            except tk.TclError:
                pass
            try:
                self.root.lift()
                self.root.focus_force()
            except tk.TclError:
                pass

            self.status_var.set("就绪")
            self.start_button.config(state=tk.NORMAL)
            self.bg_start_button.config(state=tk.NORMAL)
            self.bg_stop_button.config(state=tk.DISABLED)
            self.log("🛑 后台监控已停止，窗口已恢复")

        def _background_monitor_loop(self):
            """后台线程：轮询可移动驱动器，发现新 U 盘插入时自动复制（无弹窗）"""
            logger.info(f"后台监控线程已启动，stop={self._bg_monitor_stop}, running={self._bg_running}")
            tick = 0
            try:
                while not self._bg_monitor_stop:
                    tick += 1
                    try:
                        current = set(_get_removable_drives())
                        # 计算新插入的盘符
                        new_drives = current - self._bg_drives_last - self._bg_drives_done
                        if new_drives and not self.is_running:
                            # 只处理"首次出现"且"未复制过"的盘符
                            logger.info(f"后台监控：发现新 U 盘 {new_drives}")
                            self.root.after(0, lambda d=list(new_drives): self._bg_copy_drives_silent(d))
                            self._bg_drives_done.update(new_drives)
                        self._bg_drives_last = current
                    except Exception as e:
                        logger.error(f"后台监控轮询异常：{e}")
                    # 间隔 3 秒检查一次（插入/拔出都能及时感知）
                    for _ in range(30):
                        if self._bg_monitor_stop:
                            break
                        time.sleep(0.1)
                logger.info(f"后台监控线程正常退出（tick={tick}, stop={self._bg_monitor_stop}, running={self._bg_running}）")
            except Exception as e:
                logger.exception(f"后台监控线程异常崩溃：{e}")
            finally:
                # 这里只记日志，**不自动恢复窗口**——恢复窗口只在用户主动点"停止后台"按钮时发生
                logger.info(f"后台监控线程已结束（tick={tick}, stop={self._bg_monitor_stop}）")

        def _bg_copy_drives_silent(self, drives):
            """静默版 direct 复制：不弹任何 messagebox，纯日志 + 后台执行
            检测到新 U 盘插入时由 _background_monitor_loop 触发"""
            if self.is_running:
                logger.warning(f"后台复制触发但已有任务在运行，跳过本次（{drives}）")
                return
            if not drives:
                return

            target_dir = self.target_dir_var.get()
            # 确保目录存在（可能在后台运行期间被删除）
            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception as e:
                logger.error(f"后台模式：目标目录无法创建 {e}")
                return

            selected_formats = self.get_selected_formats()
            if not selected_formats:
                logger.warning("后台模式：没有选中任何文件格式，跳过复制")
                return

            self.reset_queue_and_threads()
            with self.lock:
                self.found_files = []
                self.scan_total = len(drives)
                self.scan_done = 0
                self.worker_done = 0
                self.copied_count = 0
            self.stopped = False

            self.is_running = True
            self.status_var.set(f"后台复制中：{', '.join(drives)}（窗口已最小化）")
            self.log(f"🔌 检测到新 U 盘：{', '.join(drives)}，开始自动复制")

            # 启动复制线程
            for _ in range(self.worker_count):
                t = threading.Thread(target=self.copy_file_task)
                t.daemon = True
                t.start()
                self.copy_threads.append(t)

            # 启动扫描线程
            for drive in drives:
                t = threading.Thread(target=self.scan_drive, args=(drive,))
                t.daemon = True
                t.start()

        def update_video_checkboxes(self):
            """更新所有视频格式复选框状态"""
            is_selected = self.select_all_videos_var.get()
            for var in self.video_vars.values():
                var.set(is_selected)

        def update_image_checkboxes(self):
            """更新所有图片格式复选框状态"""
            is_selected = self.select_all_images_var.get()
            for var in self.image_vars.values():
                var.set(is_selected)

        def on_video_checkbox_changed(self):
            """当单个视频格式复选框状态改变时更新全选复选框状态"""
            all_selected = all(var.get() for var in self.video_vars.values())
            self.select_all_videos_var.set(all_selected)

        def on_image_checkbox_changed(self):
            """当单个图片格式复选框状态改变时更新全选复选框状态"""
            all_selected = all(var.get() for var in self.image_vars.values())
            self.select_all_images_var.set(all_selected)

    # 判断运行方式：存在可用根窗口说明是被主程序调用，否则为单文件独立运行
    master = parent if parent is not None else getattr(tk, "_default_root", None)
    standalone = master is None

    if standalone:
        # 独立运行：直接以 Tk 作为主窗口
        root = tk.Tk()
        logger.info("运行方式：独立运行")
    else:
        # 被主程序调用：创建 Toplevel 子窗口，避免与主窗口冲突
        root = tk.Toplevel(master)
        root.transient(master)
        logger.info("运行方式：被主程序调用（子窗口）")

    # 创建并运行应用
    app = USBDriveCopier(root)

    # 仅独立运行时才进入主循环；被主程序调用时由主程序的主循环驱动
    if standalone:
        root.mainloop()
        logger.info("U盘文件复制工具已关闭")

    # 返回应用实例，方便外部控制
    return app


if __name__ == "__main__":
    run_usb_file_copier()