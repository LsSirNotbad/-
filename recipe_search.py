# -*- coding: utf-8 -*-
"""
菜谱查询工具
- 搜索菜谱 + 查看详细教程（简介 / 食材 / 步骤 / 图片）
- 日志：log/YYYYMMDD/recipe_search.log（打包后写在 exe 同级目录）
- 支持独立运行和被主程序调用（双模式）
"""
import os
import sys
import json
import logging
import threading
from datetime import datetime
from io import BytesIO

import requests
import tkinter as tk
from tkinter import ttk, messagebox

# Pillow 用于显示图片，属于可选依赖：未安装时仅跳过图片，其余功能正常
try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except Exception:
    Image = ImageTk = None
    _HAS_PIL = False


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
    """标准化日志：写入 log/YYYYMMDD/recipe_search.log（跨天自动切换新日期文件），同时输出到控制台"""
    global logger
    if "logger" in globals() and logger is not None:
        return logger
    logger = logging.getLogger("recipe_search")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        fh = _DailyFileHandler("recipe_search")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        logger.propagate = False
        logger.info(f"日志文件路径: {os.path.join(_base_dir(), 'log', datetime.now().strftime('%Y%m%d'), 'recipe_search.log')}")
    return logger


logger = _setup_logger()


# ================= 1. 克制配色（清爽浅色卡片风格） =================
COLOR_PRIMARY = "#3B82F6"       # 主色（蓝，用于强调文字和按钮）
COLOR_PRIMARY_DARK = "#2563EB"  # 主色加深（按钮悬停）
COLOR_BG = "#EEF3FB"            # 页面背景（浅蓝白）
COLOR_CARD = "#FFFFFF"          # 卡片底色（纯白）
COLOR_TEXT = "#1E293B"          # 正文文字
COLOR_SUB = "#64748B"           # 次要文字（说明/提示）
COLOR_BORDER = "#DBE6F5"        # 边框（浅灰蓝）
COLOR_ROW_ALT = "#F7FAFF"       # 列表隔行底色


def _bind_hover(widget, normal, hover):
    """给按钮绑定鼠标悬停变色效果"""
    widget.bind("<Enter>", lambda e: widget.config(bg=hover))
    widget.bind("<Leave>", lambda e: widget.config(bg=normal))


class RecipeApp:
    def __init__(self, parent=None):
        # 双模式：parent=None 独立运行（Tk + mainloop）；否则作为 Toplevel 嵌入主程序
        self._standalone = parent is None
        self.root = tk.Tk() if self._standalone else tk.Toplevel(parent)
        self.root.title("菜谱查询工具")
        # 固定窗口大小
        self.root.geometry("920x720")
        self.root.resizable(False, False)
        self.root.config(bg=COLOR_BG)

        # 防止图片被回收
        self.image_refs = {}
        self.recipe_map = {}

        # 全局统一字体与控件样式
        self._setup_style()

        # 布局先行：先划分大区域
        self._build_top_bar()
        self._build_main_area()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        logger.info("========== 程序启动 ==========")
        if self._standalone:
            self.root.mainloop()

    def _on_close(self):
        logger.info("========== 程序退出 ==========")
        self.root.destroy()

    def _setup_style(self):
        """列表样式设置（使用独立样式名 Recipe.*，避免影响其它窗口的 ttk 外观）"""
        self.style = ttk.Style()
        # 注意：不要调用 theme_use("clam")，也不要配置全局 "Treeview" 样式，
        # 否则会污染整个程序其它窗口的 ttk 外观（导致加密/office/酷狗/修改文件时间等窗口变灰）。
        # 这里使用独立样式名 "Recipe.Treeview"，只作用于本窗口的列表。

        # 统一列表样式：加高行距、去除边框、选中态用主色
        self.style.configure("Recipe.Treeview",
                             font=("微软雅黑", 10),
                             rowheight=32,
                             background=COLOR_CARD,
                             fieldbackground=COLOR_CARD,
                             foreground=COLOR_TEXT,
                             borderwidth=0)
        self.style.map("Recipe.Treeview",
                       background=[("selected", COLOR_PRIMARY)],
                       foreground=[("selected", "white")])
        self.style.configure("Recipe.Treeview.Heading",
                             font=("微软雅黑", 10, "bold"),
                             background=COLOR_BG,
                             foreground=COLOR_PRIMARY,
                             relief="flat")
        self.style.map("Recipe.Treeview.Heading", background=[("active", COLOR_BORDER)])

    def _build_top_bar(self):
        """顶部操作区：白色卡片风格"""
        top_card = tk.Frame(self.root, bg=COLOR_CARD, highlightthickness=1,
                            highlightbackground=COLOR_BORDER, highlightcolor=COLOR_BORDER)
        top_card.pack(fill=tk.X, padx=15, pady=(15, 0))

        top_frame = tk.Frame(top_card, bg=COLOR_CARD, padx=18, pady=14)
        top_frame.pack(fill=tk.X)

        tk.Label(top_frame, text="🍳 菜谱查询", font=("微软雅黑", 15, "bold"),
                 bg=COLOR_CARD, fg=COLOR_TEXT).pack(side=tk.LEFT, padx=(0, 18))

        self.keyword_var = tk.StringVar()
        # 输入框：浅底 + 聚焦变蓝边
        self.keyword_input = tk.Entry(top_frame, textvariable=self.keyword_var,
                                      font=("微软雅黑", 11), width=28, bd=0,
                                      bg="#F8FAFC", fg=COLOR_TEXT, insertbackground=COLOR_TEXT,
                                      highlightthickness=1, highlightbackground=COLOR_BORDER,
                                      highlightcolor=COLOR_PRIMARY)
        self.keyword_input.pack(side=tk.LEFT, padx=5, ipady=6)
        self.keyword_input.bind("<Return>", lambda e: self.search_recipe())

        # 搜索按钮：主色 + 悬停加深
        self.btn_search = tk.Button(top_frame, text="搜 索", font=("微软雅黑", 10, "bold"),
                                    bg=COLOR_PRIMARY, fg="white", activebackground=COLOR_PRIMARY_DARK,
                                    activeforeground="white", bd=0, padx=22, pady=6,
                                    cursor="hand2", relief="flat", command=self.search_recipe)
        self.btn_search.pack(side=tk.LEFT, padx=10)
        _bind_hover(self.btn_search, COLOR_PRIMARY, COLOR_PRIMARY_DARK)

        tk.Label(top_frame, text="输入菜名或食材后回车 / 点击搜索", font=("微软雅黑", 9),
                 bg=COLOR_CARD, fg=COLOR_SUB).pack(side=tk.LEFT, padx=10)

    def _build_main_area(self):
        """主内容区：左右分栏"""
        main_content = tk.Frame(self.root, bg=COLOR_BG)
        main_content.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        # 使用 PanedWindow 左右分栏（分隔条更细更柔和）
        self.paned = tk.PanedWindow(main_content, orient=tk.HORIZONTAL,
                                    bg=COLOR_BG, sashwidth=8, sashrelief="flat",
                                    bd=0, showhandle=False)
        self.paned.pack(fill=tk.BOTH, expand=True)

        # ================= 左侧：搜索结果 =================
        left_panel = tk.Frame(self.paned, bg=COLOR_CARD, highlightthickness=1,
                              highlightbackground=COLOR_BORDER)
        self.paned.add(left_panel, minsize=260, width=280)

        tk.Label(left_panel, text="📋 搜索结果", font=("微软雅黑", 11, "bold"),
                 bg=COLOR_CARD, fg=COLOR_PRIMARY).pack(anchor="w", padx=14, pady=(12, 6))
        tk.Frame(left_panel, height=1, bg=COLOR_BORDER).pack(fill=tk.X, padx=10)

        tree_wrap = tk.Frame(left_panel, bg=COLOR_CARD)
        tree_wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(tree_wrap, columns=("title",), show="headings",
                                 selectmode="browse", style="Recipe.Treeview")
        self.tree.heading("title", text="菜谱名称")
        self.tree.column("title", anchor="w", width=220)
        self.tree.tag_configure("odd", background=COLOR_ROW_ALT)  # 隔行底色
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_recipe)

        # ================= 右侧：详情展示 =================
        right_panel = tk.Frame(self.paned, bg=COLOR_BG)
        self.paned.add(right_panel, minsize=600)

        # 滚动区容器
        self.detail_canvas = tk.Canvas(right_panel, bg=COLOR_BG, highlightthickness=0)
        self.detail_scrollbar = tk.Scrollbar(right_panel, orient="vertical", command=self.detail_canvas.yview)

        self.detail_frame = tk.Frame(self.detail_canvas, bg=COLOR_BG)
        self.detail_frame.bind("<Configure>",
                               lambda e: self.detail_canvas.configure(scrollregion=self.detail_canvas.bbox("all")))
        self.detail_canvas.create_window((0, 0), window=self.detail_frame, anchor="nw", width=580)
        self.detail_canvas.configure(yscrollcommand=self.detail_scrollbar.set)

        self.detail_canvas.pack(side="left", fill="both", expand=True)
        self.detail_scrollbar.pack(side="right", fill="y")

        # 鼠标滚轮绑定到窗口级别：事件会从详情区的任意子控件冒泡到顶层窗口，
        # 因此在标题/文字/图片等子控件上滚动都能生效；处理器内再校验鼠标是否在详情区内，
        # 从而不会影响左侧列表，也不会在嵌入主程序时抢占全局滚轮。
        self.root.bind("<MouseWheel>", self._on_mousewheel)

        # 欢迎提示
        tk.Label(self.detail_frame, text="👈 请在左侧点击菜谱查看详情",
                 font=("微软雅黑", 12), bg=COLOR_BG, fg=COLOR_SUB).pack(pady=150)

    def _on_mousewheel(self, event):
        # 仅当鼠标位于详情区内时才滚动，避免影响左侧列表
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = None
        while w is not None:
            if w is self.detail_canvas:
                self.detail_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return
            try:
                w = w.master
            except Exception:
                w = None

    def _section_card(self, title):
        """生成统一风格的详情区块卡片，返回可放内容的 body 容器"""
        card = tk.Frame(self.detail_frame, bg=COLOR_CARD, highlightthickness=1,
                        highlightbackground=COLOR_BORDER)
        card.pack(fill=tk.X, pady=(0, 12), padx=5)

        tk.Label(card, text=title, font=("微软雅黑", 11, "bold"),
                 bg=COLOR_CARD, fg=COLOR_PRIMARY).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Frame(card, height=1, bg=COLOR_BORDER).pack(fill=tk.X, padx=12, pady=(8, 0))

        body = tk.Frame(card, bg=COLOR_CARD)
        body.pack(fill=tk.X, padx=16, pady=12)
        return body

    # ================= 业务逻辑 =================
    def search_recipe(self):
        keyword = self.keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("提示", "请输入菜谱名称或食材！")
            return

        logger.info(f"开始搜索菜谱：{keyword}")
        self.btn_search.config(state="disabled", text="查询中...")
        for item in self.tree.get_children():
            self.tree.delete(item)

        threading.Thread(target=self._do_search, args=(keyword,), daemon=True).start()

    def _do_search(self, keyword):
        api_url = "https://uapis.cn/api/v1/food/recipe"
        params = {"keyword": keyword}
        try:
            logger.info(f"请求接口: {api_url} 参数: {params}")
            r = requests.get(api_url, params=params, timeout=10)
            logger.info(f"接口状态码: {r.status_code}")
            if r.status_code == 404:
                logger.info("无匹配结果")
                self.root.after(0, lambda: messagebox.showinfo("无结果", "没有找到匹配的菜谱，请换个关键词。"))
                return
            if r.status_code != 200:
                logger.error(f"查询失败：状态码 {r.status_code}，返回内容: {r.text[:500]}")
                self.root.after(0, lambda: messagebox.showerror("接口错误", f"状态码：{r.status_code}"))
                return

            try:
                data = r.json()
            except ValueError:
                logger.error(f"接口返回非 JSON：{r.text[:500]}")
                self.root.after(0, lambda: messagebox.showerror("接口错误", "接口返回格式异常，详情见日志文件"))
                return

            logger.info(f"接口返回: {json.dumps(data, ensure_ascii=False)}")
            items = data.get("items", [])
            self.root.after(0, lambda: self._update_list(items))
        except requests.exceptions.RequestException as e:
            logger.error(f"网络异常：{e}")
            self.root.after(0, lambda: messagebox.showerror("网络异常", "请求超时或网络断开。"))
        finally:
            self.root.after(0, lambda: self.btn_search.config(state="normal", text="搜 索"))

    def _update_list(self, items):
        if not items:
            logger.info("搜索结果为空")
            messagebox.showinfo("提示", "未找到相关菜谱。")
            return
        self.recipe_map = {}
        for i, item in enumerate(items):
            title = item.get("title", "未知菜谱")
            self.recipe_map[title] = item.get("id", "")
            tag = "odd" if i % 2 else ""
            self.tree.insert("", tk.END, values=(title,), tags=(tag,) if tag else ())

    def on_select_recipe(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        title = self.tree.item(selection[0])["values"][0]
        recipe_id = self.recipe_map.get(title)
        if recipe_id:
            logger.info(f"选中菜谱：{title}（id={recipe_id}）")
            self._show_loading()
            threading.Thread(target=self._fetch_detail, args=(recipe_id,), daemon=True).start()

    def _show_loading(self):
        for w in self.detail_frame.winfo_children():
            w.destroy()
        tk.Label(self.detail_frame, text="⏳ 正在加载详细教程...",
                 font=("微软雅黑", 12), bg=COLOR_BG, fg=COLOR_SUB).pack(pady=100)

    def _fetch_detail(self, recipe_id):
        api_url = "https://uapis.cn/api/v1/food/recipe/detail"
        try:
            logger.info(f"请求接口: {api_url} 参数: {{\"id\": \"{recipe_id}\"}}")
            r = requests.get(api_url, params={"id": recipe_id}, timeout=(3, 15))

            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    logger.error(f"返回非 JSON：{r.text[:500]}")
                    self.root.after(0, lambda: messagebox.showerror("数据异常", "接口返回格式错误"))
                    return
                logger.info(f"接口返回: {json.dumps(data, ensure_ascii=False)}")
                self.root.after(0, lambda d=data: self._render_detail(d))
            else:
                code = r.status_code
                body = r.text[:500]
                logger.error(f"详情查询失败：状态码 {code}，返回内容: {body}")
                self.root.after(0, lambda c=code: messagebox.showerror("接口错误", f"状态码：{c}"))

        except Exception as e:
            logger.error(f"详情查询异常：{e}")
            err_msg = str(e)
            self.root.after(0, lambda msg=err_msg: messagebox.showerror("网络异常", msg))

    def _render_detail(self, data):
        """按区块划分展示：简介 / 食材 / 步骤"""
        for w in self.detail_frame.winfo_children():
            w.destroy()
        self.image_refs.clear()

        # 区块 1：菜谱简介
        body = self._section_card("📖 菜谱简介")
        tk.Label(body, text=data.get("title", "未知菜谱"), font=("微软雅黑", 16, "bold"),
                 bg=COLOR_CARD, fg=COLOR_TEXT, wraplength=500, justify="left").pack(anchor="w")

        info_text = f"分类：{data.get('category', '--')}    |    评分：{data.get('rating', '--')} 分"
        tk.Label(body, text=info_text, font=("微软雅黑", 10), bg=COLOR_CARD, fg=COLOR_SUB).pack(anchor="w", pady=(6, 0))

        # 封面图
        if _HAS_PIL and data.get("cover"):
            try:
                img_resp = requests.get(data["cover"], timeout=5)
                img = Image.open(BytesIO(img_resp.content))
                img.thumbnail((500, 400))
                photo = ImageTk.PhotoImage(img)
                self.image_refs["cover"] = photo
                tk.Label(body, image=photo, bg=COLOR_CARD).pack(pady=(12, 0))
            except Exception as e:
                logger.warning(f"封面图加载失败：{e}")

        # 区块 2：准备食材
        body = self._section_card("🥗 准备食材")
        for ing in data.get("ingredients", ["暂无食材信息"]):
            tk.Label(body, text=f"• {ing}", font=("微软雅黑", 10), bg=COLOR_CARD, fg=COLOR_TEXT).pack(anchor="w", pady=2)

        # 区块 3：制作步骤
        body = self._section_card("👩‍🍳 制作步骤")
        for step in data.get("steps", []):
            step_num = step.get("step", "")
            step_box = tk.Frame(body, bg="#F8FAFC", highlightthickness=1, highlightbackground=COLOR_BORDER)
            step_box.pack(fill=tk.X, pady=6)

            inner = tk.Frame(step_box, bg="#F8FAFC")
            inner.pack(fill=tk.X, padx=12, pady=10)

            tk.Label(inner, text=f"步骤 {step_num}", font=("微软雅黑", 10, "bold"),
                     bg="#F8FAFC", fg=COLOR_PRIMARY).pack(anchor="w")
            tk.Label(inner, text=step.get("text", ""), font=("微软雅黑", 10),
                     bg="#F8FAFC", fg=COLOR_TEXT, wraplength=460, justify="left").pack(anchor="w", pady=(5, 0))

            if _HAS_PIL and step.get("image"):
                try:
                    img_resp = requests.get(step["image"], timeout=5)
                    img = Image.open(BytesIO(img_resp.content))
                    img.thumbnail((450, 300))
                    photo = ImageTk.PhotoImage(img)
                    self.image_refs[f"step_{step_num}"] = photo
                    tk.Label(inner, image=photo, bg="#F8FAFC").pack(anchor="w", pady=(8, 0))
                except Exception as e:
                    logger.warning(f"步骤图加载失败：{e}")

        self.detail_canvas.yview_moveto(0)


if __name__ == "__main__":
    RecipeApp()
