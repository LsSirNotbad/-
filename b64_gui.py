# -*- coding: utf-8 -*-
"""
编码 / 加密 工具集
- 编码：Base64、Base64URL、Base32、Hex
- 哈希：MD5、SHA-1、SHA-256、SHA-512、SM3
- 校验：HMAC（MD5/SHA-1/SHA-256/SHA-512）
- 对称加密：AES（Fernet）、SM4（CBC）
- 密码哈希：bcrypt
- 支持选择字符编码（默认 UTF-8）
- 文件编码：自动加 Data URL 前缀；结果过大时只预览 + 复制到剪贴板

用法：
    from b64_gui import B64Window
    B64Window()          # 独立运行
    B64Window(parent)    # 挂在已有 Tk 主界面上
"""
import os
import sys
import base64
import hashlib
import hmac
import binascii
import logging
import secrets
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ---------- 可选依赖 ----------
try:
    from cryptography.fernet import Fernet, InvalidToken
    HAS_FERNET = True
except ImportError:
    HAS_FERNET = False

try:
    import bcrypt
    HAS_BCRYPT = True
except ImportError:
    HAS_BCRYPT = False

try:
    from gmssl import sm3, sm4, func as gm_func
    HAS_SM3 = True
    HAS_SM4 = True
except ImportError:
    HAS_SM3 = False
    HAS_SM4 = False

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
    log = logging.getLogger("b64_gui")
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
        file_handler = _DailyFileHandler("b64_gui")  # 按天自动滚动，跨天写入新日期目录
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

# ---------- 常量 ----------
# 输出框最多显示的字符数（超过就截断，完整结果进剪贴板）
MAX_PREVIEW = 50000

# 常见文件的 MIME 类型，用于拼 Data URL 前缀
MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".json": "application/json",
    ".txt": "text/plain",
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
}


def _truncate_for_display(s: str, limit: int = MAX_PREVIEW) -> str:
    """结果太长时截断，避免 Tk Text 卡死"""
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n\n... [结果共 {len(s)} 字符，此处只显示前 {limit} 字符]"


class B64Window:
    def __init__(self, parent=None):
        # 支持独立运行
        if parent is None:
            self._own_root = tk.Tk()
            self._own_root.withdraw()
            parent = self._own_root
        else:
            self._own_root = None

        self.win = tk.Toplevel(parent)
        self.win.title("编码 / 加密 工具")
        self.win.geometry("800x760")
        self.win.minsize(740, 660)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        # ---------- 模式选择 ----------
        mode_frame = ttk.LabelFrame(self.win, text="功能", padding=6)
        mode_frame.pack(fill="x", padx=10, pady=6)

        self.mode = tk.StringVar(value="b64")
        modes = [
            ("Base64 编解码", "b64"),
            ("Base64URL 编解码", "b64url"),
            ("Base32 编解码", "b32"),
            ("Hex 编解码", "hex"),
            ("哈希 (MD5/SHA/SM3)", "hash"),
            ("HMAC 校验", "hmac"),
            ("AES 对称加密", "aes"),
            ("SM4 对称加密", "sm4"),
            ("bcrypt 密码哈希", "bcrypt"),
        ]
        row = tk.Frame(mode_frame)
        row.pack(fill="x")
        for i, (text, val) in enumerate(modes):
            ttk.Radiobutton(row, text=text, variable=self.mode, value=val,
                            command=self._on_mode_change).pack(side="left", padx=4)
            if (i + 1) % 4 == 0:
                row = tk.Frame(mode_frame)
                row.pack(fill="x")

        # ---------- 方向选择 ----------
        dir_frame = ttk.LabelFrame(self.win, text="方向", padding=6)
        dir_frame.pack(fill="x", padx=10, pady=4)
        self.direction = tk.StringVar(value="encode")
        self.rb_encode = ttk.Radiobutton(dir_frame, text="加密 / 编码 / 计算",
                                         variable=self.direction, value="encode")
        self.rb_encode.pack(side="left", padx=10)
        self.rb_decode = ttk.Radiobutton(dir_frame, text="解密 / 解码 / 校验",
                                         variable=self.direction, value="decode")
        self.rb_decode.pack(side="left", padx=10)

        # ---------- 密钥 / 算法 / 编码 区 ----------
        self.key_frame = ttk.LabelFrame(self.win, text="密钥 / 算法参数", padding=6)
        self.key_frame.pack(fill="x", padx=10, pady=4)

        self.key_var = tk.StringVar()
        self.algo_var = tk.StringVar(value="SHA-256")
        self.encoding_var = tk.StringVar(value="utf-8")

        # 密钥
        tk.Label(self.key_frame, text="密钥：", font=("微软雅黑", 10)).grid(
            row=0, column=0, sticky="e", padx=4, pady=3)
        self.key_entry = tk.Entry(self.key_frame, textvariable=self.key_var,
                                  font=("Consolas", 10), width=52)
        self.key_entry.grid(row=0, column=1, columnspan=3, padx=4, pady=3, sticky="w")

        # 算法
        tk.Label(self.key_frame, text="算法：", font=("微软雅黑", 10)).grid(
            row=1, column=0, sticky="e", padx=4, pady=3)
        self.algo_combo = ttk.Combobox(
            self.key_frame, textvariable=self.algo_var, state="readonly",
            values=["MD5", "SHA-1", "SHA-256", "SHA-512", "SM3"],
            width=14, font=("微软雅黑", 10))
        self.algo_combo.grid(row=1, column=1, padx=4, pady=3, sticky="w")

        tk.Button(self.key_frame, text="生成随机密钥",
                  command=self.gen_key).grid(row=1, column=2, padx=6)

        # 字符编码
        tk.Label(self.key_frame, text="字符编码：", font=("微软雅黑", 10)).grid(
            row=2, column=0, sticky="e", padx=4, pady=3)
        self.encoding_combo = ttk.Combobox(
            self.key_frame, textvariable=self.encoding_var, state="readonly",
            values=["utf-8", "gbk", "gb2312", "gb18030", "big5",
                    "ascii", "latin-1", "utf-16", "utf-16-le", "utf-16-be",
                    "utf-32", "utf-32-le", "utf-32-be"],
            width=14, font=("微软雅黑", 10))
        self.encoding_combo.grid(row=2, column=1, padx=4, pady=3, sticky="w")
        tk.Label(self.key_frame, text="（加密/编码时文字按此编码转成字节）",
                 fg="gray", font=("微软雅黑", 8)).grid(
            row=2, column=2, columnspan=2, sticky="w", padx=4)

        # ---------- 输入区 ----------
        in_frame = ttk.LabelFrame(self.win, text="输入内容", padding=6)
        in_frame.pack(fill="both", expand=True, padx=10, pady=4)
        self.txt_in = tk.Text(in_frame, height=7, wrap="word",
                              font=("Consolas", 10))
        self.txt_in.pack(fill="both", expand=True)

        # ---------- 按钮区 ----------
        btn_frame = ttk.Frame(self.win)
        btn_frame.pack(fill="x", padx=10, pady=4)
        ttk.Button(btn_frame, text="▶ 执行", command=self.convert).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="⇅ 交换", command=self.swap).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="📋 复制结果", command=self.copy_result).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="🗑 清空", command=self.clear_all).pack(side="left", padx=4)

        # ---------- 文件操作区 ----------
        file_frame = ttk.LabelFrame(self.win, text="文件操作", padding=6)
        file_frame.pack(fill="x", padx=10, pady=4)
        ttk.Button(file_frame, text="📂 选择文件并处理（编码类）",
                   command=self.convert_file).pack(side="left", padx=4)
        ttk.Button(file_frame, text="📂 计算文件哈希",
                   command=self.hash_file).pack(side="left", padx=4)

        # ---------- 输出区 ----------
        out_frame = ttk.LabelFrame(self.win, text="输出结果", padding=6)
        out_frame.pack(fill="both", expand=True, padx=10, pady=4)
        self.txt_out = tk.Text(out_frame, height=7, wrap="word",
                               font=("Consolas", 10), bg="#f5f5f5")
        self.txt_out.pack(fill="both", expand=True)

        # ---------- 状态栏 ----------
        self.status = tk.StringVar(value="就绪")
        ttk.Label(self.win, textvariable=self.status, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")

        self._on_mode_change()
        logger.info("界面初始化完成，工具已打开")

    # ================= 窗口生命周期 =================
    def _on_close(self):
        try:
            self.win.destroy()
        finally:
            if self._own_root is not None:
                self._own_root.destroy()

    def _log(self, msg, level="info"):
        getattr(logger, level, logger.info)(msg)

    # ================= 模式切换 =================
    def _on_mode_change(self):
        mode = self.mode.get()
        # 密钥：编码类和哈希不需要；其它需要
        if mode in ("hash", "b64", "b64url", "b32", "hex"):
            state = "disabled"
        else:
            state = "normal"
        self.key_entry.config(state=state)
        # 算法下拉：只有 hash / hmac 用
        if mode in ("hash", "hmac"):
            self.algo_combo.config(state="readonly")
        else:
            self.algo_combo.config(state="disabled")
        # 方向：hash 和 bcrypt 只有单向
        if mode in ("hash", "bcrypt"):
            self.rb_decode.config(state="disabled")
            self.direction.set("encode")
        else:
            self.rb_decode.config(state="normal")

    # ================= 核心：文本处理 =================
    def convert(self):
        self._log("开始执行…")
        raw = self.txt_in.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showwarning("提示", "请输入要处理的内容")
            return
        mode = self.mode.get()
        direction = self.direction.get()
        key = self.key_var.get()
        enc = self.encoding_var.get()

        # 输入过大时给个提示
        if len(raw) > 1_000_000:
            if not messagebox.askyesno(
                    "提示",
                    f"输入内容约 {len(raw)} 字符，处理可能需要几秒。\n继续吗？"):
                return

        self.status.set("处理中…")
        self.win.update_idletasks()

        try:
            if mode == "b64":
                result = self._b64(raw, direction, enc)
            elif mode == "b64url":
                result = self._b64url(raw, direction, enc)
            elif mode == "b32":
                result = self._b32(raw, direction, enc)
            elif mode == "hex":
                result = self._hex(raw, direction, enc)
            elif mode == "hash":
                result = self._hash(raw, self.algo_var.get(), enc)
            elif mode == "hmac":
                if not key:
                    messagebox.showwarning("提示", "HMAC 需要密钥")
                    return
                result = self._hmac(raw, key, self.algo_var.get(), enc)
            elif mode == "aes":
                if not HAS_FERNET:
                    messagebox.showerror("错误", "需要 cryptography 库：\npip install cryptography")
                    return
                result = self._aes(raw, key, direction, enc)
            elif mode == "sm4":
                if not HAS_SM4:
                    messagebox.showerror("错误", "需要 gmssl 库：\npip install gmssl")
                    return
                result = self._sm4(raw, key, direction, enc)
            elif mode == "bcrypt":
                if not HAS_BCRYPT:
                    messagebox.showerror("错误", "需要 bcrypt 库：\npip install bcrypt")
                    return
                result = self._bcrypt_op(raw, key, direction, enc)
            else:
                result = "未支持的模式"

            # 只在输出框显示前 MAX_PREVIEW 字符，避免 Tk 卡死
            display = _truncate_for_display(result)
            self.txt_out.delete("1.0", tk.END)
            self.txt_out.insert(tk.END, display)

            # 结果太长时，自动复制完整结果到剪贴板
            if len(result) > MAX_PREVIEW:
                self.win.clipboard_clear()
                self.win.clipboard_append(result)
                self.status.set(f"✅ 完成，结果共 {len(result)} 字符（完整结果已复制到剪贴板）")
            else:
                self.status.set(f"✅ {mode} / {direction} 完成（{enc}）")

            self._log(f"{mode} / {direction} 完成，编码={enc}，输入 {len(raw)} → 输出 {len(result)}")

        except UnicodeDecodeError as e:
            self.status.set("❌ 编码错误")
            messagebox.showerror("错误",
                f"字符编码不匹配，无法用 {enc} 解码。\n\n"
                f"常见原因：\n"
                f"· 加密时用 UTF-8，解密时选了 GBK\n"
                f"· 密文里含非文本字节\n\n"
                f"详细：{e}")
            self._log(f"{mode} 编码错误：{e}", "error")
        except Exception as e:
            self.status.set("❌ 失败")
            messagebox.showerror("错误", f"处理失败：\n{e}")
            self._log(f"{mode} 失败：{e}", "error")

    # ---------- 编码 ----------
    @staticmethod
    def _b64(raw, direction, enc="utf-8"):
        if direction == "encode":
            return base64.b64encode(raw.encode(enc)).decode("utf-8")
        s = raw.strip()
        missing = len(s) % 4
        if missing:
            s += "=" * (4 - missing)
        return base64.b64decode(s.encode("utf-8")).decode(enc)

    @staticmethod
    def _b64url(raw, direction, enc="utf-8"):
        if direction == "encode":
            return base64.urlsafe_b64encode(raw.encode(enc)).decode("utf-8")
        s = raw.strip()
        missing = len(s) % 4
        if missing:
            s += "=" * (4 - missing)
        return base64.urlsafe_b64decode(s.encode("utf-8")).decode(enc)

    @staticmethod
    def _b32(raw, direction, enc="utf-8"):
        if direction == "encode":
            return base64.b32encode(raw.encode(enc)).decode("utf-8")
        return base64.b32decode(raw.strip().encode("utf-8")).decode(enc)

    @staticmethod
    def _hex(raw, direction, enc="utf-8"):
        if direction == "encode":
            return binascii.hexlify(raw.encode(enc)).decode("utf-8")
        return binascii.unhexlify(raw.strip().encode("utf-8")).decode(enc)

    # ---------- 哈希 ----------
    @staticmethod
    def _hash(raw, algo, enc="utf-8"):
        data = raw.encode(enc)
        if algo == "MD5":
            return hashlib.md5(data).hexdigest()
        elif algo == "SHA-1":
            return hashlib.sha1(data).hexdigest()
        elif algo == "SHA-256":
            return hashlib.sha256(data).hexdigest()
        elif algo == "SHA-512":
            return hashlib.sha512(data).hexdigest()
        elif algo == "SM3":
            if not HAS_SM3:
                raise RuntimeError("SM3 需要 gmssl：pip install gmssl")
            return sm3.sm3_hash(gm_func.bytes_to_list(data))
        else:
            raise ValueError(f"未知算法：{algo}")

    # ---------- HMAC ----------
    @staticmethod
    def _hmac(raw, key, algo, enc="utf-8"):
        data = raw.encode(enc)
        k = key.encode(enc)
        if algo == "MD5":
            return hmac.new(k, data, hashlib.md5).hexdigest()
        elif algo == "SHA-1":
            return hmac.new(k, data, hashlib.sha1).hexdigest()
        elif algo == "SHA-256":
            return hmac.new(k, data, hashlib.sha256).hexdigest()
        elif algo == "SHA-512":
            return hmac.new(k, data, hashlib.sha512).hexdigest()
        else:
            raise ValueError(f"HMAC 不支持：{algo}")

    # ---------- AES ----------
    @staticmethod
    def _aes(raw, key, direction, enc="utf-8"):
        if not key:
            raise ValueError("AES 需要密钥")
        digest = hashlib.sha256(key.encode(enc)).digest()
        fernet_key = base64.urlsafe_b64encode(digest)
        f = Fernet(fernet_key)
        if direction == "encode":
            return f.encrypt(raw.encode(enc)).decode("utf-8")
        try:
            return f.decrypt(raw.encode("utf-8")).decode(enc)
        except InvalidToken:
            raise RuntimeError("解密失败：密钥错误或内容被篡改")

    # ---------- SM4 ----------
    @staticmethod
    def _sm4(raw, key, direction, enc="utf-8"):
        if not key:
            raise ValueError("SM4 需要密钥")
        key_bytes = hashlib.sha256(key.encode(enc)).digest()[:16]
        iv = b"\x00" * 16
        crypt = sm4.CryptSM4()
        if direction == "encode":
            crypt.set_key(key_bytes, sm4.SM4_ENCRYPT)
            ciphertext = crypt.crypt_cbc(iv, raw.encode(enc))
            return base64.b64encode(ciphertext).decode("utf-8")
        else:
            crypt.set_key(key_bytes, sm4.SM4_DECRYPT)
            ciphertext = base64.b64decode(raw.strip().encode("utf-8"))
            plaintext = crypt.crypt_cbc(iv, ciphertext)
            return plaintext.decode(enc)

    # ---------- bcrypt ----------
    @staticmethod
    def _bcrypt_op(raw, key, direction, enc="utf-8"):
        if direction == "encode":
            salt = bcrypt.gensalt()
            return bcrypt.hashpw(raw.encode(enc), salt).decode("utf-8")
        else:
            if not key:
                raise ValueError("校验需要密钥（明文）")
            ok = bcrypt.checkpw(key.encode(enc), raw.encode("utf-8"))
            return "✅ 匹配" if ok else "❌ 不匹配"

    # ================= 快捷 =================
    def gen_key(self):
        self._log("开始生成随机密钥…")
        self.key_var.set(secrets.token_urlsafe(16))
        self.status.set("已生成随机密钥")

    def swap(self):
        self._log("开始交换输入/输出…")
        out = self.txt_out.get("1.0", tk.END).strip()
        if not out:
            return
        self.txt_in.delete("1.0", tk.END)
        self.txt_in.insert(tk.END, out)
        self.txt_out.delete("1.0", tk.END)
        if self.mode.get() not in ("hash", "bcrypt"):
            self.direction.set("decode" if self.direction.get() == "encode" else "encode")
        self.status.set("已交换输入/输出")

    def copy_result(self):
        self._log("开始复制结果…")
        out = self.txt_out.get("1.0", tk.END).strip()
        if not out:
            messagebox.showinfo("提示", "没有可复制的结果")
            return
        self.win.clipboard_clear()
        self.win.clipboard_append(out)
        self.status.set("✅ 已复制到剪贴板")

    def clear_all(self):
        self._log("开始清空…")
        self.txt_in.delete("1.0", tk.END)
        self.txt_out.delete("1.0", tk.END)
        self.status.set("已清空")

    # ================= 文件操作 =================
    def convert_file(self):
        self._log("开始处理文件…")
        path = filedialog.askopenfilename(title="选择文件")
        if not path:
            return
        mode = self.mode.get()
        direction = self.direction.get()

        try:
            if direction == "encode":
                # ========== 编码方向：只显示 + 复制，不自动落盘 ==========
                with open(path, "rb") as f:
                    data = f.read()

                if mode == "b64":
                    text = base64.b64encode(data).decode("ascii")
                elif mode == "b64url":
                    text = base64.urlsafe_b64encode(data).decode("ascii")
                elif mode == "b32":
                    text = base64.b32encode(data).decode("ascii")
                elif mode == "hex":
                    text = binascii.hexlify(data).decode("ascii")
                else:
                    messagebox.showinfo("提示", "该模式不支持文件整体处理，请用「计算文件哈希」")
                    return

                # Base64 / Base64URL 加 Data URL 前缀
                if mode in ("b64", "b64url"):
                    ext = os.path.splitext(path)[1].lower()
                    mime = MIME_MAP.get(ext)
                    if mime:
                        text = f"data:{mime};base64,{text}"

                # 只显示前 MAX_PREVIEW 字符
                display = _truncate_for_display(text)
                self.txt_out.delete("1.0", tk.END)
                self.txt_out.insert(tk.END, display)

                # 完整结果自动复制到剪贴板
                self.win.clipboard_clear()
                self.win.clipboard_append(text)

                total_len = len(text)
                if total_len > MAX_PREVIEW:
                    self.status.set(f"✅ 编码完成，共 {total_len} 字符（前 {MAX_PREVIEW} 显示，完整已复制）")
                else:
                    self.status.set(f"✅ 编码完成，共 {total_len} 字符（已复制到剪贴板）")

                self._log(f"文件编码：{path} → {total_len} 字符")

                # 询问是否保存为文件
                if messagebox.askyesno(
                        "完成",
                        f"编码完成，共 {total_len} 字符。\n"
                        f"已显示在下方，并已复制到剪贴板。\n\n"
                        f"是否另存为 txt 文件？"):
                    save_path = filedialog.asksaveasfilename(
                        title="保存编码结果",
                        defaultextension=".txt",
                        initialfile=os.path.basename(path) + ".b64.txt",
                        filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
                    if save_path:
                        with open(save_path, "w", encoding="utf-8") as f:
                            f.write(text)
                        self._log(f"编码结果保存：{save_path}")
                        messagebox.showinfo("成功", f"已保存：\n{save_path}")
                return

            # ========== 解码方向：从输入框读，还原成文件 ==========
            raw_text = self.txt_in.get("1.0", tk.END).strip()
            if not raw_text:
                # 输入框为空，就从选中文件读
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    raw_text = f.read().strip()

            # 剥掉 data:xxx;base64, 前缀
            if raw_text.startswith("data:") and "," in raw_text:
                raw_text = raw_text.split(",", 1)[1]

            # 去掉空白字符
            raw_text = "".join(raw_text.split())

            if mode == "b64":
                result = base64.b64decode(raw_text)
            elif mode == "b64url":
                result = base64.urlsafe_b64decode(raw_text)
            elif mode == "b32":
                result = base64.b32decode(raw_text)
            elif mode == "hex":
                result = binascii.unhexlify(raw_text)
            else:
                messagebox.showinfo("提示", "该模式不支持文件整体处理，请用「计算文件哈希」")
                return

            save_path = filedialog.asksaveasfilename(
                title="保存解码结果",
                defaultextension=".bin",
                initialfile=os.path.basename(path).replace(".b64.txt", "").replace(".txt", ""),
                filetypes=[("所有文件", "*.*")])
            if not save_path:
                return
            with open(save_path, "wb") as f:
                f.write(result)
            self.status.set(f"✅ 已保存：{os.path.basename(save_path)}")
            messagebox.showinfo("成功", f"已解码并保存：\n{save_path}")
            self._log(f"文件解码完成：{path} → {save_path}")

        except Exception as e:
            messagebox.showerror("错误", f"文件处理失败：\n{e}")
            self._log(f"文件处理失败：{e}", "error")

    def hash_file(self):
        self._log("开始计算文件哈希…")
        path = filedialog.askopenfilename(title="选择要计算哈希的文件")
        if not path:
            return
        algo = self.algo_var.get()
        try:
            h = {
                "MD5": hashlib.md5(),
                "SHA-1": hashlib.sha1(),
                "SHA-256": hashlib.sha256(),
                "SHA-512": hashlib.sha512(),
            }.get(algo)
            if h is None:
                messagebox.showinfo("提示", f"文件哈希不支持 {algo}")
                return
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            digest = h.hexdigest()
            self.txt_out.delete("1.0", tk.END)
            self.txt_out.insert(tk.END, digest)
            self.status.set(f"✅ {algo} 完成")
            self._log(f"{algo}({path}) = {digest}")
        except Exception as e:
            messagebox.showerror("错误", str(e))


# ================= 独立运行 =================
if __name__ == '__main__':
    logger.info("启动编码/加密工具集")
    B64Window()
    tk.mainloop()