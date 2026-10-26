# -*- coding: utf-8 -*-
"""
Zepp / 小米运动 微信步数修改工具

- 界面：账号列表行内直接编辑 + 勾选参与刷步的账号 + 一键批量刷步 + 定时刷步 + 实时运行日志
- 步数：账号行内「步数设置」先选类型（随机 / 固定）再自行填写数值
- 定时：账号行内「定时」列用上下箭头 / 鼠标滚轮调时、分；点「启动定时」后按已勾选账号的定时时间自动刷步
- 账号列表保存在程序目录的 zepp_accounts.json，下次打开自动载入
- 日志：log/YYYYMMDD/zepp_steps.log（打包后写在 exe 同级目录）
- 支持独立运行：python zepp_Steps.py
- 支持被主程序调用：create_zepp_steps_app(parent)
"""
import os
import sys
import re
import json
import time
import random
import socket
import struct
import queue
import logging
import threading
import urllib.parse
from datetime import datetime

import requests
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText


# ---------------- 输出安全包装（与其他模块一致，防止 Windows 控制台编码报错） ----------------
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
        return getattr(self._stream, name)


def _make_output_safe(stream):
    """修复 Windows 控制台/IDE 使用非 UTF-8 编码输出中文时的报错。"""
    if stream is None:
        return None
    try:
        stream.reconfigure(errors="replace")
        return stream
    except Exception:
        return _SafeStream(stream)


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
    """标准化日志：写入 log/YYYYMMDD/zepp_steps.log（跨天自动切换），同时输出到控制台"""
    log = logging.getLogger("zepp_steps")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    fh = _DailyFileHandler("zepp_steps")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        log.addHandler(sh)
    log.propagate = False
    log.info(f"日志文件路径: {os.path.join(_base_dir(), 'log', datetime.now().strftime('%Y%m%d'), 'zepp_steps.log')}")
    return log


logger = _setup_logger()


# ================= 核心接口（与步数平台交互） =================
def login(account, password):
    """登录 Zepp / 小米运动账号，返回 (login_token, user_id)，失败返回 (None, None)"""
    PHONE_PATTERN = r"(^(1)\d{10}$)"
    if re.match(PHONE_PATTERN, account):
        account = f"+86{account}"
        third_name = "huami_phone"
    else:
        third_name = "huami"

    headers = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "user-agent": "MiFit/6.12.0 (MCE16; Android 16; Density/1.5)",
        "app_name": "com.xiaomi.hm.health",
    }
    url1 = "https://api-user.huami.com/registrations/" + account + "/tokens"
    data1 = f"client_id=HuaMi&country_code=CN&json_response=true&name={account}&password={password}&redirect_uri=https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html&state=REDIRECTION&token=access"
    try:
        res1 = requests.post(url1, data=data1, headers=headers)
    except Exception as e:
        logger.error(f"登录请求异常：{e}")
        return None, None

    if res1.status_code == 200:
        r1sc = res1.status_code
        res1 = res1.json()
        if "access" in res1:
            logger.info(f"登录成功（第一步），Access Code={r1sc}")
            code = res1["access"]
        else:
            logger.error(f"用户名或密码不正确，Access Code={r1sc}")
            return None, None
    elif res1.status_code == 429:
        logger.error(f"请求过于频繁，请稍后再试，Code={res1.status_code}")
        return None, None
    else:
        logger.error(f"登录失败，Code={res1.status_code}")
        return None, None

    url2 = "https://account.huami.com/v2/client/login"
    data2 = f"app_name=com.xiaomi.hm.health&country_code=CN&code={code}&device_id=00:00:00:00:00:00&device_model=android_phone&app_version=6.12.0&grant_type=access_token&allow_registration=false&source=com.xiaomi.hm.health&third_name={third_name}"
    try:
        res2 = requests.post(url2, data=data2, headers=headers)
    except Exception as e:
        logger.error(f"获取登录令牌异常：{e}")
        return None, None

    if res2.status_code == 200:
        r2sc = res2.status_code
        res2 = res2.json()
        login_token = res2["token_info"]["login_token"]
        user_id = res2["token_info"]["user_id"]
        logger.info(f"登录成功，Login Code={r2sc}，User ID={user_id}")
        return login_token, user_id
    logger.error(f"登录失败，Login Code={res2.status_code}")
    return None, None


def get_app_token(login_token):
    """用 login_token 换取 app_token，失败返回 None"""
    headers = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "user-agent": "MiFit/6.12.0 (MCE16; Android 16; Density/1.5)",
        "app_name": "com.xiaomi.hm.health",
    }
    url = f"https://account-cn.huami.com/v1/client/app_tokens?login_token={login_token}"
    try:
        res = requests.get(url, headers=headers)
    except Exception as e:
        logger.error(f"获取 app_token 异常：{e}")
        return None

    if res.status_code == 200:
        res_data = res.json()
        if "token_info" in res_data:
            logger.info(f"成功获取到 app_token，Code={res.status_code}")
            return res_data["token_info"]["app_token"]
        logger.error(f"无法解析出有效的 app_token，Code={res.status_code}")
        return None
    logger.error(f"无法获取到有效的 app_token，Code={res.status_code}")
    return None


def change_steps(account, user_id, app_token, steps):
    """提交步数数据，成功返回 True"""
    sec_timestamp = get_sec_timestamp()
    if sec_timestamp is None:
        sec_timestamp = int(time.time())
        logger.warning(f"NTP 时间获取失败，使用系统时间 {sec_timestamp}")

    dateToday = time.strftime("%F")
    deviceID = "0000000000000000"

    dataJSON = "%5b%7b%22data_hr%22%3a%22" + "%5c%2fv7%2b" * 480 + f"%22%2c%22date%22%3a%22{dateToday}%22%2c%22data%22%3a%5b%7b%22start%22%3a0%2c%22stop%22%3a1439%2c%22value%22%3a%22" + "A" * 5760 + f"%22%2c%22tz%22%3a32%2c%22did%22%3a%22{deviceID}%22%2c%22src%22%3a24%7d%5d%2c%22summary%22%3a%22%7b%5c%22v%5c%22%3a6%2c%5c%22slp%5c%22%3a%7b%5c%22st%5c%22%3a0%2c%5c%22ed%5c%22%3a0%2c%5c%22dp%5c%22%3a0%2c%5c%22lt%5c%22%3a0%2c%5c%22wk%5c%22%3a0%2c%5c%22usrSt%5c%22%3a-1440%2c%5c%22usrEd%5c%22%3a-1440%2c%5c%22wc%5c%22%3a0%2c%5c%22is%5c%22%3a0%2c%5c%22lb%5c%22%3a0%2c%5c%22to%5c%22%3a0%2c%5c%22dt%5c%22%3a0%2c%5c%22rhr%5c%22%3a0%2c%5c%22ss%5c%22%3a0%7d%2c%5c%22stp%5c%22%3a%7b%5c%22ttl%5c%22%3a{steps}%2c%5c%22dis%5c%22%3a0%2c%5c%22cal%5c%22%3a0%2c%5c%22wk%5c%22%3a0%2c%5c%22rn%5c%22%3a0%2c%5c%22runDist%5c%22%3a0%2c%5c%22runCal%5c%22%3a0%2c%5c%22stage%5c%22%3a%5b%5d%7d%2c%5c%22goal%5c%22%3a0%2c%5c%22tz%5c%22%3a%5c%2228800%5c%22%7d%22%2c%22source%22%3a24%2c%22type%22%3a0%7d%5d"

    headers0 = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "user-agent": "MiFit/6.12.0 (MCE16; Android 16; Density/1.5)",
        "app_name": "com.xiaomi.hm.health",
        "apptoken": app_token,
    }
    url0 = f"https://api-mifit-cn.huami.com/v1/data/band_data.json?&t={sec_timestamp}"
    data0 = f"userid={user_id}&last_sync_data_time={sec_timestamp}&device_type=0&last_deviceid={deviceID}&data_json={dataJSON}"

    try:
        res0 = requests.post(url0, data=data0, headers=headers0)
        res0_data = res0.json()
        logger.info(f"账号:{account} 步数={steps} 返回结果={res0_data}")
        if res0_data.get('message') == 'success':
            return True
        return False
    except Exception as e:
        logger.error(f"提交步数失败:{e}")
        return False


def get_sec_timestamp():
    """通过 NTP 服务器获取秒级时间戳，失败返回 None"""
    client = None
    try:
        NTP_SERVER = 'ntp.ntsc.ac.cn'
        PORT = 123
        NTP_FORMAT = '!12I'
        NTP_DELTA = 2208988800

        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(5)

        data = bytearray(48)
        data[0] = 0x1B

        client.sendto(data, (NTP_SERVER, PORT))
        data, address = client.recvfrom(1024)

        if data:
            unpacked = struct.unpack(NTP_FORMAT, data[:48])
            timestamp = unpacked[10] - NTP_DELTA
            logger.info(f"成功通过 NTP 服务器获取到秒级时间戳，SecTimeStamp={timestamp}")
            return int(timestamp)
        return None
    except socket.timeout:
        logger.error("连接 NTP 服务器超时")
        return None
    except socket.gaierror:
        logger.error("无法解析 NTP 服务器地址")
        return None
    except Exception as e:
        logger.error(f"获取时间戳失败 - {e}")
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


def URL_encode_dataJSON(dateToday, deviceID, steps):
    """备用：另一种精简 JSON 编码方式"""
    JSON_Frame = f'[{{"date":"{dateToday}","data":[{{"start":0,"stop":1439,"did":"{deviceID}","src":24}}],"summary":"{{\\"stp\\":{{\\"ttl\\":{steps}}}}}","source":24,"type":0}}]'
    return urllib.parse.quote(JSON_Frame, safe='')


# ================= 界面 =================
COLOR_PRIMARY = "#3B82F6"       # 主色
COLOR_PRIMARY_DARK = "#2563EB"  # 主色加深
COLOR_BG = "#EEF3FB"            # 页面背景
COLOR_CARD = "#FFFFFF"          # 卡片底色
COLOR_TEXT = "#1E293B"          # 正文文字
COLOR_SUB = "#64748B"           # 次要文字
COLOR_BORDER = "#DBE6F5"        # 卡片边框
COLOR_BTN_BORDER = "#B9C8E4"    # 按钮边框
COLOR_ROW_ALT = "#F7FAFF"       # 列表隔行底色
COLOR_FIELD = "#F5F8FE"         # 输入框底色

DEFAULT_RANGE_MIN = 35000       # 新增账号时的默认随机范围
DEFAULT_RANGE_MAX = 65000

# 「步数设置」先选类型（随机 / 固定），再由用户自行填写数值
STEP_MODES = ("随机", "固定")

FONT_UI = ('微软雅黑', 10)
FONT_SMALL = ('微软雅黑', 9)
_TREE_COLUMNS = ('select', 'index', 'account', 'password', 'steps', 'schedule')
_TREE_HEADINGS = (('select', '✓', 40), ('index', '序号', 44), ('account', '账号', 165),
                  ('password', '密码', 110), ('steps', '步数设置', 172),
                  ('schedule', '定时', 210))
_TIME_RE = re.compile(r'^(0?\d|1\d|2[0-3]):([0-5]\d|\d)$')


def _format_step(step_config):
    """把步数配置转成「步数设置」列显示文本"""
    if isinstance(step_config, (list, tuple)) and len(step_config) == 2:
        return f"随机 {step_config[0]}-{step_config[1]}"
    try:
        return f"固定 {int(step_config)}"
    except (TypeError, ValueError):
        return f"随机 {DEFAULT_RANGE_MIN}-{DEFAULT_RANGE_MAX}"


def _parse_step_value(mode, text):
    """按所选类型解析「步数设置」数值：
    「随机」填 「最小值-最大值」（如 10000-50000）；「固定」填单个整数（如 30000）。非法时抛 ValueError"""
    s = (text or '').strip().replace(' ', '')
    if not s:
        raise ValueError("请输入步数")
    s = s.replace('～', '-').replace('~', '-').replace('—', '-').replace('－', '-')
    if mode == '随机':
        if '-' not in s:
            raise ValueError("随机范围需填「最小值-最大值」，如 10000-50000")
        lo_s, hi_s = s.split('-', 1)
        lo, hi = int(lo_s), int(hi_s)
        if lo < 0 or hi < lo:
            raise ValueError("需满足 0 ≤ 最小值 ≤ 最大值")
        return [lo, hi]
    value = int(s)
    if value <= 0:
        raise ValueError("固定步数必须大于 0")
    return value


def _parse_times(text):
    """解析「定时」输入文本，返回 ['08:30', ...]；留空返回 []；非法时抛 ValueError"""
    raw = (text or '').strip()
    if not raw:
        return []
    result = []
    for part in re.split(r'[,，;；\s]+', raw):
        part = part.strip()
        if not part:
            continue
        m = _TIME_RE.match(part)
        if not m:
            raise ValueError(f"时间格式应为 HH:MM（如 21:30），收到「{part}」")
        normalized = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
        if normalized not in result:
            result.append(normalized)
    if not result:
        raise ValueError("请填写时间，如 21:30")
    return result


def _times_of(text):
    """宽松版解析：非法或为空时返回 []，供定时线程使用"""
    try:
        return _parse_times(text)
    except ValueError:
        return []


def _normalize_account(raw):
    """兼容旧版配置，把单条账号数据规范成统一结构"""
    step = raw.get('step_config')
    if isinstance(step, (list, tuple)) and len(step) == 2:
        try:
            step = [int(step[0]), int(step[1])]
        except (TypeError, ValueError):
            step = [DEFAULT_RANGE_MIN, DEFAULT_RANGE_MAX]
    else:
        try:
            step = int(step)
        except (TypeError, ValueError):
            step = [DEFAULT_RANGE_MIN, DEFAULT_RANGE_MAX]
    return {
        'account': str(raw.get('account') or ''),
        'password': str(raw.get('password') or ''),
        'step_config': step,
        'schedule': ','.join(_times_of(str(raw.get('schedule') or ''))),
        'enabled': bool(raw.get('enabled', True)),
    }


class _UiLogHandler(logging.Handler):
    """把日志转发到界面队列，由主线程消费后写入日志窗口（避免子线程直接操作控件）"""

    def __init__(self, ui_queue):
        super().__init__()
        self.ui_queue = ui_queue
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record):
        try:
            self.ui_queue.put(('log', (record.levelname, self.format(record))))
        except Exception:
            pass


class ZeppStepsApp:
    def __init__(self, parent=None):
        # 双模式：parent=None 独立运行（Tk + mainloop）；否则作为 Toplevel 嵌入主程序
        self._standalone = parent is None
        self.root = tk.Tk() if self._standalone else tk.Toplevel(parent)
        self.root.title("Zepp 运动刷步")
        self.root.geometry("880x690")
        self.root.minsize(820, 600)
        self.root.configure(bg=COLOR_BG)

        self._accounts = []             # [{"account","password","step_config","schedule","enabled"}]
        self._ui_queue = queue.Queue()  # 子线程 -> 主线程的消息队列
        self._running = False
        self._scheduler_on = False
        self._scheduler_stop = True
        self._sched_last = {}            # 定时任务当日去重：{账号|时间: 日期}
        self._last_toggle = (None, 0.0)  # 勾选框双击去重

        # 行内编辑状态
        self._editor = None              # 编辑容器 Frame
        self._eye_btn = None
        self._pwd_entry = None
        self._edit_iid = None
        self._edit_col = None
        self._edit_var = None
        self._step_mode_var = None
        self._sched_dlg = None

        self._setup_style()
        self._build_ui()

        # 日志同时写入界面日志窗口
        self._ui_log_handler = _UiLogHandler(self._ui_queue)
        logger.addHandler(self._ui_log_handler)

        self._load_accounts()
        self._refresh_tree()
        self._pump_ui()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        logger.info("========== Zepp 运动刷步界面已打开 ==========")
        if self._standalone:
            self.root.mainloop()

    # ---------- 样式（仅使用独立样式名，避免影响其它窗口） ----------
    def _setup_style(self):
        style = ttk.Style()
        style.configure("Zepp.Treeview", font=FONT_UI, rowheight=28,
                        background=COLOR_CARD, fieldbackground=COLOR_CARD,
                        foreground=COLOR_TEXT)
        style.configure("Zepp.Treeview.Heading", font=('微软雅黑', 10, 'bold'))
        style.map("Zepp.Treeview", background=[("selected", "#DCE9FF")],
                  foreground=[("selected", COLOR_TEXT)])
        style.configure("Zepp.TCombobox", font=FONT_UI, padding=1)

    # ---------- 通用控件 ----------
    def _card(self, title, desc=None):
        card = tk.Frame(self.root, bg=COLOR_CARD, highlightthickness=1,
                        highlightbackground=COLOR_BORDER)
        card.pack(fill='x', padx=16, pady=(10, 0))
        tk.Label(card, text=title, font=('微软雅黑', 11, 'bold'),
                 bg=COLOR_CARD, fg=COLOR_TEXT).pack(anchor='w', padx=12, pady=(8, 0))
        if desc:
            tk.Label(card, text=desc, font=('微软雅黑', 9), justify='left',
                     wraplength=760, bg=COLOR_CARD, fg=COLOR_SUB).pack(anchor='w', padx=12)
        body = tk.Frame(card, bg=COLOR_CARD)
        body.pack(fill='both', expand=True, padx=12, pady=(6, 10))
        return body

    def _btn(self, parent, text, command, width=None, primary=False):
        """描边按钮：外层 1px 边框容器 + 内部按钮，保证边框清晰可见"""
        border_color = COLOR_PRIMARY if primary else COLOR_BTN_BORDER
        bg = COLOR_PRIMARY if primary else COLOR_CARD
        fg = '#FFFFFF' if primary else COLOR_TEXT
        border = tk.Frame(parent, bg=border_color, highlightthickness=0)
        button = tk.Button(border, text=text, command=command, width=width,
                           font=FONT_UI, bg=bg, fg=fg,
                           activebackground=COLOR_PRIMARY_DARK if primary else '#E8EEF9',
                           activeforeground=fg, relief='flat', bd=0,
                           padx=10, pady=4, cursor='hand2')
        button.pack(padx=1, pady=1)
        border.button = button  # 便于外部控制状态
        return border

    # ---------- 界面搭建 ----------
    def _build_ui(self):
        tk.Label(self.root, text="Zepp 运动刷步", font=('微软雅黑', 15, 'bold'),
                 bg=COLOR_BG, fg=COLOR_TEXT).pack(anchor='w', padx=18, pady=(14, 0))
        tk.Label(self.root, text="支持 Zepp / 小米运动账号，多账号批量修改微信步数",
                 font=('微软雅黑', 9), bg=COLOR_BG, fg=COLOR_SUB).pack(anchor='w', padx=18)

        # ===== 1. 账号列表（行内编辑） =====
        body = self._card(
            "账号列表",
            "只有勾选了「✓」列的账号才会参与刷步和定时；双击单元格直接编辑，点「添加账号」新增一行后直接在行内填写。"
            "密码平时显示为掩码，编辑时点右侧眼睛可查看。"
            "「步数设置」先选类型（随机 / 固定）再填数值，「随机」填「最小值-最大值」。"
            "「定时」双击后弹出设置窗，可添加多个时间点（每个用上下箭头或鼠标滚轮调节），"
            "保存后列表会显示所有时间点，所有时间点都会独立触发刷步。")
        self.tree = ttk.Treeview(body, columns=_TREE_COLUMNS, show='headings',
                                 height=7, style='Zepp.Treeview')
        for col, text, width in _TREE_HEADINGS:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor='center')
        self.tree.tag_configure('odd', background=COLOR_ROW_ALT)
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<Button-1>', self._on_tree_click)
        self.tree.bind('<Double-1>', self._on_tree_double_click)
        self.tree.bind('<Return>', lambda e: self._edit_selected())
        self.tree.bind('<F2>', lambda e: self._edit_selected())
        self.tree.bind('<MouseWheel>', lambda e: self._commit_edit())

        bar = tk.Frame(body, bg=COLOR_CARD)
        bar.pack(side='right', fill='y', padx=(10, 0))
        self._btn(bar, "全选", self._select_all).pack(fill='x', pady=(0, 6))
        self._btn(bar, "取消全选", self._select_none).pack(fill='x', pady=(0, 6))
        self._btn(bar, "添加账号", self._add_account).pack(fill='x', pady=(0, 6))
        self._btn(bar, "删除选中", self._delete_account).pack(fill='x', pady=(0, 6))
        self._btn(bar, "清空列表", self._clear_accounts).pack(fill='x')

        # ===== 2. 操作 =====
        body = self._card("操作", "「开始刷步」只处理已勾选的账号；"
                                  "「启动定时」按已勾选账号的定时时间自动刷步（需保持本窗口打开）")
        row = tk.Frame(body, bg=COLOR_CARD)
        row.pack(fill='x')
        self.status_var = tk.StringVar(value="就绪")
        self.start_btn = self._btn(row, "开始刷步", self._start_run, width=10, primary=True)
        self.start_btn.pack(side='left')
        self.sched_btn = self._btn(row, "启动定时", self._start_schedule, width=10)
        self.sched_btn.pack(side='left', padx=(8, 0))
        self.sched_stop_btn = self._btn(row, "停止定时", self._stop_schedule, width=10)
        self.sched_stop_btn.pack(side='left', padx=(8, 0))
        self.sched_stop_btn.button.config(state='disabled')
        tk.Label(row, textvariable=self.status_var, font=FONT_UI,
                 bg=COLOR_CARD, fg=COLOR_SUB).pack(side='left', padx=(14, 0))

        # ===== 3. 运行日志 =====
        card = tk.Frame(self.root, bg=COLOR_CARD, highlightthickness=1,
                        highlightbackground=COLOR_BORDER)
        card.pack(fill='both', expand=True, padx=16, pady=(10, 14))
        tk.Label(card, text="运行日志", font=('微软雅黑', 11, 'bold'),
                 bg=COLOR_CARD, fg=COLOR_TEXT).pack(anchor='w', padx=12, pady=(8, 0))
        self.log_box = ScrolledText(card, height=10, font=('微软雅黑', 9),
                                    bg='#FBFCFE', fg=COLOR_TEXT, relief='solid',
                                    bd=1, wrap='word', state='disabled')
        self.log_box.pack(fill='both', expand=True, padx=12, pady=(6, 10))

    # ---------- 账号数据读写 ----------
    def _accounts_file(self):
        return os.path.join(_base_dir(), "zepp_accounts.json")

    def _load_accounts(self):
        try:
            with open(self._accounts_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("accounts", []) if isinstance(data, dict) else data
            self._accounts = [_normalize_account(a) for a in raw if isinstance(a, dict)]
            logger.info(f"已载入 {len(self._accounts)} 个账号")
        except FileNotFoundError:
            logger.info("未发现账号配置文件，从空列表开始")
        except Exception as e:
            logger.error(f"读取账号配置失败：{e}")

    def _save_accounts(self, silent=False):
        try:
            with open(self._accounts_file(), "w", encoding="utf-8") as f:
                json.dump({"accounts": self._accounts}, f, ensure_ascii=False, indent=2)
            if not silent:
                logger.info("账号列表已保存")
        except Exception as e:
            logger.error(f"保存账号配置失败：{e}")

    # ---------- 列表渲染 ----------
    def _refresh_tree(self):
        self._destroy_editor()
        self.tree.delete(*self.tree.get_children())
        for i, item in enumerate(self._accounts):
            tag = 'odd' if (i + 1) % 2 else 'even'
            self.tree.insert('', 'end', iid=str(i), tags=(tag,), values=(
                '☑' if item.get('enabled') else '☐',
                i + 1,
                item.get('account', ''),
                '******' if item.get('password') else '',
                _format_step(item.get('step_config')),
                ', '.join(_times_of(item.get('schedule', ''))) or '—'))

    @staticmethod
    def _cell_text(item, col):
        """取单元格的原始值（用于填充文本类编辑器）"""
        if col == 'account':
            return item.get('account', '')
        if col == 'password':
            return item.get('password', '')
        return ''

    # ---------- 行内编辑 ----------
    def _on_tree_click(self, event=None):
        self._commit_edit()
        if event is None:
            return
        col_id = self.tree.identify_column(event.x)
        iid = self.tree.identify_row(event.y)
        if col_id != '#1' or not iid:
            return
        now = time.time()
        key = (iid, col_id)
        if self._last_toggle[0] == key and now - self._last_toggle[1] < 0.35:
            return  # 双击产生的第二下点击，忽略
        self._last_toggle = (key, now)
        self._toggle_enabled(iid)

    def _toggle_enabled(self, iid):
        if not iid.isdigit() or int(iid) >= len(self._accounts):
            return
        item = self._accounts[int(iid)]
        item['enabled'] = not item.get('enabled')
        enabled = item['enabled']
        self.tree.set(iid, 'select', '☑' if enabled else '☐')
        self._save_accounts(silent=True)
        logger.info(f"{'已勾选' if enabled else '已取消勾选'}账号：{item.get('account') or '(未填写)'}")

    def _on_tree_double_click(self, event):
        self._commit_edit()
        iid = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not iid or not col_id:
            return
        index = int(col_id.replace('#', '')) - 1
        if not (0 <= index < len(_TREE_COLUMNS)):
            return
        col = _TREE_COLUMNS[index]
        if col in ('select', 'index'):
            return
        self._begin_edit(iid, col)

    def _edit_selected(self):
        sel = self.tree.selection()
        if sel:
            self._begin_edit(sel[0], 'account')

    def _select_all(self):
        self._commit_edit()
        for item in self._accounts:
            item['enabled'] = True
        self._save_accounts(silent=True)
        self._refresh_tree()
        logger.info(f"已全选 {len(self._accounts)} 个账号")

    def _select_none(self):
        self._commit_edit()
        for item in self._accounts:
            item['enabled'] = False
        self._save_accounts(silent=True)
        self._refresh_tree()
        logger.info("已取消全选")

    def _begin_edit(self, iid, col):
        self._commit_edit()
        if not iid.isdigit() or int(iid) >= len(self._accounts):
            return
        bbox = self.tree.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        item = self._accounts[int(iid)]
        self._edit_iid, self._edit_col = iid, col

        frame = tk.Frame(self.tree, bg=COLOR_CARD,
                         highlightthickness=1, highlightbackground=COLOR_PRIMARY)
        self._editor = frame
        if col == 'steps':
            self._build_steps_editor(frame, item)
        elif col == 'schedule':
            # 弹窗处理，不走行内编辑器，销毁刚创建的空 frame
            try:
                frame.destroy()
            except tk.TclError:
                pass
            self._editor = None
            self._open_schedule_dialog(iid)
            return
        else:
            self._edit_var = tk.StringVar(value=self._cell_text(item, col))
            self._build_text_editor(frame, col)
        frame.place(in_=self.tree, x=x, y=y, width=w - 1, height=h)

    def _mk_entry(self, parent, var, show=None):
        entry = tk.Entry(parent, textvariable=var, font=FONT_UI, bg=COLOR_FIELD,
                         fg=COLOR_TEXT, relief='flat', bd=0, insertbackground=COLOR_TEXT,
                         highlightthickness=0, show=show or '')
        entry.bind('<Return>', self._on_editor_return)
        entry.bind('<Escape>', self._on_editor_escape)
        entry.bind('<FocusOut>', self._on_editor_focus_out)
        return entry

    def _build_text_editor(self, frame, col):
        entry = self._mk_entry(frame, self._edit_var, '*' if col == 'password' else None)
        if col == 'password':
            self._pwd_entry = entry
            eye = tk.Button(frame, text='👁', command=self._toggle_pwd_visible,
                            font=FONT_SMALL, bg='#E8EEF9', fg=COLOR_TEXT,
                            activebackground='#DCE9FF', relief='flat', bd=0,
                            takefocus=0, cursor='hand2')
            eye.pack(side='right', fill='y')
            self._eye_btn = eye
        entry.pack(side='left', fill='both', expand=True, padx=2)
        entry.focus_set()
        try:
            entry.select_range(0, 'end')
        except tk.TclError:
            pass

    def _build_steps_editor(self, frame, item):
        """步数设置：左边下拉选「随机 / 固定」，右边由用户自行输入数值"""
        mode, value = self._split_step(item.get('step_config'))
        self._step_mode_var = tk.StringVar(value=mode)
        combo = ttk.Combobox(frame, textvariable=self._step_mode_var, values=list(STEP_MODES),
                             state='readonly', width=4, font=FONT_UI, style='Zepp.TCombobox')
        combo.pack(side='left', fill='y', padx=(2, 0))
        self._edit_var = tk.StringVar(value=value)
        entry = self._mk_entry(frame, self._edit_var)
        entry.pack(side='left', fill='both', expand=True, padx=3)

        def on_mode(_event=None):
            self._edit_var.set('10000-50000' if self._step_mode_var.get() == '随机' else '20000')
            entry.focus_set()
            try:
                entry.select_range(0, 'end')
            except tk.TclError:
                pass

        combo.bind('<<ComboboxSelected>>', on_mode)
        entry.focus_set()
        try:
            entry.select_range(0, 'end')
        except tk.TclError:
            pass

    @staticmethod
    def _split_step(step_config):
        """把步数配置拆成 (类型, 数值文本)"""
        if isinstance(step_config, (list, tuple)) and len(step_config) == 2:
            return '随机', f"{step_config[0]}-{step_config[1]}"
        return '固定', str(step_config)

    def _on_spin_wheel(self, event):
        try:
            event.widget.invoke('buttonup' if event.delta > 0 else 'buttondown')
        except tk.TclError:
            pass
        return 'break'

    def _build_schedule_row(self, parent, hh, mm, entries_ref):
        """在父容器里添加一行时间点编辑器（时、分 Spinbox + 删除按钮）。
        返回 (hour_var, min_var, row_frame)。"""
        row = tk.Frame(parent, bg=COLOR_BG)
        row.pack(fill='x', pady=3)

        h_var = tk.StringVar(value=hh)
        m_var = tk.StringVar(value=mm)

        hour = tk.Spinbox(row, from_=0, to=23, wrap=True, width=3, format='%02.0f',
                          textvariable=h_var, font=FONT_SMALL, justify='center',
                          bg=COLOR_FIELD, fg=COLOR_TEXT, relief='solid', bd=1,
                          buttonbackground='#E8EEF9')
        hour.pack(side='left', fill='y')
        tk.Label(row, text=':', font=FONT_SMALL, bg=COLOR_BG,
                 fg=COLOR_TEXT, padx=4).pack(side='left')
        minute = tk.Spinbox(row, from_=0, to=59, wrap=True, width=3, format='%02.0f',
                            textvariable=m_var, font=FONT_SMALL, justify='center',
                            bg=COLOR_FIELD, fg=COLOR_TEXT, relief='solid', bd=1,
                            buttonbackground='#E8EEF9')
        minute.pack(side='left', fill='y')

        for spin in (hour, minute):
            spin.bind('<MouseWheel>', self._on_spin_wheel)

        def _delete_row():
            row.destroy()
            entries_ref[:] = [e for e in entries_ref if e[2] is not row]

        del_btn = tk.Button(row, text='✕', command=_delete_row,
                            font=FONT_SMALL, bg='#FBE9E9', fg='#B91C1C',
                            activebackground='#F8D7D7', relief='flat', bd=0,
                            takefocus=0, cursor='hand2', width=3)
        del_btn.pack(side='left', padx=(6, 0))

        return (h_var, m_var, row)

    def _open_schedule_dialog(self, iid):
        """弹出定时时间编辑对话框：可添加/删除多个时间点，每个用 Spinbox 调节"""
        # 防止重复打开多个弹窗：已有弹窗存在时直接提到前台
        if self._sched_dlg is not None:
            try:
                if self._sched_dlg.winfo_exists():
                    self._sched_dlg.lift()
                    self._sched_dlg.focus_set()
                    return
            except tk.TclError:
                pass
        if not iid.isdigit() or int(iid) >= len(self._accounts):
            return
        item = self._accounts[int(iid)]

        dlg = tk.Toplevel(self.root)
        self._sched_dlg = dlg
        dlg.title("设置定时时间")
        dlg.geometry("440x400")
        dlg.minsize(380, 320)
        dlg.configure(bg=COLOR_BG)
        dlg.transient(self.root)
        try:
            dlg.grab_set()
        except tk.TclError:
            pass

        # 顶部标题区
        header = tk.Frame(dlg, bg=COLOR_BG)
        header.pack(fill='x', padx=16, pady=(14, 0))
        tk.Label(header, text=f"账号：{item.get('account') or '(未填写)'}",
                 font=('微软雅黑', 10, 'bold'), bg=COLOR_BG,
                 fg=COLOR_TEXT).pack(anchor='w')
        tk.Label(header, text="可添加多个时间点，每个用上下箭头或鼠标滚轮调节，"
                              "保存后所有时间点都会独立触发刷步",
                 font=('微软雅黑', 9), bg=COLOR_BG, fg=COLOR_SUB,
                 wraplength=400, justify='left').pack(anchor='w', pady=(2, 0))

        # 时间点列表（Canvas + Scrollbar，支持滚动）
        list_frame = tk.Frame(dlg, bg=COLOR_BG)
        list_frame.pack(fill='both', expand=True, padx=16, pady=(10, 0))
        canvas = tk.Canvas(list_frame, bg=COLOR_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(list_frame, orient='vertical', command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=COLOR_BG)

        scroll_frame.bind('<Configure>',
                          lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=scroll_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # 鼠标滚轮绑定到 Canvas
        def _on_canvas_wheel(event):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
            return 'break'
        canvas.bind('<MouseWheel>', _on_canvas_wheel)
        scroll_frame.bind('<MouseWheel>', _on_canvas_wheel)

        # 已有时间点 → 预填
        entries = []
        existing = _times_of(item.get('schedule', ''))
        for hhmm in existing:
            hh, mm = hhmm.split(':')
            entries.append(self._build_schedule_row(scroll_frame, hh, mm, entries))

        # 没有任何时间点时，默认给一行示例
        if not entries:
            entries.append(self._build_schedule_row(scroll_frame, '08', '30', entries))

        # 底部按钮区
        btn_frame = tk.Frame(dlg, bg=COLOR_BG)
        btn_frame.pack(fill='x', padx=16, pady=12)

        def _add_row():
            # 复用最后一行的值作为新行默认值，没有则用 08:30
            hh = entries[-1][0].get() if entries else '08'
            mm = entries[-1][1].get() if entries else '30'
            entries.append(self._build_schedule_row(scroll_frame, hh, mm, entries))
            scroll_frame.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox('all'))
            # 滚动到底部
            canvas.yview_moveto(1.0)

        def _on_save():
            nonlocal dlg
            # 收集所有有效时间点
            times = []
            for h_var, m_var, _row in entries:
                h_text = h_var.get().strip()
                m_text = m_var.get().strip()
                if not h_text and not m_text:
                    continue
                try:
                    h_i = int(h_text or 0)
                    m_i = int(m_text or 0)
                except ValueError:
                    messagebox.showwarning("提示", "时间必须是数字", parent=dlg)
                    return
                if not (0 <= h_i <= 23 and 0 <= m_i <= 59):
                    messagebox.showwarning("提示", f"时间需在 00:00 ~ 23:59 之间，收到 {h_i}:{m_i}", parent=dlg)
                    return
                times.append(f"{h_i:02d}:{m_i:02d}")
            # 去重 + 按时间排序
            seen = set()
            unique = []
            for t in sorted(times):
                if t not in seen:
                    seen.add(t)
                    unique.append(t)
            item['schedule'] = ','.join(unique)
            self._save_accounts(silent=True)
            self._refresh_tree()
            self._sched_dlg = None
            dlg.destroy()
            logger.info(f"已更新账号定时：{item.get('account') or '(未填写)'} → {unique}")

        def _on_cancel():
            self._sched_dlg = None
            dlg.destroy()

        def _on_close():
            self._sched_dlg = None
            try:
                dlg.destroy()
            except tk.TclError:
                pass

        dlg.protocol("WM_DELETE_WINDOW", _on_close)

        self._btn(btn_frame, "➕ 添加时间点", _add_row).pack(side='left')
        self._btn(btn_frame, "取消", _on_cancel).pack(side='right', padx=(8, 0))
        self._btn(btn_frame, "保存", _on_save, primary=True).pack(side='right')

        dlg.focus_set()
        try:
            entries[0][2].focus_set()
        except (IndexError, tk.TclError):
            pass

    def _on_editor_return(self, event=None):
        self._commit_edit()
        return 'break'

    def _on_editor_escape(self, event=None):
        self._cancel_edit()
        return 'break'

    def _on_editor_focus_out(self, event=None):
        self.root.after(120, self._commit_if_focus_lost)

    def _is_editor_widget(self, widget):
        node = widget
        while node is not None:
            if node is self._editor or node is self._eye_btn:
                return True
            node = getattr(node, 'master', None)
        return False

    def _commit_if_focus_lost(self):
        if self._editor is None:
            return
        try:
            focused = self.root.focus_get()
        except (KeyError, tk.TclError):
            focused = None
        # 焦点不知去向 / 仍在编辑器内 / 下拉列表展开中，都不提交
        if focused is None or self._is_editor_widget(focused) or 'popdown' in str(focused):
            return
        self._commit_edit()

    def _toggle_pwd_visible(self):
        if self._pwd_entry is None:
            return
        try:
            now_hidden = bool(self._pwd_entry.cget('show'))
            self._pwd_entry.config(show='' if now_hidden else '*')
            self._eye_btn.config(text='🙈' if now_hidden else '👁')
            self._pwd_entry.focus_set()
        except tk.TclError:
            pass

    def _commit_edit(self, event=None):
        if self._editor is None:
            return
        iid, col = self._edit_iid, self._edit_col
        text = self._edit_var.get().strip() if self._edit_var is not None else ''
        mode = self._step_mode_var.get() if self._step_mode_var is not None else '随机'
        self._destroy_editor()
        if not iid.isdigit() or int(iid) >= len(self._accounts):
            self._refresh_tree()
            return
        item = self._accounts[int(iid)]

        if col == 'account':
            if not text:
                if not item.get('account'):
                    # 新增的空行未填写账号 -> 视为放弃添加
                    self._accounts.pop(int(iid))
                    self._save_accounts()
                    self._refresh_tree()
                    logger.info("已取消该空行（账号未填写）")
                    return
                text = item.get('account', '')
            item['account'] = text
        elif col == 'password':
            item['password'] = text
        elif col == 'steps':
            try:
                item['step_config'] = _parse_step_value(mode, text)
            except ValueError as e:
                messagebox.showwarning("提示", f"步数设置无效：{e}")
                self._refresh_tree()
                return
        # schedule 列现在走弹窗，不再在行内编辑器提交
        self._save_accounts()
        self._refresh_tree()

    def _cancel_edit(self):
        self._destroy_editor()

    def _destroy_editor(self):
        for widget in (self._eye_btn, self._editor):
            if widget is not None:
                try:
                    widget.destroy()
                except tk.TclError:
                    pass
        self._eye_btn = None
        self._editor = None
        self._pwd_entry = None
        self._edit_iid = None
        self._edit_col = None
        self._edit_var = None
        self._step_mode_var = None

    # ---------- 账号增删 ----------
    def _add_account(self):
        self._commit_edit()
        self._accounts.append({'account': '', 'password': '',
                               'step_config': [DEFAULT_RANGE_MIN, DEFAULT_RANGE_MAX],
                               'schedule': '', 'enabled': True})
        self._refresh_tree()
        iid = str(len(self._accounts) - 1)
        self.tree.selection_set(iid)
        self.tree.see(iid)
        self.tree.update_idletasks()
        self._begin_edit(iid, 'account')
        logger.info("已新增一行，请直接在行内填写账号")

    def _delete_account(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选择一个账号")
            return
        self._commit_edit()
        indexes = sorted((int(i) for i in sel if i.isdigit()), reverse=True)
        names = [self._accounts[i].get('account') or '(未填写)' for i in indexes
                 if 0 <= i < len(self._accounts)]
        if not names:
            return
        if not messagebox.askyesno("确认", f"确定删除以下账号吗？\n{'、'.join(names)}"):
            return
        for i in indexes:
            if 0 <= i < len(self._accounts):
                self._accounts.pop(i)
        self._save_accounts()
        self._refresh_tree()
        logger.info(f"已删除账号：{'、'.join(names)}")

    def _clear_accounts(self):
        if not self._accounts:
            return
        if not messagebox.askyesno("确认", "确定清空全部账号吗？"):
            return
        self._destroy_editor()
        self._accounts = []
        self._save_accounts()
        self._refresh_tree()
        logger.info("已清空账号列表")

    # ---------- 刷步 ----------
    @staticmethod
    def _resolve_step(account, step_config):
        if isinstance(step_config, (list, tuple)) and len(step_config) == 2:
            step = random.randint(int(step_config[0]), int(step_config[1]))
            kind = '随机范围'
        else:
            step = int(step_config)
            kind = '固定'
        logger.info(f"账号:{account} 步数类型:{kind} 本次最终步数:{step}")
        return step

    def _selected_accounts(self):
        """当前勾选的账号"""
        return [a for a in self._accounts if a.get('enabled')]

    def _start_run(self, accounts=None, reason='手动'):
        if self._running:
            if reason != '手动':
                logger.warning(f"定时任务（{reason}）触发，但当前正在刷步，本次跳过")
            return
        self._commit_edit()
        source = self._selected_accounts() if accounts is None else accounts
        todo = [a for a in source if a.get('account') and a.get('password')]
        if not todo:
            if reason == '手动':
                if not source:
                    messagebox.showwarning("提示", "请先勾选需要刷步的账号")
                else:
                    messagebox.showwarning("提示", "已勾选的账号还需填写账号与密码")
            else:
                logger.warning(f"定时任务（{reason}）触发，但没有可用的账号")
            return
        self._save_accounts()

        self._running = True
        self.start_btn.button.config(state='disabled')
        self.status_var.set(f"正在刷步（共 {len(todo)} 个账号）...")
        logger.info(f"========== 开始刷步（{reason}），共 {len(todo)} 个账号 ==========")
        threading.Thread(target=self._worker_run, args=(todo,), daemon=True).start()

    def _worker_run(self, accounts):
        """子线程：只写日志与队列，不直接操作任何 Tk 控件"""
        success = 0
        total = len(accounts)
        try:
            for index, item in enumerate(accounts, 1):
                account = item.get('account', '')
                password = item.get('password', '')
                self._ui_queue.put(('status', f"正在处理 {index}/{total}：{account}"))
                logger.info(f"[{index}/{total}] 开始处理账号：{account}")

                step = self._resolve_step(account, item.get('step_config'))

                login_token, userid = login(account, password)
                if not login_token:
                    logger.error(f"账号:{account} 登录失败，跳过")
                    continue
                app_token = get_app_token(login_token)
                if not app_token:
                    logger.error(f"账号:{account} 获取 app_token 失败，跳过")
                    continue
                if change_steps(account, userid, app_token, str(step)):
                    success += 1
                    logger.info(f"账号:{account} 步数修改成功 {step}")
                else:
                    logger.error(f"账号:{account} 步数修改失败")
        except Exception as e:
            logger.error(f"刷步过程异常：{e}")
        finally:
            # 显式通知主线程收尾，重置按钮状态
            self._ui_queue.put(('done', (success, total)))

    def _on_run_finished(self, success, total):
        self._running = False
        self.start_btn.button.config(state='normal')
        if self._scheduler_on:
            self.status_var.set(f"已完成：成功 {success}/{total}（定时任务运行中）")
        else:
            self.status_var.set(f"已完成：成功 {success}/{total}")
        logger.info(f"========== 刷步结束，成功 {success}/{total} 个账号 ==========")

    # ---------- 定时任务 ----------
    def _start_schedule(self):
        self._commit_edit()
        if self._scheduler_on:
            logger.info("定时任务已在运行中")
            return
        checked = self._selected_accounts()
        if not checked:
            messagebox.showwarning("提示", "请先勾选需要定时的账号")
            return
        timed = [a for a in checked if _times_of(a.get('schedule', ''))]
        no_time = [a.get('account') or '(未填写)' for a in checked
                   if not _times_of(a.get('schedule', ''))]
        if not timed:
            messagebox.showwarning("提示", "所勾选的账号都还没有设置定时时间，请先在「定时」列设置时间")
            return
        if no_time:
            if not messagebox.askyesno(
                    "提示",
                    f"以下已勾选账号没有设置定时时间，将被忽略：\n{'、'.join(no_time)}\n\n是否继续启动定时任务？"):
                return
        self._scheduler_on = True
        self._scheduler_stop = False
        self.sched_btn.button.config(state='disabled')
        self.sched_stop_btn.button.config(state='normal')
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        detail = '、'.join(f"{a.get('account')}({a.get('schedule')})" for a in timed)
        self.status_var.set(f"定时任务运行中（{len(timed)} 个账号）")
        logger.info(f"========== 定时任务已启动：{detail} ==========")

    def _stop_schedule(self):
        if not self._scheduler_on:
            return
        self._scheduler_stop = True
        self._scheduler_on = False
        self.sched_btn.button.config(state='normal')
        self.sched_stop_btn.button.config(state='disabled')
        self.status_var.set("定时任务已停止")
        logger.info("========== 定时任务已停止 ==========")

    def _scheduler_loop(self):
        while not self._scheduler_stop:
            try:
                now = datetime.now()
                hhmm = now.strftime('%H:%M')
                today = now.strftime('%Y-%m-%d')
                due = []
                for item in list(self._accounts):
                    account = item.get('account', '')
                    if not item.get('enabled') or not account or not item.get('password'):
                        continue
                    if hhmm not in _times_of(item.get('schedule', '')):
                        continue
                    key = f"{account}|{hhmm}"
                    if self._sched_last.get(key) != today:
                        self._sched_last[key] = today
                        due.append(item)
                if due:
                    self._ui_queue.put(('scheduled', (due, hhmm)))
            except Exception as e:
                logger.error(f"定时任务检查异常：{e}")
            time.sleep(5)

    def _on_scheduled_trigger(self, due, hhmm):
        if not self._scheduler_on:
            return
        names = '、'.join(a.get('account', '') for a in due)
        if self._running:
            logger.warning(f"定时任务 {hhmm} 触发，但当前正在刷步，本次跳过（账号：{names}）")
            return
        logger.info(f"定时任务 {hhmm} 触发，账号：{names}")
        self._start_run(due, reason=f"定时 {hhmm}")

    # ---------- 主线程 UI 泵 ----------
    def _pump_ui(self):
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == 'log':
                    level, text = payload
                    self._append_log(level, text)
                elif kind == 'status':
                    self.status_var.set(payload)
                elif kind == 'done':
                    self._on_run_finished(*payload)
                elif kind == 'scheduled':
                    self._on_scheduled_trigger(*payload)
        except queue.Empty:
            pass
        try:
            self.root.after(120, self._pump_ui)
        except tk.TclError:
            pass

    def _append_log(self, level, text):
        stamp = datetime.now().strftime('%H:%M:%S')
        line = f"{stamp} [{level}] {text}\n"
        self.log_box.config(state='normal')
        self.log_box.insert('end', line)
        # 只保留最近 500 行，避免长时间运行占用内存
        if int(self.log_box.index('end-1c').split('.')[0]) > 500:
            self.log_box.delete('1.0', '100.0')
        self.log_box.see('end')
        self.log_box.config(state='disabled')

    def _on_close(self):
        if self._running and not messagebox.askyesno("确认", "正在刷步中，确定关闭窗口吗？"):
            return
        self._scheduler_stop = True
        self._destroy_editor()
        try:
            logger.removeHandler(self._ui_log_handler)
        except Exception:
            pass
        logger.info("========== Zepp 运动刷步界面已关闭 ==========")
        self.root.destroy()


def create_zepp_steps_app(parent=None):
    """入口：parent=None 独立运行，否则作为子窗口嵌入主程序"""
    return ZeppStepsApp(parent)


# 主程序（独立运行）
if __name__ == "__main__":
    create_zepp_steps_app()
