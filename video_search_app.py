import os
import re
import sys
import json
import queue
import logging
import threading
import webbrowser
from datetime import datetime
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import messagebox, font, scrolledtext

import requests
from bs4 import BeautifulSoup


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
    log = logging.getLogger("video_search_app")
    if log.handlers:
        return log

    log.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S")

    try:
        # 日志根目录：打包后取 exe 所在目录，开发时取脚本目录
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        log_root = os.path.join(base_dir, "log")
        # 当前日期文件夹 YYYYMMDD
        date_folder = datetime.now().strftime("%Y%m%d")
        log_dir = os.path.join(log_root, date_folder)
        # 无文件夹自动创建，存在直接复用
        os.makedirs(log_dir, exist_ok=True)
        log_file_path = os.path.join(log_dir, "video_search_app.log")

        file_handler = _DailyFileHandler("video_search_app")  # 按天自动滚动，跨天写入新日期目录
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)
    except Exception as e:
        # 把异常打印到控制台，方便排查！原来的 pass 吞掉错误
        print(f"日志文件初始化失败: {e}")

    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        log.addHandler(stream_handler)

    # 独立维护日志，避免与主程序的日志配置相互干扰
    log.propagate = False
    return log

logger = _setup_logger()

# 测试一行日志，看能否生成文件
logger.info("日志初始化测试")

def create_video_search_app(parent=None):
    """
    创建并配置视频搜索应用

    - 单文件独立运行：不传参（parent=None），自动创建主窗口；
    - 被主程序调用：传入主窗口对象，工具以 Toplevel 子窗口形式弹出，避免与主窗口冲突。

    :param parent: 主程序窗口对象，独立运行时为 None
    :return: 应用的主函数（独立运行时调用它进入主循环）
    """
    # 应用状态
    app_running = True
    after_ids = []  # 存储所有after调度的ID

    # 判断运行方式：存在可用根窗口说明是被主程序调用，否则为单文件独立运行
    master = parent if parent is not None else getattr(tk, "_default_root", None)
    standalone = master is None

    # 创建主窗口
    if standalone:
        root = tk.Tk()
    else:
        root = tk.Toplevel(master)
        root.transient(master)
    root.title("多平台影视搜索与播放")
    root.geometry("950x750")
    root.resizable(True, True)

    # 平台配置
    platforms = {
        "腾讯视频": {
            "search_url": "https://pbaccess.video.qq.com/trpc.videosearch.mobile_search.MultiTerminalSearch/MbSearch",
            "headers": {
                'accept': 'application/json',
                'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                'content-type': 'application/json',
                'origin': 'https://v.qq.com',
                'referer': 'https://v.qq.com/',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0',
            },
            "params": {
                'vplatform': '2',
            },
            "json_data": {
                'version': '25101301',
                'clientType': 1,
                'filterValue': '',
                'retry': 0,
                'pagenum': 0,
                'isPrefetch': True,
                'pagesize': 30,
                'queryFrom': 0,
                'searchDatakey': '',
                'transInfo': '',
                'isneedQc': True,
                'preQid': '',
                'adClientInfo': '',
                'extraInfo': {
                    'isNewMarkLabel': '1',
                    'multi_terminal_pc': '1',
                    'themeType': '1',
                    'sugRelatedIds': '{}',
                    'appVersion': '',
                },
            },
            "parse_url": "https://jx.xmflv.com/?url={}",
            "url_pattern": r'https?://v\.qq\.com/.*'
        },

        "爱奇艺": {
            "search_url": "https://mesh.if.iqiyi.com/portal/lw/search/homePageV3",
            "headers": {
                'accept': '*/*',
                'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                'origin': 'https://www.iqiyi.com',
                'referer': 'https://www.iqiyi.com/',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0',
            },
            "params": lambda name: {
                'key': name,
                'current_page': '1',
                'mode': '1',
                'source': 'input',
                'version': '13.052.21953',
                'pageNum': '1',
                'pageSize': '25',
            },
            "parse_url": "https://jx.xmflv.com/?url={}",
            "url_pattern": r'https?://www\.iqiyi\.com/.*'
        },
        "优酷": {
            "search_url": "https://so.youku.com/search/q_{}",
            "headers": {
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                'cache-control': 'max-age=0',
                'priority': 'u=0, i',
                'referer': 'https://www.youku.com/ku/webtv',
                'sec-ch-ua': '"Chromium";v="136", "Microsoft Edge";v="136", "Not.A/Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'document',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'same-site',
                'sec-fetch-user': '?1',
                'upgrade-insecure-requests': '1',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0',
            },
            "params": lambda name: {},
            "parse_url": "https://jx.xmflv.com/?url={}",
            "url_pattern": r'https?://v\.youku\.com/.*'
        },
        "哔哩哔哩": {
            "search_url": "https://search.bilibili.com/all",
            "headers": {
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                'priority': 'u=0, i',
                'sec-ch-ua': '"Chromium";v="142", "Microsoft Edge";v="142", "Not_A Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'document',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'none',
                'sec-fetch-user': '?1',
                'upgrade-insecure-requests': '1',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0',
            },
            "cookies": {
                'DedeUserID': '1658148678',
                'DedeUserID__ckMd5': '47bea224b840392c',
                '_uuid': 'B7B931072-9465-6843-2DB7-BC171544F531067809infoc',
                'buvid_fp': 'b6189a28749737c09b173d0a8c57e5b0',
                'header_theme_version': 'OPEN',
                'enable_web_push': 'DISABLE',
                'home_feed_column': '5',
                'theme-tip-show': 'SHOWED',
                'browser_resolution': '1699-941',
                'theme-avatar-tip-show': 'SHOWED',
                'buvid3': '0CE64D9B-B372-AAE9-06ED-AB14D1451DE567184infoc',
                'b_nut': '1756179467',
                'rpdid': "|(k))JJlumR)0J'u~lYu~mR~J",
                'buvid4': 'BC4820D7-5B65-1704-40A0-2C9E1F9D17DE61235-023052920-tIPa9o7+yx8DhnFqiox4Nw%3D%3D',
                'theme-switch-show': 'SHOWED',
                'CURRENT_QUALITY': '80',
                'CURRENT_FNVAL': '4048',
                'b_lsid': '737272A9_19B01184235',
                'bili_ticket': 'eyJhbGciOiJIUzI1NiIsImtpZCI6InMwMyIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3NjU1MDkxMzQsImlhdCI6MTc2NTI0OTg3NCwicGx0IjotMX0.kI6VmIVbRcbklbxV3KLo6Sin3GyN1kld9UQ9QhIsIC0',
                'bili_ticket_expires': '1765509074',
                'SESSDATA': 'b7ac60e4%2C1780801936%2Cbd950%2Ac2CjAc6tJ50ow-n6XPx7XDJmctA8HNjAsIJ1bQHQWJkzCaa3Xu9vbZF_q82beBZAuR2W4SVkVwdmJJQUhHQlg3bmZfUndzaHhkZ0pqUmR2Y0g5ZmctdFMyNmdWbGxXM052eWZBQmZ3bjY2OHd1TEhPT0lpa0RJUmN3bDZjX3hlb0l1UnpscmZQMnZBIIEC',
                'bili_jct': 'db6074e12dafca4ce0aa475d0274d917',
                'ogv_device_support_hdr': '0',
                'sid': '7iq9wz5x',
                'bmg_af_switch': '1',
                'bmg_src_def_domain': 'i0.hdslb.com',
            },
            "params": lambda name: {'keyword': name},
            "parse_url": "https://jx.xmflv.com/?url={}",
            "url_pattern": r'https?://www\.bilibili\.com/.*'  # B站URL正则匹配
        }
    }

    # 解析地址列表
    parse_sites = {
        "默认": {
            "url": "https://jx.mmkv.cn/tv.php?url={}",
            "priority": 1,
            "description": "通用解析线路，速度较快，兼容性好"
        },
        "推荐1": {
            "url": "https://www.yemu.xyz/?url={}",
            "priority": 2,
            "description": "高清解析线路，支持多种格式，广告较少"
        },
        "推荐2": {
            "url": "https://jx.xmflv.cc/?url={}",
            "priority": 3,
            "description": "备用高清解析，速度稳定，适合大多数视频"
        },
        "推荐3": {
            "url": "https://jx.77flv.cc/?url={}",
            "priority": 4,
            "description": "快速解析线路，适合手机端播放"
        },
        "推荐4": {
            "url": "https://jx.aidouer.net/?url={}",
            "priority": 5,
            "description": "多格式支持，解析成功率较高"
        },
        "线路5": {
            "url": "https://jx.xmflv.cc/?url={}",
            "priority": 6,
            "description": "高清解析备用线路"
        },
        "线路6": {
            "url": "https://yparse.ik9.cc/index.php?url={}",
            "priority": 7,
            "description": "解析速度快，界面简洁"
        },
        "线路7": {
            "url": "https://am1907.top/?jx={}",
            "priority": 8,
            "description": "适合解析加密视频，兼容性强"
        },
        "线路8": {
            "url": "https://yemu.xyz/?url={}",
            "priority": 9,
            "description": "高清稳定，适合大屏幕播放"
        }
    }

    # 网络请求队列和结果队列
    request_queue = queue.Queue()
    result_queue = queue.Queue()

    # 创建变量
    platform_combobox = None
    parse_combobox = None
    search_entry = None
    url_entry = None
    info_text = None
    videos_tree = None
    status_var = tk.StringVar()

    # 说明：不再修改全局 TkDefaultFont、也不调用 option_add("*Font")，
    # 这些设置是整个程序共享的，会造成字体泄漏、影响其它窗口。
    # 改为使用本窗口专属的 ttk 样式名（统一加 "Video." 前缀）。

    def setup_styles():
        """设置本窗口专属 UI 样式（样式名带 Video. 前缀，避免全局字体泄漏）"""
        try:
            style = ttk.Style()
            f10 = ("SimHei", 10)
            style.configure("Video.TLabel", font=f10)
            style.configure("Video.TButton", font=f10)
            style.configure("Video.TCombobox", font=f10)
            style.configure("Video.TEntry", font=f10)
            style.configure("Video.TLabelframe", font=f10)
            style.configure("Video.TLabelframe.Label", font=f10)
            style.configure("Video.Treeview", font=f10)
            style.configure("Video.Treeview.Heading", font=("SimHei", 10, "bold"))
            logger.debug("UI样式配置完成")
        except Exception as e:
            logger.error(f"UI样式配置失败: {str(e)}", exc_info=True)

    def create_widgets():
        """创建UI组件"""
        nonlocal search_entry, url_entry, info_text, videos_tree, platform_combobox, parse_combobox
        try:
            # 顶部平台和搜索区域
            top_frame = ttk.Frame(root, padding="10")
            top_frame.pack(fill=tk.X)

            ttk.Label(top_frame, text="选择平台:", style="Video.TLabel").pack(side=tk.LEFT, padx=(0, 10))

            platform_combobox = ttk.Combobox(
                top_frame,
                values=list(platforms.keys()),
                state="readonly", width=10,
                style="Video.TCombobox"
            )
            platform_combobox.pack(side=tk.LEFT, padx=(0, 10))
            platform_combobox.current(0)
            platform_combobox.bind("<<ComboboxSelected>>", lambda event: update_status())

            ttk.Label(top_frame, text="选择解析源:", style="Video.TLabel").pack(side=tk.LEFT, padx=(0, 10))

            parse_combobox = ttk.Combobox(
                top_frame,
                values=list(parse_sites.keys()),
                state="readonly", width=10,
                style="Video.TCombobox"
            )
            parse_combobox.pack(side=tk.LEFT, padx=(0, 10))
            parse_combobox.current(0)
            parse_combobox.bind("<<ComboboxSelected>>", lambda event: update_status())

            ttk.Label(top_frame, text="输入影视名称:", style="Video.TLabel").pack(side=tk.LEFT, padx=(0, 10))

            search_entry = ttk.Entry(top_frame, width=30, style="Video.TEntry")
            search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
            search_entry.bind("<Return>", on_search)

            search_button = ttk.Button(top_frame, text="搜索", command=on_search, style="Video.TButton")
            search_button.pack(side=tk.LEFT)

            copy_button = ttk.Button(top_frame, text="复制视频URL", command=copy_selected_url, style="Video.TButton")
            copy_button.pack(side=tk.LEFT, padx=(10, 0))

            # URL解析区域
            url_frame = ttk.LabelFrame(root, text="URL解析", padding="10", style="Video.TLabelframe")
            url_frame.pack(fill=tk.X, pady=(0, 10))

            ttk.Label(url_frame, text="粘贴视频URL:", style="Video.TLabel").pack(side=tk.LEFT, padx=(0, 10))

            url_entry = ttk.Entry(url_frame, width=50, style="Video.TEntry")
            url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
            url_entry.insert(0, "请复制你想要看的视频网址，粘贴到此处点击播放即可!")
            url_entry.configure(foreground='gray')
            url_entry.bind("<FocusIn>", _on_url_entry_focus_in)
            url_entry.bind("<FocusOut>", _on_url_entry_focus_out)

            parse_button = ttk.Button(url_frame, text="播放", command=parse_url, style="Video.TButton")
            parse_button.pack(side=tk.LEFT, padx=(10, 0))

            # 结果显示区域
            result_frame = ttk.Frame(root, padding="10")
            result_frame.pack(fill=tk.BOTH, expand=True)

            # 剧集信息区域
            info_frame = ttk.LabelFrame(result_frame, text="剧集信息", padding="10", style="Video.TLabelframe")
            info_frame.pack(fill=tk.X, pady=(0, 10))

            info_text = scrolledtext.ScrolledText(info_frame, height=8, wrap=tk.WORD, font=("SimHei", 10))
            info_text.pack(fill=tk.BOTH, expand=True)
            info_text.config(state=tk.DISABLED)

            # 视频列表区域
            videos_frame = ttk.LabelFrame(result_frame, text="视频列表 (双击播放)", padding="10", style="Video.TLabelframe")
            videos_frame.pack(fill=tk.BOTH, expand=True)

            columns = ("标题", "副标题", "视频URL")
            videos_tree = ttk.Treeview(videos_frame, columns=columns, show="headings", style="Video.Treeview")

            for col in columns:
                videos_tree.heading(col, text=col)
                if col == "视频URL":
                    videos_tree.column(col, width=400, anchor=tk.W)
                else:
                    videos_tree.column(col, width=250, anchor=tk.W)

            scrollbar_y = ttk.Scrollbar(videos_frame, orient=tk.VERTICAL, command=videos_tree.yview)
            scrollbar_x = ttk.Scrollbar(videos_frame, orient=tk.HORIZONTAL, command=videos_tree.xview)
            videos_tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)

            scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
            scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
            videos_tree.pack(fill=tk.BOTH, expand=True)

            videos_tree.bind("<Double-1>", on_video_select)

            # 状态栏
            status_var.set("请选择平台和解析地址，然后输入影视名称进行搜索")
            status_bar = ttk.Label(root, textvariable=status_var, relief=tk.SUNKEN, anchor=tk.W, style="Video.TLabel")
            status_bar.pack(side=tk.BOTTOM, fill=tk.X)

            logger.debug("UI组件创建完成")
        except Exception as e:
            logger.error(f"UI组件创建失败: {str(e)}", exc_info=True)
            messagebox.showerror("错误", f"UI初始化失败: {str(e)}")

    def update_status():
        """更新状态栏显示当前选择的平台和解析源"""
        try:
            platform = platform_combobox.get()
            parse_site = parse_combobox.get()
            status_var.set(f"当前平台: {platform} | 当前解析源: {parse_site}")
            logger.debug(f"状态更新：平台={platform}，解析源={parse_site}")
        except Exception as e:
            logger.error(f"状态更新失败: {str(e)}", exc_info=True)

    def schedule_check_results():
        """安全地调度结果检查"""
        if app_running:
            after_id = root.after(100, _check_results)
            after_ids.append(after_id)

    def _on_url_entry_focus_in(event):
        """当URL输入框获得焦点时，如果是占位文本则清空"""
        try:
            if url_entry.get() == "请复制你想要看的视频网址，粘贴到此处点击播放即可!":
                url_entry.delete(0, tk.END)
                url_entry.configure(foreground='black')
            logger.debug("URL输入框获得焦点")
        except Exception as e:
            logger.error(f"URL输入框焦点事件处理失败: {str(e)}", exc_info=True)

    def _on_url_entry_focus_out(event):
        """当URL输入框失去焦点时，如果为空则显示占位文本"""
        try:
            if not url_entry.get().strip():
                url_entry.insert(0, "请复制你想要看的视频网址，粘贴到此处点击播放即可!")
                url_entry.configure(foreground='gray')
            logger.debug("URL输入框失去焦点")
        except Exception as e:
            logger.error(f"URL输入框失焦事件处理失败: {str(e)}", exc_info=True)

    def _check_results():
        """检查结果队列并更新UI"""
        if not app_running:
            return

        while not result_queue.empty():
            try:
                callback, result, error = result_queue.get()
                if callback:
                    if error:
                        callback(None, error)
                    else:
                        callback(result, None)
            except Exception as e:
                logger.error(f"结果处理失败: {str(e)}", exc_info=True)

        # 继续调度下一次检查
        schedule_check_results()

    def on_search(event=None):
        """处理搜索请求"""
        try:
            platform = platform_combobox.get()
            name = search_entry.get().strip()

            if not name:
                messagebox.showwarning("警告", "请输入要搜索的影视名称")
                logger.warning("搜索失败：未输入影视名称")
                return

            logger.info(f"开始搜索：平台={platform}，关键词={name}")
            status_var.set(f"正在{platform}搜索: {name}...")
            root.update()

            # 清空当前结果
            info_text.config(state=tk.NORMAL)
            info_text.delete(1.0, tk.END)
            for item in videos_tree.get_children():
                videos_tree.delete(item)
            info_text.config(state=tk.DISABLED)

            # 将搜索任务加入队列
            request_queue.put((
                perform_search,
                (platform, name),
                {},
                _search_callback
            ))
        except Exception as e:
            logger.error(f"搜索请求处理失败: {str(e)}", exc_info=True)
            messagebox.showerror("错误", f"搜索初始化失败: {str(e)}")

    def _search_callback(result, error):
        """处理搜索结果的回调函数"""
        if not app_running:
            return

        try:
            if error:
                msg = f"搜索出错: {str(error)}"
                status_var.set(msg)
                messagebox.showerror("错误", f"搜索过程中发生错误:\n{str(error)}")
                logger.error(f"搜索回调错误: {str(error)}", exc_info=True)
                return

            if not result:
                status_var.set("未找到搜索结果")
                info_text.config(state=tk.NORMAL)
                info_text.insert(tk.END, "未找到相关内容")
                info_text.config(state=tk.DISABLED)
                logger.info("搜索回调：未找到结果")
                return

            current_platform = platform_combobox.get()
            video_count = len(result.get('videos', []))
            status_var.set(f"{current_platform}搜索完成，共找到{video_count}个视频")
            logger.info(f"搜索回调：平台={current_platform}，找到{video_count}个视频")

            # 更新剧集信息
            info_text.config(state=tk.NORMAL)
            info_text.delete(1.0, tk.END)
            info_text.insert(tk.END, result.get('info', '无剧集信息'))
            info_text.config(state=tk.DISABLED)

            # 更新视频列表
            for item in result.get('videos', []):
                title = item.get('title', '未知标题')
                subtitle = item.get('subtitle', '')
                url = item.get('url', '')
                videos_tree.insert('', tk.END, values=(title, subtitle, url))
                logger.debug(f"添加视频列表项：标题={title}，URL={url}")
        except Exception as e:
            logger.error(f"搜索结果回调处理失败: {str(e)}", exc_info=True)
            messagebox.showerror("错误", f"结果显示失败: {str(e)}")

    def perform_search(platform, name):
        """执行搜索并返回结果数据"""
        logger.debug(f"执行搜索：平台={platform}，关键词={name}")
        platform_config = platforms.get(platform)
        if not platform_config:
            logger.error(f"不支持的平台: {platform}")
            raise ValueError(f"不支持的平台: {platform}")

        try:
            if platform == "爱奇艺":
                return search_iqiyi(platform_config, name)
            elif platform == "腾讯视频":
                return search_tencent_video(platform_config, name)
            elif platform == "优酷":
                return search_youku(platform_config, name)
            elif platform == "哔哩哔哩":  # 新增B站分支
                return search_bilibili(platform_config, name)
            else:
                return {
                    'info': f"不支持的平台: {platform}",
                    'videos': []
                }
        except Exception as e:
            logger.error(f"搜索执行失败：平台={platform}，关键词={name}，错误={str(e)}", exc_info=True)
            raise

    def search_iqiyi(platform_config, name):
        """搜索爱奇艺视频"""
        logger.debug(f"开始爱奇艺搜索：关键词={name}")
        cookies = {
            'QC005': 'e85212ec7f68b2a3b36d4f13104889ce',
            'QC007': 'https%3A%2F%2Fntp.msn.cn%2F',
            'QC142': 'ac30238882b84c8c',
            'QC240': '%7B%22279043417441330035%22%3A%5B1748231948539%5D%7D',
            'curDeviceState': 'width%3D1912%3BconduitId%3Df_588_wrb%3Bscale%3D100%3Bbrightness%3Ddark%3BisLowPerformPC%3D0%3Bos%3Dbrowser%3Bosv%3D10.0.19044',
            'QP0037': '0',
            'T00404': '764ac4691d9dc48048ae4b3ff996024b',
            'QP0042': '{"v":1,"avc":{"de":1,"wv":1},"av1":{"de":1}}',
            'QC234': '34d5718eab0bf6c11a182e3c66c98d13',
            'QC006': '0a3b50cdaeb9e2f80c65575f8535ff33',
            'IMS': 'IggQABj_n9LBBioqCiRkYjM3OWQ1NC03NmI5LTRkYTktYTk1My00ODQ3NGFhYTAzM2QQACIAcigKJGRiMzc5ZDU0LTc2YjktNGRhOS1hOTUzLTQ4NDc0YWFhMDMzZBAAggEEIgIQBIoBKAomCiRkYjM3OWQ1NC03NmI5LTRkYTktYTk1My00ODQ3NGFhYTAzM2Q',
        }

        params = platform_config["params"](name)
        logger.debug(f"爱奇艺请求参数：{params}")

        try:
            response = requests.get(
                platform_config["search_url"],
                params=params,
                cookies=cookies,
                headers=platform_config["headers"],
                timeout=30
            )
            response.raise_for_status()
            logger.debug(f"爱奇艺请求成功，状态码={response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"爱奇艺请求失败：{str(e)}", exc_info=True)
            return {
                'info': f"请求失败，状态码: {getattr(response, 'status_code', '未知')}",
                'videos': []
            }

        try:
            data = response.json()
            logger.debug("爱奇艺响应JSON解析成功")
        except json.JSONDecodeError as e:
            logger.error(f"爱奇艺JSON解析失败：{str(e)}", exc_info=True)
            return {
                'info': "无法解析JSON响应",
                'videos': []
            }

        templates = data.get('data', {}).get('templates', [])
        if not templates:
            logger.warning("爱奇艺未找到templates数据")
            return {
                'info': "未找到剧集信息",
                'videos': []
            }

        first_template = templates[0]
        album_info = first_template.get('albumInfo', {})

        title = album_info.get('title', '未知标题')
        year = album_info.get('year', {}).get('value', '未知年份')
        region = album_info.get('region', {}).get('value', '未知区域')
        actors = [actor.get('title', '') for actor in
                  album_info.get('actors', {}).get('value', []) if
                  actor.get('title')]
        brief = album_info.get('brief', {}).get('value', '暂无简介')

        # 构建要显示的文本
        info_text = f"《{title}》 ({year} | {region})\n"
        info_text += f"主演: {', '.join(actors) if actors else '未知'}\n\n"
        info_text += f"简介: {brief}\n"
        logger.debug(f"爱奇艺剧集信息：{info_text[:100]}...")

        videos = first_template.get('videos', [])
        if not videos:
            videos = album_info.get('videos', [])

        video_items = []
        if videos:
            for video in videos:
                title = video.get('title', '')
                subtitle = video.get('subtitle', '')
                page_url = video.get('pageUrl', '')
                if page_url:
                    video_items.append({
                        'title': title,
                        'subtitle': subtitle,
                        'url': page_url
                    })
            logger.debug(f"爱奇艺直接获取视频列表，共{len(video_items)}个")
        else:
            logger.debug("爱奇艺未直接获取视频列表，尝试通过qipuId获取")
            title = first_template.get('albumInfo', {}).get('title', '')
            subtitle = first_template.get('albumInfo', {}).get('introduction', '')
            pages_url = first_template.get('albumInfo', {}).get('qipuId', '')

            if not pages_url:
                logger.warning("爱奇艺未获取到qipuId")
                video_items.append({
                    'title': "未找到视频列表",
                    'subtitle': "",
                    'url': ""
                })
                return {
                    'info': info_text,
                    'videos': video_items
                }

            headers = {
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0',
            }
            params = {
                'id': pages_url,
                'locale': 'zh_cn',
            }

            try:
                response = requests.get(
                    'https://mesh.if.iqiyi.com/player/pcw/video/playervideoinfo',
                    params=params,
                    headers=headers,
                    timeout=30
                )
                response.raise_for_status()
                json_data = response.json()
                vpic_value = json_data["data"]["vu"]
                if vpic_value:
                    video_items.append({
                        'title': title,
                        'subtitle': subtitle,
                        'url': vpic_value
                    })
                logger.debug(f"通过qipuId获取爱奇艺视频URL：{vpic_value}")
            except Exception as e:
                logger.error(f"爱奇艺通过qipuId获取视频失败：{str(e)}", exc_info=True)
                video_items.append({
                    'title': "视频列表获取失败",
                    'subtitle': "",
                    'url': ""
                })

        return {
            'info': info_text,
            'videos': video_items
        }

    def search_tencent_video(platform_config, name):
        """搜索腾讯视频"""
        logger.debug(f"开始腾讯视频搜索：关键词={name}")
        json_data = platform_config["json_data"].copy()
        json_data["query"] = name
        logger.debug(f"腾讯视频请求JSON：{json_data}")

        try:
            # 第一步：搜索视频
            response = requests.post(
                platform_config["search_url"],
                params=platform_config["params"],
                headers=platform_config["headers"],
                json=json_data,
                timeout=30
            )
            response.raise_for_status()
            logger.debug(f"腾讯视频搜索请求成功，状态码={response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"腾讯视频搜索请求失败：{str(e)}", exc_info=True)
            return {
                'info': f"腾讯视频请求失败，状态码: {getattr(response, 'status_code', '未知')}",
                'videos': []
            }

        try:
            data = response.json()
            logger.debug("腾讯视频搜索响应JSON解析成功")
        except json.JSONDecodeError as e:
            logger.error(f"腾讯视频搜索JSON解析失败：{str(e)}", exc_info=True)
            return {
                'info': "无法解析腾讯视频的JSON响应",
                'videos': []
            }

        # 确保 data 结构完整
        normal_list = data.get('data', {}).get('normalList', {}) or {}
        areaBox_List = data.get('data', {}).get('areaBoxList', []) or []
        item_list = normal_list.get('itemList', []) or []
        if normal_list is None:
            logger.warning("腾讯视频未找到normalList数据")
            return {
                'info': "未找到腾讯视频搜索结果。建议：检查version值",
                'videos': []
            }

        if not item_list:
            logger.warning("腾讯视频未找到itemList数据")
            return {
                'info': "未找到腾讯视频搜索结果",
                'videos': []
            }

        # 提取第一个搜索结果的信息
        first_item = item_list[0] if item_list else {}
        video_info = first_item.get('videoInfo', {}) or {}
        # 空值判断后再处理
        if not video_info or str(video_info.get('title', '')).strip() == '':
            first_areaBox = areaBox_List[0] if areaBox_List else {}
            areaBox_list = first_areaBox.get('itemList', []) or []
            first_item = areaBox_list[0] if areaBox_list else {}
            video_info = first_item.get('videoInfo', {}) or {}

        # 强制兜底：确保video_info是字典，避免None
        video_info = video_info or {}

        title = video_info.get('title', '未知标题')
        year = video_info.get('year', '未知年份')
        area = video_info.get('area', '未知区域')
        actors = video_info.get('actors', [])
        actors_text = ', '.join(actors) if isinstance(actors, list) else actors
        descrip = video_info.get('descrip', '暂无简介')

        # 构建要显示的文本
        info_text = f"《{title}》 ({year} | {area})\n"
        info_text += f"主演: {actors_text if actors_text else '未知'}\n\n"
        info_text += f"简介: {descrip}\n"
        logger.debug(f"腾讯视频剧集信息：{info_text[:100]}...")

        # 从doc字段中提取正确的cid
        cid = _extract_cid(first_item)
        if not cid:
            logger.warning("腾讯视频未提取到cid")
            info_text += "\n无法获取视频cid，使用搜索结果列表...\n"
            return {
                'info': info_text,
                'videos': _process_simple_video_list(item_list)
            }
        logger.debug(f"腾讯视频提取到cid：{cid}")

        # 第二步：获取视频列表
        cookies = {
            'ptcz': '54897e5fe4b124dc3eb77fecae118dea388262f27e3afa16703e94297b6f482b',
            'video_platform': '2',
            'pgv_pvid': '3382610315',
        }

        headers = {
            'accept': 'application/json',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'content-type': 'application/json',
            'origin': 'https://v.qq.com',
            'referer': 'https://v.qq.com/',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0',
        }

        params = {
            'video_appid': '3000010',
            'vplatform': '2',
            'vversion_name': '8.2.96',
        }

        # 初始化分页参数
        all_videos = []
        page_num = 1
        page_size = 100
        max_pages = 10  # 最大页数限制，防止无限循环

        while True:
            # 动态计算当前页的episode_begin和episode_end
            episode_begin = (page_num - 1) * page_size + 1
            episode_end = page_num * page_size

            # 构建请求的json数据
            json_data = {
                'has_cache': 1,
                'page_params': {
                    'detail_page_type': '1',
                    'id_type': '1',
                    'lid': '',
                    'page_context': (
                        f'chapter_name=&cid={cid}&detail_page_type=1&'
                        f'episode_begin={episode_begin}&episode_end={episode_end}&'
                        f'episode_step={page_size}&filter_rule_id=&id_type=1&'
                        f'is_nocopyright=false&is_skp_style=false&lid=&list_page_context=&'
                        f'mvl_strategy_id=&need_tab=1&order=&page_num={page_num}&'
                        f'page_size={page_size}&req_from=web_vsite&req_from_second_type=&'
                        f'req_type=0&siteName=&tab_type=1&title_style=&ui_type=null&'
                        f'un_strategy_id=ea35cb94195c48c091172a047da3e761&'
                        f'watch_together_pay_status=0&year='
                    ),
                    'page_id': 'vsite_episode_list_search',
                    'page_num': str(page_num),
                    'page_size': str(page_size),
                    'page_type': 'detail_operation',
                    'req_from': 'web_vsite',
                    'cid': cid,
                },
            }

            logger.debug(f"腾讯视频获取第{page_num}页视频列表，cid={cid}")
            try:
                # 获取视频列表数据
                response = requests.post(
                    'https://pbaccess.video.qq.com/trpc.universal_backend_service.page_server_rpc.PageServer/GetPageData',
                    params=params,
                    cookies=cookies,
                    headers=headers,
                    json=json_data,
                    timeout=30
                )
                response.raise_for_status()
                logger.debug(f"腾讯视频第{page_num}页请求成功，状态码={response.status_code}")
            except requests.exceptions.RequestException as e:
                logger.error(f"腾讯视频第{page_num}页请求失败：{str(e)}", exc_info=True)
                info_text += f"获取第{page_num}页视频列表请求失败，状态码: {getattr(response, 'status_code', '未知')}\n"
                break

            try:
                # 解析视频列表数据
                page_data = response.json()
                video_items = _extract_video_items(page_data)
                if video_items is None:
                    video_items = []

                if not video_items:
                    logger.warning(f"腾讯视频第{page_num}页未找到视频数据")
                    info_text += f"\n第{page_num}页未找到视频列表数据，停止获取...\n"
                    break

                # 转换为标准格式
                page_videos = []
                for item in video_items:
                    if item is None:
                        continue

                    play_title = item.get('play_title', '未知标题')
                    video_subtitle = item.get('video_subtitle', '')
                    item_cid = item.get('cid')
                    vid = item.get('vid')

                    if not item_cid or not vid:
                        continue

                    url = f'https://v.qq.com/x/cover/{item_cid}/{vid}.html'
                    page_videos.append({
                        'title': play_title,
                        'subtitle': video_subtitle,
                        'url': url
                    })

                all_videos.extend(page_videos)
                info_text += f"成功获取第{page_num}页，共{len(page_videos)}个视频\n"
                logger.debug(f"腾讯视频第{page_num}页获取{len(page_videos)}个视频")

                # 如果当前页数量不足page_size，说明已到最后一页
                if len(page_videos) < page_size:
                    info_text += "已获取全部视频页\n"
                    logger.debug("腾讯视频已获取全部视频页")
                    break

                # 限制最大页数
                if page_num >= max_pages:
                    info_text += f"已达到最大页数限制({max_pages})，停止获取\n"
                    logger.debug(f"腾讯视频达到最大页数限制({max_pages})")
                    break

                page_num += 1  # 页码递增

            except Exception as e:
                logger.error(f"腾讯视频第{page_num}页解析失败：{str(e)}", exc_info=True)
                info_text += f"解析第{page_num}页视频列表失败: {str(e)}，停止获取...\n"
                break

        # 处理最终结果
        if all_videos:
            logger.info(f"腾讯视频共获取{len(all_videos)}个视频")
            return {
                'info': info_text,
                'videos': all_videos
            }
        else:
            info_text += "\n未找到任何视频列表数据，使用搜索结果列表...\n"
            logger.warning("腾讯视频未获取到任何视频列表数据")
            return {
                'info': info_text,
                'videos': _process_simple_video_list(item_list)
            }

    def _extract_cid(item):
        """从搜索结果项中提取cid"""
        cid = item.get('doc', {}).get('id', '')
        if not cid:
            cid = item.get('cid', '')
        if not cid:
            cid = item.get('videoInfo', {}).get('cid', '')
        return cid

    def _extract_video_items(page_data):
        """从分页数据中提取视频项"""
        try:
            # 尝试多层级提取视频项
            module_list = page_data.get('data', {}).get('module_list_datas', [])

            for module in module_list:
                module_datas = module.get('module_datas', [])
                for data_item in module_datas:
                    item_data_lists = data_item.get('item_data_lists', {})
                    item_datas = item_data_lists.get('item_datas', [])

                    if item_datas:
                        # 转换视频项格式
                        video_items = []
                        for item in item_datas:
                            item_params = item.get('item_params', {})
                            video_items.append({
                                'play_title': item_params.get('play_title', ''),
                                'video_subtitle': item_params.get('video_subtitle', ''),
                                'cid': item_params.get('cid'),
                                'vid': item_params.get('vid')
                            })
                        return video_items

            return []
        except Exception as e:
            logger.error(f"提取视频项时出错: {e}", exc_info=True)
            return []

    def _process_simple_video_list(item_list):
        """处理简单的视频列表（当无法获取详细列表时使用）"""
        videos = []
        for item in item_list:
            title = item.get('title', '')
            subtitle = ''

            if 'episodeInfo' in item:
                subtitle = item['episodeInfo'].get('title', '')
            elif 'shortTitle' in item:
                subtitle = item['shortTitle']

            page_url = ''
            if 'playUrl' in item:
                page_url = item['playUrl']
            elif 'cid' in item and 'vid' in item:
                page_url = f"https://v.qq.com/x/cover/{item['cid']}/{item['vid']}.html"

            # 只添加有标题和有效URL的项
            if title and page_url:
                videos.append({
                    'title': title,
                    'subtitle': subtitle,
                    'url': page_url
                })
                logger.debug(f"腾讯视频简单列表添加项：标题={title}，URL={page_url}")

        logger.info(f"腾讯视频简单列表共{len(videos)}个视频")
        return videos

    def search_youku(platform_config, name):
        """搜索优酷视频"""
        logger.debug(f"开始优酷视频搜索：关键词={name}")
        cookies = {
            'isI18n': 'false',
            'cna': 'XGCsHLBYpmECARvbn0zeb2Km',
            '_m_h5_tk': '19bb8d38e8cd14831838874fbda13811_1748265425191',
            '_m_h5_tk_enc': '128b02da9b5f264735c67f66badf60d',
            '__ysuid': '1748261678419ABJ',
            '__ayft': '1748261678421',
            '__aysid': '1748261678421jUC',
            '__ayscnt': '1',
            'HISTORY_KEY': '%5B%22%E6%89%A7%E6%B3%95%E8%80%85%E4%BB%AC%22%5D',
            'ctoken': 'VGzJZuwwSbpfrOu8-9OTOupb',
            'xlly_s': '1',
            'login_index': '3_1748261678346',
            '__arpvid': '174826306538452WbRn-1748263065393',
            '__aypstp': '5',
            '__ayspstp': '5',
            'tfstk': 'g9EtW9fCN6fMloqtKR7HiO7gWfXhtw2aL5yWmjcMlWFLhWuXIcDGMnGLgf0iffxxD-F4ifgGbHKEebdXCOQ6OHEoEP6j1OIBAbqJGPtjcHpIEbUwcOQj8HnaxPxms1oYh7mAZ_jlqRyZum1lZeSWmrnjhicf-VN7Tc9FZ_jlxN9ID1CuijsVo9HqOAMjCj6KOYMqcAij59hIHY8X1SNfpMMqnh965msCAXkjcjNjc9eITxnjGXffnXTqGnUuDsZbbDcpcnZKCsl7BXHELkHtWbgs9nagvA3tNRZJF6uodkqKr2vcwDwz-5MK2iKIX4U_6vwFM3h_r8oIOSOOtvz_6kgYxt7apohtPlgpH3ynDxNjky5JqAngJ4ET7tRQC7cTPcyDe12Kyy3rd29v5c4uEou7fGdrt4kYwYF96gzQquLrsQc-nF6dpE8q5vurKSMIc5rldvhlBZY2u2bSpbXdpE8q5vkKZ9qJuEuhP',
            'isg': 'BAIC-snF3umI3cI1J_6FjvE0Uwhk0wbtks8nTUwZPnUpn6AZNGIM_JbNSZvjy36F',
            '__ayvstp': '14',
            '__aysvstp': '14',
        }

        try:
            # 发送请求获取页面内容
            search_url = platform_config["search_url"].format(name)
            logger.debug(f"优酷请求URL：{search_url}")
            response = requests.get(
                search_url,
                cookies=cookies,
                headers=platform_config["headers"],
                timeout=30
            )
            response.raise_for_status()
            html_content = response.text
            logger.debug(f"优酷请求成功，状态码={response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"优酷请求失败：{str(e)}", exc_info=True)
            return {
                'info': f"请求出错: {str(e)}\n",
                'videos': []
            }

        # 解析HTML标签中的信息
        soup = BeautifulSoup(html_content, 'html.parser')

        # 使用正则表达式提取window.__INITIAL_DATA__中的JSON数据
        pattern = r'window.__INITIAL_DATA__\s*=\s*({.*?});'
        match = re.search(pattern, html_content, re.DOTALL)

        if not match:
            logger.warning("优酷未找到window.__INITIAL_DATA__")
            return {
                'info': "未找到JSON数据，无法获取视频信息\n",
                'videos': []
            }

        json_str = match.group(1)
        try:
            data = json.loads(json_str)
            logger.debug("优酷JSON数据解析成功")
        except json.JSONDecodeError as e:
            logger.error(f"优酷JSON解析失败：{str(e)}", exc_info=True)
            return {
                'info': f"JSON解析错误: {e}\n",
                'videos': []
            }

        # 提取顶部信息
        show_info = "未找到节目基本信息\n"
        try:
            # 尝试多种路径获取顶部信息
            top_node = None
            paths = [
                ["data", "nodes", 0, "nodes", 0, "nodes", 0, "data", "focusInfo"],
                ["data", "nodes", 0, "nodes", 0, "data", "focusInfo"],
                ["data", "nodes", 0, "nodes", 0, "nodes", 0, "data"],
                ["data", "nodes", 0, "nodes", 0, "data"],
                ["data", "nodes", 0, "data"]
            ]

            for path in paths:
                try:
                    top_node = data
                    for part in path:
                        top_node = top_node[part]
                    if top_node:
                        break
                except (KeyError, IndexError, TypeError):
                    continue

            if top_node:
                # 提取所需信息 - 针对电影优化
                if "focusInfo" in top_node:
                    # 电影信息结构
                    movie_info = top_node["focusInfo"]
                    temp_title = movie_info.get("title", "")
                    show_type = movie_info.get("feature", "")
                    notice = movie_info.get("actor", "") or movie_info.get("director", "")
                    info = movie_info.get("intro", "")
                else:
                    # 通用信息结构
                    notice = top_node.get("notice", "") or top_node.get("director", "")
                    info = top_node.get("info", "")
                    temp_title = top_node.get("tempTitle", "")
                    show_type = top_node.get("featureDTO", {}).get("text", "")

                # 构建节目信息文本
                show_info = f"《{temp_title}》|{show_type}\n"
                show_info += f"{notice}\n\n"
                show_info += f"{info}\n"
                logger.debug(f"优酷剧集信息：{show_info[:100]}...")
            else:
                show_info += "无法找到顶部信息节点\n"
                logger.warning("优酷未找到顶部信息节点")

        except Exception as e:
            show_info += f"提取顶部信息时出错: {e}\n"
            logger.error(f"优酷提取顶部信息失败：{str(e)}", exc_info=True)

        # 从JavaScript变量提取视频列表信息
        valid_items = []
        if data:
            # 尝试多种路径提取视频列表
            video_list_paths = [
                ["data", "nodes", 0, "nodes", 0, "data", "focusInfo", "videoList"],
                ["data", "nodes", 0, "nodes", 0, "nodes", 0, "data", "previewId"],
                ["data", "nodes", 0, "nodes", 0, "data", "videos", "items"],
                ["data", "nodes", 0, "nodes", 1, "data", "items"],
                ["data", "nodes", 0, "nodes", 1, "data", "videos", "items"],
                ["data", "nodes", 1, "data", "items"],
            ]

            for path in video_list_paths:
                try:
                    video_list = data
                    for part in path:
                        video_list = video_list[part]

                    if isinstance(video_list, list) and len(video_list) > 0:
                        show_info += f"从路径 {path} 提取到 {len(video_list)} 个视频项\n"
                        logger.debug(f"优酷从路径{path}提取{len(video_list)}个视频项")

                        # 处理视频列表
                        for item in video_list:
                            # 提取视频ID和spm
                            video_id = item.get("videoId") or item.get("vid") or item.get("previewId")
                            spm = item.get("spm") or item.get("action", {}).get("report", {}).get("spm")

                            # 提取标题信息
                            title = item.get("title") or item.get("k") or item.get("object_title")
                            subtitle = item.get("subtitle") or item.get("desc")
                            if video_id and spm and title:
                                valid_items.append({
                                    "videoId": video_id,
                                    "spm": spm,
                                    "k": title,
                                    "subtitle": subtitle
                                })

                        break  # 找到有效视频列表后不再尝试其他路径
                except (KeyError, IndexError, TypeError):
                    continue

            # 如果没有找到视频列表，尝试递归遍历整个数据结构
            if not valid_items:
                show_info += "未找到明确的视频列表，尝试递归遍历...\n"
                logger.debug("优酷未找到明确视频列表，尝试递归遍历")
                valid_items = traverse_nodes(data)

        # 转换为标准格式
        videos = []
        if valid_items:
            for idx, item in enumerate(valid_items, 1):
                # 构建标题
                title_parts = []
                primary_title = item.get('k')
                if primary_title:
                    title_parts.append(primary_title)

                # 添加orderId作为集数信息
                if 'orderId' in item and item['orderId']:
                    order_id = item['orderId']
                    try:
                        order_num = int(order_id)
                        title_parts.append(f"第{order_num}集")
                    except (ValueError, TypeError):
                        title_parts.append(str(order_id))

                display_title = " - ".join(title_parts) if title_parts else "未知标题"
                subtitle = item.get('subtitle', '')

                # 确定正确的视频ID和spm
                video_id = item.get('videoId') or item.get('vid') or item.get('previewId')
                spm = item.get('spm')

                if not video_id or not spm:
                    continue

                if len(video_id) == 20:
                    video_url = f"https://v.youku.com/video?s={video_id}"
                else:
                    video_url = f"https://v.youku.com/video?spm={spm}&vid={video_id}"

                videos.append({
                    'title': display_title,
                    'subtitle': subtitle,
                    'url': video_url
                })
                logger.debug(f"优酷添加视频项：标题={display_title}，URL={video_url}")

        else:
            # 尝试从HTML中提取视频列表
            video_items = soup.select('.sk-mod-list .item')
            if video_items:
                show_info += f"从HTML中提取了{len(video_items)}个视频项\n"
                logger.debug(f"优酷从HTML提取{len(video_items)}个视频项")
                for item in video_items:
                    title_elem = item.select_one('.title')
                    if title_elem:
                        title_text = title_elem.get_text(strip=True)
                        href = title_elem.get('href', '')
                        if href:
                            video_id_match = re.search(r'/id_([a-zA-Z0-9]+)\.html', href)
                            if video_id_match:
                                video_id = video_id_match.group(1)
                                video_url = f"https://v.youku.com/v_show/id_{video_id}.html"
                                videos.append({
                                    'title': title_text,
                                    'subtitle': '',
                                    'url': video_url
                                })
                                logger.debug(f"优酷HTML提取视频项：标题={title_text}，URL={video_url}")

            if not videos:
                videos.append({
                    'title': "未找到视频数据",
                    'subtitle': "",
                    'url': ""
                })
                show_info += "未找到任何视频数据\n"
                logger.warning("优酷未找到任何视频数据")

        logger.info(f"优酷共获取{len(videos)}个视频")
        return {
            'info': show_info,
            'videos': videos
        }

    def search_bilibili(platform_config, name):
        """搜索哔哩哔哩视频（番剧/电影/单个视频）"""
        logger.debug(f"开始B站搜索：关键词={name}")
        cookies = platform_config.get("cookies", {})
        params = platform_config["params"](name)
        logger.debug(f"B站请求参数：{params}")

        try:
            # 发送搜索请求
            response = requests.get(
                platform_config["search_url"],
                params=params,
                cookies=cookies,
                headers=platform_config["headers"],
                timeout=30
            )
            response.raise_for_status()
            logger.debug(f"B站请求成功，状态码={response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"B站请求失败：{str(e)}", exc_info=True)
            return {
                'info': f"B站请求失败，状态码: {getattr(response, 'status_code', '未知')}",
                'videos': []
            }

        # 解析HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        info_text = ""
        video_items = []

        try:
            # ========== 步骤1：提取剧集基础信息（标题/简介等） ==========
            # 优先提取第一个搜索结果的基础信息（番剧/视频）

            first_result = soup.find('div', class_='media-card-content-head') or soup.find('div', class_='media-card-content')
            if first_result:
                # 提取标题
                title_elem = first_result.find('em', class_='keyword') or first_result.find('h3',
                                                                                         class_='media-card-title')
                title = title_elem.get_text(strip=True) if title_elem else '未知标题'

                # 提取简介/UP主/播放量等
                desc_elem = first_result.find('div', class_='media-card-content-head-desc') or first_result.find('div',
                                                                                         class_='media-card-desc')
                desc = desc_elem.get_text(strip=True) if desc_elem else '暂无简介'

                tag_elem = first_result.find('div', class_ = 'media-card-content-head-title-tag')
                extracted_info = tag_elem.get_text(strip=True) if desc_elem else '暂无简介'

                up_elem = first_result.find('span', class_='up-name') or first_result.find('span',
                                                                                           class_='media-card-owner')
                up_name = up_elem.get_text(strip=True) if up_elem else '未知UP主'

                # 构建基础信息文本
                info_text = f"《{title}》\n"
                info_text += f"UP主: {up_name}\n\n"
                info_text += f"标签: {extracted_info}\n\n"
                info_text += f"{desc}\n"
                logger.debug(f"B站剧集信息：{info_text[:100]}...")
            else:
                info_text = "未找到相关剧集信息\n"
                logger.warning("B站未找到基础信息节点")

            # ========== 步骤2：提取视频列表（集数+播放链接） ==========
            # 定位media-footer容器（番剧集数列表）
            media_footer = soup.find('div', class_='media-footer')
            if media_footer:
                a_tags = media_footer.find_all('a', class_='p_relative')
                for a in a_tags:
                    # 提取相对链接 → 补全为完整URL
                    href = a.get('href', '')
                    full_url = f"https://www.bilibili.com{href}" if href.startswith('/') else href

                    # 提取集数（button内的数字）
                    num = a.find('button', class_='media-footer-btn').get_text(strip=True) if a.find('button',
                                                                                                     class_='media-footer-btn') else ''

                    # 3. 提取badge文本（预告/其他）
                    badge = a.find('span', class_='media-footer-badge')
                    text = badge.get_text(strip=True) if badge else '免费'

                    # 构建视频项
                    video_items.append({
                        'title': f"{title} 第{num}集" if num else title,
                        'subtitle': f"B站第{num}集({text})" if num else "",
                        'url': full_url
                    })
                logger.debug(f"B站提取到{len(video_items)}个集数链接")
            else:
                # 无集数列表 → 提取单个视频链接（适配单个视频/电影）
                video_cards = soup.find_all('div', class_='video-card-small') or soup.find_all('div',
                                                                                               class_='media-card')
                for card in video_cards[:10]:  # 最多提取前10个结果
                    # 提取视频链接
                    video_href = card.find('a', class_='cover').get('href', '') if card.find('a',
                                                                                             class_='cover') else ''
                    full_url = f"https://www.bilibili.com{video_href}" if video_href.startswith('/') else ''

                    # 提取视频标题
                    video_title = card.find('a', class_='title').get_text(strip=True) if card.find('a',
                                                                                                   class_='title') else '未知视频'

                    if full_url:
                        video_items.append({
                            'title': video_title,
                            'subtitle': '',
                            'url': full_url
                        })
                logger.debug(f"B站提取到{len(video_items)}个单个视频链接")

            # 空结果处理
            if not video_items:
                info_text += "\n未找到任何视频链接\n"
                video_items.append({
                    'title': "未找到B站视频数据",
                    'subtitle': "",
                    'url': ""
                })

        except Exception as e:
            logger.error(f"B站解析失败：{str(e)}", exc_info=True)
            info_text += f"\n解析B站数据失败: {str(e)}\n"
            video_items = [{
                'title': "解析失败",
                'subtitle': "",
                'url': ""
            }]

        logger.info(f"B站共获取{len(video_items)}个视频")
        return {
            'info': info_text,
            'videos': video_items
        }

    def traverse_nodes(data):
        """递归遍历数据结构中的所有节点，提取videoId和spm都不为空的信息"""
        results = []

        if isinstance(data, dict):
            # 提取当前节点中的信息
            video_id = data.get("videoId") or data.get("vid") or data.get("previewId")
            showId = data.get("showId") or ""
            spm = data.get("spm") or data.get("action", {}).get("report", {}).get("spm", "") or data.get(
                "report", {}).get("spm", "")

            # 提取标题相关信息
            k = data.get("k") or \
                data.get("action", {}).get("report", {}).get("trackInfo", {}).get("k", "") or \
                data.get("tempTitle") or \
                data.get("object_title") or \
                data.get("displayName") or \
                data.get("title")

            order_id = data.get("orderId") or \
                       data.get("showVideoStage") or \
                       data.get("orderStage")

            object_title = data.get("object_title") or \
                           data.get("title") or \
                           data.get("displayName")

            # 如果videoId和spm都不为空，则添加到结果列表
            if (video_id or showId) and spm:
                results.append({
                    "videoId": video_id or showId,
                    "spm": spm,
                    "k": k,
                    "orderId": order_id,
                    "subtitle": data.get("subtitle"),
                    "titleDTO": data.get("titleDTO"),
                    "object_title": object_title,
                    "tempTitle": data.get("tempTitle"),
                })
            # 递归遍历子节点
            for key, value in data.items():
                results.extend(traverse_nodes(value))

        elif isinstance(data, list):
            # 递归遍历列表中的每个元素
            for item in data:
                results.extend(traverse_nodes(item))

        return results

    def on_video_select(event):
        """处理视频选择事件"""
        try:
            selection = videos_tree.selection()
            if not selection:
                logger.warning("视频播放：未选择任何视频项")
                return

            item = videos_tree.item(selection[0])
            video_url = item['values'][2]
            video_title = item['values'][0]

            if not video_url:
                messagebox.showinfo("提示", "该视频没有可用的播放URL")
                logger.warning(f"视频播放：{video_title} 没有可用URL")
                return

            # 获取当前选择的解析源
            parse_site_name = parse_combobox.get()
            parse_site_config = parse_sites.get(parse_site_name)

            if not parse_site_config or not isinstance(parse_site_config, dict):
                messagebox.showerror("错误", f"无效的解析源配置: {parse_site_name}")
                logger.error(f"解析源配置错误: {parse_site_name}，配置={parse_site_config}")
                return

            parse_url_template = parse_site_config.get("url", "https://jx.xmflv.com/?url={}")
            if not parse_url_template or '{}' not in parse_url_template:
                messagebox.showerror("错误", f"无效的解析源URL模板: {parse_site_name}")
                logger.error(f"解析源URL模板错误: {parse_site_name}，模板={parse_url_template}")
                return

            # 格式化播放URL
            play_url = parse_url_template.format(video_url)
            logger.info(f"开始播放视频：标题={video_title}，原始URL={video_url}，解析后URL={play_url}")

            # 显示播放提示
            status_var.set(f"正在准备播放: {video_title}")
            root.update()

            # 在新线程中打开浏览器
            threading.Thread(target=lambda: webbrowser.open(play_url)).start()
        except Exception as e:
            logger.error(f"视频播放失败：{str(e)}", exc_info=True)
            messagebox.showerror("错误", f"播放失败: {str(e)}")

    def copy_selected_url():
        """复制选中项的URL到剪贴板"""
        if not app_running:
            return

        try:
            selected_item = videos_tree.selection()
            if not selected_item:
                messagebox.showinfo("提示", "请先选择一个视频项")
                logger.warning("复制URL：未选择任何视频项")
                return

            item = videos_tree.item(selected_item[0])
            url = item['values'][2]
            video_title = item['values'][0]

            if not url:
                messagebox.showinfo("提示", "该视频没有可用的URL")
                logger.warning(f"复制URL：{video_title} 没有可用URL")
                return

            root.clipboard_clear()
            root.clipboard_append(url)
            status_var.set("已复制URL到剪贴板")
            logger.info(f"复制URL成功：标题={video_title}，URL={url}")
        except Exception as e:
            logger.error(f"复制URL失败：{str(e)}", exc_info=True)
            messagebox.showerror("错误", f"复制失败: {str(e)}")

    def parse_url():
        """解析并播放视频URL"""
        try:
            parse_site_key = parse_combobox.get()
            url = url_entry.get().strip()

            if not url or url == "请复制你想要看的视频网址，粘贴到此处点击播放即可!":
                messagebox.showwarning("警告", "请先粘贴视频URL")
                logger.warning("URL解析：未输入URL")
                return

            # 验证URL格式
            platform = None
            for plat, config in platforms.items():
                if re.match(config["url_pattern"], url):
                    platform = plat
                    break

            if not platform:
                messagebox.showwarning("警告", "不支持的URL格式")
                logger.warning(f"URL解析：不支持的URL格式，URL={url}")
                return

            # 获取解析源配置
            parse_config = parse_sites.get(parse_site_key, parse_sites["默认"])
            url_template = parse_config.get("url") if isinstance(parse_config, dict) else parse_config

            if not url_template or not isinstance(url_template, str) or '{}' not in url_template:
                messagebox.showerror("错误", "解析地址配置错误")
                logger.error(f"解析源配置错误：{parse_site_key}，模板={url_template}")
                return

            # 格式化播放URL
            parsed_url = url_template.format(url)
            logger.info(f"URL解析播放：原始URL={url}，解析后URL={parsed_url}，平台={platform}")

            status_var.set(f"正在解析并打开视频: {url[:50]}...")
            webbrowser.open(parsed_url)
        except Exception as e:
            logger.error(f"URL解析失败：{str(e)}", exc_info=True)
            messagebox.showerror("错误", f"解析失败: {str(e)}")

    def _process_requests():
        """后台线程处理网络请求"""
        logger.debug("网络请求处理线程启动")
        while app_running:
            try:
                request = request_queue.get(timeout=1)
                func, args, kwargs, callback = request
                logger.debug(f"处理请求：函数={func.__name__}，参数={args}")

                try:
                    result = func(*args, **kwargs)
                    result_queue.put((callback, result, None))
                    logger.debug(f"请求处理成功：函数={func.__name__}")
                except Exception as e:
                    result_queue.put((callback, None, e))
                    logger.error(f"请求处理失败：函数={func.__name__}，错误={str(e)}", exc_info=True)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"请求处理线程异常：{str(e)}", exc_info=True)

    def on_closing():
        """窗口关闭时调用"""
        nonlocal app_running
        app_running = False
        logger.info("视频解析工具准备退出")

        # 取消所有待处理的after回调
        for after_id in after_ids:
            try:
                root.after_cancel(after_id)
            except:
                pass

        root.destroy()
        logger.info("视频解析工具已退出")

    # 初始化界面
    setup_styles()
    create_widgets()
    schedule_check_results()

    # 启动后台线程处理网络请求
    network_thread = threading.Thread(target=_process_requests, daemon=True)
    network_thread.start()

    # 绑定窗口关闭事件
    root.protocol("WM_DELETE_WINDOW", on_closing)

    logger.info("视频解析工具启动成功")

    # 返回应用的主函数
    def run():
        """运行应用程序（仅独立运行时才进入主循环）"""
        if standalone:
            root.mainloop()

    return run


if __name__ == "__main__":
    app = create_video_search_app()
    app()