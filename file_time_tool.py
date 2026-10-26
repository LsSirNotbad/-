import os
import sys
import ctypes
import logging
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

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
    log = logging.getLogger("file_time_tool")
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
        file_handler = _DailyFileHandler("file_time_tool")  # 按天自动滚动，跨天写入新日期目录
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

# ---------- Windows 属性常量 ----------
FILE_ATTRIBUTE_READONLY = 0x01
FILE_ATTRIBUTE_HIDDEN = 0x02
FILE_ATTRIBUTE_SYSTEM = 0x04
FILE_ATTRIBUTE_ARCHIVE = 0x20
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None


def get_file_attrs(path):
    """返回 Windows 属性 int；失败返回 0"""
    if not kernel32:
        return 0
    attrs = kernel32.GetFileAttributesW(str(path))
    if attrs == INVALID_FILE_ATTRIBUTES:
        return 0
    return attrs


def set_file_attrs(path, attrs):
    """设置属性，返回是否成功"""
    if not kernel32:
        return False
    return bool(kernel32.SetFileAttributesW(str(path), attrs))


def attrs_to_str(attrs):
    """属性 int → 可读字符串"""
    if attrs == 0:
        return "-"
    names = []
    if attrs & FILE_ATTRIBUTE_READONLY:
        names.append("只读")
    if attrs & FILE_ATTRIBUTE_HIDDEN:
        names.append("隐藏")
    if attrs & FILE_ATTRIBUTE_SYSTEM:
        names.append("系统")
    if attrs & FILE_ATTRIBUTE_ARCHIVE:
        names.append("存档")
    if not names:
        names.append("普通")
    return "、".join(names)


def ts_to_str(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def str_to_ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


class FileTimeTool:
    def __init__(self, parent=None):
        # ---------- 支持独立运行 ----------
        if parent is None:
            self._own_root = tk.Tk()
            self._own_root.withdraw()
            parent = self._own_root
        else:
            self._own_root = None

        self.win = tk.Toplevel(parent)
        self.win.title("文件时间 / 属性修改工具")
        self.win.geometry("1050x720")
        self.win.minsize(900, 600)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        # 数据：每一项是一个 dict
        self.files = []

        # ========== 顶部工具条 ==========
        top = tk.Frame(self.win)
        top.pack(fill="x", padx=10, pady=8)

        tk.Button(top, text="📂 选择文件", width=12,
                  command=self.add_files).pack(side="left", padx=4)
        tk.Button(top, text="📁 选择文件夹", width=12,
                  command=self.add_folder).pack(side="left", padx=4)
        tk.Button(top, text="🗑 移除选中", width=12,
                  command=self.remove_selected).pack(side="left", padx=4)
        tk.Button(top, text="🧹 清空全部", width=12,
                  command=self.clear_all).pack(side="left", padx=4)

        self.status = tk.StringVar(value="就绪")
        tk.Label(top, textvariable=self.status, fg="gray",
                 font=("微软雅黑", 9)).pack(side="right", padx=8)

        # ========== 表格区 ==========
        table_frame = tk.Frame(self.win)
        table_frame.pack(fill="both", expand=True, padx=10, pady=4)

        columns = ("name", "dir", "ctime", "atime", "mtime", "attr")
        self.tree = ttk.Treeview(table_frame, columns=columns,
                                 show="headings", selectmode="extended")

        headers = {
            "name": ("文件名", 200),
            "dir": ("所在目录", 320),
            "ctime": ("创建时间", 150),
            "atime": ("最后访问时间", 150),
            "mtime": ("修改时间", 150),
            "attr": ("属性", 120),
        }
        for col, (text, w) in headers.items():
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="w")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # 绑定选择事件
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # ========== 时间修改区 ==========
        time_frame = tk.LabelFrame(self.win, text="修改时间（默认显示选中文件的时间）",
                                   padx=8, pady=8)
        time_frame.pack(fill="x", padx=10, pady=6)

        # 三行时间
        self.var_ctime = tk.StringVar()
        self.var_atime = tk.StringVar()
        self.var_mtime = tk.StringVar()

        for i, (label, var) in enumerate([
            ("创建时间：", self.var_ctime),
            ("访问时间：", self.var_atime),
            ("修改时间：", self.var_mtime),
        ]):
            tk.Label(time_frame, text=label, font=("微软雅黑", 10)).grid(
                row=i, column=0, sticky="e", padx=4, pady=3)
            tk.Entry(time_frame, textvariable=var, width=24,
                     font=("Consolas", 10)).grid(row=i, column=1, padx=4, pady=3)
            tk.Label(time_frame, text="格式：YYYY-MM-DD HH:MM:SS",
                     fg="gray", font=("微软雅黑", 8)).grid(
                row=i, column=2, sticky="w", padx=4)

        # 常用时间快捷按钮
        quick = tk.Frame(time_frame)
        quick.grid(row=0, column=3, rowspan=3, padx=10)

        def set_now():
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.var_ctime.set(now)
            self.var_atime.set(now)
            self.var_mtime.set(now)

        tk.Button(quick, text="填入当前时间", width=12,
                  command=set_now).pack(pady=2)
        tk.Button(quick, text="读取选中文件", width=12,
                  command=self.load_selected_time).pack(pady=2)

        # ========== 属性修改区 ==========
        attr_frame = tk.LabelFrame(self.win, text="修改文件属性",
                                   padx=8, pady=8)
        attr_frame.pack(fill="x", padx=10, pady=6)

        self.var_readonly = tk.BooleanVar()
        self.var_hidden = tk.BooleanVar()
        self.var_system = tk.BooleanVar()
        self.var_archive = tk.BooleanVar()

        tk.Checkbutton(attr_frame, text="只读", variable=self.var_readonly,
                       font=("微软雅黑", 10)).pack(side="left", padx=10)
        tk.Checkbutton(attr_frame, text="隐藏", variable=self.var_hidden,
                       font=("微软雅黑", 10)).pack(side="left", padx=10)
        tk.Checkbutton(attr_frame, text="系统", variable=self.var_system,
                       font=("微软雅黑", 10)).pack(side="left", padx=10)
        tk.Checkbutton(attr_frame, text="存档", variable=self.var_archive,
                       font=("微软雅黑", 10)).pack(side="left", padx=10)

        tk.Label(attr_frame, text="（勾选=设置，取消=清除；加密属性 Windows 不支持 API 修改）",
                 fg="gray", font=("微软雅黑", 8)).pack(side="left", padx=10)

        # ========== 操作按钮 ==========
        op_frame = tk.Frame(self.win)
        op_frame.pack(fill="x", padx=10, pady=10)

        tk.Button(op_frame, text="✔ 修改选中文件", width=16, height=2,
                  bg="#27ae60", fg="white", font=("微软雅黑", 10, "bold"),
                  command=lambda: self.apply_changes(False)).pack(side="left", padx=6)
        tk.Button(op_frame, text="✔✔ 修改全部文件", width=16, height=2,
                  bg="#2980b9", fg="white", font=("微软雅黑", 10, "bold"),
                  command=lambda: self.apply_changes(True)).pack(side="left", padx=6)
        tk.Button(op_frame, text="🔄 刷新信息", width=12, height=2,
                  font=("微软雅黑", 10),
                  command=self.refresh_all).pack(side="left", padx=6)

        logger.info("界面初始化完成，工具已打开")

    # ================= 窗口关闭 =================
    def _on_close(self):
        try:
            self.win.destroy()
        finally:
            if self._own_root is not None:
                self._own_root.destroy()

    def _log(self, msg, level="info"):
        getattr(logger, level, logger.info)(msg)

    # ================= 添加 / 移除文件 =================
    def add_files(self):
        self._log("开始添加文件…")
        paths = filedialog.askopenfilenames(title="选择文件（可多选）")
        if not paths:
            return
        added = 0
        for p in paths:
            if self._add_one(p):
                added += 1
        self._log(f"添加文件 {added} 个")
        self._refresh_tree()

    def add_folder(self):
        self._log("开始从文件夹添加文件…")
        folder = filedialog.askdirectory(title="选择文件夹（会递归添加其中所有文件）")
        if not folder:
            return
        added = 0
        for root, _, files in os.walk(folder):
            for name in files:
                full = os.path.join(root, name)
                if self._add_one(full):
                    added += 1
        self._log(f"从文件夹添加 {added} 个文件：{folder}")
        self._refresh_tree()

    def _add_one(self, path):
        path = os.path.abspath(path)
        if any(f["path"] == path for f in self.files):
            return False
        try:
            stat = os.stat(path)
        except Exception as e:
            self._log(f"读取失败：{path}，{e}", "warning")
            return False
        attrs = get_file_attrs(path)
        self.files.append({
            "path": path,
            "name": os.path.basename(path),
            "dir": os.path.dirname(path),
            "ctime": stat.st_ctime,
            "atime": stat.st_atime,
            "mtime": stat.st_mtime,
            "attr": attrs,
        })
        return True

    def remove_selected(self):
        self._log("开始移除选中文件…")
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在表格中选择要移除的行")
            return
        # 用 path 反查
        paths_to_remove = set()
        for iid in sel:
            vals = self.tree.item(iid, "values")
            if vals:
                paths_to_remove.add(vals[0] + "|" + vals[1])   # name + dir 近似匹配
        # 更稳的做法：iid 存 index
        idx_to_remove = sorted([int(iid) for iid in sel], reverse=True)
        for i in idx_to_remove:
            if 0 <= i < len(self.files):
                del self.files[i]
        self._log(f"移除 {len(idx_to_remove)} 个文件")
        self._refresh_tree()

    def clear_all(self):
        self._log("开始清空全部文件…")
        if not self.files:
            return
        if not messagebox.askyesno("确认", f"确定清空全部 {len(self.files)} 个文件？"):
            return
        self.files.clear()
        self._refresh_tree()
        self.status.set("已清空")

    # ================= 表格刷新 =================
    def _refresh_tree(self):
        # 清空
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        # 重新填充；iid = index
        for i, f in enumerate(self.files):
            self.tree.insert("", "end", iid=str(i), values=(
                f["name"],
                f["dir"],
                ts_to_str(f["ctime"]),
                ts_to_str(f["atime"]),
                ts_to_str(f["mtime"]),
                attrs_to_str(f["attr"]),
            ))
        self.status.set(f"共 {len(self.files)} 个文件")

    def refresh_all(self):
        self._log("开始刷新文件信息…")
        for f in self.files:
            try:
                stat = os.stat(f["path"])
                f["ctime"] = stat.st_ctime
                f["atime"] = stat.st_atime
                f["mtime"] = stat.st_mtime
                f["attr"] = get_file_attrs(f["path"])
            except Exception as e:
                self._log(f"刷新失败：{f['path']}，{e}", "warning")
        self._refresh_tree()
        self._log("已刷新全部文件信息")

    # ================= 选择行时联动 =================
    def on_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.files):
            f = self.files[idx]
            self.var_ctime.set(ts_to_str(f["ctime"]))
            self.var_atime.set(ts_to_str(f["atime"]))
            self.var_mtime.set(ts_to_str(f["mtime"]))
            attrs = f["attr"]
            self.var_readonly.set(bool(attrs & FILE_ATTRIBUTE_READONLY))
            self.var_hidden.set(bool(attrs & FILE_ATTRIBUTE_HIDDEN))
            self.var_system.set(bool(attrs & FILE_ATTRIBUTE_SYSTEM))
            self.var_archive.set(bool(attrs & FILE_ATTRIBUTE_ARCHIVE))

    def load_selected_time(self):
        self._log("开始读取选中文件时间…")
        self.on_select()

    # ================= 应用修改 =================
    def apply_changes(self, all_files):
        self._log("开始修改文件时间…")
        if not self.files:
            messagebox.showinfo("提示", "请先添加文件")
            return

        # 收集目标索引
        if all_files:
            targets = list(range(len(self.files)))
        else:
            sel = self.tree.selection()
            if not sel:
                messagebox.showinfo("提示", "请先在表格中选择要修改的文件")
                return
            targets = [int(i) for i in sel]

        # 校验三个时间
        try:
            ctime = str_to_ts(self.var_ctime.get().strip())
            atime = str_to_ts(self.var_atime.get().strip())
            mtime = str_to_ts(self.var_mtime.get().strip())
        except ValueError:
            messagebox.showerror("错误", "时间格式错误，应为：YYYY-MM-DD HH:MM:SS")
            return

        # 计算新属性
        new_attr = 0
        if self.var_readonly.get():
            new_attr |= FILE_ATTRIBUTE_READONLY
        if self.var_hidden.get():
            new_attr |= FILE_ATTRIBUTE_HIDDEN
        if self.var_system.get():
            new_attr |= FILE_ATTRIBUTE_SYSTEM
        if self.var_archive.get():
            new_attr |= FILE_ATTRIBUTE_ARCHIVE
        if new_attr == 0:
            new_attr = FILE_ATTRIBUTE_NORMAL

        ok, fail = 0, 0
        for i in targets:
            f = self.files[i]
            path = f["path"]
            try:
                # 修改时间（创建时间在 Windows 上用 os.utime 改不了，
                # 这里 ctime 只能尽量模拟，一般无效；atime/mtime 有效）
                os.utime(path, (atime, mtime))

                # 改属性（先清除只读，避免后面写失败）
                set_file_attrs(path, FILE_ATTRIBUTE_NORMAL)
                if not set_file_attrs(path, new_attr):
                    raise OSError("设置属性失败（可能权限不足）")

                # 刷新缓存
                stat = os.stat(path)
                f["atime"] = stat.st_atime
                f["mtime"] = stat.st_mtime
                f["attr"] = new_attr

                # 创建时间：Windows 需要 pywin32，这里优雅降级
                try:
                    import win32file
                    import pywintypes
                    handle = win32file.CreateFile(
                        path, win32file.GENERIC_WRITE,
                        0, None, win32file.OPEN_EXISTING,
                        win32file.FILE_ATTRIBUTE_NORMAL, None
                    )
                    win32file.SetFileTime(
                        handle, pywintypes.Time(ctime), None, None
                    )
                    handle.Close()
                    f["ctime"] = ctime
                except ImportError:
                    # 没装 pywin32，创建时间改不了，记录一下
                    self._log(f"  {f['name']}：创建时间未修改（装 pywin32 可支持）", "warning")
                except Exception as e:
                    self._log(f"  {f['name']}：修改创建时间失败 {e}", "warning")

                ok += 1
                self._log(f"✅ {path}")

            except Exception as e:
                fail += 1
                self._log(f"❌ {path} 修改失败：{e}", "error")

        self._refresh_tree()
        messagebox.showinfo("完成", f"成功 {ok} 个，失败 {fail} 个")


# ================= 独立运行 =================
if __name__ == '__main__':
    logger.info("启动文件时间/属性工具")
    FileTimeTool()
    tk.mainloop()