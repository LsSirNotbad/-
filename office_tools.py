# -*- coding: utf-8 -*-
"""
Office 工具集（无 pandas 版本）
- 合并 PDF / 拆分 PDF / 图片转 PDF / 提取文字 / PDF 转图片 / PDF 转 Word
- 合并 Excel/CSV / 拆分工作表 / 按列去重 / 批量重命名列

依赖：
    pip install pypdf pillow openpyxl
    # 可选
    pip install pdf2image pdf2docx
"""
import os
import sys
import csv
import json
import logging
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from pypdf import PdfReader, PdfWriter
from PIL import Image
from openpyxl import load_workbook, Workbook


def _default_output_dir():
    """读取主程序「设置 → 默认输出目录」配置（app_config.json）；无效时返回空字符串"""
    try:
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, "app_config.json"), "r", encoding="utf-8") as f:
            path = (json.load(f).get("output_dir") or "").strip()
        return path if path and os.path.isdir(path) else ""
    except Exception:
        return ""

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
    log = logging.getLogger("office_tools")
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
        file_handler = _DailyFileHandler("office_tools")  # 按天自动滚动，跨天写入新日期目录
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

try:
    from pdf2docx import Converter
    HAS_PDF2DOCX = True
except ImportError:
    HAS_PDF2DOCX = False

POPPLER_PATH = None
try:
    from pdf2image import convert_from_path
    HAS_PDF2IMAGE = True
except ImportError:
    HAS_PDF2IMAGE = False


# ==================== Excel/CSV 通用读写 ====================
def read_table(file):
    """
    读取 xlsx/xls/csv 为一个统一结构：
      headers: [列名, ...]
      rows: [[值, ...], ...]
    """
    ext = os.path.splitext(file)[1].lower()
    if ext == ".csv":
        with open(file, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            data = list(reader)
        if not data:
            return [], []
        return data[0], data[1:]
    else:
        wb = load_workbook(file, data_only=True, read_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        try:
            headers = list(next(it))
        except StopIteration:
            wb.close()
            return [], []
        rows = [list(r) for r in it]
        wb.close()
        return headers, rows


def write_table(path, headers, rows):
    """根据扩展名保存为 xlsx 或 csv"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)
    else:
        wb = Workbook()
        ws = wb.active
        ws.append(headers)
        for r in rows:
            ws.append(r)
        wb.save(path)


def read_sheets(file):
    """读取 xlsx 的所有工作表：返回 {sheet_name: (headers, rows)}"""
    wb = load_workbook(file, data_only=True, read_only=True)
    result = {}
    for name in wb.sheetnames:
        ws = wb[name]
        it = ws.iter_rows(values_only=True)
        try:
            headers = list(next(it))
        except StopIteration:
            result[name] = ([], [])
            continue
        rows = [list(r) for r in it]
        result[name] = (headers, rows)
    wb.close()
    return result


# ==================== 主窗口 ====================
class OfficeToolsWindow:
    def __init__(self, parent=None):
        if parent is None:
            self._own_root = tk.Tk()
            self._own_root.withdraw()
            parent = self._own_root
        else:
            self._own_root = None

        self.win = tk.Toplevel(parent)
        self.win.title("Office 工具集")
        self.win.geometry("700x720")
        self.win.minsize(640, 600)
        self.win.resizable(True, True)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        tk.Label(self.win, text="🧰 Office 工具集",
                 font=("微软雅黑", 16, "bold"), fg="#2c3e50").pack(pady=10)

        # ---------- PDF 区 ----------
        pdf_frame = ttk.LabelFrame(self.win, text="PDF 工具", padding=6)
        pdf_frame.pack(fill="x", padx=14, pady=6)
        self._btn(pdf_frame, "① 合并 PDF", self.merge_pdf)
        self._btn(pdf_frame, "② 拆分 PDF（每页一个文件）", self.split_pdf)
        self._btn(pdf_frame, "③ 图片转 PDF", self.images_to_pdf)
        self._btn(pdf_frame, "④ 提取 PDF 文字", self.extract_text)
        if HAS_PDF2IMAGE:
            self._btn(pdf_frame, "⑤ PDF 转图片（PNG）", self.pdf_to_images)
        if HAS_PDF2DOCX:
            self._btn(pdf_frame, "⑥ PDF 转 Word", self.pdf_to_word)

        # ---------- Excel 区 ----------
        excel_frame = ttk.LabelFrame(self.win, text="Excel / CSV 工具", padding=6)
        excel_frame.pack(fill="x", padx=14, pady=6)
        self._btn(excel_frame, "① 合并多个 Excel/CSV", self.merge_excel)
        self._btn(excel_frame, "② 拆分工作表为多个文件", self.split_sheets)
        self._btn(excel_frame, "③ 按列去重（勾选列）", self.drop_duplicates_gui)
        self._btn(excel_frame, "④ 批量重命名列（搜索+批量填充）", self.rename_columns_gui)

        # ---------- 日志 ----------
        log_frame = ttk.LabelFrame(self.win, text="操作日志", padding=6)
        log_frame.pack(fill="both", expand=True, padx=14, pady=8)
        self.log = tk.Text(log_frame, height=10, wrap="word",
                           font=("Consolas", 10), bg="#f7f7f7")
        self.log.pack(fill="both", expand=True)

        self.status = tk.StringVar(value="就绪")
        tk.Label(self.win, textvariable=self.status, relief="sunken",
                 anchor="w").pack(fill="x", side="bottom")

        logger.info("界面初始化完成，工具已打开")

    # ================= 工具 =================
    def _on_close(self):
        try:
            self.win.destroy()
        finally:
            if self._own_root is not None:
                self._own_root.destroy()

    def _btn(self, parent, text, command):
        tk.Button(parent, text=text, width=40, height=1,
                  font=("微软雅黑", 10), bd=1, relief="groove",
                  anchor="w", command=command).pack(pady=2)

    def _log(self, msg):
        logger.info(msg)
        if hasattr(self, "log") and self.log.winfo_exists():
            self.log.insert(tk.END, msg + "\n")
            self.log.see(tk.END)
            self.win.update_idletasks()

    # ================= PDF 功能 =================
    def merge_pdf(self):
        self._log("开始合并 PDF…")
        files = filedialog.askopenfilenames(
            title="选择要合并的 PDF（可多选，按顺序合并）",
            filetypes=[("PDF 文件", "*.pdf")])
        if not files:
            return
        save_path = filedialog.asksaveasfilename(
            title="保存合并后的 PDF", defaultextension=".pdf",
            initialfile="merged.pdf", initialdir=_default_output_dir(),
            filetypes=[("PDF 文件", "*.pdf")])
        if not save_path:
            return
        try:
            writer = PdfWriter()
            for f in files:
                reader = PdfReader(f)
                for page in reader.pages:
                    writer.add_page(page)
                self._log(f"  已加入：{os.path.basename(f)}（{len(reader.pages)} 页）")
            with open(save_path, "wb") as out:
                writer.write(out)
            self._log(f"✅ 合并完成：{save_path}")
            self.status.set("合并完成")
            messagebox.showinfo("成功", f"已合并 {len(files)} 个文件")
        except Exception as e:
            self._log(f"❌ 合并失败：{e}")
            messagebox.showerror("错误", str(e))

    def split_pdf(self):
        self._log("开始拆分 PDF…")
        file = filedialog.askopenfilename(title="选择要拆分的 PDF",
                                          filetypes=[("PDF 文件", "*.pdf")])
        if not file:
            return
        out_dir = filedialog.askdirectory(title="选择输出目录",
                                          initialdir=_default_output_dir())
        if not out_dir:
            return
        try:
            reader = PdfReader(file)
            base = os.path.splitext(os.path.basename(file))[0]
            n = len(reader.pages)
            for i, page in enumerate(reader.pages, 1):
                writer = PdfWriter()
                writer.add_page(page)
                out_path = os.path.join(out_dir, f"{base}_第{i}页.pdf")
                with open(out_path, "wb") as f:
                    writer.write(f)
            self._log(f"✅ 已拆分为 {n} 个文件：{out_dir}")
            self.status.set("拆分完成")
            messagebox.showinfo("成功", f"已拆分为 {n} 个文件")
        except Exception as e:
            self._log(f"❌ 拆分失败：{e}")
            messagebox.showerror("错误", str(e))

    def images_to_pdf(self):
        self._log("开始图片转 PDF…")
        files = filedialog.askopenfilenames(
            title="选择图片（可多选，按顺序合并为一个 PDF）",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp")])
        if not files:
            return
        save_path = filedialog.asksaveasfilename(
            title="保存 PDF", defaultextension=".pdf",
            initialfile="images.pdf", initialdir=_default_output_dir(),
            filetypes=[("PDF 文件", "*.pdf")])
        if not save_path:
            return
        try:
            imgs = []
            for f in files:
                img = Image.open(f)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                imgs.append(img)
            imgs[0].save(save_path, "PDF", save_all=True, append_images=imgs[1:])
            self._log(f"✅ 已生成 PDF：{save_path}（{len(imgs)} 页）")
            self.status.set("转 PDF 完成")
            messagebox.showinfo("成功", f"已生成 {len(imgs)} 页 PDF")
        except Exception as e:
            self._log(f"❌ 图片转 PDF 失败：{e}")
            messagebox.showerror("错误", str(e))

    def extract_text(self):
        self._log("开始提取 PDF 文字…")
        file = filedialog.askopenfilename(title="选择 PDF",
                                          filetypes=[("PDF 文件", "*.pdf")])
        if not file:
            return
        save_path = filedialog.asksaveasfilename(
            title="保存文本", defaultextension=".txt",
            initialfile=os.path.splitext(os.path.basename(file))[0] + ".txt",
            initialdir=_default_output_dir(),
            filetypes=[("文本文件", "*.txt")])
        if not save_path:
            return
        try:
            reader = PdfReader(file)
            text = ""
            for i, page in enumerate(reader.pages, 1):
                t = page.extract_text() or ""
                text += f"\n===== 第 {i} 页 =====\n{t}\n"
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(text)
            self._log(f"✅ 已提取文字：{save_path}")
            self.status.set("提取完成")
            messagebox.showinfo("成功", "文字已提取并保存")
        except Exception as e:
            self._log(f"❌ 提取失败：{e}")
            messagebox.showerror("错误", str(e))

    def pdf_to_images(self):
        self._log("开始拆分为图片…")
        file = filedialog.askopenfilename(title="选择 PDF",
                                          filetypes=[("PDF 文件", "*.pdf")])
        if not file:
            return
        out_dir = filedialog.askdirectory(title="选择输出目录",
                                          initialdir=_default_output_dir())
        if not out_dir:
            return
        try:
            kwargs = {"dpi": 200}
            if POPPLER_PATH:
                kwargs["poppler_path"] = POPPLER_PATH
            images = convert_from_path(file, **kwargs)
            base = os.path.splitext(os.path.basename(file))[0]
            for i, img in enumerate(images, 1):
                img.save(os.path.join(out_dir, f"{base}_第{i}页.png"), "PNG")
            self._log(f"✅ 已导出 {len(images)} 张图片：{out_dir}")
            self.status.set("转图片完成")
            messagebox.showinfo("成功", f"已导出 {len(images)} 张 PNG")
        except Exception as e:
            self._log(f"❌ 转图片失败：{e}")
            messagebox.showerror("错误", str(e))

    def pdf_to_word(self):
        self._log("开始将 PDF 转 Word…")
        file = filedialog.askopenfilename(title="选择 PDF",
                                          filetypes=[("PDF 文件", "*.pdf")])
        if not file:
            return
        save_path = filedialog.asksaveasfilename(
            title="保存 Word", defaultextension=".docx",
            initialfile=os.path.splitext(os.path.basename(file))[0] + ".docx",
            initialdir=_default_output_dir(),
            filetypes=[("Word 文档", "*.docx")])
        if not save_path:
            return
        try:
            self._log(f"  正在转换：{os.path.basename(file)}…")
            self.status.set("PDF 转 Word 中…")
            self.win.update_idletasks()
            cv = Converter(file)
            cv.convert(save_path)
            cv.close()
            self._log(f"✅ 已转为 Word：{save_path}")
            self.status.set("转 Word 完成")
            messagebox.showinfo("成功", "已生成 Word 文件")
        except Exception as e:
            self._log(f"❌ 转 Word 失败：{e}")
            messagebox.showerror("错误", str(e))

    # ================= Excel 功能 =================
    def merge_excel(self):
        self._log("开始合并 Excel/CSV…")
        files = filedialog.askopenfilenames(
            title="选择要合并的文件（可多选）",
            filetypes=[("Excel/CSV", "*.xlsx *.xls *.csv")])
        if not files:
            return
        save_path = filedialog.asksaveasfilename(
            title="保存合并结果", defaultextension=".xlsx",
            initialfile="merged.xlsx", initialdir=_default_output_dir(),
            filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")])
        if not save_path:
            return
        try:
            # 以第一个文件的表头为准
            base_headers, base_rows = read_table(files[0])
            all_rows = list(base_rows)
            self._log(f"  已读取：{os.path.basename(files[0])}（{len(base_rows)} 行）")

            for f in files[1:]:
                h, rows = read_table(f)
                # 按 base_headers 顺序对齐
                idx_map = {name: i for i, name in enumerate(h)}
                for r in rows:
                    new_r = [r[idx_map.get(c, -1)] if c in idx_map else "" for c in base_headers]
                    all_rows.append(new_r)
                self._log(f"  已读取：{os.path.basename(f)}（{len(rows)} 行）")

            write_table(save_path, base_headers, all_rows)
            self._log(f"✅ 合并完成：{save_path}（共 {len(all_rows)} 行）")
            self.status.set("合并完成")
            messagebox.showinfo("成功", f"已合并 {len(files)} 个文件，共 {len(all_rows)} 行")
        except Exception as e:
            self._log(f"❌ 合并失败：{e}")
            messagebox.showerror("错误", str(e))

    def split_sheets(self):
        self._log("开始拆分工作表…")
        file = filedialog.askopenfilename(title="选择 Excel",
                                          filetypes=[("Excel", "*.xlsx *.xls")])
        if not file:
            return
        out_dir = filedialog.askdirectory(title="选择输出目录",
                                          initialdir=_default_output_dir())
        if not out_dir:
            return
        try:
            sheets = read_sheets(file)
            base = os.path.splitext(os.path.basename(file))[0]
            for name, (headers, rows) in sheets.items():
                out_path = os.path.join(out_dir, f"{base}_{name}.xlsx")
                write_table(out_path, headers, rows)
                self._log(f"  ✅ {name} → {os.path.basename(out_path)}（{len(rows)} 行）")
            self._log(f"✅ 拆分完成，共 {len(sheets)} 个工作表")
            self.status.set("拆分完成")
            messagebox.showinfo("成功", f"已拆分为 {len(sheets)} 个文件")
        except Exception as e:
            self._log(f"❌ 拆分失败：{e}")
            messagebox.showerror("错误", str(e))

    # ---------- 按列去重 ----------
    def drop_duplicates_gui(self):
        self._log("开始去重…")
        file = filedialog.askopenfilename(
            title="选择文件",
            filetypes=[("Excel/CSV", "*.xlsx *.xls *.csv")])
        if not file:
            return
        try:
            headers, rows = read_table(file)
        except Exception as e:
            messagebox.showerror("错误", f"读取失败：{e}")
            return
        if not headers:
            messagebox.showwarning("提示", "表格为空")
            return

        selected = self._multi_select_dialog(
            title="按列去重",
            prompt=f"共 {len(rows)} 行，{len(headers)} 列。\n勾选要去重的列（不勾=整行去重）：",
            options=[str(c) for c in headers])
        if selected is None:
            return

        try:
            if not selected:
                seen = set()
                new_rows = []
                for r in rows:
                    key = tuple(r)
                    if key not in seen:
                        seen.add(key)
                        new_rows.append(r)
                self._log(f"  整行去重：{len(rows)} → {len(new_rows)} 行")
            else:
                idxs = [headers.index(c) for c in selected]
                seen = set()
                new_rows = []
                for r in rows:
                    key = tuple(r[i] for i in idxs)
                    if key not in seen:
                        seen.add(key)
                        new_rows.append(r)
                self._log(f"  按 {selected} 去重：{len(rows)} → {len(new_rows)} 行")

            save_path = filedialog.asksaveasfilename(
                title="保存去重结果", defaultextension=".xlsx",
                initialfile=os.path.splitext(os.path.basename(file))[0] + "_去重.xlsx",
                initialdir=_default_output_dir(),
                filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")])
            if not save_path:
                return
            write_table(save_path, headers, new_rows)
            self._log(f"✅ 已保存：{save_path}")
            self.status.set("去重完成")
            messagebox.showinfo("成功", f"去重完成，剩余 {len(new_rows)} 行")
        except Exception as e:
            self._log(f"❌ 去重失败：{e}")
            messagebox.showerror("错误", str(e))

    # ---------- 批量重命名列 ----------
    def rename_columns_gui(self):
        self._log("开始批量重命名列…")
        file = filedialog.askopenfilename(
            title="选择文件",
            filetypes=[("Excel/CSV", "*.xlsx *.xls *.csv")])
        if not file:
            return
        try:
            headers, rows = read_table(file)
        except Exception as e:
            messagebox.showerror("错误", f"读取失败：{e}")
            return
        if not headers:
            messagebox.showwarning("提示", "表格为空")
            return

        mapping = self._rename_dialog([str(c) for c in headers])
        if not mapping:
            return

        try:
            new_headers = [mapping.get(str(h), h) for h in headers]
            save_path = filedialog.asksaveasfilename(
                title="保存结果", defaultextension=".xlsx",
                initialfile=os.path.splitext(os.path.basename(file))[0] + "_重命名.xlsx",
                initialdir=_default_output_dir(),
                filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")])
            if not save_path:
                return
            write_table(save_path, new_headers, rows)
            self._log(f"✅ 重命名 {len(mapping)} 列，已保存：{save_path}")
            self.status.set("重命名完成")
            messagebox.showinfo("成功", f"已重命名 {len(mapping)} 列")
        except Exception as e:
            self._log(f"❌ 重命名失败：{e}")
            messagebox.showerror("错误", str(e))

    # ================= 通用对话框 =================
    def _multi_select_dialog(self, title, prompt, options):
        dlg = tk.Toplevel(self.win)
        dlg.title(title)
        dlg.geometry("400x560")
        dlg.minsize(360, 420)
        dlg.transient(self.win)
        dlg.grab_set()

        tk.Label(dlg, text=prompt, font=("微软雅黑", 10),
                 wraplength=370, justify="left").pack(padx=10, pady=8, anchor="w")

        result = {"value": None}

        def on_ok():
            result["value"] = [opt for opt, v in vars_ if v.get()]
            _unbind_wheel()
            dlg.destroy()

        def on_cancel():
            result["value"] = None
            _unbind_wheel()
            dlg.destroy()

        btn = tk.Frame(dlg)
        btn.pack(side="bottom", fill="x", pady=8)
        tk.Button(btn, text="确定", width=10, command=on_ok).pack(side="right", padx=10)
        tk.Button(btn, text="取消", width=10, command=on_cancel).pack(side="right")

        ctrl = tk.Frame(dlg)
        ctrl.pack(fill="x", padx=10)

        wrap = tk.Frame(dlg)
        wrap.pack(fill="both", expand=True, padx=10, pady=6)

        canvas = tk.Canvas(wrap, highlightthickness=0)
        scrollbar = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", on_wheel)
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

        def _unbind_wheel():
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        vars_ = []
        for opt in options:
            v = tk.BooleanVar(value=False)
            tk.Checkbutton(inner, text=opt, variable=v,
                           font=("微软雅黑", 10), anchor="w").pack(fill="x")
            vars_.append((opt, v))

        tk.Button(ctrl, text="全选",
                  command=lambda: [v.set(True) for _, v in vars_]).pack(side="left")
        tk.Button(ctrl, text="全不选",
                  command=lambda: [v.set(False) for _, v in vars_]).pack(side="left", padx=6)

        dlg.wait_window()
        return result["value"]

    def _rename_dialog(self, old_cols):
        dlg = tk.Toplevel(self.win)
        dlg.title("批量重命名列")
        dlg.geometry("560x680")
        dlg.minsize(480, 520)
        dlg.transient(self.win)
        dlg.grab_set()

        search_frame = tk.Frame(dlg)
        search_frame.pack(fill="x", padx=10, pady=8)
        tk.Label(search_frame, text="🔍 搜索列名：",
                 font=("微软雅黑", 10)).pack(side="left")
        search_var = tk.StringVar()
        search_entry = tk.Entry(search_frame, textvariable=search_var,
                                font=("微软雅黑", 10))
        search_entry.pack(side="left", fill="x", expand=True, padx=6)

        bulk_frame = ttk.LabelFrame(dlg, text="批量填充（对当前搜索结果的列统一改名）",
                                    padding=6)
        bulk_frame.pack(fill="x", padx=10, pady=4)
        tk.Label(bulk_frame, text="统一改名：", font=("微软雅黑", 10)).pack(side="left")
        bulk_name = tk.StringVar()
        tk.Entry(bulk_frame, textvariable=bulk_name, width=14,
                 font=("微软雅黑", 10)).pack(side="left", padx=4)
        add_index = tk.BooleanVar(value=True)
        tk.Checkbutton(bulk_frame, text="加序号", variable=add_index,
                       font=("微软雅黑", 10)).pack(side="left", padx=6)

        result = {"value": {}}

        def on_ok():
            mapping = {}
            for col, (row, e, _) in rows_map.items():
                if not row.winfo_ismapped():
                    continue
                new = e.get().strip()
                if new and new != col:
                    mapping[col] = new
            result["value"] = mapping
            _unbind_wheel()
            dlg.destroy()

        def on_cancel():
            result["value"] = {}
            _unbind_wheel()
            dlg.destroy()

        btn = tk.Frame(dlg)
        btn.pack(side="bottom", fill="x", pady=8)
        tk.Button(btn, text="确定", width=10, command=on_ok).pack(side="right", padx=10)
        tk.Button(btn, text="取消", width=10, command=on_cancel).pack(side="right")

        wrap = tk.Frame(dlg)
        wrap.pack(fill="both", expand=True, padx=10, pady=6)
        canvas = tk.Canvas(wrap, highlightthickness=0)
        scrollbar = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", on_wheel)
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

        def _unbind_wheel():
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        rows_map = {}
        for col in old_cols:
            row = tk.Frame(inner)
            row.pack(fill="x", pady=2)
            lb = tk.Label(row, text=col, width=18, anchor="w",
                          font=("微软雅黑", 10), fg="#2c3e50")
            lb.pack(side="left")
            e = tk.Entry(row, font=("微软雅黑", 10))
            e.pack(side="left", fill="x", expand=True, padx=4)
            rows_map[col] = (row, e, lb)

        def bulk_fill():
            name = bulk_name.get().strip()
            if not name:
                messagebox.showwarning("提示", "请先填要统一改成的名字", parent=dlg)
                return
            idx = 1
            for col, (row, e, _) in rows_map.items():
                if row.winfo_ismapped():
                    e.delete(0, tk.END)
                    if add_index.get():
                        e.insert(0, f"{name}{idx}")
                        idx += 1
                    else:
                        e.insert(0, name)

        tk.Button(bulk_frame, text="应用", command=bulk_fill).pack(side="left", padx=6)

        def apply_filter(*_):
            kw = search_var.get().strip().lower()
            for col, (row, e, lb) in rows_map.items():
                if not kw or kw in str(col).lower():
                    row.pack(fill="x", pady=2)
                else:
                    row.pack_forget()
            inner.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))

        search_var.trace_add("write", apply_filter)
        search_entry.focus_set()

        dlg.wait_window()
        return result["value"]


# ================= 独立运行 =================
if __name__ == '__main__':
    logger.info("启动办公文档工具集")
    OfficeToolsWindow()
    tk.mainloop()