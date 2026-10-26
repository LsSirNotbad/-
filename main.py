from tkinter import Label
from datetime import datetime
import win32con, win32api
import tkinter as tk
import threading
import logging
import random
import socket
import sys
import re
import os
import json
import importlib
import subprocess
import api_interface_call
import api_key_get
from consts import CODE_AREA_DICT, BOY_IDIOMS, GIRL_IDIOMS, CONSTELLATION_IMAGES, FORTUNE_CONFIG

class _LazyModule:
    """懒加载模块代理，第一次访问属性时才真正 import"""
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


# 应用基准目录：PyInstaller 打包后为 exe 所在目录，开发时为脚本所在目录
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# 确保log文件夹存在：log/<年月日>/（与其他模块保持一致的日志目录结构）
# 打包后日志写在 exe 所在目录的 log/ 下（而不是 _internal 内部目录，方便查看）
log_dir = os.path.join(APP_DIR, 'log', datetime.now().strftime('%Y%m%d'))
os.makedirs(log_dir, exist_ok=True)  # 不存在则创建
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
        log_dir = os.path.join(APP_DIR, 'log', today)
        os.makedirs(log_dir, exist_ok=True)
        self._handler = logging.FileHandler(
            os.path.join(log_dir, f"{self._module_name}.log"), encoding='utf-8')
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


# 日志配置（UTF-8 + 按天自动滚动，避免中文写入 Windows 默认编码报错 / 乱码）
logging.getLogger('PIL.Image').setLevel(logging.INFO)  # 将日志级别设置为 INFO，可根据需要调整
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.DEBUG)
_fh = _DailyFileHandler("main")
_fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
_root_logger.addHandler(_fh)
logger = logging.getLogger('my_logger')

# ---------------- 轻度美化：统一 UI 风格常量与控件工厂 ----------------
# 外观（配色主题）：仅「白天 / 夜晚」两种，可在「设置 → 外观设置」中切换，启动时从配置读取
APPEARANCE_THEMES = {
    "白天": {"bg": "#F5F7FB", "fg": "#333333", "card": "#FFFFFF",
             "btn": "#FFFFFF", "active": "#E8EEF9",
             "field": "#EEF3FB", "border": "#B9C8E4", "btnborder": "#9DB2D4"},
    "夜晚": {"bg": "#2B2B2B", "fg": "#E8E8E8", "card": "#3A3A3A",
             "btn": "#3A3A3A", "active": "#4A4A4A",
             "field": "#232323", "border": "#5A5A5A", "btnborder": "#6E6E6E"},
}

UI_BG = '#F5F7FB'          # 窗口/标签统一浅色背景
UI_FG = '#333333'          # 标签默认文字色
UI_CARD = '#FFFFFF'        # 卡片/分区底色
UI_BTN_BG = '#FFFFFF'      # 按钮底色
UI_BTN_ACTIVE = '#E8EEF9'  # 按钮按下时的底色
UI_BTN_BORDER = '#9DB2D4'  # 按钮边框色
UI_FIELD = '#EEF3FB'       # 输入框底色（与卡片区分，便于识别）
UI_BORDER = '#B9C8E4'      # 输入框/卡片边框色


def _apply_appearance(cfg):
    """根据配置应用配色主题（设置全局常量，供 L()/B() 及窗口背景使用）"""
    global UI_BG, UI_FG, UI_CARD, UI_BTN_BG, UI_BTN_ACTIVE, UI_BTN_BORDER, UI_FIELD, UI_BORDER
    appearance = cfg.get("appearance", {}) if isinstance(cfg, dict) else {}
    theme = APPEARANCE_THEMES.get(appearance.get("theme", "白天"), APPEARANCE_THEMES["白天"])
    UI_BG = theme["bg"]
    UI_FG = theme["fg"]
    UI_CARD = theme["card"]
    UI_BTN_BG = theme["btn"]
    UI_BTN_ACTIVE = theme["active"]
    UI_BTN_BORDER = theme["btnborder"]
    UI_FIELD = theme["field"]
    UI_BORDER = theme["border"]


# 界面风格统一
def L(master=None, **kwargs):
    """统一风格的 Label：默认浅色背景 + 默认文字色（显式传入的 fg 优先）"""
    if master is None:
        master = window
    kwargs.setdefault('bg', UI_BG)
    kwargs.setdefault('fg', UI_FG)
    return tk.Label(master, **kwargs)


# 界面风格统一
def B(master=None, **kwargs):
    """统一风格的 Button：白底、扁平、手型光标（尺寸/文字/颜色沿用调用处）"""
    if master is None:
        master = window
    kwargs.setdefault('bg', UI_BTN_BG)
    kwargs.setdefault('activebackground', UI_BTN_ACTIVE)
    kwargs.setdefault('relief', 'flat')
    kwargs.setdefault('bd', 1)
    kwargs.setdefault('cursor', 'hand2')
    return tk.Button(master, **kwargs)


# 全局变量
global_var = ""


# 检查身份证号码格式
def check_id_number_format(id_number):
    # 取身份证号前十七位，判断是否有非数字字符
    first_17_digits = id_number[0:17]
    alphabets = len(re.findall(r'[a-zA-Z]', first_17_digits))
    # 判断身份证号是否为18位，且无中文字符和空格
    total_chars = len(id_number)
    chinese_count = len(re.findall(r'[\u4e00-\u9fff]+', id_number))
    non_chinese_count = id_number.count(' ')
    if total_chars == 18 and chinese_count == 0 and non_chinese_count == 0 and alphabets == 0:
        return True
    return False


# 检查出生日期是否合法
def check_birth_date(id_number):
    birth_year = id_number[6:10]
    birth_month = id_number[10:12]
    birth_day = id_number[12:14]
    birth_date_str = id_number[6:14]
    current_date_str = datetime.now().strftime('%Y%m%d')
    if int(birth_date_str) > int(current_date_str):
        show_error_message(f"出生年月日{birth_date_str}不能大于当前时间{current_date_str}")
        return False
    if birth_month > '12' or birth_month < '01':
        show_error_message("出生月份不能大于12月或者小于1月")
        return False
    if birth_month in ['01', '03', '05', '07', '08', '10', '12']:
        if birth_day > '31' or birth_day < '01':
            show_error_message(f"{birth_month}月不能大于31号或者小于1号")
            return False
    elif birth_month == '02':
        leap_year = int(birth_year)
        if (leap_year % 4) == 0:
            if (leap_year % 100) == 0:
                if (leap_year % 400) == 0:
                    if birth_day > '29' or birth_day < '01':
                        show_error_message(f"{birth_year}年{birth_month}月不能大于29号或者小于1号")
                        return False
                else:
                    if birth_day > '28' or birth_day < '01':
                        show_error_message(f"{birth_year}年{birth_month}月不能大于28号或者小于1号")
                        return False
            else:
                if birth_day > '29' or birth_day < '01':
                    show_error_message(f"{birth_year}年{birth_month}月不能大于29号或者小于1号")
                    return False
        else:
            if birth_day > '28' or birth_day < '01':
                show_error_message(f"{birth_year}年{birth_month}月不能大于28号或者小于1号")
                return False
    else:
        if birth_day > '30' or birth_day < '01':
            show_error_message(f"{birth_year}年{birth_month}月不能大于30号或者小于1号")
            return False
    return True


# 验证身份证号码的校验位
def verify_id_number(id_number):
    sum = 0
    for index, item in enumerate(id_number[:-1]):
        sum += 2 ** (17 - index) % 11 * int(item)
    num = (12 - sum % 11) % 11
    if num < 10:
        if id_number[-1] == str(num):
            show_verification_succeed()
            return True
        else:
            show_verification_error(f"校验失败，正确尾号应为：{num}")
            return False
    else:
        if id_number[-1] in ['X', 'x']:
            show_verification_succeed()
            return True
        else:
            show_verification_error("校验失败，正确尾号应为：X")
            return False


# 显示校验错误信息
def show_verification_error(message):
    label_verify = L(text=message, fg='red', font=('宋体', 8))
    label_verify.place(x=250, y=70)


# 显示校验正确信息
def show_verification_succeed():
    label_verify = L(text='                                         ', fg='red', font=('宋体', 8))
    label_verify.place(x=250, y=70)


# 显示错误消息框
def show_error_message(message):
    win32api.MessageBox(0, message, "错误", win32con.MB_SYSTEMMODAL)


# 处理身份证号码输入
def handle_id_number_input():
    id_number = input_box_number.get()
    logger.debug('身份证号：' + id_number)
    if not check_id_number_format(id_number):
        show_error_message("身份证号码输入格式错误")
    elif not check_birth_date(id_number):
        pass
    elif verify_id_number(id_number):
        show_id_info(id_number)
    # 清除不必要的控件
    clear_unnecessary_widgets()


# 清除不必要的控件
def clear_unnecessary_widgets():
    if 'combo_box' in globals() and combo_box.winfo_exists():
        combo_box.pack_forget()
    if 'txt_type' in globals() and txt_type.winfo_exists():
        txt_type.destroy()
    if 'Fortune_button' in globals() and Fortune_button.winfo_exists():
        Fortune_button.destroy()


# 显示身份证信息
def show_id_info(id_number):
    # 年份
    birth_year = id_number[6:10]
    # 月份
    birth_month = id_number[10:12]
    # 天数
    birth_day = id_number[12:14]
    # 性别
    gender_digit = id_number[16:17]
    gender = '男' if int(gender_digit) % 2 == 1 else '女'
    birth_date = datetime.strptime(f'{birth_year}{birth_month}{birth_day}', '%Y%m%d')
    age = datetime.now().year - birth_date.year
    if datetime.now().month < birth_date.month or (
            datetime.now().month == birth_date.month and datetime.now().day < birth_date.day):
        age -= 1
    idiom_dict = BOY_IDIOMS if gender == '男' else GIRL_IDIOMS
    idioms = get_random_idioms(idiom_dict)
    # 显示出生年月日
    show_text(YY_outcome, birth_year)
    show_text(MM_outcome, birth_month)
    show_text(DD_outcome, birth_day)
    # 显示性别
    show_text(gender_outcome, gender)
    # 日志保存
    logger.info('性别：' + gender)
    # 显示年龄
    show_text(age_outcome, f'{age}岁')
    # 日志保存
    logger.info('年龄：' + f'{age}岁')
    # 显示形容词语
    show_text(nature_outcome, idioms)
    # 显示生肖图片
    show_zodiac_by_year(id_number)
    # 显示星座图片（与生肖图片并排同时显示，无需再点按钮）
    show_constellation_image(get_constellation(id_number[10:14]))
    # 显示星座查询提示
    show_constellation_prompt()
    # 显示户籍所在地
    show_household_info(id_number)


# 显示文本信息
def show_text(text_widget, text):
    text_widget.delete('1.0', tk.END)
    text_widget.insert(tk.END, text)
    text_widget.tag_add('tag1', '1.0', '1.255')
    text_widget.tag_config('tag1', justify="center")


# 生肖图片处理
def resource_path(relative_path):
    """ 获取资源的绝对路径，适用于开发环境和打包后的环境 """
    try:
        # PyInstaller 创建一个临时文件夹并将路径存储在 _MEIPASS 中
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


# 图标文件路径：依次查找打包内嵌目录(_MEIPASS)、exe/脚本所在目录的根级与 image/ 子目录
def icon_path(filename):
    base_dirs = []
    try:
        base_dirs.append(sys._MEIPASS)  # PyInstaller 打包内嵌资源目录（onedir 为 _internal）
    except AttributeError:
        pass
    if APP_DIR not in base_dirs:
        base_dirs.append(APP_DIR)  # exe 所在目录（打包后）或脚本所在目录（开发时）
    for base in base_dirs:
        for candidate in (os.path.join(base, filename), os.path.join(base, "image", filename)):
            if os.path.exists(candidate):
                return candidate
    return os.path.join(base_dirs[-1], "image", filename)


# 判断生肖
def show_zodiac_by_year(id_number):
    from PIL import Image, ImageTk
    # 十二生肖 JPEG 图片文件名列表，需替换为实际文件名
    zodiac_images = [
        icon_path("rat.webp"),
        icon_path("ox.webp"),
        icon_path("tiger.webp"),
        icon_path("rabbit.webp"),
        icon_path("dragon.webp"),
        icon_path("snake.webp"),
        icon_path("horse.webp"),
        icon_path("goat.webp"),
        icon_path("monkey.webp"),
        icon_path("rooster.webp"),
        icon_path("dog.webp"),
        icon_path("pig.webp")
    ]
    try:
        # 年份
        birth_year = id_number[6:10]
        # 计算生肖索引
        index = (int(birth_year) - 1900) % 12
        zodiac_image = zodiac_images[index]
        # 打开 JPEG 图片
        image = Image.open(zodiac_image)
        # 缩小图片
        resized_image = image.resize((35, 35), Image.LANCZOS)
        photo = ImageTk.PhotoImage(resized_image)
        image_label.config(image=photo)
        image_label.image = photo
        # 使用 place 方法设置图片位置
        image_label.place(x=405, y=0)
        logger.info('生肖图片：' + zodiac_images[index])
    except FileNotFoundError:
        show_error_message(f"生肖图片不见了: {zodiac_images[index]}")
    except Exception as e:
        show_error_message(f"发生错误: {e}")


# 显示星座图片（与生肖图片格式一致、位置一致，共用 image_label）
def show_constellation_image(constellation_name):
    from PIL import Image, ImageTk
    try:
        filename = CONSTELLATION_IMAGES.get(constellation_name)
        if not filename:
            return
        image = Image.open(icon_path(f"{filename}.webp"))
        resized_image = image.resize((35, 35), Image.LANCZOS)
        photo = ImageTk.PhotoImage(resized_image)
        constellation_image_label.config(image=photo)
        constellation_image_label.image = photo
        # 与生肖图片并排显示（生肖 x=405，星座 x=450，同一排 y=0）
        constellation_image_label.place(x=450, y=0)
        logger.info('星座图片：' + constellation_name)
    except FileNotFoundError:
        show_error_message(f"星座图片不见了: {constellation_name}")
    except Exception as e:
        show_error_message(f"发生错误: {e}")


# 获取随机成语
def get_random_idioms(idiom_dict):
    num_samples = 10
    if num_samples > len(idiom_dict):
        raise ValueError("选择的样本数量大于字典中的元素数量")
    random_keys = random.sample(list(idiom_dict.keys()), num_samples)
    idioms = [idiom_dict[key] for key in random_keys]
    return '、'.join(sorted(idioms))


# 显示星座查询提示
def show_constellation_prompt():
    global type_button
    txt_Horoscope = L(text='您可以查询星座运势哦，点击👉👉👉', fg='#4169E1', font=('仿宋-Bold', 16))
    txt_Horoscope.place(x=38, y=275)
    type_button = B(text='选择类型', width=10, height=1, fg='#4169E1', command=constellation)
    type_button.place(x=380, y=275)


# 显示户籍所在地信息
def show_household_info(id_number):
    household_code = id_number[0:6]
    logger.info('户籍所在地编号：' + household_code)
    if household_code in CODE_AREA_DICT:
        household = CODE_AREA_DICT[household_code]
        show_text(Household_outcome, household)
        logger.info('户籍所在地：' + household)
    else:
        show_error_message("没有该代码地区，请确认")


# 获取本地IP地址
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        return str(e)


# 获取本地公网IP地址
def get_public_ip():
    """获取本地电脑的公网IP"""
    # 第三方IP查询接口（逐个尝试，任一成功即返回）
    api_urls = [
        "https://api.ipify.org",
        "https://httpbin.org/ip",
        "https://myip.ipip.net"
    ]

    for url in api_urls:
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            text = resp.text
            # httpbin 返回 json，取 origin 字段
            if "httpbin" in url:
                ip = resp.json()["origin"]
            else:
                # ipify 返回纯 IP；myip.ipip.net 返回“当前 IP：x.x.x.x 来自于：...”，用正则取出 IP
                match = re.search(r'(\d{1,3}(?:\.\d{1,3}){3})', text)
                ip = match.group(1) if match else text.strip()
            logger.info(f"公网IP获取成功（{url}）：{ip}")
            return ip
        except Exception as e:
            # 一个接口失败，继续尝试下一个
            logger.warning(f"公网IP接口失败（{url}）：{e}")
            continue
    logger.error("所有公网IP接口均失败")
    return "获取公网IP失败，请检查网络或重试"


# 获取IP归属地
def get_ip_address(ip):
    apiUrl = 'https://whyta.cn/api/tx/ipquery'
    apiKey = api_key_get.load_key()  # 密钥统一从 api_key_get 模块读取
    requestParams = {
        'key': apiKey,
        'ip': ip
    }
    try:
        response = requests.get(apiUrl, params=requestParams, timeout=5)
        logger.info(f"IP归属地接口响应状态码：{response.status_code}（ip={ip}）")
        if response.status_code == 200:
            result = response.json().get('result') or {}
            country = result.get('country', '')
            province = result.get('province', '')
            city = result.get('city', '')
            district = result.get('district', '')
            isp = result.get('isp', '')
            if not any([country, province, city, district, isp]):
                logger.error(f"IP归属地接口返回空数据：{response.text}")
                return "获取归属地错误"
            return country + province + city + district + "\n" + '运营商:' + isp
        logger.error(f"IP归属地接口异常状态码：{response.status_code}")
        return "请求数据错误"
    except Exception as e:
        logger.error(f"获取IP归属地失败：{e}")
        return "获取归属地错误"


#
def load_ip_info_async(label_local, label_public, label_addr):
    """后台线程查 IP，查完通过 window.after 回主线程更新 Label"""
    def worker():
        try:
            local_ip = get_local_ip()
        except Exception as e:
            logger.error(f"获取本地IP失败：{e}")
            local_ip = "获取失败"

        try:
            public_ip = get_public_ip()
        except Exception as e:
            logger.error(f"获取公网IP失败：{e}")
            public_ip = "获取失败"

        # 只有拿到形如 IP 的字符串才继续查归属地（避免把错误提示当 IP 传给接口）
        if re.match(r'^\d{1,3}(?:\.\d{1,3}){3}$', public_ip or ''):
            try:
                ip_address = get_ip_address(public_ip)
            except Exception as e:
                logger.error(f"获取IP归属地失败：{e}")
                ip_address = "获取失败"
        else:
            logger.warning(f"未取得有效公网IP（{public_ip}），跳过归属地查询")
            ip_address = "获取失败"

        logger.info(f"IP信息：本地={local_ip}，公网={public_ip}，归属地={ip_address}")

        # 回到主线程更新 UI（Tk 不允许子线程直接操作控件）
        def update():
            label_local.config(text=local_ip)
            label_public.config(text=public_ip)
            label_addr.config(text=ip_address)

        window.after(0, update)

    threading.Thread(target=worker, daemon=True).start()


# base64工具
def open_b64():
    from b64_gui import B64Window
    B64Window(window)


# office工具
def open_office():
    from office_tools import OfficeToolsWindow
    OfficeToolsWindow(window)


# 文件修改工具
def open_filetime():
    from file_time_tool import FileTimeTool
    FileTimeTool(window)


'''# 步数提交工具
def Step_Submission():
    from step_tool import StepTool
    StepTool(window)'''


# Zepp 运动刷步（Zepp / 小米运动账号，多账号批量修改微信步数）
def Zepp_Steps():
    from zepp_Steps import create_zepp_steps_app
    create_zepp_steps_app(window)


# 酷狗定时开关工具
def KuGou_scheduled():
    from kugou_auto_restart import create_kugou_tool
    create_kugou_tool(window)


# windows配置工具
def windows_config():
    import windows_config_gui
    windows_config_gui.create_config_app(window)


# 处理星座选择
def constellation():
    import tkinter.ttk as ttk
    global combo_box, txt_type, Fortune_button
    id_number = input_box_number.get()
    birth_month_day = id_number[10:14]
    constellation_name = get_constellation(birth_month_day)
    # 显示对应的星座图片（与生肖图片位置一致，按当前操作切换显示）
    show_constellation_image(constellation_name)

    def dropdown_changed(event):
        selected_type = combo_box.get()
        selected_type = re.sub('\\(.*?\\)', '', selected_type)
        global global_var,Fortune_button
        if 'Fortune_button' in globals() and Fortune_button.winfo_exists():
            Fortune_button.destroy()
        global_var = selected_type
        txt_type.destroy()
        Fortune_button = B(text='查询运势', width=10, height=1, fg='#4169E1', command=get_Fortune)
        Fortune_button.place(x=178, y=2)

    combo_box = ttk.Combobox(window)
    combo_box["values"] = ('today(今日)', 'tomorrow(明日)', 'week(本周)', 'month(本月)', 'year(本年)')
    combo_box.bind("<<ComboboxSelected>>", dropdown_changed)
    combo_box.pack(anchor='sw')

    txt_type = L(text='👈👈👈先在这里选择类型哦', fg='blue', font=('宋体', 13))
    txt_type.place(x=178, y=2)
    type_button.config(state="disabled")  # 禁用按钮


# 获取星座名称
def get_constellation(birth_month_day):
    if '0120' <= birth_month_day <= '0218':
        return '水瓶座'
    elif '0219' <= birth_month_day <= '0320':
        return '双鱼座'
    elif '0321' <= birth_month_day <= '0419':
        return '白羊座'
    elif '0420' <= birth_month_day <= '0520':
        return '金牛座'
    elif '0521' <= birth_month_day <= '0621':
        return '双子座'
    elif '0622' <= birth_month_day <= '0722':
        return '巨蟹座'
    elif '0723' <= birth_month_day <= '0822':
        return '狮子座'
    elif '0823' <= birth_month_day <= '0922':
        return '处女座'
    elif '0923' <= birth_month_day <= '1023':
        return '天秤座'
    elif '1024' <= birth_month_day <= '1122':
        return '天蝎座'
    elif '1123' <= birth_month_day <= '1221':
        return '射手座'
    else:
        return '摩羯座'


# 查询星座运势
def get_Fortune():
    apiUrl = 'http://web.juhe.cn/constellation/getAll'
    apiKey = 'ceb019ad015db9ce1d8a6f3ce0b0601b'
    id_number = input_box_number.get()
    birth_month_day = id_number[10:14]
    constellation_name = get_constellation(birth_month_day)
    requestParams = {
        'key': apiKey,
        'consName': constellation_name,
        'type': global_var,
    }
    logger.info('星座运势入参：' + f'apiUrl:{apiUrl}"\n"apiKey:{apiKey}"\n"requestParams:{requestParams}')
    try:
        response = requests.get(apiUrl, params=requestParams)
        if response.status_code == 200:
            responseResult = response.json()
            show_fortune_result(responseResult, global_var)
        else:
            show_error_message("请求数据错误，联系开发人员")
    except Exception as e:
        logger.error(e)
        show_error_message("网络异常，请检查网络情况")


def _show_result_window(title, data, key_mapping, height=700, resizable_y=True):
    """公共结果弹窗：创建窗口并按 key_mapping 显示键值对（运势/黄历共用）。"""
    root = tk.Tk()
    root.geometry("450x%d+%d+%d" % (height, screen_width - 550, screen_height - 950))
    root.title(title)
    root.resizable(False, resizable_y)
    root.configure(bg=UI_BG)
    logger.error(f'********************{title}接口返回数据开始********************')
    for key, value in data.items():
        type_name = key_mapping[key]
        label = L(root, text=f"{type_name}: {value}", wraplength=440, justify='left', width=68, anchor="w")
        label.pack()
        logger.error(f"{type_name}: {value}")
    logger.error(f'********************{title}接口返回数据结束********************')


# 显示星座运势结果
def show_fortune_result(responseResult, fortune_type):
    title, del_keys, key_mapping = FORTUNE_CONFIG.get(fortune_type, FORTUNE_CONFIG['year'])
    for k in del_keys:
        responseResult.pop(k, None)
    if fortune_type == 'year':
        # 本年运势接口返回嵌套结构，需拍平为单值
        responseResult = {
            'name': responseResult['name'],
            'year': responseResult['year'],
            'date': responseResult['date'],
            'career': responseResult['career'][0],
            'love': responseResult['love'][0],
            'health': responseResult['health'][0],
            'finance': responseResult['finance'][0],
            'info': responseResult['mima']['info'],
            'text': responseResult['mima']['text'][0],
            'luckyStone': responseResult['luckyStone'],
        }
    _show_result_window(title, responseResult, key_mapping)


# 查询老黄历
def Yellow_calendar():
    selected_date = date_outcome.get()
    apiUrl = 'http://v.juhe.cn/laohuangli/d'
    apiKey = '795ae444d821212bd4777a4e3a068d77'
    requestParams = {
        'key': apiKey,
        'date': selected_date,
    }
    logger.info('老黄历入参：' + f'apiUrl:{apiUrl}"\n"apiKey:{apiKey}"\n"requestParams:{requestParams}')
    try:
        response = requests.get(apiUrl, params=requestParams)
        if response.status_code == 200:
            responseResult = response.json()['result']
            del responseResult['id']
            show_huangli_result(responseResult)
        else:
            show_error_message("请求数据错误，联系开发人员")
    except Exception as e:
        logger.error(e)
        show_error_message("网络异常，请检查网络情况")


# 显示老黄历结果
def show_huangli_result(responseResult):
    key_mapping = {
        'yangli': '阳历', 'yinli': '阴历', 'wuxing': '五行',
        'chongsha': '冲煞', 'baiji': '彭祖百忌', 'jishen': '吉神宜趋',
        'yi': '宜', 'xiongshen': '凶神宜忌', 'ji': '忌',
    }
    _show_result_window('老黄历', responseResult, key_mapping, height=650, resizable_y=False)


# 解压压缩包
def integrated_file_extractor():
    from extract_tool import integrated_file_extractor
    integrated_file_extractor(window)


# 视频解析
def create_video_search_app():
    from video_search_app import create_video_search_app
    create_video_search_app(window)


# U盘复制视频、图片
def run_usb_file_copier(parent=None):
    from usb_file_copier import run_usb_file_copier
    run_usb_file_copier(window)


# 天气详情查询
def Weather_search():
    import weather_tool
    weather_tool.WeatherApp(window)



# 菜谱查询+详情
def Recipe_Search():
    import recipe_search
    recipe_search.RecipeApp(window)


# ============ 统一配置：app_config.json（启动设置 / 功能启用 / 外观 / 输出目录 / 定时开关机）============
CONFIG_FILE = os.path.join(APP_DIR, "app_config.json")

DEFAULT_CONFIG = {
    "auto_open": [],           # 启动主程序时自动打开的功能 id
    "disabled_tools": [],      # 被禁用的功能 id（对应菜单项置灰）
    "appearance": {"theme": "白天"},
    "output_dir": "",          # PDF / Excel 处理后文件的默认保存目录
    "power": {"shutdown_enabled": False, "shutdown_time": "23:00",
              "boot_enabled": False, "boot_time": "07:00"},
}

# 可自动打开的功能：id -> (显示名, 打开函数)
AUTO_OPEN_TOOLS = [
    ("b64", "Base64 编码工具", open_b64),
    ("office", "Office 文档处理", open_office),
    ("file_time", "文件时间修改", open_filetime),
    ("extract", "压缩包解密", integrated_file_extractor),
    ("windows", "Windows系统优化", windows_config),
    ("kugou", "酷狗音乐定时", KuGou_scheduled),
    # ("step", "微信运动刷步", Step_Submission),
    ("zepp", "Zepp 运动刷步", Zepp_Steps),
    ("video", "视频在线解析", create_video_search_app),
    ("usb", "U盘文件拷贝", run_usb_file_copier),
    ("weather", "天气预报", Weather_search),
    ("recipe", "菜谱搜索", Recipe_Search),
]

# 推荐组合（预设）：一键勾选常用搭配
CONFIG_PRESETS = [
    ("日常模式", ["weather", "recipe", "usb", "video"]),
    ("办公模式", ["b64", "office", "file_time", "extract", "windows"]),
    ("娱乐模式", ["kugou", "zepp"]),
    ("全部取消", []),
]

# 「工具」菜单的分组结构（原「选项」「功能」合并）：组名 -> [(功能 id, 显示名, 打开函数)]
TOOL_MENU_GROUPS = [
    ("文件工具", [
        ("b64", "Base64 编码工具", open_b64),
        ("office", "Office 文档处理", open_office),
        ("file_time", "文件时间修改", open_filetime),
        ("extract", "压缩包解密", integrated_file_extractor),
    ]),
    ("系统工具", [
        ("windows", "Windows 系统优化", windows_config),
        ("kugou", "酷狗音乐定时", KuGou_scheduled),
        # ("step", "微信运动刷步", Step_Submission),
        ("zepp", "Zepp 运动刷步", Zepp_Steps),
    ]),
    ("媒体与生活", [
        ("video", "视频在线解析", create_video_search_app),
        ("usb", "U盘文件拷贝", run_usb_file_copier),
        ("weather", "天气预报", Weather_search),
        ("recipe", "菜谱搜索", Recipe_Search),
    ]),
]


def _load_config():
    """读取配置文件，缺失的键用默认值补齐"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝默认值
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(f"读取配置失败：{e}")
    return cfg


def _save_config(cfg):
    """保存配置到 app_config.json"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        logger.info(f"配置已保存：{cfg}")
        return True
    except Exception as e:
        logger.error(f"保存配置失败：{e}")
        return False


def get_output_dir():
    """PDF / Excel 处理后文件的默认保存目录（供各工具模块调用，未设置时返回空字符串）"""
    return (_load_config().get("output_dir") or "").strip()


# 菜单中已注册的工具项：tool_id -> (子菜单, 菜单标签)，构建菜单时填充
TOOL_MENU_ITEMS = {}


def _apply_tool_states(cfg):
    """按「功能启用」配置把被禁用的功能菜单项置灰"""
    disabled = set(cfg.get("disabled_tools", []))
    for tool_id, (submenu, label) in TOOL_MENU_ITEMS.items():
        try:
            submenu.entryconfig(label, state=('disabled' if tool_id in disabled else 'normal'))
        except Exception as e:
            logger.error(f"设置菜单项状态失败（{tool_id}）：{e}")


def _auto_open_on_startup():
    """主程序启动后，按配置自动打开选中的功能（已被停用的功能不打开）"""
    cfg = _load_config()
    ids = set(cfg.get("auto_open", []))
    disabled = set(cfg.get("disabled_tools", []))
    if not ids:
        return
    for tool_id, label, func in AUTO_OPEN_TOOLS:
        if tool_id in ids and tool_id not in disabled:
            try:
                logger.info(f"启动时自动打开:{label}")
                func()
            except Exception as e:
                logger.error(f"自动打开「{label}」失败：{e}")


# 定时开关机的系统计划任务名
POWER_SHUTDOWN_TASK = "CID_DailyShutdown"
POWER_BOOT_TASK = "CID_DailyBoot"


def _settings_card(parent, title=None, desc=None):
    """设置窗口内的卡片分区，返回用于放置内容的 body 容器"""
    card = tk.Frame(parent, bg=UI_CARD, bd=0, highlightthickness=1,
                    highlightbackground=UI_BORDER)
    card.pack(fill='x', pady=(0, 8))
    if title:
        tk.Label(card, text=title, font=('微软雅黑', 10, 'bold'),
                 bg=UI_CARD, fg=UI_FG).pack(anchor='w', padx=12, pady=(8, 0))
    if desc:
        tk.Label(card, text=desc, font=('微软雅黑', 9),
                 bg=UI_CARD, fg='#8A8A8A').pack(anchor='w', padx=12, pady=(1, 0))
    body = tk.Frame(card, bg=UI_CARD)
    body.pack(fill='x', padx=12, pady=(6, 8))
    return body


def _settings_entry(parent, **kwargs):
    """设置窗口内的统一风格输入框：底色与卡片区分，边框清晰，聚焦时高亮"""
    kwargs.setdefault('font', ('微软雅黑', 10))
    kwargs.setdefault('relief', 'solid')
    kwargs.setdefault('bd', 1)
    return tk.Entry(parent, bg=UI_FIELD, fg=UI_FG, insertbackground=UI_FG,
                    readonlybackground=UI_FIELD, disabledbackground=UI_FIELD,
                    highlightthickness=1, highlightbackground=UI_BORDER,
                    highlightcolor='#4169E1', **kwargs)


def _settings_button(parent, text=None, command=None, width=None,
                     font=None, padx=10, pady=3, **kwargs):
    """设置面板按钮：外层 1px 描边容器 + 内部按钮，确保边框清晰可见"""
    border = tk.Frame(parent, bg=UI_BTN_BORDER, bd=0, highlightthickness=0)
    button = tk.Button(border, text=text, command=command, width=width,
                       font=font or ('微软雅黑', 10),
                       bg=UI_BTN_BG, fg=UI_FG,
                       activebackground=UI_BTN_ACTIVE, activeforeground=UI_FG,
                       relief='flat', bd=0, padx=padx, pady=pady,
                       cursor='hand2', **kwargs)
    button.pack(padx=1, pady=1)   # 1px 外边距即为可见边框
    border.button = button         # 便于外部取用内部按钮
    return border


def _settings_check(parent, text, var, width=17, **kwargs):
    """设置窗口内的统一风格复选框"""
    return tk.Checkbutton(parent, text=text, variable=var,
                          font=('微软雅黑', 10), bg=UI_CARD, fg=UI_FG,
                          activebackground=UI_CARD, activeforeground=UI_FG,
                          selectcolor=UI_FIELD, anchor='w', cursor='hand2',
                          width=width, **kwargs)


def open_log_folder(subdir=None):
    """在资源管理器中打开日志文件夹（默认 log 根目录，传入 subdir 则打开对应日期目录）"""
    log_dir = os.path.join(APP_DIR, "log")
    if subdir:
        log_dir = os.path.join(log_dir, subdir)
    try:
        os.makedirs(log_dir, exist_ok=True)
        logger.info(f"打开日志文件夹：{log_dir}")
        os.startfile(log_dir)
    except Exception as e:
        logger.error(f"打开日志文件夹失败：{e}")
        win32api.MessageBox(0, f"打开日志文件夹失败：{e}", "错误",
                            win32con.MB_OK | win32con.MB_ICONERROR)


def _open_dir(path):
    """在资源管理器中打开指定目录"""
    if not path:
        win32api.MessageBox(0, "尚未设置目录。", "提示", win32con.MB_OK | win32con.MB_ICONINFORMATION)
        return
    try:
        os.makedirs(path, exist_ok=True)
        os.startfile(path)
    except Exception as e:
        logger.error(f"打开目录失败：{e}")
        win32api.MessageBox(0, f"打开目录失败：{e}", "错误",
                            win32con.MB_OK | win32con.MB_ICONERROR)


def _run_hidden(cmd):
    """静默执行系统命令，返回 (returncode, 输出文本)"""
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000) if os.name == 'nt' else 0
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              encoding='gbk', errors='replace', creationflags=flags)
        return proc.returncode, ((proc.stdout or '') + (proc.stderr or '')).strip()
    except Exception as e:
        return -1, str(e)


def _apply_power_schedule(power):
    """把「定时开关机」配置写入 Windows 计划任务，返回 [(名称, 是否成功, 输出)]"""
    results = []
    shutdown_time = (power.get("shutdown_time") or "").strip()
    if power.get("shutdown_enabled") and shutdown_time:
        rc, out = _run_hidden(
            f'schtasks /Create /TN "{POWER_SHUTDOWN_TASK}" /TR "shutdown /s /t 60" '
            f'/SC DAILY /ST {shutdown_time} /F')
        results.append((f"定时关机（每天 {shutdown_time}）", rc == 0, out))
    else:
        _run_hidden(f'schtasks /Delete /TN "{POWER_SHUTDOWN_TASK}" /F')

    boot_time = (power.get("boot_time") or "").strip()
    if power.get("boot_enabled") and boot_time:
        ps = ("Register-ScheduledTask -TaskName '{0}' "
              "-Trigger (New-ScheduledTaskTrigger -Daily -At '{1}') "
              "-Settings (New-ScheduledTaskSettingsSet -WakeToRun) "
              "-Action (New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c exit') -Force"
              ).format(POWER_BOOT_TASK, boot_time)
        rc, out = _run_hidden(f'powershell -NoProfile -ExecutionPolicy Bypass -Command "{ps}"')
        results.append((f"定时开机（每天 {boot_time}）", rc == 0, out))
    else:
        _run_hidden(f'schtasks /Delete /TN "{POWER_BOOT_TASK}" /F')

    for name, ok, out in results:
        logger.info(f"定时任务[{name}]：{'成功' if ok else '失败'} {out[:200]}")
    return results


def open_settings_window():
    """统一的「设置」窗口：启动设置 / 功能启用 / 接口密钥 / 输出目录 / 外观 / 定时开关机 / 日志"""
    cfg = _load_config()
    disabled_now = set(cfg.get("disabled_tools", []))

    win = tk.Toplevel(window)
    win.title("设置")
    win.geometry("620x640")
    win.minsize(560, 440)
    win.configure(bg=UI_BG)
    try:
        win.transient(window)
    except Exception:
        pass

    tk.Label(win, text="⚙ 程序设置", font=('微软雅黑', 14, 'bold'),
             bg=UI_BG, fg=UI_FG).pack(anchor='w', padx=16, pady=(12, 4))

    # 底部按钮区（先 pack，保证始终可见）
    btn_frame = tk.Frame(win, bg=UI_BG)
    btn_frame.pack(fill='x', padx=16, pady=10, side='bottom')

    # 可滚动内容区
    wrap = tk.Frame(win, bg=UI_BG)
    wrap.pack(fill='both', expand=True, padx=16)
    canvas = tk.Canvas(wrap, bg=UI_BG, highlightthickness=0)
    vbar = tk.Scrollbar(wrap, orient='vertical', command=canvas.yview)
    inner = tk.Frame(canvas, bg=UI_BG)
    inner_id = canvas.create_window((0, 0), window=inner, anchor='nw')
    canvas.configure(yscrollcommand=vbar.set)
    canvas.pack(side='left', fill='both', expand=True)
    vbar.pack(side='right', fill='y')

    inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>', lambda e: canvas.itemconfig(inner_id, width=e.width))
    win.bind('<MouseWheel>', lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units'))

    # ===== 1. 启动设置 =====
    body = _settings_card(inner, "启动设置", "启动主程序时自动打开以下功能（已停用的功能不可选）")
    preset_row = tk.Frame(body, bg=UI_CARD)
    preset_row.pack(fill='x', pady=(0, 4))
    tk.Label(preset_row, text="推荐：", font=('微软雅黑', 9),
             bg=UI_CARD, fg=UI_FG).pack(side='left')
    auto_vars = {}
    auto_checks = {}

    def apply_preset(ids):
        for k, v in auto_vars.items():
            v.set(k in ids and k not in disabled_now)

    for name, ids in CONFIG_PRESETS:
        _settings_button(preset_row, text=name, font=('微软雅黑', 9),
                         padx=8, pady=2,
                         command=lambda i=ids: apply_preset(i)).pack(side='left', padx=3)

    auto_grid = tk.Frame(body, bg=UI_CARD)
    auto_grid.pack(fill='x')
    auto_now = set(cfg.get("auto_open", []))
    for i, (tool_id, label, _func) in enumerate(AUTO_OPEN_TOOLS):
        var = tk.BooleanVar(value=tool_id in auto_now)
        auto_vars[tool_id] = var
        check = _settings_check(auto_grid, label, var)
        auto_checks[tool_id] = check
        check.grid(row=i // 3, column=i % 3, sticky='w', padx=(0, 4), pady=2)

    # ===== 2. 功能启用 =====
    body = _settings_card(inner, "功能启用", "取消勾选后，对应功能在菜单中置灰停用")
    enable_vars = {}

    def sync_auto_from_enable(tool_id):
        """功能被停用时，同步禁用并取消其「启动时自动打开」勾选"""
        check = auto_checks.get(tool_id)
        if check is None:
            return
        if enable_vars[tool_id].get():
            check.config(state='normal')
        else:
            auto_vars[tool_id].set(False)
            check.config(state='disabled')

    def set_all_enabled(value):
        for k, v in enable_vars.items():
            v.set(value)
            sync_auto_from_enable(k)

    enable_btn_row = tk.Frame(body, bg=UI_CARD)
    enable_btn_row.pack(fill='x', pady=(0, 4))
    _settings_button(enable_btn_row, text="全选", font=('微软雅黑', 9),
                     padx=10, pady=2,
                     command=lambda: set_all_enabled(True)).pack(side='left')
    _settings_button(enable_btn_row, text="取消全选", font=('微软雅黑', 9),
                     padx=10, pady=2,
                     command=lambda: set_all_enabled(False)).pack(side='left', padx=(6, 0))

    enable_grid = tk.Frame(body, bg=UI_CARD)
    enable_grid.pack(fill='x')
    for i, (tool_id, label, _func) in enumerate(AUTO_OPEN_TOOLS):
        var = tk.BooleanVar(value=tool_id not in disabled_now)
        enable_vars[tool_id] = var
        _settings_check(enable_grid, label, var,
                        command=lambda t=tool_id: sync_auto_from_enable(t)).grid(
            row=i // 3, column=i % 3, sticky='w', padx=(0, 4), pady=2)

    # 初始化：被停用的功能，其「启动时自动打开」勾选框置灰
    for tool_id in auto_checks:
        sync_auto_from_enable(tool_id)

    # ===== 3. 接口密钥 =====
    body = _settings_card(inner, "接口密钥", "用于语录文案+IP归属地接口的 APIKEY")
    key_var = tk.StringVar()
    try:
        key_var.set(api_key_get.load_key())
    except Exception as e:
        logger.error(f"读取密钥失败：{e}")
    _settings_entry(body, textvariable=key_var).pack(side='left', fill='x',
                                                     expand=True, ipady=3)

    def on_save_key():
        new_key = key_var.get().strip()
        if not new_key:
            win32api.MessageBox(0, "请输入有效的密钥！", "错误",
                                win32con.MB_OK | win32con.MB_ICONERROR)
            return
        try:
            api_key_get.save_key(new_key)
            win32api.MessageBox(0, "密钥已保存！", "设置",
                                win32con.MB_OK | win32con.MB_ICONINFORMATION)
        except Exception as e:
            logger.error(f"保存密钥失败：{e}")
            win32api.MessageBox(0, f"保存密钥失败：{e}", "错误",
                                win32con.MB_OK | win32con.MB_ICONERROR)

    _settings_button(body, text="保存", width=8, command=on_save_key).pack(side='left', padx=(8, 0))
    _settings_button(body, text="获取密钥", width=10,
                     command=lambda: api_key_get.show_image(win)).pack(side='left', padx=(8, 0))

    # ===== 4. 默认输出目录 =====
    body = _settings_card(inner, "默认输出目录", "PDF / Excel 等处理后文件的默认保存位置")
    out_var = tk.StringVar(value=cfg.get("output_dir", "") or "")
    _settings_entry(body, textvariable=out_var).pack(side='left', fill='x',
                                                     expand=True, ipady=3)

    def choose_dir():
        from tkinter import filedialog
        path = filedialog.askdirectory(title="选择默认输出目录", parent=win)
        if path:
            out_var.set(path)

    _settings_button(body, text="选择目录", width=10, command=choose_dir).pack(side='left', padx=(8, 0))
    _settings_button(body, text="打开", width=6,
                     command=lambda: _open_dir(out_var.get().strip())).pack(side='left', padx=(8, 0))

    # ===== 5. 外观设置 =====
    body = _settings_card(inner, "外观设置", "提供白天 / 夜晚两种配色（下次启动主程序时生效）")
    theme_row = tk.Frame(body, bg=UI_CARD)
    theme_row.pack(fill='x')
    tk.Label(theme_row, text="配色主题：", font=('微软雅黑', 10),
             bg=UI_CARD, fg=UI_FG).pack(side='left')
    theme_var = tk.StringVar(value=cfg.get("appearance", {}).get("theme", "白天"))
    for theme_name in APPEARANCE_THEMES:
        tk.Radiobutton(theme_row, text=theme_name, value=theme_name, variable=theme_var,
                       font=('微软雅黑', 10), bg=UI_CARD, fg=UI_FG,
                       activebackground=UI_CARD, selectcolor=UI_FIELD,
                       cursor='hand2').pack(side='left', padx=(6, 0))

    # ===== 6. 定时开关机 =====
    power = cfg.get("power", {})
    body = _settings_card(inner, "定时开关机",
                          "每日定时关机（无需管理员）；定时开机需管理员权限并在 BIOS 中启用唤醒")
    sd_var = tk.BooleanVar(value=bool(power.get("shutdown_enabled", False)))
    sd_time = tk.StringVar(value=power.get("shutdown_time", "23:00"))
    row_sd = tk.Frame(body, bg=UI_CARD)
    row_sd.pack(fill='x', pady=3)
    _settings_check(row_sd, "每天定时关机", sd_var, width=14).pack(side='left')
    tk.Label(row_sd, text="时间：", font=('微软雅黑', 10), bg=UI_CARD,
             fg=UI_FG).pack(side='left', padx=(10, 0))
    _settings_entry(row_sd, textvariable=sd_time, width=8).pack(side='left')
    tk.Label(row_sd, text="（HH:MM，如 23:00）", font=('微软雅黑', 9),
             bg=UI_CARD, fg='#8A8A8A').pack(side='left', padx=(6, 0))

    bt_var = tk.BooleanVar(value=bool(power.get("boot_enabled", False)))
    bt_time = tk.StringVar(value=power.get("boot_time", "07:00"))
    row_bt = tk.Frame(body, bg=UI_CARD)
    row_bt.pack(fill='x', pady=3)
    _settings_check(row_bt, "每天定时开机", bt_var, width=14).pack(side='left')
    tk.Label(row_bt, text="时间：", font=('微软雅黑', 10), bg=UI_CARD,
             fg=UI_FG).pack(side='left', padx=(10, 0))
    _settings_entry(row_bt, textvariable=bt_time, width=8).pack(side='left')
    tk.Label(row_bt, text="（需管理员 + BIOS 唤醒支持）", font=('微软雅黑', 9),
             bg=UI_CARD, fg='#8A8A8A').pack(side='left', padx=(6, 0))

    # ===== 7. 日志 =====
    body = _settings_card(inner, "日志", "查看程序运行日志")
    _settings_button(body, text="打开日志文件夹", width=14,
                     command=lambda: open_log_folder()).pack(side='left')
    _settings_button(body, text="打开今日日志", width=14,
                     command=lambda: open_log_folder(datetime.now().strftime("%Y%m%d"))).pack(side='left', padx=(8, 0))

    # ===== 底部：保存 / 关闭 =====
    def on_save():
        cfg["disabled_tools"] = [k for k, v in enable_vars.items() if not v.get()]
        disabled_set = set(cfg["disabled_tools"])
        # 已被停用的功能不能作为启动自启项
        cfg["auto_open"] = [k for k, v in auto_vars.items() if v.get() and k not in disabled_set]
        cfg["appearance"] = {"theme": theme_var.get()}
        cfg["output_dir"] = out_var.get().strip()
        cfg["power"] = {
            "shutdown_enabled": bool(sd_var.get()),
            "shutdown_time": sd_time.get().strip() or "23:00",
            "boot_enabled": bool(bt_var.get()),
            "boot_time": bt_time.get().strip() or "07:00",
        }
        if not _save_config(cfg):
            win32api.MessageBox(0, "保存失败，请查看日志。", "错误",
                                win32con.MB_OK | win32con.MB_ICONERROR)
            return
        _apply_appearance(cfg)
        _apply_tool_states(cfg)
        power_results = []
        try:
            power_results = _apply_power_schedule(cfg["power"])
        except Exception as e:
            logger.error(f"应用定时开关机失败：{e}")

        msg = "设置已保存。\n· 功能启用：立即生效\n· 外观：下次启动主程序时生效\n· 启动设置：下次启动主程序时生效"
        if power_results:
            msg += "\n\n定时开关机："
            for name, ok, _out in power_results:
                msg += f"\n· {name}：{'已生效' if ok else '设置失败（可能需要管理员权限）'}"
        win.destroy()
        win32api.MessageBox(0, msg, "设置", win32con.MB_OK | win32con.MB_ICONINFORMATION)

    _settings_button(btn_frame, text="保存设置", width=10, command=on_save).pack(side='right')
    _settings_button(btn_frame, text="关闭", width=10, command=win.destroy).pack(side='right', padx=8)


# 重启程序
def restart():
    os.execl(sys.executable, sys.executable, *sys.argv)


# 删除输入框中的信息
def delete_Entry():
    number_outcome.delete('0', tk.END)


# 程序说明简介
def show_function_help():
    from tkinter import ttk
    """显示各功能简介的窗口（现代风 + 滑入 + 卡片渐入 + 呼吸按钮）"""
    # ---------- 配色 ----------
    C_BG = "#f5f6f8"
    C_CARD = "#ffffff"
    C_TITLE = "#1f2937"
    C_TEXT = "#374151"
    C_ACCENT = "#4f46e5"
    C_ACCENT2 = "#06b6d4"

    help_win = tk.Toplevel(window)
    help_win.title("功能说明")
    help_win.geometry("720x640")
    help_win.minsize(600, 500)
    help_win.transient(window)
    help_win.configure(bg=C_BG)

    # ---------- 顶部标题区 ----------
    header = tk.Frame(help_win, bg=C_ACCENT, height=88)
    header.pack(fill="x")
    header.pack_propagate(False)

    header_title = tk.Label(header, text="功能说明",
                            font=("微软雅黑", 18, "bold"),
                            bg=C_ACCENT, fg="white")
    header_sub = tk.Label(header,
                          text="身份证号查询信息 v3.8.6    ·    发布者：贼拉爱",
                          font=("微软雅黑", 9),
                          bg=C_ACCENT, fg="#e0e7ff")
    header_title.place(x=-300, y=16)
    header_sub.place(x=-300, y=52)

    def ease_out_cubic(t):
        return 1 - (1 - t) ** 3

    def slide_in_header(step=0):
        if not help_win.winfo_exists():
            return
        total = 20
        if step > total:
            header_title.place(x=24, y=16)
            header_sub.place(x=24, y=52)
            return
        t = step / total
        ease = ease_out_cubic(t)
        x = -300 + (24 - (-300)) * ease
        header_title.place(x=int(x), y=16)
        header_sub.place(x=int(x), y=52)
        help_win.after(12, lambda: slide_in_header(step + 1))

    slide_in_header()

    # ---------- 内容区（可滚动）----------
    wrap = tk.Frame(help_win, bg=C_BG)
    wrap.pack(fill="both", expand=True, padx=14, pady=12)

    canvas = tk.Canvas(wrap, highlightthickness=0, bg=C_BG)
    scrollbar = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=C_BG)
    inner_window = canvas.create_window((0, 0), window=inner, anchor="nw")

    def on_inner_config(event):
        canvas.configure(scrollregion=canvas.bbox("all"))

    inner.bind("<Configure>", on_inner_config)

    # 让 inner 宽度跟随 canvas，卡片才能撑满
    def on_canvas_config(event):
        canvas.itemconfig(inner_window, width=event.width)

    canvas.bind("<Configure>", on_canvas_config)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def on_wheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind_all("<MouseWheel>", on_wheel)

    # ---------- 功能简介数据 ----------
    sections = [
        ("🔍", "身份证号查询",
         "输入 18 位身份证号，自动解析出：\n"
         "出生年月日、性别、年龄、户籍所在地、校验码验证。\n"
         "支持回车键快速查询。"),
        ("📅", "老黄历",
         "选择日期后查询当天农历信息：\n"
         "农历日期、干支、生肖、宜忌事项、吉神凶煞、冲煞方位。"),
        ("🎭", "语录文案（菜单「语录文案」）",
         "菜单下分三个子菜单，进入后点击具体接口即可弹窗显示随机内容：\n"
         "【语录】朋友圈文案 · 励志古言 · 渣男语录 · 毒鸡汤 · 土味情话\n"
         "彩虹屁 · 舔狗日记 · 失恋分手 · 奇葩对话\n"
         "【生活常用】神回复 · 每日一句 · 每日英语 · 早安心语 · 晚安心语\n"
         "历史上的今天 · 周公解梦 · 菜谱查询\n"
         "【教育文学】诗词问答 · 优美诗句 · 唐诗大全 · 百科题库\n"
         "脑筋急转弯 · 雷人笑话 · 十万为什么"),
        ("�", "文件工具（菜单「工具」）",
         "Base64 编码工具：Base64 / Base32 / Hex 编解码，MD5 / SHA 系列哈希，\n"
         "AES / SM4 加密，文件转 Base64。\n"
         "Office 文档处理：【PDF】合并 · 拆分 · 图片转 PDF · 提取文字 · 转图片 · 转 Word；\n"
         "【Excel】合并 · 拆分工作表 · 按列去重 · 批量重命名列。\n"
         "文件时间修改：批量修改创建 / 访问 / 修改时间与只读 / 隐藏属性。\n"
         "压缩包解密：对加密的 zip / rar 尝试口令破解。"),
        ("🖥", "系统工具（菜单「工具」）",
         "Windows系统优化：常用系统设置与 Windows 11 专属功能。\n"
         "酷狗音乐定时：定时启动 / 关闭酷狗并自动播放。\n"
         "Zepp 运动刷步：使用 Zepp / 小米运动账号，多账号批量刷步。"),
        ("🎬", "媒体与生活（菜单「工具」）",
         "视频在线解析：解析视频平台的真实播放地址。\n"
         "U盘文件拷贝：插入 U 盘后自动拷贝视频 / 图片。\n"
         "天气预报：查询城市天气与未来预报。\n"
         "菜谱搜索：按关键词搜索菜谱并查看详细教程。"),
        ("⚙️", "设置（菜单「设置」）",
         "启动设置：配置启动主程序时自动打开的功能。\n"
         "功能启用：勾选 / 停用各项功能。\n"
         "接口密钥：填写并保存在线接口的 APIKEY。\n"
         "默认输出目录：设置 PDF / Excel 处理后文件的保存位置。\n"
         "外观设置：切换界面配色主题与字体大小。\n"
         "定时开关机：设置每天定时关机 / 定时开机。\n"
         "日志：一键打开日志文件夹。"),
        ("📮", "联系与反馈",
         "邮箱：1497388243@qq.com\n"
         "获取密钥：菜单「设置」→「接口密钥」→「获取密钥」\n\n"
         "本工具仅供个人学习使用，请勿用于商业用途。"),
    ]

    # ---------- 创建所有卡片（先隐藏，后面逐张显示）----------
    card_widgets = []
    for icon, title, content in sections:
        card = tk.Frame(inner, bg=C_CARD, bd=0, highlightthickness=0)

        accent = tk.Frame(card, bg=C_ACCENT, width=4)
        accent.pack(side="left", fill="y")

        body = tk.Frame(card, bg=C_CARD)
        body.pack(side="left", fill="both", expand=True, padx=14, pady=12)

        title_row = tk.Frame(body, bg=C_CARD)
        title_row.pack(anchor="w")
        tk.Label(title_row, text=icon,
                 font=("Segoe UI Emoji", 15),
                 bg=C_CARD).pack(side="left", padx=(0, 8))
        tk.Label(title_row, text=title,
                 font=("微软雅黑", 12, "bold"),
                 bg=C_CARD, fg=C_TITLE).pack(side="left")

        tk.Label(body, text=content,
                 font=("微软雅黑", 10),
                 bg=C_CARD, fg=C_TEXT,
                 anchor="w", justify="left",
                 wraplength=600).pack(anchor="w", pady=(6, 0))

        card_widgets.append(card)

    # ---------- 卡片依次出现的动画（纯 pack，稳定）----------
    def show_card(idx=0):
        if not help_win.winfo_exists():
            return
        if idx >= len(card_widgets):
            return
        # 逐张 pack 出来
        card_widgets[idx].pack(fill="x", padx=4, pady=6)
        # 每次 pack 后刷新滚动区域
        inner.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        help_win.after(70, lambda: show_card(idx + 1))

    # 延迟 150ms 开始卡片动画，让 header 先滑入
    help_win.after(150, show_card)

    # ---------- 底部按钮 + 呼吸效果 ----------
    bottom = tk.Frame(help_win, bg=C_BG)
    bottom.pack(fill="x", pady=(0, 14))

    def close_help():
        try:
            canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        try:
            help_win.destroy()
        except Exception:
            pass

    btn_close = tk.Button(bottom, text="关闭",
                          font=("微软雅黑", 10),
                          bg=C_ACCENT, fg="white",
                          activebackground=C_ACCENT2, activeforeground="white",
                          bd=0, padx=24, pady=6,
                          cursor="hand2",
                          command=close_help)
    btn_close.pack()

    # 呼吸颜色（围绕主色深浅变化）
    breathe_colors = ["#4f46e5", "#5850ec", "#6366f1", "#6d69f2",
                      "#6366f1", "#5850ec"]

    def breathe(step=0):
        if not help_win.winfo_exists():
            return
        color = breathe_colors[step % len(breathe_colors)]
        try:
            btn_close.config(bg=color, activebackground=color)
        except Exception:
            return
        help_win.after(450, lambda: breathe(step + 1))

    breathe()

    help_win.protocol("WM_DELETE_WINDOW", close_help)


# ============ 语录文案（原「语录」「生活常用」「教育文学」三类合并）============
QUOTE_CATEGORIES = [
    ("语录", [
        ("朋友圈文案", api_interface_call.Moments_copywriting),
        ("励志古言", api_interface_call.Inspirational_old_sayings),
        ("渣男语录", api_interface_call.Scumbag_quotes),
        ("毒鸡汤", api_interface_call.Poisonous_chicken_soup),
        ("土味情话", api_interface_call.Earthy_love_words),
        ("彩虹屁", api_interface_call.Rainbow_fart),
        ("舔狗日记", api_interface_call.Licking_Dog_Diary),
        ("失恋分手", api_interface_call.Broken_up),
        ("奇葩对话", api_interface_call.Strange_dialogue),
    ]),
    ("生活常用", [
        ("神回复", api_interface_call.God_replied),
        ("每日一句", api_interface_call.One_sentence_a_day),
        ("每日英语", api_interface_call.English_daily),
        ("早安心语", api_interface_call.Say_goodbye_early),
        ("晚安心语", api_interface_call.Evening_peace_of_mind),
        ("历史上的今天", api_interface_call.Today_in_istory),
        ("周公解梦", api_interface_call.interprets_dreams),
        ("菜谱查询", api_interface_call.Recipe_inquiry),
    ]),
    ("教育文学", [
        ("诗词问答", api_interface_call.Poetry_QA),
        ("优美诗句", api_interface_call.Beautiful_verses),
        ("唐诗大全", api_interface_call.Tang_Poems),
        ("百科题库", api_interface_call.Encyclopedia_question_bank),
        ("脑筋急转弯", api_interface_call.Brain_teasers),
        ("雷人笑话", api_interface_call.Thunder_man_jokes),
        ("十万为什么", api_interface_call.whys),
    ]),
]


def show_about():
    """「关于」窗口：本程序的简单介绍"""
    win = tk.Toplevel(window)
    win.title("关于")
    win.geometry("380x190")
    win.resizable(False, False)
    win.configure(bg=UI_BG)
    try:
        win.transient(window)
    except Exception:
        pass

    tk.Label(win, text="身份证号查询信息", font=('微软雅黑', 16, 'bold'),
             bg=UI_BG, fg=UI_FG).pack(pady=(18, 2))
    tk.Label(win, text="版本 v3.8.6", font=('微软雅黑', 10),
             bg=UI_BG, fg='#8A8A8A').pack()

    tk.Frame(win, bg='#DDDDDD', height=1).pack(fill='x', padx=24, pady=12)

    intro = (
        "           一款集实用工具于一身的个人小工具箱。"
    )
    tk.Label(win, text=intro, font=('微软雅黑', 10), bg=UI_BG, fg=UI_FG,
             justify='left', anchor='w').pack(fill='x', padx=28)

    tk.Label(win, text="发布者：贼拉爱    邮箱：1497388243@qq.com",
             font=('微软雅黑', 9), bg=UI_BG, fg='#8A8A8A').pack(side='bottom', pady=14)


if __name__ == '__main__':
    # 提权入口：以管理员身份单独打开 Windows 配置助手（不显示主窗口）
    if '--windows-config' in sys.argv:
        import windows_config_gui
        windows_config_gui.main()
        sys.exit(0)

    # 应用外观配置（配色主题 / 字体大小）—— 必须在创建窗口与控件之前执行
    _startup_cfg = _load_config()
    _apply_appearance(_startup_cfg)

    # 创建窗口
    window = tk.Tk()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    window.geometry("500x350+%d+%d" % (screen_width / 2 - 250, screen_height / 2 - 150))
    window.title('身份证号查询信息')
    # 设置窗口左上角图标
    # window.iconbitmap('image.ico')
    window.resizable(False, False)
    window.configure(bg=UI_BG)  # 轻度美化：主窗口统一浅色背景

    # 创建菜单栏
    menu_bar = tk.Menu(window)

    # 创建“设置”菜单（统一设置入口 + 退出）
    settings_root = tk.Menu(menu_bar, tearoff=0)
    settings_root.add_command(label="打开设置面板", command=open_settings_window)
    settings_root.add_separator()
    settings_root.add_command(label="退出", command=window.quit)
    menu_bar.add_cascade(label="设置", menu=settings_root)

    # 创建“工具”菜单（原「选项」「功能」合并，按用途分三个子菜单）
    tools_root = tk.Menu(menu_bar, tearoff=0)
    for group_name, group_items in TOOL_MENU_GROUPS:
        group_menu = tk.Menu(tools_root, tearoff=0)
        for tool_id, label, func in group_items:
            group_menu.add_command(label=label, command=func)
            TOOL_MENU_ITEMS[tool_id] = (group_menu, label)
        tools_root.add_cascade(label=group_name, menu=group_menu)
    menu_bar.add_cascade(label="工具", menu=tools_root)

    # 创建“语录文案”菜单（原「语录」「生活常用」「教育文学」合并为一个带子菜单的菜单）
    quotes_root = tk.Menu(menu_bar, tearoff=0)
    for category_name, category_items in QUOTE_CATEGORIES:
        category_menu = tk.Menu(quotes_root, tearoff=0)
        for item_name, item_func in category_items:
            category_menu.add_command(label=item_name, command=item_func)
        quotes_root.add_cascade(label=category_name, menu=category_menu)
    menu_bar.add_cascade(label="语录文案", menu=quotes_root)

    # 创建“帮助”菜单（原「关于」）
    help_menu = tk.Menu(menu_bar, tearoff=0)
    help_menu.add_command(label="功能说明", command=show_function_help)
    help_menu.add_command(label="关于", command=show_about)
    menu_bar.add_cascade(label="帮助", menu=help_menu)

    # 应用「功能启用」配置：被停用的功能菜单项置灰
    _apply_tool_states(_startup_cfg)

    # 将菜单栏添加到主窗口
    window.config(menu=menu_bar)

    # 设置字体、输入框
    txt_number = L(text='请输入您的身份证号: ', fg='purple', font=('宋体', 13))
    txt_number.place(x=10, y=40)
    input_box_number = tk.StringVar()
    number_outcome = tk.Entry(window, textvariable=input_box_number, font=('宋体', 16), bd=5)
    number_outcome.place(x=180, y=38)

    # 获取当前本地时间
    now = datetime.now()
    # 格式化时间，确保月和日显示为两位数
    formatted_time = now.strftime("%m%d")
    # 查询当前日期的语句，月份：month = today.month  几号：day = today.day
    today = now.strftime("%Y-%m-%d")
    image_label = Label(window, bg=UI_BG)          # 生肖图片
    constellation_image_label = Label(window, bg=UI_BG)  # 星座图片（与生肖并排）
    # 创建身份证号提示检验数字控件
    label = L(text="")
    label.place(x=250, y=70)

    from tkcalendar import DateEntry
    # 自动获取当前日期查询老黄历
    date_outcome = DateEntry(window, width=11, background='darkblue', foreground='white', borderwidth=5,
                             date_pattern="yyyy-mm-dd", state='readonly')
    date_outcome.place(x=275, y=190)

    # 老黄历查询按钮
    Yellow_calendar_button = B(text='老黄历', width=10, height=1, fg='red', command=Yellow_calendar)
    Yellow_calendar_button.place(x=380, y=187)

    # 回车键绑定信息的显示
    number_outcome.bind('<Return>', lambda event: handle_id_number_input())

    # 添加各种按钮
    Inquire_button = B(text='查询', width=13, height=2, fg='green', command=handle_id_number_input)
    Inquire_button.place(x=350, y=90)
    Reboot_button = B(text="重启", width=13, height=2, fg='black', command=restart)
    Reboot_button.place(x=210, y=90)
    Delete_button = B(text="删除", width=8, height=1, fg='red', command=delete_Entry)
    Delete_button.place(x=420, y=38)

    # 添加文本框前的文字说明
    # 本地IP
    L(text='本地IP地址：', fg='black', font=('宋体', 10)).place(x=10, y=70)
    label_local = L(text='查询中...', fg='black', font=('宋体', 10))
    label_local.place(x=90, y=70)

    # 公网IP
    L(text='公网IP地址：', fg='black', font=('宋体', 10)).place(x=10, y=90)
    label_public = L(text='查询中...', fg='black', font=('宋体', 10))
    label_public.place(x=90, y=90)

    # IP归属地
    L(text='IP归属地：', fg='black', font=('宋体', 10)).place(x=10, y=110)
    label_addr = L(text='查询中...', fg='black', font=('宋体', 10))
    label_addr.place(x=72, y=110)

    # ★ 启动后台查询
    load_ip_info_async(label_local, label_public, label_addr)
    txt_birth = L(text='出生年月:      年    月    日', fg='blue', font=('宋体', 13))
    txt_birth.place(x=30, y=150)
    txt_Household = L(text='户籍所在地：', fg='magenta', font=('宋体', 13))
    txt_Household.place(x=300, y=150)
    txt_gender = L(text='性别: ', fg='blue', font=('宋体', 12))
    txt_gender.place(x=30, y=190)
    txt_age = L(text='年龄: ', fg='blue', font=('宋体', 13))
    txt_age.place(x=115, y=190)
    txt_time = L(text='日期:', fg='blue', font=('宋体', 12))
    txt_time.place(x=225, y=190)
    txt_nature = L(text='您一定是: ', fg='blue', font=('宋体', 13))
    txt_nature.place(x=30, y=230)
    # 仅作个人标志，个人喜好
    txt_sign = L(text='————————仅限私人使用————————', fg='red', font=('仿宋 - Bold', 14))
    txt_sign.place(x=38, y=320)

    # 设置文本框的定位
    YY_outcome = tk.Text(window, height=1, width=7)
    YY_outcome.place(x=110, y=152)
    MM_outcome = tk.Text(window, height=1, width=4)
    MM_outcome.place(x=182, y=152)
    DD_outcome = tk.Text(window, height=1, width=4)
    DD_outcome.place(x=238, y=152)
    Household_outcome = tk.Text(window, height=2, width=12)
    Household_outcome.place(x=390, y=148)
    gender_outcome = tk.Text(window, height=1, width=4)
    gender_outcome.place(x=80, y=193)
    age_outcome = tk.Text(window, height=1, width=8)
    age_outcome.place(x=165, y=193)
    nature_outcome = tk.Text(window, height=2, width=50)
    nature_outcome.place(x=115, y=228)

    # 设置弹窗始终保持在所有程序上方
    # window.wm_attributes("-topmost", 1)

    # 按「配置设置」自动打开选中的功能（延迟一点，等主窗口绘制完成）
    window.after(300, _auto_open_on_startup)

    # 显示窗口
    window.mainloop()


