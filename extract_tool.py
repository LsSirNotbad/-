import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import logging
import time
import os
from datetime import datetime
import itertools
import shutil
import sys
import rarfile
import pyzipper


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
    """修复 Windows 控制台/IDE 使用非 UTF-8 编码（如 cp1252）输出中文时的报错：
    'charmap' codec can't encode characters ... character maps to <undefined>
    """
    if stream is None:
        return None
    try:
        # 保留原编码，仅在遇到无法编码的字符时用替换字符兜底
        stream.reconfigure(errors="replace")
        return stream
    except Exception:
        return _SafeStream(stream)


# 让标准输出/标准错误在遇到无法编码的字符时不再报错
sys.stdout = _make_output_safe(sys.stdout)
sys.stderr = _make_output_safe(sys.stderr)


# ===================== 全局日志配置（单文件运行 / 被主程序调用均可用）=====================
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
    """配置日志：写入 UTF-8 日志文件，同时安全输出到控制台。"""
    log = logging.getLogger("extract_tool")
    if log.handlers:
        return log

    log.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S")

    # 1. 写入日志文件（显式使用 UTF-8，中文写入不会报错）
    try:
        stamp = datetime.now().strftime("%Y%m%d")
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        log_dir = os.path.join(base_dir, "log", stamp)
        os.makedirs(log_dir, exist_ok=True)
        file_handler = _DailyFileHandler("extract_tool")  # 按天自动滚动，跨天写入新日期目录
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)
    except Exception:
        pass

    # 2. 输出到控制台（已做编码安全处理）
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        log.addHandler(stream_handler)

    # 独立维护日志，避免与主程序的日志配置相互干扰
    log.propagate = False
    return log


logger = _setup_logger()


def integrated_file_extractor(parent=None):
    """
    运行压缩文件解压工具

    - 单文件独立运行：不传参（parent=None），自动创建主窗口；
    - 被主程序调用：传入主窗口对象，工具以 Toplevel 子窗口形式弹出，避免与主窗口冲突。

    :param parent: 主程序窗口对象，独立运行时为 None
    """
    logger.info("启动压缩文件解压工具")

    # 判断运行方式：存在可用根窗口说明是被主程序调用，否则为单文件独立运行
    master = parent if parent is not None else getattr(tk, "_default_root", None)
    standalone = master is None

    if standalone:
        # 独立运行：直接以 Tk 作为主窗口
        root = tk.Tk()
    else:
        # 被主程序调用：创建 Toplevel 子窗口，避免与主窗口冲突
        root = tk.Toplevel(master)
        root.transient(master)

    root.title("压缩文件解压工具 v4.0")
    root.geometry("900x650")
    root.resizable(True, True)
    root.grab_set()

    # 使用独立的字体设置，不影响主窗口
    default_font = ('Microsoft YaHei UI', 11)
    title_font = ('Microsoft YaHei UI', 12, 'bold')

    # 全局变量
    stop_event = threading.Event()

    # 记录操作开始时间
    start_time = datetime.now()
    logger.info(f"工具界面创建完成，开始时间：{start_time}")

    # 状态更新函数 - 增加日志记录
    def update_status(message, level=logging.INFO):
        """更新状态并记录日志"""
        status_text.insert(tk.END, f"{time.strftime('%H:%M:%S')} - {message}")
        status_text.see(tk.END)
        root.update_idletasks()

        # 根据级别记录日志
        if level == logging.DEBUG:
            logger.debug(message.strip())
        elif level == logging.WARNING:
            logger.warning(message.strip())
        elif level == logging.ERROR:
            logger.error(message.strip())
        else:
            logger.info(message.strip())

    # 文件选择函数
    def select_file():
        logger.debug("开始选择压缩文件")
        file_path = filedialog.askopenfilename(
            filetypes=[("压缩文件", "*.zip *.rar"), ("ZIP文件", "*.zip"), ("RAR文件", "*.rar"), ("所有文件", "*.*")],
            parent=root
        )
        if file_path:
            file_var.set(file_path)
            if not extract_var.get():
                extract_var.set(os.path.dirname(file_path))
            update_status(f"已选择文件：{os.path.basename(file_path)}\n")
            logger.info(f"选择的压缩文件：{file_path}")
        else:
            logger.debug("用户取消选择文件")

    def select_extract_path():
        logger.debug("开始选择解压路径")
        default_path = extract_var.get() or os.path.dirname(file_var.get()) or os.getcwd()
        extract_path = filedialog.askdirectory(initialdir=default_path, title="选择解压路径", parent=root)
        if extract_path:
            extract_var.set(extract_path)
            update_status(f"解压路径：{extract_path}\n")
            logger.info(f"选择的解压路径：{extract_path}")
        else:
            logger.debug("用户取消选择解压路径")

    def select_dict_file():
        logger.debug("开始选择字典文件")
        dict_path = filedialog.askopenfilename(
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            title="选择字典文件",
            parent=root
        )
        if dict_path:
            dict_var.set(dict_path)
            update_status(f"已选择字典文件：{os.path.basename(dict_path)}\n")
            logger.info(f"选择的字典文件：{dict_path}")

            # 记录字典文件信息
            try:
                file_size = os.path.getsize(dict_path)
                update_status(f"字典文件大小：{file_size} 字节\n")
                logger.info(f"字典文件大小：{file_size} 字节")
            except Exception as e:
                logger.error(f"获取字典文件大小失败：{e}")
        else:
            logger.debug("用户取消选择字典文件")

    # 清空尝试密码记录
    def clear_tried_passwords():
        logger.info("清空密码尝试记录")
        if os.path.exists('tried_passwords.txt'):
            try:
                os.remove('tried_passwords.txt')
                update_status("已清空密码尝试记录\n")
                logger.info("密码尝试记录文件已删除")
                messagebox.showinfo("成功", "密码尝试记录已清空", parent=root)
            except Exception as e:
                update_status(f"清空记录失败：{str(e)}\n", logging.ERROR)
                logger.error(f"清空记录失败：{e}", exc_info=True)
                messagebox.showerror("错误", f"清空记录失败：{str(e)}", parent=root)
        else:
            logger.warning("密码尝试记录文件不存在")
            messagebox.showinfo("提示", "没有密码尝试记录文件", parent=root)

    # 生成字典文件
    def generate_dict_file():
        logger.info("开始生成字典文件")
        try:
            min_len = int(min_digits_var.get())
            max_len = int(max_digits_var.get())
            chars = charset_var.get()

            if min_len <= 0 or max_len < min_len or not chars:
                logger.warning("字典参数无效：min_len=%s, max_len=%s, chars=%s", min_len, max_len, chars)
                messagebox.showerror("错误", "请输入有效的字典参数", parent=root)
                return

            dict_path = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[("文本文件", "*.txt")],
                title="保存字典文件",
                parent=root
            )
            if dict_path:
                update_status(f"正在生成字典文件...\n")
                logger.info(f"生成字典文件：{dict_path}，长度范围：{min_len}-{max_len}，字符集：{chars}")

                count = 0
                start_gen = time.time()

                with open(dict_path, 'w', encoding='utf-8') as f:
                    for length in range(min_len, max_len + 1):
                        for pwd_tuple in itertools.product(chars, repeat=length):
                            if stop_event.is_set():
                                update_status("字典生成已取消\n", logging.WARNING)
                                logger.warning("字典生成被用户取消")
                                return

                            f.write(''.join(pwd_tuple) + '\n')
                            count += 1

                            # 每生成10000个密码记录一次进度
                            if count % 10000 == 0:
                                update_status(f"已生成{count}个密码...\n")
                                logger.debug(f"字典生成进度：{count}个密码")

                dict_var.set(dict_path)
                gen_time = time.time() - start_gen
                update_status(f"字典生成完成，共{count}个密码，耗时{gen_time:.2f}秒\n")
                logger.info(f"字典生成完成：{count}个密码，耗时{gen_time:.2f}秒")
                messagebox.showinfo("成功", f"字典生成完成！\n共生成{count}个密码\n耗时{gen_time:.2f}秒", parent=root)

        except ValueError as e:
            logger.error(f"字典参数值错误：{e}", exc_info=True)
            messagebox.showerror("错误", "请输入有效的数字", parent=root)
        except Exception as e:
            update_status(f"生成字典失败：{str(e)}\n", logging.ERROR)
            logger.error(f"生成字典失败：{e}", exc_info=True)
            messagebox.showerror("错误", f"生成字典失败：{str(e)}", parent=root)

    # 编码检测
    def get_zip_encoding(filename):
        """
        修复压缩包内文件名乱码，返回 (编码, 修正后的文件名)。

        - 文件名本身已是正常 Unicode（含中文）时直接返回，避免再按 cp437 转换；
        - 否则按常见编码尝试还原（cp437 → gbk/utf-8 等）；
        - 全部失败时返回原文件名，避免编码转换异常中断解压。
        """
        logger.debug(f"检测文件名编码：{filename}")

        # 已经是正常的 Unicode 文件名（含中文），无需转换
        if any('\u4e00' <= char <= '\u9fff' for char in filename):
            return 'utf-8', filename

        encodings = ['cp437', 'gbk', 'gb2312', 'utf-8', 'latin-1', 'iso-8859-1']

        for encoding in encodings:
            try:
                decoded = filename.encode('cp437').decode(encoding)
                if any('\u4e00' <= char <= '\u9fff' for char in decoded):
                    logger.debug(f"文件名编码检测结果：{encoding}")
                    return encoding, decoded
            except Exception:
                continue

        logger.debug("文件名编码检测结果：保持原文件名")
        return 'cp437', filename

    def _password_byte_candidates(password):
        """生成密码的候选字节编码并去重。

        纯 ASCII 密码的 utf-8/cp437/gbk/latin-1 编码完全相同，去重后可少测 3/4；
        同时避免中文密码无法按 cp437 编码时抛异常中断测试。
        """
        candidates = []
        for encoding in ('utf-8', 'cp437', 'gbk', 'latin-1'):
            try:
                pwd_bytes = password.encode(encoding)
            except Exception:
                continue
            if pwd_bytes not in candidates:
                candidates.append(pwd_bytes)
        return candidates

    # 密码测试函数
    def test_zip_password(zip_file_path, password):
        # logger.debug(f"测试ZIP密码：{password[:3]}***（长度：{len(password)}）")
        try:
            with pyzipper.AESZipFile(zip_file_path, 'r') as zf:
                test_file = None
                for info in zf.infolist():
                    if not info.is_dir() and len(info.filename) > 0:
                        test_file = info.filename
                        break

                if not test_file:
                    logger.warning("压缩包内无文件可测试")
                    return False, "压缩包内无文件"

                password_encodings = _password_byte_candidates(password)

                for pwd_bytes in password_encodings:
                    try:
                        zf.setpassword(pwd_bytes)
                        with zf.open(test_file) as f:
                            f.read(10)
                        logger.info(f"找到正确的ZIP密码：{password}")
                        return True, "密码正确"
                    except RuntimeError as e:
                        if "password" in str(e).lower():
                            continue
                        else:
                            logger.error(f"ZIP密码测试运行时错误：{e}")
                            return False, str(e)
                    except:
                        continue

                # logger.debug(f"密码不正确：{password[:3]}***")
                return False, "密码错误"

        except Exception as e:
            logger.error(f"ZIP密码测试失败：{e}", exc_info=True)
            return False, str(e)

    def test_rar_password(rar_file_path, password):
        # logger.debug(f"测试RAR密码：{password[:3]}***（长度：{len(password)}）")
        try:
            if sys.platform == 'win32' and not rarfile.UNRAR_TOOL:
                rarfile.UNRAR_TOOL = r"C:\Program Files\WinRAR\UnRAR.exe"
                logger.debug(f"设置UNRAR路径：{rarfile.UNRAR_TOOL}")

            with rarfile.RarFile(rar_file_path, 'r') as rf:
                test_file = None
                for filename in rf.namelist():
                    if not filename.endswith('/') and not filename.endswith('\\'):
                        test_file = filename
                        break

                if not test_file:
                    logger.warning("压缩包内无文件可测试")
                    return False, "压缩包内无文件"

                password_encodings = _password_byte_candidates(password)

                for pwd_bytes in password_encodings:
                    try:
                        with rf.open(test_file, pwd=pwd_bytes) as f:
                            f.read(10)
                        logger.info(f"找到正确的RAR密码：{password}")
                        return True, "密码正确"
                    except Exception as e:
                        continue

                # logger.debug(f"密码不正确：{password[:3]}***")
                return False, "密码错误"

        except Exception as e:
            logger.error(f"RAR密码测试失败：{e}", exc_info=True)
            return False, str(e)

    # ========== 多线程并发破解 ==========
    def _worker_count():
        """根据 CPU 核心数确定并发破解线程数（最多 16，至少 1）。"""
        return max(1, min(os.cpu_count() or 4, 16))

    def _parallel_crack(file_path, candidates, is_zip, label):
        """
        多线程并发测试候选密码，各线程通过加锁轮流从 candidates 取值。

        :return: (是否找到, 密码, 已尝试数量)
        """
        iterator = iter(candidates)
        iter_lock = threading.Lock()
        result = {'found': False, 'password': ''}
        state = {'count': 0, 'last_ui': 0}

        def next_password():
            with iter_lock:
                try:
                    password = next(iterator)
                except StopIteration:
                    return None
                state['count'] += 1
                return password

        def report(password):
            count = state['count']
            if count - state['last_ui'] >= 100:
                state['last_ui'] = count
                update_status(f"{label}已尝试{count}个密码... 当前：{password[:10]}\n")
            if count % 2000 == 0:
                logger.info(f"{label}进度：已尝试{count}个密码")

        def worker():
            try:
                while True:
                    if stop_event.is_set() or result['found']:
                        return
                    password = next_password()
                    if password is None:
                        return

                    if is_zip:
                        is_valid, _ = test_zip_password(file_path, password)
                    else:
                        is_valid, _ = test_rar_password(file_path, password)

                    if is_valid:
                        with iter_lock:
                            if not result['found']:
                                result['found'] = True
                                result['password'] = password
                        return
                    report(password)
            except Exception as e:
                logger.error(f"{label}线程异常：{e}", exc_info=True)

        count = _worker_count()
        update_status(f"{label}启动 {count} 个线程并发破解...\n")
        logger.info(f"{label}并发线程数：{count}")

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        return result['found'], result['password'], state['count']

    # 解压函数
    def extract_file(file_path, extract_path, password):
        logger.info(f"开始解压文件：{os.path.basename(file_path)} 到 {extract_path}")
        if not os.path.exists(file_path):
            logger.error(f"文件不存在：{file_path}")
            return False, "文件不存在"

        os.makedirs(extract_path, exist_ok=True)
        logger.debug(f"确保解压目录存在：{extract_path}")

        if file_path.lower().endswith('.zip'):
            return extract_zip_file_raw(file_path, extract_path, password)
        elif file_path.lower().endswith('.rar'):
            return extract_rar_file_raw(file_path, extract_path, password)
        else:
            logger.error(f"不支持的文件格式：{file_path}")
            return False, "不支持的文件格式"

    def extract_zip_file_raw(zip_file_path, extract_path, password):
        try:
            is_valid, msg = test_zip_password(zip_file_path, password)
            if not is_valid:
                return False, msg

            with pyzipper.AESZipFile(zip_file_path, 'r') as zf:
                password_bytes = None
                for encoding in ['utf-8', 'cp437', 'gbk']:
                    try:
                        password_bytes = password.encode(encoding)
                        zf.setpassword(password_bytes)
                        logger.debug(f"使用{encoding}编码设置密码")
                        break
                    except:
                        continue

                file_list = zf.infolist()
                if not file_list:
                    logger.warning("压缩包内无文件")
                    return False, "压缩包内无文件"

                success_count = 0
                total_files = sum(1 for info in file_list if not info.is_dir())
                logger.info(f"开始解压{total_files}个文件")

                for info in file_list:
                    if stop_event.is_set():
                        logger.warning("用户终止解压操作")
                        return False, "用户终止操作"

                    if info.is_dir():
                        continue

                    raw_name = info.filename
                    encoding, fixed_name = get_zip_encoding(raw_name)
                    target_path = os.path.join(extract_path, fixed_name)

                    os.makedirs(os.path.dirname(target_path), exist_ok=True)

                    try:
                        with zf.open(raw_name) as source, open(target_path, 'wb') as target:
                            shutil.copyfileobj(source, target)

                        success_count += 1
                        update_status(f"解压成功：{fixed_name}\n")
                        logger.debug(f"成功解压：{fixed_name}")

                        # 记录解压进度
                        if success_count % 10 == 0:
                            logger.info(f"解压进度：{success_count}/{total_files}")
                    except Exception as e:
                        update_status(f"解压失败 {raw_name} → {fixed_name}：{str(e)}\n", logging.WARNING)
                        logger.warning(f"解压失败 {raw_name} → {fixed_name}：{e}")
                        continue

                logger.info(f"解压完成：成功解压{success_count}/{total_files}个文件")
                if success_count > 0:
                    return True, f"成功解压{success_count}/{total_files}个文件"
                else:
                    return False, "未能解压任何文件"

        except Exception as e:
            logger.error(f"ZIP解压失败：{e}", exc_info=True)
            return False, str(e)

    def extract_rar_file_raw(rar_file_path, extract_path, password):
        try:
            is_valid, msg = test_rar_password(rar_file_path, password)
            if not is_valid:
                return False, msg

            if sys.platform == 'win32' and not rarfile.UNRAR_TOOL:
                rarfile.UNRAR_TOOL = r"C:\Program Files\WinRAR\UnRAR.exe"

            encodings = ['utf-8', 'gbk', 'cp936', 'cp437']

            for encoding in encodings:
                try:
                    rarfile.RarFile.CODEC = encoding
                    with rarfile.RarFile(rar_file_path, 'r') as rf:
                        pwd_bytes = None
                        for enc in ['utf-8', 'cp437', 'gbk']:
                            try:
                                pwd_bytes = password.encode(enc)
                                break
                            except:
                                continue

                        file_list = rf.namelist()
                        if not file_list:
                            continue

                        success_count = 0
                        total_files = sum(1 for f in file_list if not f.endswith('/') and not f.endswith('\\'))
                        logger.info(f"开始解压{total_files}个文件（编码：{encoding}）")

                        for filename in file_list:
                            if stop_event.is_set():
                                logger.warning("用户终止解压操作")
                                return False, "用户终止操作"

                            if filename.endswith('/') or filename.endswith('\\'):
                                continue

                            _, fixed_name = get_zip_encoding(filename)
                            target_path = os.path.join(extract_path, fixed_name)

                            try:
                                rf.extract(filename, extract_path, pwd=pwd_bytes)

                                temp_path = os.path.join(extract_path, filename)
                                if os.path.exists(temp_path) and temp_path != target_path:
                                    try:
                                        os.rename(temp_path, target_path)
                                    except:
                                        pass

                                success_count += 1
                                update_status(f"解压成功：{fixed_name}\n")
                                logger.debug(f"成功解压：{fixed_name}")

                                # 记录解压进度
                                if success_count % 10 == 0:
                                    logger.info(f"解压进度：{success_count}/{total_files}")
                            except Exception as e:
                                update_status(f"解压失败 {filename} → {fixed_name}：{str(e)}\n", logging.WARNING)
                                logger.warning(f"解压失败 {filename} → {fixed_name}：{e}")
                                continue

                        logger.info(f"解压完成：成功解压{success_count}/{total_files}个文件")
                        if success_count > 0:
                            return True, f"成功解压{success_count}/{total_files}个文件"
                        else:
                            continue

                except Exception as e:
                    logger.warning(f"使用{encoding}编码解压失败：{e}")
                    continue

            logger.error("无法识别RAR文件编码或解压失败")
            return False, "无法识别RAR文件编码或解压失败"

        except Exception as e:
            logger.error(f"RAR解压失败：{e}", exc_info=True)
            return False, str(e)

    # 直接解压线程
    def start_direct_extract():
        def extract_thread():
            file_path = file_var.get()
            extract_path = extract_var.get()
            password = password_var.get()

            if not file_path or not os.path.exists(file_path):
                logger.warning("未选择有效的压缩文件")
                messagebox.showerror("错误", "请选择有效的压缩文件", parent=root)
                return

            if not extract_path:
                logger.warning("未选择解压路径")
                messagebox.showerror("错误", "请选择解压路径", parent=root)
                return

            stop_button.config(state=tk.NORMAL)
            direct_button.config(state=tk.DISABLED)
            stop_event.clear()

            try:
                update_status(f"开始解压：{os.path.basename(file_path)}\n")
                update_status(f"解压路径：{extract_path}\n")
                logger.info(f"直接解压开始：{file_path}")

                start_time = time.time()
                success, msg = extract_file(file_path, extract_path, password)

                elapsed_time = time.time() - start_time
                if success:
                    update_status(f"{msg}，耗时：{elapsed_time:.2f}秒\n")
                    logger.info(f"解压成功：{msg}，耗时{elapsed_time:.2f}秒")
                    messagebox.showinfo("成功", f"{msg}\n耗时：{elapsed_time:.2f}秒", parent=root)
                else:
                    update_status(f"解压失败：{msg}\n", logging.ERROR)
                    logger.error(f"解压失败：{msg}")
                    messagebox.showerror("失败", f"解压失败：{msg}", parent=root)

            except Exception as e:
                update_status(f"错误：{str(e)}\n", logging.ERROR)
                logger.error(f"解压错误：{e}", exc_info=True)
                messagebox.showerror("错误", f"解压错误：{str(e)}", parent=root)
            finally:
                stop_button.config(state=tk.DISABLED)
                direct_button.config(state=tk.NORMAL)

        threading.Thread(target=extract_thread, daemon=True).start()

    # 暴力破解线程
    def start_brute_force():
        def brute_thread():
            file_path = file_var.get()
            extract_path = extract_var.get()

            if not file_path or not os.path.exists(file_path):
                logger.warning("未选择有效的压缩文件")
                messagebox.showerror("错误", "请选择有效的压缩文件", parent=root)
                return

            if not extract_path:
                logger.warning("未选择解压路径")
                messagebox.showerror("错误", "请选择解压路径", parent=root)
                return

            try:
                min_len = int(min_digits_var.get())
                max_len = int(max_digits_var.get())
                charset = charset_var.get()

                if min_len <= 0 or max_len < min_len or not charset:
                    logger.warning(f"暴力破解参数无效：min_len={min_len}, max_len={max_len}, chars={charset}")
                    messagebox.showerror("错误", "请输入有效的破解参数", parent=root)
                    return

            except ValueError as e:
                logger.error(f"暴力破解参数值错误：{e}", exc_info=True)
                messagebox.showerror("错误", "请输入有效的数字", parent=root)
                return

            stop_button.config(state=tk.NORMAL)
            brute_button.config(state=tk.DISABLED)
            stop_event.clear()

            tried_fh = None
            try:
                update_status(f"开始暴力破解：{os.path.basename(file_path)}\n")
                update_status(f"密码长度：{min_len}-{max_len}\n")
                update_status(f"字符集：{charset}\n")
                logger.info(f"暴力破解开始：{file_path}，长度{min_len}-{max_len}，字符集{charset}")

                tried_passwords = set()
                if os.path.exists('tried_passwords.txt'):
                    with open('tried_passwords.txt', 'r', encoding='utf-8') as f:
                        tried_passwords = set(line.strip() for line in f)
                    logger.info(f"已尝试密码数量：{len(tried_passwords)}")

                os.makedirs(extract_path, exist_ok=True)

                is_zip = file_path.lower().endswith('.zip')

                # 只保持一个带缓冲的追加句柄，避免每个密码都开关一次文件
                tried_fh = open('tried_passwords.txt', 'a', encoding='utf-8')

                def candidates():
                    for length in range(min_len, max_len + 1):
                        for pwd_tuple in itertools.product(charset, repeat=length):
                            if stop_event.is_set():
                                return
                            password = ''.join(pwd_tuple)
                            if password in tried_passwords:
                                continue
                            tried_passwords.add(password)
                            tried_fh.write(password + '\n')
                            yield password

                start_time = time.time()
                found, found_password, count = _parallel_crack(file_path, candidates(), is_zip, "暴力破解")
                elapsed_time = time.time() - start_time

                if found:
                    update_status(f"找到正确密码：{found_password}，开始解压...\n")
                    logger.info(f"找到正确密码：{found_password}")
                    success, extract_msg = extract_file(file_path, extract_path, found_password)

                    if success:
                        update_status(f"破解成功！密码：{found_password}\n")
                        update_status(f"{extract_msg}，共尝试{count}个密码，耗时：{elapsed_time:.2f}秒\n")
                        logger.info(f"暴力破解成功：密码={found_password}，尝试{count}个，耗时{elapsed_time:.2f}秒")
                        messagebox.showinfo("成功",
                                            f"破解成功！\n密码：{found_password}\n{extract_msg}\n共尝试{count}个密码\n耗时：{elapsed_time:.2f}秒",
                                            parent=root)
                    else:
                        update_status(f"密码正确但解压失败：{extract_msg}\n", logging.WARNING)
                        logger.warning(f"密码正确但解压失败：{extract_msg}")
                        messagebox.showerror("失败", f"密码正确但解压失败：{extract_msg}", parent=root)
                elif stop_event.is_set():
                    update_status("用户终止破解\n", logging.WARNING)
                    logger.warning("用户终止暴力破解")
                else:
                    update_status(f"暴力破解失败，未找到密码（共尝试{count}个）\n", logging.WARNING)
                    logger.warning(f"暴力破解失败：尝试{count}个密码，耗时{elapsed_time:.2f}秒")
                    messagebox.showinfo("结果",
                                        f"暴力破解完成\n未找到有效密码\n共尝试{count}个密码\n耗时：{elapsed_time:.2f}秒",
                                        parent=root)

            except Exception as e:
                update_status(f"破解错误：{str(e)}\n", logging.ERROR)
                logger.error(f"暴力破解错误：{e}", exc_info=True)
                messagebox.showerror("错误", f"破解错误：{str(e)}", parent=root)
            finally:
                if tried_fh is not None:
                    try:
                        tried_fh.close()
                    except Exception:
                        pass
                stop_button.config(state=tk.DISABLED)
                brute_button.config(state=tk.NORMAL)

        threading.Thread(target=brute_thread, daemon=True).start()

    # 字典破解线程
    def start_dict_extract():
        def dict_thread():
            file_path = file_var.get()
            extract_path = extract_var.get()
            dict_path = dict_var.get()

            if not file_path or not os.path.exists(file_path):
                logger.warning("未选择有效的压缩文件")
                messagebox.showerror("错误", "请选择有效的压缩文件", parent=root)
                return

            if not extract_path:
                logger.warning("未选择解压路径")
                messagebox.showerror("错误", "请选择解压路径", parent=root)
                return

            if not dict_path or not os.path.exists(dict_path):
                logger.warning("未选择有效的字典文件")
                messagebox.showerror("错误", "请选择有效的字典文件", parent=root)
                return

            stop_button.config(state=tk.NORMAL)
            dict_crack_button.config(state=tk.DISABLED)
            stop_event.clear()

            try:
                update_status(f"开始字典破解：{os.path.basename(file_path)}\n")
                update_status(f"字典文件：{os.path.basename(dict_path)}\n")
                logger.info(f"字典破解开始：{file_path}，字典：{dict_path}")

                passwords = []
                encodings = ['utf-8', 'gbk', 'gb2312', 'cp1252']

                for encoding in encodings:
                    try:
                        with open(dict_path, 'r', encoding=encoding) as f:
                            passwords = [line.strip() for line in f if line.strip()]
                        if passwords:
                            update_status(f"使用{encoding}编码读取字典\n")
                            logger.info(f"使用{encoding}编码读取字典，共{len(passwords)}个密码")
                            break
                    except Exception:
                        continue

                if not passwords:
                    with open(dict_path, 'rb') as f:
                        content = f.read().decode('utf-8', errors='ignore')
                        passwords = [line.strip() for line in content.split('\n') if line.strip()]

                if not passwords:
                    update_status("字典文件为空\n", logging.WARNING)
                    logger.warning("字典文件为空")
                    messagebox.showerror("错误", "字典文件为空", parent=root)
                    return

                update_status(f"字典加载完成，共{len(passwords)}个密码\n")
                logger.info(f"字典加载完成：{len(passwords)}个密码")

                os.makedirs(extract_path, exist_ok=True)

                is_zip = file_path.lower().endswith('.zip')

                start_time = time.time()
                found, found_password, count = _parallel_crack(
                    file_path, (p for p in passwords if p), is_zip, "字典破解")
                elapsed_time = time.time() - start_time

                if found:
                    update_status(f"找到正确密码：{found_password}，开始解压...\n")
                    logger.info(f"找到正确密码：{found_password}")
                    success, extract_msg = extract_file(file_path, extract_path, found_password)

                    if success:
                        update_status(f"破解成功！密码：{found_password}\n")
                        update_status(f"{extract_msg}，共尝试{count}个密码，耗时：{elapsed_time:.2f}秒\n")
                        logger.info(f"字典破解成功：密码={found_password}，尝试{count}个，耗时{elapsed_time:.2f}秒")
                        messagebox.showinfo("成功",
                                            f"破解成功！\n密码：{found_password}\n{extract_msg}\n共尝试{count}个密码\n耗时：{elapsed_time:.2f}秒",
                                            parent=root)
                    else:
                        update_status(f"密码正确但解压失败：{extract_msg}\n", logging.WARNING)
                        logger.warning(f"密码正确但解压失败：{extract_msg}")
                        messagebox.showerror("失败", f"密码正确但解压失败：{extract_msg}", parent=root)
                elif stop_event.is_set():
                    update_status("用户终止破解\n", logging.WARNING)
                    logger.warning("用户终止字典破解")
                else:
                    update_status(f"字典破解失败，未找到密码（共尝试{count}个）\n", logging.WARNING)
                    logger.warning(f"字典破解失败：尝试{count}/{len(passwords)}个密码，耗时{elapsed_time:.2f}秒")
                    messagebox.showinfo("结果",
                                        f"字典破解完成\n未找到有效密码\n共尝试{count}个密码\n耗时：{elapsed_time:.2f}秒",
                                        parent=root)

            except Exception as e:
                update_status(f"破解错误：{str(e)}\n", logging.ERROR)
                logger.error(f"字典破解错误：{e}", exc_info=True)
                messagebox.showerror("错误", f"破解错误：{str(e)}", parent=root)
            finally:
                stop_button.config(state=tk.DISABLED)
                dict_crack_button.config(state=tk.NORMAL)

        threading.Thread(target=dict_thread, daemon=True).start()

    # 停止操作
    def stop_operation():
        logger.warning("用户请求停止操作")
        stop_event.set()
        update_status("正在停止操作...\n", logging.WARNING)
        stop_button.config(state=tk.DISABLED)

    # ========== UI布局 ==========
    # 使用独立的变量定义，避免冲突
    file_var = tk.StringVar()
    extract_var = tk.StringVar()
    password_var = tk.StringVar(value="")  # 默认密码为空
    min_digits_var = tk.StringVar(value="6")  # 默认最小6位
    max_digits_var = tk.StringVar(value="6")  # 默认最大6位
    # 更全面的字符集：数字+大小写字母+常见符号
    charset_var = tk.StringVar(
        value="0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$%^&*()_+-=[]{}|;:,.<>?")
    dict_var = tk.StringVar()

    # 主容器
    main_frame = tk.Frame(root)
    main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    # 1. 文件选择区域
    file_frame = tk.LabelFrame(main_frame, text="文件选择")
    file_frame.pack(fill=tk.X, pady=(0, 10))
    file_frame.config(font=title_font)

    tk.Label(file_frame, text="压缩文件：", font=default_font).grid(row=0, column=0, sticky=tk.W, padx=(10, 5), pady=5)
    file_entry = tk.Entry(file_frame, textvariable=file_var, font=default_font)
    file_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 10), pady=5)
    file_button = tk.Button(file_frame, text="浏览", command=select_file, font=default_font)
    file_button.grid(row=0, column=2, padx=(0, 10), pady=5)

    tk.Label(file_frame, text="解压路径：", font=default_font).grid(row=1, column=0, sticky=tk.W, padx=(10, 5), pady=5)
    extract_entry = tk.Entry(file_frame, textvariable=extract_var, font=default_font)
    extract_entry.grid(row=1, column=1, sticky=tk.EW, padx=(0, 10), pady=5)
    extract_button = tk.Button(file_frame, text="浏览", command=select_extract_path, font=default_font)
    extract_button.grid(row=1, column=2, padx=(0, 10), pady=5)

    file_frame.columnconfigure(1, weight=1)

    # 其他区域布局保持不变...
    # 2. 直接解压区域
    direct_frame = tk.LabelFrame(main_frame, text="直接解压", font=title_font)
    direct_frame.pack(fill=tk.X, pady=(0, 10))

    tk.Label(direct_frame, text="解压密码：", font=default_font).grid(row=0, column=0, sticky=tk.W, padx=(10, 5), pady=5)
    password_entry = tk.Entry(direct_frame, textvariable=password_var, show="*", font=default_font)
    password_entry.grid(row=0, column=1, sticky=tk.W, padx=(0, 10), pady=5)
    direct_button = tk.Button(direct_frame, text="开始解压", command=start_direct_extract, font=default_font)
    direct_button.grid(row=0, column=2, padx=(0, 10), pady=5)

    # 3. 暴力破解区域
    brute_frame = tk.LabelFrame(main_frame, text="暴力破解", font=title_font)
    brute_frame.pack(fill=tk.X, pady=(0, 10))

    tk.Label(brute_frame, text="密码位数：", font=default_font).grid(row=0, column=0, sticky=tk.W, padx=(10, 5), pady=5)
    min_digits_entry = tk.Entry(brute_frame, textvariable=min_digits_var, width=8, font=default_font)
    min_digits_entry.grid(row=0, column=1, sticky=tk.W, pady=5)
    tk.Label(brute_frame, text="-", font=default_font).grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
    max_digits_entry = tk.Entry(brute_frame, textvariable=max_digits_var, width=8, font=default_font)
    max_digits_entry.grid(row=0, column=3, sticky=tk.W, padx=(0, 10), pady=5)

    tk.Label(brute_frame, text="字符集：", font=default_font).grid(row=0, column=4, sticky=tk.W, padx=(10, 5), pady=5)
    charset_entry = tk.Entry(brute_frame, textvariable=charset_var, font=default_font)
    charset_entry.grid(row=0, column=5, sticky=tk.W, padx=(0, 10), pady=5)

    brute_button = tk.Button(brute_frame, text="开始破解", command=start_brute_force, font=default_font)
    brute_button.grid(row=0, column=6, padx=(0, 10), pady=5)
    clear_button = tk.Button(brute_frame, text="清空记录", command=clear_tried_passwords, font=default_font)
    clear_button.grid(row=0, column=7, padx=(0, 10), pady=5)

    # 4. 字典破解区域
    dict_frame = tk.LabelFrame(main_frame, text="字典破解", font=title_font)
    dict_frame.pack(fill=tk.X, pady=(0, 10))

    tk.Label(dict_frame, text="字典文件：", font=default_font).grid(row=0, column=0, sticky=tk.W, padx=(10, 5), pady=5)
    dict_entry = tk.Entry(dict_frame, textvariable=dict_var, font=default_font)
    dict_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 10), pady=5)
    dict_button = tk.Button(dict_frame, text="浏览", command=select_dict_file, font=default_font)
    dict_button.grid(row=0, column=2, padx=(0, 10), pady=5)
    generate_dict_button = tk.Button(dict_frame, text="生成字典", command=generate_dict_file, font=default_font)
    generate_dict_button.grid(row=0, column=3, padx=(10, 10), pady=5)
    dict_crack_button = tk.Button(dict_frame, text="开始破解", command=start_dict_extract, font=default_font)
    dict_crack_button.grid(row=0, column=4, pady=5)

    dict_frame.columnconfigure(1, weight=1)

    # 5. 控制按钮区域
    control_frame = tk.Frame(main_frame)
    control_frame.pack(fill=tk.X, pady=(0, 10))

    stop_button = tk.Button(control_frame, text="停止操作", command=stop_operation,
                            state=tk.DISABLED, font=default_font)
    stop_button.pack(side=tk.RIGHT)

    # 6. 状态显示区域
    status_frame = tk.LabelFrame(main_frame, text="操作状态", font=title_font)
    status_frame.pack(fill=tk.BOTH, expand=True)

    status_text = tk.Text(status_frame, font=default_font, wrap=tk.WORD)
    scrollbar = tk.Scrollbar(status_frame, orient=tk.VERTICAL, command=status_text.yview)
    status_text.configure(yscrollcommand=scrollbar.set)

    status_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # 初始状态
    update_status("工具已启动，等待操作...\n")
    logger.info("压缩文件解压工具界面初始化完成")

    # 等待窗口关闭
    if standalone:
        # 独立运行时必须进入 mainloop，否则后台线程访问 Tk 变量时会报
        # RuntimeError: main thread is not in main loop
        root.mainloop()
    else:
        # 被主程序调用时，主程序已处于 mainloop 中，wait_window 等待子窗口关闭后返回
        root.wait_window()

    # 记录工具关闭
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    logger.info(f"压缩文件解压工具已关闭，运行时长：{duration:.2f}秒")


# ===================== 单文件独立运行入口 =====================
if __name__ == "__main__":
    integrated_file_extractor()

