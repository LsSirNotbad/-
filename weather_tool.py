# -*- coding: utf-8 -*-
"""
天气查询工具
- 实时天气 + 空气质量 + 生活指数 + 未来7天预报
- 日志：log/YYYYMMDD/weather_tool.log（打包后写在 exe 同级目录）
- 支持独立运行和被主程序调用（双模式）
"""
import os
import sys
import json
import logging
import threading
from datetime import datetime
import requests
import tkinter as tk
from tkinter import ttk, messagebox


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
    """标准化日志：写入 log/YYYYMMDD/weather_tool.log（跨天自动切换新日期文件），同时输出到控制台"""
    global logger
    if "logger" in globals() and logger is not None:
        return logger
    logger = logging.getLogger("weather_tool")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        fh = _DailyFileHandler("weather_tool")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        logger.propagate = False
        logger.info(f"日志文件路径: {os.path.join(_base_dir(), 'log', datetime.now().strftime('%Y%m%d'), 'weather_tool.log')}")
    return logger


logger = _setup_logger()


# ================= 极致美化配色 =================
BG_MAIN = "#F0F7FF"
CARD_BG = "#FFFFFF"
HEADER_BG = "#4086D6"
TEXT_MAIN = "#1E293B"
TEXT_SUB = "#64748B"
ACCENT = "#2563EB"
BORDER_COLOR = "#D1E0F3"
BOX_BG = "#F8FAFC"


class WeatherApp:
    def __init__(self, parent=None):
        # 双模式：parent=None 独立运行（Tk + mainloop）；否则作为 Toplevel 嵌入主程序
        self._standalone = parent is None
        self.root = tk.Tk() if self._standalone else tk.Toplevel(parent)
        self.root.title("天气查询工具 | 实时+7天预报")
        # ★ 修改 1：窗口整体缩小到 780x780，且限制不能拉伸
        self.root.geometry("780x780")
        self.root.resizable(False, False)
        self.root.config(bg=BG_MAIN)
        root = self.root  # 局部别名：后续界面代码沿用原有 root 引用

        # 注意：不要在此调用 theme_use("clam") 或配置全局 ttk 样式，
        # 否则会全局生效，导致其它窗口（加密/office/酷狗/修改文件时间等）的 ttk 外观变成灰色。
        # 本窗口界面全部使用 tk 控件，无需设置 ttk 样式。

        # ================= 1. 顶部输入区域 =================
        top_frame = tk.Frame(root, bg=HEADER_BG, padx=15, pady=10) # 高度微缩
        top_frame.pack(fill=tk.X)

        tk.Label(top_frame, text="城市查询：", font=("微软雅黑", 12, "bold"), bg=HEADER_BG, fg="white").pack(
            side=tk.LEFT, padx=(0, 5))

        self.city_var = tk.StringVar()
        self.city_input = tk.Entry(top_frame, textvariable=self.city_var, width=18, font=("微软雅黑", 11),
                                   bd=0, highlightthickness=1, highlightbackground="#A0C4E8", highlightcolor="white")
        self.city_input.pack(side=tk.LEFT, padx=5, ipady=3)
        self.city_input.bind("<Return>", lambda e: self.query_all_weather())

        self.btn_search = tk.Button(top_frame, text="查询天气", font=("微软雅黑", 9, "bold"),
                                    bg="#2B6CB0", fg="white", activebackground="#2C5282", activeforeground="white",
                                    bd=0, padx=20, pady=3, cursor="hand2", command=self.query_all_weather)
        self.btn_search.pack(side=tk.LEFT, padx=6)

        tk.Label(top_frame, text="（留空自动定位）", font=("微软雅黑", 9), bg=HEADER_BG, fg="#A0C4E8").pack(side=tk.LEFT, padx=5)

        # ================= 主内容区 =================
        main_content = tk.Frame(root, bg=BG_MAIN)
        main_content.pack(fill=tk.BOTH, expand=True, padx=15, pady=10) # 减少边距

        # ================= 2. 实时天气面板 =================
        frame_now = tk.LabelFrame(main_content, text=" 🌤 实时天气 ", bg=CARD_BG, fg=ACCENT,
                                  font=("微软雅黑", 11, "bold"), bd=1, relief="solid")
        frame_now.pack(fill=tk.X, pady=(0, 8))

        inner_now = tk.Frame(frame_now, bg=CARD_BG)
        inner_now.pack(fill=tk.X, padx=20, pady=(8, 12))

        # ★ 修复：取消固定宽高和 place，改用自动堆叠
        left_now = tk.Frame(inner_now, bg=CARD_BG)
        left_now.pack(side=tk.LEFT, anchor="n")

        self.lbl_now_city = tk.Label(left_now, text="定位中...", font=("微软雅黑", 13, "bold"), bg=CARD_BG,
                                     fg=TEXT_MAIN)
        self.lbl_now_city.pack(anchor="w", pady=(5, 0))

        # 温度字号微调到 42，避免太挤
        self.lbl_now_temp = tk.Label(left_now, text="--°", font=("Segoe UI", 42, "bold"), bg=CARD_BG, fg=ACCENT)
        self.lbl_now_temp.pack(anchor="w", pady=(2, 5))

        self.lbl_now_weather = tk.Label(left_now, text="暂无数据", font=("微软雅黑", 13), bg=CARD_BG, fg=TEXT_SUB)
        self.lbl_now_weather.pack(anchor="w")

        right_now = tk.Frame(inner_now, bg=CARD_BG)
        right_now.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))

        now_details = [
            ("体感温度", "-- ℃"), ("相对湿度", "-- %"), ("风向", "--"), ("风力等级", "-- 级"),
            ("气压", "-- hPa"), ("能见度", "-- km"), ("降水量", "-- mm"), ("云量", "-- %")
        ]
        self.now_labels = []

        for i, (name, val) in enumerate(now_details):
            r, c = i // 4, i % 4
            box = tk.Frame(right_now, bg=BOX_BG, padx=6, pady=6)  # 框内部空间压缩
            box.grid(row=r, column=c, padx=3, pady=3, sticky="nsew")
            right_now.grid_columnconfigure(c, weight=1)

            tk.Label(box, text=name, font=("微软雅黑", 9), bg=BOX_BG, fg=TEXT_SUB).pack(anchor="w")
            lbl = tk.Label(box, text=val, font=("微软雅黑", 10, "bold"), bg=BOX_BG, fg=TEXT_MAIN)
            lbl.pack(anchor="w", pady=(2, 0))

            self.now_labels.append((lbl, name))

        # ================= 3. 空气质量面板 =================
        frame_aqi = tk.LabelFrame(main_content, text=" 🌫 空气质量 ", bg=CARD_BG, fg=ACCENT,
                                  font=("微软雅黑", 11, "bold"), bd=1, relief="solid")
        frame_aqi.pack(fill=tk.X, pady=(0, 8))

        inner_aqi = tk.Frame(frame_aqi, bg=CARD_BG)
        inner_aqi.pack(fill=tk.X, padx=20, pady=10) # 高度缩减

        self.lbl_aqi_val = tk.Label(inner_aqi, text="--", font=("Segoe UI", 22, "bold"), bg=CARD_BG, fg="#10B981")
        self.lbl_aqi_val.pack(side=tk.LEFT)

        self.lbl_aqi_cat = tk.Label(inner_aqi, text="等待查询", font=("微软雅黑", 12), bg=CARD_BG, fg=TEXT_MAIN)
        self.lbl_aqi_cat.pack(side=tk.LEFT, padx=15)

        self.lbl_aqi_pm25 = tk.Label(inner_aqi, text="PM2.5：-- μg/m³", font=("微软雅黑", 10), bg=CARD_BG, fg=TEXT_SUB)
        self.lbl_aqi_pm25.pack(side=tk.RIGHT)

        # ================= 4. 生活指数面板 =================
        frame_index = tk.LabelFrame(main_content, text=" 💡 生活指数 ", bg=CARD_BG, fg=ACCENT,
                                    font=("微软雅黑", 11, "bold"), bd=1, relief="solid")
        frame_index.pack(fill=tk.X, pady=(0, 8))

        inner_index = tk.Frame(frame_index, bg=CARD_BG)
        inner_index.pack(fill=tk.X, padx=15, pady=10) # 高度缩减

        self.index_labels = []
        for i in range(4):
            box = tk.Frame(inner_index, bg=BOX_BG, padx=10, pady=8)
            box.grid(row=0, column=i, padx=5, sticky="nsew")
            inner_index.grid_columnconfigure(i, weight=1)

            title = tk.Label(box, text="--", font=("微软雅黑", 10, "bold"), bg=BOX_BG, fg=TEXT_MAIN)
            title.pack(anchor="w")
            desc = tk.Label(box, text="暂无建议", font=("微软雅黑", 9), bg=BOX_BG, fg=TEXT_SUB, wraplength=150,
                            justify="left")
            desc.pack(anchor="w", pady=(2, 0))
            self.index_labels.append((title, desc))

        # ================= 5. 7天预报面板 =================
        # ★ 修改：高度从 200 缩减到 160，确保整体窗口不会超出屏幕
        frame_7d = tk.LabelFrame(main_content, text=" 📅 未来 7 天天气预报 & 温度趋势 ",
                                 bg=CARD_BG, fg=ACCENT, font=("微软雅黑", 11, "bold"),
                                 bd=1, relief="solid", height=160)
        frame_7d.pack(fill=tk.BOTH, expand=True, pady=(0, 5))
        frame_7d.pack_propagate(False)

        inner_7d = tk.Frame(frame_7d, bg=CARD_BG)
        inner_7d.pack(fill=tk.X, padx=10, pady=15) # 上下间距稍微缩回

        self.forecast_labels = []
        for i in range(7):
            col = tk.Frame(inner_7d, bg=CARD_BG)
            col.pack(side=tk.LEFT, expand=True, fill=tk.X)

            date = tk.Label(col, text="--", font=("微软雅黑", 9), bg=CARD_BG, fg=TEXT_SUB)
            date.pack(pady=(5, 8))

            weather = tk.Label(col, text="--", font=("微软雅黑", 10, "bold"), bg=CARD_BG, fg=TEXT_MAIN)
            weather.pack(pady=5)

            tmax = tk.Label(col, text="--°", font=("Segoe UI", 15, "bold"), bg=CARD_BG, fg=TEXT_MAIN)
            tmax.pack(pady=(2, 0))

            # 连接线微缩一点
            tk.Frame(col, bg=BORDER_COLOR, width=1, height=18).pack(pady=3)

            tmin = tk.Label(col, text="--°", font=("Segoe UI", 12), bg=CARD_BG, fg=TEXT_SUB)
            tmin.pack(pady=(0, 5))

            self.forecast_labels.append({"date": date, "weather": weather, "tmax": tmax, "tmin": tmin})

        self.lbl_trend = tk.Label(frame_7d, text="等待查询...", font=("微软雅黑", 9), bg=CARD_BG, fg=TEXT_SUB)
        self.lbl_trend.pack(side=tk.BOTTOM, pady=5)

        # 启动后直接自动查询
        self.root.after(200, self.query_all_weather)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        logger.info("========== 程序启动 ==========")
        if self._standalone:
            self.root.mainloop()

    def _on_close(self):
        logger.info("========== 程序退出 ==========")
        self.root.destroy()

    # ================= 业务逻辑 =================
    def query_all_weather(self):
        city_name = self.city_var.get().strip()
        self.btn_search.config(state="disabled", text="查询中...")
        self.lbl_now_city.config(text="正在获取天气...")
        logger.info(f"开始查询天气：{city_name if city_name else '自动定位'}")
        threading.Thread(target=self._do_query, args=(city_name,), daemon=True).start()

    def _do_query(self, city_name):
        api_url = "https://uapis.cn/api/v1/misc/weather"
        params = {"extended": "true", "forecast": "true", "indices": "true", "lang": "zh"}
        if city_name:
            params["city"] = city_name

        try:
            logger.info(f"请求接口: {api_url} 参数: {params}")
            r = requests.get(api_url, params=params, timeout=10)
            if r.status_code != 200:
                logger.error(f"查询失败：接口状态码异常 {r.status_code}，返回内容: {r.text[:500]}")
                self.root.after(0, lambda: messagebox.showerror("查询失败", f"接口状态码异常：{r.status_code}"))
                self.root.after(0, self._reset_btn)
                return

            try:
                data = r.json()
            except ValueError:
                logger.error(f"接口返回非 JSON：{r.text[:500]}")
                self.root.after(0, lambda: messagebox.showerror("查询失败", "接口返回格式异常，详情见日志文件"))
                self.root.after(0, self._reset_btn)
                return

            # 记录接口返回的完整数据（含实时天气、空气质量、生活指数、7天预报）
            logger.info(f"接口返回: {json.dumps(data, ensure_ascii=False)}")

            if "temperature" not in data:
                err_msg = data.get("message", "无法获取天气数据")
                logger.error(f"查询失败：接口返回 {err_msg}")
                self.root.after(0,
                                lambda: messagebox.showerror("查询失败", f"接口返回：{err_msg}\n请尝试手动输入城市名。"))
                self.root.after(0, self._reset_btn)
                return

            loc = f"{data.get('province', '')}{data.get('city', '')}{data.get('district', '')}" or "当前定位"
            logger.info(f"天气数据获取成功：{loc} {data.get('weather', '')} {data.get('temperature', '--')}°")
            self.root.after(0, lambda: self._update_ui(data))

        except requests.exceptions.Timeout:
            logger.error("查询失败：请求超时")
            self.root.after(0, lambda: messagebox.showerror("网络错误", "请求超时，请检查网络。"))
            self.root.after(0, self._reset_btn)
        except Exception as e:
            logger.error(f"查询异常：{e}")
            self.root.after(0, lambda: messagebox.showerror("请求异常", f"发生错误：{str(e)}"))
            self.root.after(0, self._reset_btn)

    def _reset_btn(self):
        self.btn_search.config(state="normal", text="查询天气")

    def _update_ui(self, data):
        self._reset_btn()

        # ========== 1. 实时天气 ==========
        loc = f"{data.get('province', '')}{data.get('city', '')}{data.get('district', '')}" or "当前定位"
        self.lbl_now_city.config(text=loc)
        self.lbl_now_temp.config(text=f"{data.get('temperature', '--')}°")
        self.lbl_now_weather.config(text=data.get('weather', '--'))

        now_map = {
            "体感温度": f"{data.get('feels_like', '--')} ℃",
            "相对湿度": f"{data.get('humidity', '--')} %",
            "风向": data.get('wind_direction', '--'),
            "风力等级": f"{data.get('wind_power', '--')} 级",
            "气压": f"{data.get('pressure', '--')} hPa",
            "能见度": f"{data.get('visibility', '--')} km",
            "降水量": f"{data.get('precipitation', '--')} mm",
            "云量": f"{data.get('cloud', '--')} %"
        }
        for lbl, name in self.now_labels:
            lbl.config(text=now_map.get(name, "--"))

        # ========== 2. 空气质量 ==========
        if data.get("aqi") is not None:
            self.lbl_aqi_val.config(text=data['aqi'])
            self.lbl_aqi_cat.config(text=data.get('aqi_category', '--'))

            cat = data.get('aqi_category', '')
            color = "#10B981" if "优" in cat or "良" in cat else ("#F59E0B" if "轻度" in cat else "#EF4444")
            self.lbl_aqi_val.config(fg=color)

            if data.get("air_pollutants"):
                pm25 = data["air_pollutants"].get("pm25", "--")
                self.lbl_aqi_pm25.config(text=f"PM2.5：{pm25} μg/m³")

        # ========== 3. 生活指数 ==========
        indices = data.get("life_indices", {})
        keys = ["clothing", "uv", "car_wash", "exercise"]
        for i, (t_lbl, d_lbl) in enumerate(self.index_labels):
            if i < len(keys) and keys[i] in indices:
                item = indices[keys[i]]
                t_lbl.config(text=item.get('level', '--'))
                d_lbl.config(text=item.get('advice', '--'))
            else:
                t_lbl.config(text="--")
                d_lbl.config(text="--")

        # ========== 4. 7天预报 ==========
        forecast = data.get("forecast", [])
        temp_list = []
        for i, col in enumerate(self.forecast_labels):
            if i < len(forecast):
                day = forecast[i]
                col["date"].config(text=day.get("date", "")[5:])
                col["weather"].config(text=day.get("weather_day", ""))
                col["tmax"].config(text=f"{day.get('temp_max', '--')}°")
                col["tmin"].config(text=f"{day.get('temp_min', '--')}°")
                if day.get('temp_max') is not None:
                    temp_list.append(int(day['temp_max']))
            else:
                col["date"].config(text="--")
                col["weather"].config(text="--")
                col["tmax"].config(text="--°")
                col["tmin"].config(text="--°")

        # 温度趋势
        if temp_list:
            trend_text = f"【7天温度趋势】 最高温 {max(temp_list)}℃，最低温 {min(temp_list)}℃"
            self.lbl_trend.config(text=trend_text)
        else:
            self.lbl_trend.config(text="【7天温度趋势】 暂无数据")


if __name__ == "__main__":
    WeatherApp()