# -*- coding: utf-8 -*-
"""
Windows 系统配置助手 v5（最终版）
- 系统概览（基础信息 + 硬件信息可选检测）
- 网络共享 / 系统安全 / 系统优化 / 区域与显示
- 显示扩展名、显示隐藏文件、显示系统文件
- 桌面显示此电脑和控制面板
- 关闭 Windows 自动更新
- 勾选式操作 + 一键执行 + 实时状态检测
- 所有耗时操作后台线程，UI 流畅
"""
import os
import sys
import ctypes
import json
import winreg
import subprocess
import platform
import threading
import logging
import time
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

try:
    import wmi
    HAS_WMI = True
except ImportError:
    HAS_WMI = False


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
        self._rollover()
        self._handler.emit(record)

    def close(self):
        if self._handler is not None:
            self._handler.close()
        super().close()


def _setup_logger():
    """标准化日志：写入 log/YYYYMMDD/windows_config_gui.log（跨天自动滚动），同时输出控制台"""
    log = logging.getLogger("windows_config_gui")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    fh = _DailyFileHandler("windows_config_gui")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    log.propagate = False
    return log


logger = _setup_logger()


# ================= 工具函数 =================
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_as_admin():
    """以管理员身份重新启动。打包后带 --windows-config 参数直接打开配置助手。
    返回 True 表示已成功发起提权（用户已同意 UAC），False 表示失败或被取消。"""
    try:
        if getattr(sys, "frozen", False):
            # 打包后：以管理员身份重启 exe，并让其直接打开配置助手
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, "--windows-config", None, 1)
        else:
            # 开发环境：以管理员身份运行当前脚本（脚本启动时会自行做权限检查）
            script = os.path.abspath(__file__)
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f'"{script}"', None, 1)
        return ret > 32
    except Exception as e:
        logger.error(f"提权失败：{e}")
        messagebox.showerror("错误", f"提权失败：{e}")
        return False


def get_windows_version():
    try:
        ver = platform.version().split('.')
        return int(ver[0]), int(ver[1])
    except Exception:
        return 10, 0


def get_build_number():
    """获取系统内部版本号（如 Win11 24H2=26100、Win10=19045、Win7=7601），与系统概览显示一致"""
    try:
        return int(platform.version().split('.')[2])
    except Exception:
        return 0


def is_windows11():
    """检测是否为 Windows 11（内部版本 >= 22000）"""
    return get_build_number() >= 22000


def _svc_disable(name):
    """停止并禁用系统服务，返回 (是否成功, 说明)"""
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        subprocess.run(["net", "stop", name], capture_output=True,
                       timeout=30, creationflags=creationflags)
        r = subprocess.run(["sc", "config", name, "start=", "disabled"],
                           capture_output=True, timeout=10, creationflags=creationflags)
        if r.returncode == 0:
            return True, f"服务 {name} 已停止并禁用"
        return False, _decode_output(r.stdout or r.stderr).strip() or f"禁用 {name} 失败（可能需要管理员权限）"
    except Exception as e:
        return False, str(e)


def _svc_is_disabled(name):
    """检测服务是否已禁用"""
    try:
        r = subprocess.run(["sc", "qc", name], capture_output=True,
                           timeout=10)
        if r.returncode != 0:
            return None  # 服务不存在等情况：未知
        return "DISABLED" in _decode_output(r.stdout).upper()
    except Exception:
        return None


def run_powershell(cmd, timeout=10):
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _refresh_explorer():
    """刷新资源管理器，不重启进程"""
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None)
    except Exception:
        pass


def _restart_explorer():
    """重启资源管理器进程（explorer.exe），用于让右键菜单风格、图标覆盖层等
    依赖 explorer 内存缓存的修改立即生效。桌面和任务栏会短暂消失后自动恢复。
    返回 (是否成功, 说明)。失败时自动尝试重新拉起 explorer。"""
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"],
                       capture_output=True, timeout=10, creationflags=creationflags)
        time.sleep(1.2)  # 等进程完全退出
        # 确认 explorer 是否已自动重启；没有则手动拉起
        chk = subprocess.run(["tasklist", "/FI", "IMAGENAME eq explorer.exe"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=creationflags)
        if "explorer.exe" not in chk.stdout:
            subprocess.Popen("explorer.exe", creationflags=creationflags)
            time.sleep(1.5)
        return True, "资源管理器已重启，修改立即生效"
    except Exception as e:
        # 兜底：无论如何尽量把 explorer 拉起来
        try:
            subprocess.Popen("explorer.exe")
        except Exception:
            pass
        return False, f"重启资源管理器失败：{e}，请按 Ctrl+Shift+Esc 手动运行 explorer"


# ================= 注册表操作 =================
def set_reg(root, path, name, value, value_type=winreg.REG_DWORD, create=False):
    try:
        if create:
            key = winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE)
        else:
            key = winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, name, 0, value_type, value)
        winreg.CloseKey(key)
        return True, f"已设置 {path}\\{name} = {value}"
    except FileNotFoundError:
        return False, f"路径不存在：{path}"
    except PermissionError:
        return False, f"权限不足，无法修改 {path}\\{name}"
    except Exception as e:
        return False, f"设置失败：{e}"


def del_reg(root, path, name):
    try:
        key = winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, name)
        winreg.CloseKey(key)
        return True, f"已删除 {path}\\{name}"
    except FileNotFoundError:
        return True, f"{path}\\{name} 不存在，无需删除"
    except PermissionError:
        return False, f"权限不足，无法删除 {path}\\{name}"
    except Exception as e:
        return False, f"删除失败：{e}"


def get_reg(root, path, name, value_type=winreg.REG_DWORD):
    try:
        key = winreg.OpenKey(root, path, 0, winreg.KEY_READ)
        try:
            value, _ = winreg.QueryValueEx(key, name)
            winreg.CloseKey(key)
            return True, value
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False, None
    except Exception:
        return False, None


# ================= 系统信息 =================
def get_basic_info(admin_ok):
    info = {}
    try:
        info["计算机名"] = platform.node() or "未知"
    except Exception:
        info["计算机名"] = "未知"
    try:
        info["当前用户"] = os.environ.get("USERNAME", "未知")
    except Exception:
        info["当前用户"] = "未知"

    # ★ 系统版本：从注册表读 ProductName + EditionID
    os_name = "Windows"
    edition = ""
    display_ver = ""
    build = ""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        try:
            os_name, _ = winreg.QueryValueEx(key, "ProductName")
        except FileNotFoundError:
            pass
        try:
            edition, _ = winreg.QueryValueEx(key, "EditionID")
        except FileNotFoundError:
            pass
        try:
            display_ver, _ = winreg.QueryValueEx(key, "DisplayVersion")
        except FileNotFoundError:
            pass
        try:
            b, _ = winreg.QueryValueEx(key, "CurrentBuild")
            build = str(b)
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
    except Exception:
        pass

    # ★ Win11 的 ProductName 还是 "Windows 10"，用 DisplayVersion 纠正
    if display_ver and display_ver.startswith(("2", "3")):
        # 21H2/22H2/23H2 等是 Win11
        if "Windows 10" in os_name:
            os_name = os_name.replace("Windows 10", "Windows 11")

    # ★ 根据 EditionID 把英文版名转成中文
    EDITION_MAP = {
        "Core": "家庭版",
        "CoreSingleLanguage": "家庭单语言版",
        "CoreCountrySpecific": "家庭中文版",
        "Professional": "专业版",
        "ProfessionalN": "专业版 N",
        "ProfessionalEducation": "专业教育版",
        "ProfessionalWorkstation": "专业工作站版",
        "Education": "教育版",
        "EducationN": "教育版 N",
        "Enterprise": "企业版",
        "EnterpriseN": "企业版 N",
        "EnterpriseS": "企业 LTSC 版",
        "ServerStandard": "Server 标准版",
        "ServerDatacenter": "Server 数据中心版",
        "ServerDatacenterCore": "Server 数据中心核心版",
        "ServerSolution": "Server 解决方案版",
    }
    edition_cn = EDITION_MAP.get(edition, edition or "未知")

    # 组合显示
    if display_ver:
        info["操作系统"] = f"{os_name} {edition_cn} {display_ver}"
    else:
        info["操作系统"] = f"{os_name} {edition_cn}"

    try:
        info["系统版本"] = platform.version()
    except Exception:
        info["系统版本"] = "未知"
    if build:
        info["内部版本"] = build

    info["管理员权限"] = "是" if admin_ok else "否"
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Services\CmBatt")
        winreg.CloseKey(key)
        info["设备类型"] = "笔记本"
    except Exception:
        info["设备类型"] = "台式机/服务器"
    return info


def get_hardware_info():
    info = {}
    if not HAS_WMI:
        return info
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pythoncom = None
    try:
        c = wmi.WMI()
        try:
            cpus = c.Win32_Processor()
            if cpus:
                info["CPU"] = cpus[0].Name.strip()
                info["CPU核心"] = f"{cpus[0].NumberOfCores}核{cpus[0].NumberOfLogicalProcessors}线程"
        except Exception:
            pass
        try:
            mems = c.Win32_PhysicalMemory()
            total = sum(int(m.Capacity) for m in mems if m.Capacity) / (1024 ** 3)
            info["内存"] = f"{total:.1f} GB"
            if mems and mems[0].Speed:
                info["内存频率"] = f"{mems[0].Speed} MHz"
        except Exception:
            pass
        try:
            gpus = c.Win32_VideoController()
            names = [g.Name for g in gpus if g.Name and "Microsoft" not in g.Name]
            if not names:
                names = [g.Name for g in gpus if g.Name]
            if names:
                info["显卡"] = names[0]
        except Exception:
            pass
        try:
            boards = c.Win32_BaseBoard()
            if boards:
                info["主板"] = f"{boards[0].Manufacturer} {boards[0].Product}"
        except Exception:
            pass
        try:
            disks = c.Win32_DiskDrive()
            lst = [f"{int(d.Size)/(1024**3):.0f}GB" for d in disks if d.Size]
            if lst:
                info["硬盘"] = " + ".join(lst)
        except Exception:
            pass
    except Exception:
        pass
    finally:
        if pythoncom:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    return info


# ================= 反向操作（用于备份还原 / 反向按钮） =================
def _svc_enable(name):
    """把系统服务改回手动启动并尝试拉起"""
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        subprocess.run(["sc", "config", name, "start=", "demand"],
                       capture_output=True, timeout=10, creationflags=creationflags)
        subprocess.run(["net", "start", name],
                       capture_output=True, timeout=15, creationflags=creationflags)
        return True, f"服务 {name} 已恢复为手动启动"
    except Exception as e:
        return False, str(e)


def task_firewall_on():
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "set", "allprofiles", "state", "on"],
            capture_output=True, text=True, shell=True, timeout=15
        )
        if r.returncode == 0:
            return [("开启 Windows 防火墙", True, "所有网络配置文件已开启")]
        return [("开启 Windows 防火墙", False, r.stderr or "未知错误")]
    except Exception as e:
        return [("开启 Windows 防火墙", False, str(e))]


def task_enable_windows_update():
    """还原 Windows Update：服务改回手动启动 + 删注册表策略"""
    results = []
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # 1. wuauserv → demand（手动）
    subprocess.run(["sc", "config", "wuauserv", "start=", "demand"],
                   capture_output=True, text=True, shell=True, timeout=10,
                   creationflags=creationflags)
    subprocess.run(["net", "start", "wuauserv"],
                   capture_output=True, text=True, shell=True, timeout=15,
                   creationflags=creationflags)
    results.append(("恢复 wuauserv 为手动启动", True, "已恢复"))
    # 2. UsoSvc → demand
    subprocess.run(["sc", "config", "UsoSvc", "start=", "demand"],
                   capture_output=True, text=True, shell=True, timeout=10,
                   creationflags=creationflags)
    subprocess.run(["net", "start", "UsoSvc"],
                   capture_output=True, text=True, shell=True, timeout=15,
                   creationflags=creationflags)
    results.append(("恢复 UsoSvc 为手动启动", True, "已恢复"))
    # 3. 删注册表策略 NoAutoUpdate
    path = r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
    ok, msg = del_reg(winreg.HKEY_LOCAL_MACHINE, path, "NoAutoUpdate")
    results.append(("删除 NoAutoUpdate 策略", ok, msg))
    return results


def task_enable_hibernate():
    try:
        r = subprocess.run(["powercfg", "-h", "on"],
                           capture_output=True, shell=True, timeout=10)
        if r.returncode == 0:
            return [("开启休眠", True, "已开启")]
        return [("开启休眠", False, _decode_output(r.stderr).strip() or "未知错误")]
    except Exception as e:
        return [("开启休眠", False, str(e))]


def task_enable_sysmain():
    name = _prefetch_service_name()
    ok, msg = _svc_enable(name)
    return [(f"恢复 {name} 为手动启动", ok, msg)]


def task_enable_windows_search():
    ok, msg = _svc_enable("WSearch")
    return [("恢复 Windows Search 索引服务为手动启动", ok, msg)]


def task_enable_autoplay():
    path = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
    ok, msg = del_reg(winreg.HKEY_CURRENT_USER, path, "NoDriveTypeAutoRun")
    _refresh_explorer()
    return [("恢复自动播放（删除 NoDriveTypeAutoRun）", ok, msg + "；立即生效")]


def task_show_extensions_off():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "HideFileExt", 1, winreg.REG_DWORD, create=True)
    _refresh_explorer()
    return [("隐藏文件扩展名 HideFileExt=1", ok, msg + "；立即生效")]


def task_show_hidden_files_off():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "Hidden", 0, winreg.REG_DWORD, create=True)
    _refresh_explorer()
    return [("隐藏隐藏文件 Hidden=0", ok, msg + "；立即生效")]


def task_fast_menu_show_on():
    """还原菜单延迟为默认 400ms"""
    path = r"Control Panel\Desktop"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "MenuShowDelay", "400", winreg.REG_SZ)
    _refresh_explorer()
    return [("恢复菜单延迟 MenuShowDelay=400", ok, msg + "；注销或重启资源管理器后生效")]


def task_disable_aero_shake_on():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "DisallowShaking", 0, winreg.REG_DWORD, create=True)
    _refresh_explorer()
    return [("恢复 Aero Shake DisallowShaking=0", ok, msg + "；重新打开窗口后生效")]


def task_remove_shortcut_arrow_on():
    """还原快捷方式箭头（删 Shell Icons\\29）"""
    shell_icons = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Icons"
    ok, msg = del_reg(winreg.HKEY_LOCAL_MACHINE, shell_icons, "29")
    # 删图标缓存让恢复生效
    cache = os.path.join(os.environ.get("USERPROFILE", ""),
                         r"AppData\Local\iconcache.db")
    if os.path.exists(cache):
        try:
            os.remove(cache)
        except Exception:
            pass
    _refresh_explorer()
    _restart_explorer()
    return [("恢复快捷方式箭头（删除 Shell Icons\\29）", ok, msg)]


def task_best_performance_on():
    """还原为系统默认视觉效果（让 Windows 自动选择）"""
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path, "VisualFXSetting",
                      5, winreg.REG_DWORD, create=True)
    _refresh_explorer()
    return [("还原视觉效果为默认 VisualFXSetting=5", ok,
             msg + "；注销或重启资源管理器后完全生效")]


def task_shutdown_speed_on():
    """恢复关机等待为默认 5000ms"""
    path = r"SYSTEM\CurrentControlSet\Control"
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, path,
                      "WaitToKillServiceTimeout", "5000", winreg.REG_SZ)
    return [("恢复关机速度 WaitToKillServiceTimeout=5000", ok, msg)]


def task_desktop_icons_off():
    """隐藏桌面上的此电脑和控制面板"""
    results = []
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\HideDesktopIcons\NewStartPanel"
    CLSID_COMPUTER = "{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
    CLSID_CONTROL = "{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      CLSID_COMPUTER, 1, winreg.REG_DWORD, create=True)
    results.append(("隐藏此电脑", ok, msg))
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      CLSID_CONTROL, 1, winreg.REG_DWORD, create=True)
    results.append(("隐藏控制面板", ok, msg))
    _refresh_explorer()
    return results


def task_lsa_security_on():
    """禁用经典共享模型（ForceGuest=1 + LimitBlankPasswordUse=1）"""
    results = []
    lsa = r"SYSTEM\CurrentControlSet\Control\Lsa"
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, lsa, "ForceGuest", 1)
    results.append(("恢复来宾身份验证模型 ForceGuest=1", ok, msg))
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, lsa, "LimitBlankPasswordUse", 1)
    results.append(("恢复空密码控制台限制 LimitBlankPasswordUse=1", ok, msg))
    return results


def task_lanman_guest_on():
    """禁止不安全来宾登录 AllowInsecureGuestAuth=0"""
    path = r"SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters"
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, path,
                      "AllowInsecureGuestAuth", 0, create=True)
    return [("禁止不安全来宾登录 AllowInsecureGuestAuth=0", ok, msg)]


# 任务 → 反向操作函数映射表。有这个才能"还原备份"和卡片上的"反向"按钮
UNDO_MAP = {
    # 网络共享
    "lsa": task_lsa_security_on,
    "lanman": task_lanman_guest_on,
    # 系统安全
    "firewall": task_firewall_on,
    "wu_off": task_enable_windows_update,
    # 系统优化
    "hibernate": task_enable_hibernate,
    "shutdown": task_shutdown_speed_on,
    "no_shortcut_arrow": task_remove_shortcut_arrow_on,
    "best_perf": task_best_performance_on,
    "no_sysmain": task_enable_sysmain,
    "no_wsearch": task_enable_windows_search,
    "no_autoplay": task_enable_autoplay,
    "fast_menu": task_fast_menu_show_on,
    "no_aero_shake": task_disable_aero_shake_on,
    # 区域与显示
    "desktop_icons": task_desktop_icons_off,
    "file_ext": task_show_extensions_off,
    "show_hidden": task_show_hidden_files_off,
}


def _backup_file():
    """备份快照文件路径（和程序同目录）"""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "windows_config_backup.json")


# ================= 网络共享 =================
def task_lsa_security():
    results = []
    lsa = r"SYSTEM\CurrentControlSet\Control\Lsa"
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, lsa, "ForceGuest", 0)
    results.append(("经典共享模型 ForceGuest=0", ok, msg))
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, lsa, "LimitBlankPasswordUse", 0)
    results.append(("禁用空密码控制台限制 LimitBlankPasswordUse=0", ok, msg))
    return results


def check_lsa_security():
    lsa = r"SYSTEM\CurrentControlSet\Control\Lsa"
    e1, v1 = get_reg(winreg.HKEY_LOCAL_MACHINE, lsa, "ForceGuest")
    e2, v2 = get_reg(winreg.HKEY_LOCAL_MACHINE, lsa, "LimitBlankPasswordUse")
    ok1 = e1 and v1 == 0
    ok2 = e2 and v2 == 0
    if ok1 and ok2:
        return ("ok", "✅ 已配置")
    elif not ok1 and not ok2:
        return ("no", "❌ 未配置")
    return ("part", "⚠️ 部分配置")


def task_lanman_guest():
    major, minor = get_windows_version()
    is_win7 = (major == 6 and minor == 1)
    path = r"SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters"
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, path,
                      "AllowInsecureGuestAuth", 1, create=is_win7)
    return [("启用不安全的来宾登录 AllowInsecureGuestAuth=1", ok, msg)]


def check_lanman_guest():
    path = r"SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters"
    e, v = get_reg(winreg.HKEY_LOCAL_MACHINE, path, "AllowInsecureGuestAuth")
    if e and v == 1:
        return ("ok", "✅ 已启用")
    return ("no", "❌ 未启用")


def task_clean_pending():
    path = r"SYSTEM\CurrentControlSet\Control\Session Manager"
    ok, msg = del_reg(winreg.HKEY_LOCAL_MACHINE, path, "PendingFileRenameOperations")
    return [("清理 PendingFileRenameOperations", ok, msg)]


def check_clean_pending():
    path = r"SYSTEM\CurrentControlSet\Control\Session Manager"
    e, _ = get_reg(winreg.HKEY_LOCAL_MACHINE, path, "PendingFileRenameOperations",
                   value_type=winreg.REG_MULTI_SZ)
    if not e:
        return ("ok", "✅ 已清理")
    return ("no", "❌ 有残留")


# ================= 系统安全 =================
def task_firewall_off():
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "set", "allprofiles", "state", "off"],
            capture_output=True, text=True, shell=True, timeout=15
        )
        if r.returncode == 0:
            return [("关闭 Windows 防火墙", True, "所有网络配置文件已关闭")]
        return [("关闭 Windows 防火墙", False, r.stderr or "未知错误")]
    except Exception as e:
        return [("关闭 Windows 防火墙", False, str(e))]


def check_firewall():
    try:
        output = run_powershell("(Get-NetFirewallProfile).Enabled")
        if not output:
            return ("unknown", "❓ 未知")
        false_count = output.count("False")
        true_count = output.count("True")
        if false_count >= 3 and true_count == 0:
            return ("ok", "✅ 已关闭")
        elif true_count >= 1:
            return ("no", "❌ 未关闭")
        return ("unknown", "❓ 未知")
    except Exception:
        return ("unknown", "❓ 未知")


def task_disable_windows_update():
    results = []
    try:
        subprocess.run(["net", "stop", "wuauserv"],
                       capture_output=True, text=True, shell=True, timeout=15)
        r = subprocess.run(["sc", "config", "wuauserv", "start=", "disabled"],
                           capture_output=True, text=True, shell=True, timeout=10)
        if r.returncode == 0:
            results.append(("禁用 Windows Update 服务", True, "wuauserv 已停止并设为禁用"))
        else:
            results.append(("禁用 Windows Update 服务", False, r.stderr or "未知错误"))
    except Exception as e:
        results.append(("禁用 Windows Update 服务", False, str(e)))

    try:
        subprocess.run(["net", "stop", "UsoSvc"],
                       capture_output=True, text=True, shell=True, timeout=15)
        r = subprocess.run(["sc", "config", "UsoSvc", "start=", "disabled"],
                           capture_output=True, text=True, shell=True, timeout=10)
        if r.returncode == 0:
            results.append(("禁用更新协调器服务 UsoSvc", True, "UsoSvc 已停止并设为禁用"))
        else:
            results.append(("禁用更新协调器服务 UsoSvc", False, r.stderr or "未知错误"))
    except Exception as e:
        results.append(("禁用更新协调器服务 UsoSvc", False, str(e)))

    path = r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, path,
                      "NoAutoUpdate", 1, winreg.REG_DWORD, create=True)
    results.append(("禁用自动更新策略 NoAutoUpdate=1", ok, msg))
    return results


def check_windows_update():
    try:
        r = subprocess.run(
            ["sc", "qc", "wuauserv"],
            capture_output=True, text=True, shell=True, timeout=10,
            encoding="gbk", errors="ignore"
        )
        if r.returncode != 0:
            return ("unknown", "❓ 未知")
        if "DISABLED" in r.stdout.upper():
            path = r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
            e, v = get_reg(winreg.HKEY_LOCAL_MACHINE, path, "NoAutoUpdate")
            if e and v == 1:
                return ("ok", "✅ 已关闭")
            return ("part", "⚠️ 部分关闭")
        return ("no", "❌ 未关闭")
    except Exception:
        return ("unknown", "❓ 未知")


# ================= 系统优化 =================
def task_power_saving():
    results = []
    commands = [
        ("关闭显示器超时（接通电源）", ["powercfg", "-change", "-monitor-timeout-ac", "0"]),
        ("关闭显示器超时（电池）", ["powercfg", "-change", "-monitor-timeout-dc", "0"]),
        ("系统睡眠超时（接通电源）", ["powercfg", "-change", "-standby-timeout-ac", "0"]),
        ("系统睡眠超时（电池）", ["powercfg", "-change", "-standby-timeout-dc", "0"]),
        ("硬盘关闭超时（接通电源）", ["powercfg", "-change", "-disk-timeout-ac", "0"]),
        ("硬盘关闭超时（电池）", ["powercfg", "-change", "-disk-timeout-dc", "0"]),
    ]
    for desc, cmd in commands:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               shell=True, timeout=10)
            if r.returncode == 0:
                results.append((desc, True, "已设置为从不"))
            else:
                results.append((desc, False, r.stderr or "未知错误"))
        except Exception as e:
            results.append((desc, False, str(e)))
    return results


def check_power_saving():
    try:
        r1 = subprocess.run(
            ["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP"],
            capture_output=True, text=True, shell=True, timeout=10,
            encoding="gbk", errors="ignore"
        )
        r2 = subprocess.run(
            ["powercfg", "/query", "SCHEME_CURRENT", "SUB_VIDEO"],
            capture_output=True, text=True, shell=True, timeout=10,
            encoding="gbk", errors="ignore"
        )
        text = r1.stdout + r2.stdout
        zero_count = text.count("0x00000000")
        if zero_count >= 4:
            return ("ok", "✅ 已设置")
        return ("part", "⚠️ 部分设置")
    except Exception:
        return ("unknown", "❓ 未知")


def _decode_output(data):
    """解码系统命令输出：不同 Windows/代码页下可能是 UTF-8 或 GBK，自动识别，避免中文匹配失败"""
    if not data:
        return ""
    if isinstance(data, str):
        return data
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="ignore")


def task_disable_hibernate():
    try:
        r = subprocess.run(["powercfg", "-h", "off"],
                           capture_output=True, shell=True, timeout=10)
        if r.returncode == 0:
            return [("关闭休眠", True, "已关闭，释放约与内存等大的硬盘空间")]
        return [("关闭休眠", False, _decode_output(r.stderr).strip() or "未知错误（通常需要管理员权限）")]
    except Exception as e:
        return [("关闭休眠", False, str(e))]


def check_hibernate():
    # 优先读注册表（最可靠、无需管理员权限）：HibernateEnabled 0=已关闭 1=开启
    try:
        exists, val = get_reg(winreg.HKEY_LOCAL_MACHINE,
                              r"SYSTEM\CurrentControlSet\Control\Power",
                              "HibernateEnabled")
        if exists:
            return ("ok", "✅ 已关闭") if int(val) == 0 else ("no", "❌ 未关闭")
    except Exception:
        pass
    # 回退：解析 powercfg /a（输出可能是 UTF-8 或 GBK）
    try:
        r = subprocess.run(["powercfg", "/a"],
                           capture_output=True, shell=True, timeout=10)
        text = _decode_output(r.stdout)
        if "尚未启用休眠" in text:
            return ("ok", "✅ 已关闭")
        if "休眠" in text:
            return ("no", "❌ 未关闭")
        return ("unknown", "❓ 未知")
    except Exception:
        return ("unknown", "❓ 未知")


def task_shutdown_speed():
    path = r"SYSTEM\CurrentControlSet\Control"
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, path,
                      "WaitToKillServiceTimeout", "2000", winreg.REG_SZ)
    return [("优化关机速度 WaitToKillServiceTimeout=2000", ok, msg)]


def check_shutdown_speed():
    path = r"SYSTEM\CurrentControlSet\Control"
    e, v = get_reg(winreg.HKEY_LOCAL_MACHINE, path,
                   "WaitToKillServiceTimeout", winreg.REG_SZ)
    if e and v == "2000":
        return ("ok", "✅ 已优化")
    return ("no", "❌ 未优化")


def task_processor_boost():
    try:
        subprocess.run(
            ["powercfg", "-attributes", "SUB_PROCESSOR",
             "PERFBOOSTMODE", "-ATTRIB_HIDE"],
            capture_output=True, shell=True)
        r = subprocess.run(
            ["powercfg", "-setacvalueindex", "SCHEME_CURRENT",
             "SUB_PROCESSOR", "PERFBOOSTMODE", "2"],
            capture_output=True, text=True, shell=True, timeout=10)
        subprocess.run(
            ["powercfg", "-setdcvalueindex", "SCHEME_CURRENT",
             "SUB_PROCESSOR", "PERFBOOSTMODE", "2"],
            capture_output=True, shell=True)
        subprocess.run(["powercfg", "-setactive", "SCHEME_CURRENT"],
                       capture_output=True, shell=True)
        if r.returncode == 0:
            return [("处理器性能提升模式=激进", True,
                     "已设置，CPU 会更积极睿频（功耗和发热会增加）")]
        return [("处理器性能提升模式", False, r.stderr or "未知错误")]
    except Exception as e:
        return [("处理器性能提升模式", False, str(e))]


def check_processor_boost():
    try:
        r = subprocess.run(
            ["powercfg", "-query", "SCHEME_CURRENT", "SUB_PROCESSOR", "PERFBOOSTMODE"],
            capture_output=True, text=True, shell=True, timeout=10,
            encoding="gbk", errors="ignore"
        )
        if "0x00000002" in r.stdout:
            return ("ok", "✅ 已激进")
        return ("no", "❌ 未设置")
    except Exception:
        return ("unknown", "❓ 未知")


def task_remove_shortcut_arrow():
    results = []
    shell_icons = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Icons"
    # 1. 写入 Shell Icons\29，用透明图标替换箭头覆盖层（Win10/Win11 通用）
    icon_value = r"%systemroot%\system32\imageres.dll,197"
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, shell_icons,
                      "29", icon_value, winreg.REG_SZ, create=True)
    results.append(("写入 Shell Icons\\29（去箭头覆盖层）", ok, msg))

    # 2. 删除图标缓存，让新设置立即生效（Win11 需要，Win10 无副作用）
    cache = os.path.join(os.environ.get("USERPROFILE", ""),
                         r"AppData\Local\iconcache.db")
    if os.path.exists(cache):
        try:
            os.remove(cache)
            results.append(("清除图标缓存", True, "已删除 iconcache.db"))
        except Exception as e:
            results.append(("清除图标缓存", False, str(e)))
    else:
        results.append(("清除图标缓存", True, "缓存文件不存在，无需处理"))

    # 3. 广播刷新并重启 explorer：图标覆盖层缓存在 explorer 内存里，重启后立即生效
    _refresh_explorer()
    ok, msg = _restart_explorer()
    results.append(("重启资源管理器（立即生效）", ok, msg))
    return results


def check_remove_shortcut_arrow():
    shell_icons = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Icons"
    exists, val = get_reg(winreg.HKEY_LOCAL_MACHINE, shell_icons, "29",
                          value_type=winreg.REG_SZ)
    if exists and val:
        return ("ok", "✅ 已去除")
    return ("no", "❌ 未去除")


# ---- 通用性能优化（Win7/10/11） ----
def task_best_performance():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path, "VisualFXSetting",
                      2, winreg.REG_DWORD, create=True)
    _refresh_explorer()
    return [("调整为最佳性能（VisualFXSetting=2）", ok, msg + "；注销或重启资源管理器后完全生效")]


def check_best_performance():
    e, v = get_reg(winreg.HKEY_CURRENT_USER,
                   r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
                   "VisualFXSetting")
    if e and v == 2:
        return ("ok", "✅ 已最佳性能")
    return ("no", "❌ 未设置")


def task_disable_taskbar_thumbnails():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    # 悬停延迟拉到极大（约 90 秒），等效于关闭任务栏缩略图预览
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path, "ExtendedUIHoverTime",
                      90000, winreg.REG_DWORD, create=True)
    _refresh_explorer()
    return [("关闭任务栏缩略图预览（ExtendedUIHoverTime）", ok,
             msg + "；重启资源管理器或注销后生效")]


def check_disable_taskbar_thumbnails():
    e, v = get_reg(winreg.HKEY_CURRENT_USER,
                   r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                   "ExtendedUIHoverTime")
    if e and isinstance(v, int) and v >= 60000:
        return ("ok", "✅ 已关闭")
    return ("no", "❌ 未关闭")


def task_disable_aero_shake():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path, "DisallowShaking",
                      1, winreg.REG_DWORD, create=True)
    _refresh_explorer()
    return [("关闭 Aero Shake（DisallowShaking=1）", ok, msg + "；重新打开窗口后生效")]


def check_disable_aero_shake():
    e, v = get_reg(winreg.HKEY_CURRENT_USER,
                   r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                   "DisallowShaking")
    if e and v == 1:
        return ("ok", "✅ 已关闭")
    return ("no", "❌ 未关闭")


def _prefetch_service_name():
    """Win7 叫 Superfetch，Win10/11 叫 SysMain"""
    return "SysMain" if get_build_number() >= 10240 else "Superfetch"


def task_disable_sysmain():
    name = _prefetch_service_name()
    ok, msg = _svc_disable(name)
    return [(f"禁用预读服务 {name}", ok, msg)]


def check_disable_sysmain():
    name = _prefetch_service_name()
    disabled = _svc_is_disabled(name)
    if disabled is True:
        return ("ok", f"✅ {name} 已禁用")
    if disabled is False:
        return ("no", f"❌ {name} 未禁用")
    return ("unknown", "❓ 未知")


def task_disable_windows_search():
    ok, msg = _svc_disable("WSearch")
    return [("关闭 Windows Search 索引服务", ok, msg + "；开始菜单搜索会变慢")]


def check_disable_windows_search():
    disabled = _svc_is_disabled("WSearch")
    if disabled is True:
        return ("ok", "✅ 已关闭")
    if disabled is False:
        return ("no", "❌ 未关闭")
    return ("unknown", "❓ 未知")


def task_disable_autoplay():
    path = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path, "NoDriveTypeAutoRun",
                      0xFF, winreg.REG_DWORD, create=True)
    _refresh_explorer()
    return [("关闭自动播放（NoDriveTypeAutoRun=255）", ok, msg + "；立即生效")]


def check_disable_autoplay():
    e, v = get_reg(winreg.HKEY_CURRENT_USER,
                   r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer",
                   "NoDriveTypeAutoRun")
    if e and v == 0xFF:
        return ("ok", "✅ 已关闭")
    return ("no", "❌ 未关闭")


def task_clean_update_cache():
    results = []
    download_dir = r"C:\Windows\SoftwareDistribution\Download"
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # 1. 停止更新服务，解除文件占用
    subprocess.run(["net", "stop", "wuauserv"], capture_output=True,
                   timeout=30, creationflags=creationflags)
    results.append(("停止 Windows 更新服务", True, "已停止（结束后会自动恢复为原启动方式）"))
    # 2. 删除缓存内容（只删目录内容，不删目录本身）
    deleted, failed = 0, 0
    if os.path.isdir(download_dir):
        for entry in os.listdir(download_dir):
            p = os.path.join(download_dir, entry)
            try:
                if os.path.isdir(p):
                    import shutil
                    shutil.rmtree(p, ignore_errors=False)
                else:
                    os.remove(p)
                deleted += 1
            except Exception:
                failed += 1
    if failed == 0:
        results.append(("清理更新缓存", True, f"已清理 {deleted} 个项目"))
    else:
        results.append(("清理更新缓存", False, f"清理 {deleted} 个，{failed} 个被占用无法删除（重试或重启后再清）"))
    # 3. 恢复更新服务运行（保持服务原有启动类型，只负责拉起来）
    subprocess.run(["net", "start", "wuauserv"], capture_output=True,
                   timeout=30, creationflags=creationflags)
    results.append(("恢复 Windows 更新服务", True, "已重新启动"))
    return results


def check_clean_update_cache():
    download_dir = r"C:\Windows\SoftwareDistribution\Download"
    try:
        if not os.path.isdir(download_dir):
            return ("unknown", "❓ 未知")
        count = len(os.listdir(download_dir))
        if count <= 5:
            return ("ok", "✅ 无需清理")
        return ("no", f"❌ 有 {count} 项缓存")
    except Exception:
        return ("unknown", "❓ 未知")


def task_fast_menu_show():
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop",
                      "MenuShowDelay", "0", winreg.REG_SZ)
    _refresh_explorer()
    return [("菜单显示延迟调为 0（MenuShowDelay）", ok, msg + "；注销或重启资源管理器后生效")]


def check_fast_menu_show():
    e, v = get_reg(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop",
                   "MenuShowDelay", value_type=winreg.REG_SZ)
    if e and str(v) == "0":
        return ("ok", "✅ 已调快")
    return ("no", "❌ 未设置")


def _task_classic_context_menu(enable_classic):
    """切换 Win11 右键菜单风格：enable_classic=True 恢复经典样式，False 还原默认。
    方案：创建/删除 HKCU\\Software\\Classes\\CLSID\\{86ca1aa0-...}\\InprocServer32（值留空）"""
    clsid = r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"
    results = []
    if enable_classic:
        try:
            key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, clsid, 0,
                                     winreg.KEY_SET_VALUE)
            # 默认值为空字符串：告诉系统该 CLSID 的进程内服务器为空，使 Win11 回退到经典菜单
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "")
            winreg.CloseKey(key)
            results.append(("写入经典菜单 CLSID", True, "已创建 InprocServer32（空值）"))
        except PermissionError:
            results.append(("写入经典菜单 CLSID", False, "权限不足"))
        except Exception as e:
            results.append(("写入经典菜单 CLSID", False, str(e)))
    else:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, clsid, 0,
                                 winreg.KEY_SET_VALUE)
            winreg.CloseKey(key)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, clsid)
            # 父键 {86ca1aa0-...} 为空则一并删除，保持干净
            try:
                winreg.DeleteKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}")
            except OSError:
                pass
            results.append(("删除经典菜单 CLSID", True, "已还原默认右键菜单"))
        except FileNotFoundError:
            results.append(("删除经典菜单 CLSID", True, "已是默认样式，无需处理"))
        except PermissionError:
            results.append(("删除经典菜单 CLSID", False, "权限不足"))
        except Exception as e:
            results.append(("删除经典菜单 CLSID", False, str(e)))
    _refresh_explorer()
    # 重启 explorer 让右键菜单风格立即生效（不重启电脑）
    ok, msg = _restart_explorer()
    results.append(("重启资源管理器（立即生效）", ok, msg))
    return results


def task_win11_classic_context_menu():
    return _task_classic_context_menu(enable_classic=True)


def task_win11_default_context_menu():
    return _task_classic_context_menu(enable_classic=False)


def check_win11_context_menu():
    clsid = r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, clsid, 0, winreg.KEY_READ)
        winreg.CloseKey(key)
        return ("ok", "✅ 经典样式")
    except FileNotFoundError:
        return ("no", "❌ Win11 默认")
    except Exception:
        return ("unknown", "❓ 未知")


# ================= 区域与显示 =================
def task_short_date():
    results = []
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER,
                      r"Control Panel\International",
                      "sShortDate", "yyyy-MM-dd", winreg.REG_SZ)
    results.append(("当前用户短日期格式", ok, msg))
    ok, msg = set_reg(winreg.HKEY_USERS,
                      r".DEFAULT\Control Panel\International",
                      "sShortDate", "yyyy-MM-dd", winreg.REG_SZ)
    results.append(("默认用户短日期格式", ok, msg))
    return results


def check_short_date():
    e1, v1 = get_reg(winreg.HKEY_CURRENT_USER,
                     r"Control Panel\International", "sShortDate", winreg.REG_SZ)
    e2, v2 = get_reg(winreg.HKEY_USERS,
                     r".DEFAULT\Control Panel\International", "sShortDate", winreg.REG_SZ)
    target = "yyyy-MM-dd"
    ok1 = e1 and v1 == target
    ok2 = e2 and v2 == target
    if ok1 and ok2:
        return ("ok", "✅ 已修改")
    elif not ok1 and not ok2:
        return ("no", "❌ 未修改")
    return ("part", "⚠️ 部分修改")


def task_time_format():
    results = []
    path = r"Control Panel\International"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "sTimeFormat", "HH:mm:ss", winreg.REG_SZ)
    results.append(("长时间格式 = HH:mm:ss", ok, msg))
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "sShortTime", "HH:mm", winreg.REG_SZ)
    results.append(("短时间格式 = HH:mm", ok, msg))
    return results


def check_time_format():
    path = r"Control Panel\International"
    e, v = get_reg(winreg.HKEY_CURRENT_USER, path, "sTimeFormat", winreg.REG_SZ)
    if e and v == "HH:mm:ss":
        return ("ok", "✅ 已设置")
    return ("no", "❌ 未设置")


def task_non_unicode_lang():
    results = []
    path = r"SYSTEM\CurrentControlSet\Control\Nls\CodePage"
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, path,
                      "ACP", "936", winreg.REG_SZ)
    results.append(("ANSI 代码页 = 936（简体中文）", ok, msg))
    ok, msg = set_reg(winreg.HKEY_LOCAL_MACHINE, path,
                      "OEMCP", "936", winreg.REG_SZ)
    results.append(("OEM 代码页 = 936（简体中文）", ok, msg))
    results.append(("提示", True, "非 Unicode 语言修改后需要重启生效"))
    return results


def check_non_unicode_lang():
    path = r"SYSTEM\CurrentControlSet\Control\Nls\CodePage"
    e, v = get_reg(winreg.HKEY_LOCAL_MACHINE, path, "ACP", winreg.REG_SZ)
    if e and v == "936":
        return ("ok", "✅ 简体中文")
    return ("no", "❌ 非简体")


def task_dpi_100():
    results = []
    path = r"Control Panel\Desktop"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "LogPixels", 96, winreg.REG_DWORD)
    results.append(("DPI 缩放 LogPixels=96（100%）", ok, msg))
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "Win8DpiScaling", 0, winreg.REG_DWORD)
    results.append(("Win8DpiScaling=0", ok, msg))
    results.append(("提示", True, "DPI 修改后需要注销或重启生效"))
    return results


def check_dpi_100():
    path = r"Control Panel\Desktop"
    e, v = get_reg(winreg.HKEY_CURRENT_USER, path, "LogPixels", winreg.REG_DWORD)
    if e and v == 96:
        return ("ok", "✅ 100%")
    return ("no", "❌ 非100%")


def task_desktop_icons():
    results = []
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\HideDesktopIcons\NewStartPanel"
    CLSID_COMPUTER = "{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
    CLSID_CONTROL = "{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      CLSID_COMPUTER, 0, winreg.REG_DWORD, create=True)
    results.append(("显示“此电脑”", ok, msg))
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      CLSID_CONTROL, 0, winreg.REG_DWORD, create=True)
    results.append(("显示“控制面板”", ok, msg))
    _refresh_explorer()
    results.append(("刷新桌面", True, "已广播刷新，图标应立即显示"))
    return results


def check_desktop_icons():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\HideDesktopIcons\NewStartPanel"
    CLSID_COMPUTER = "{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
    CLSID_CONTROL = "{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}"
    e1, v1 = get_reg(winreg.HKEY_CURRENT_USER, path, CLSID_COMPUTER)
    e2, v2 = get_reg(winreg.HKEY_CURRENT_USER, path, CLSID_CONTROL)
    comp_shown = e1 and v1 == 0
    ctrl_shown = e2 and v2 == 0
    if comp_shown and ctrl_shown:
        return ("ok", "✅ 已显示")
    elif not comp_shown and not ctrl_shown:
        return ("no", "❌ 未显示")
    return ("part", "⚠️ 部分显示")


def task_show_extensions():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "HideFileExt", 0, winreg.REG_DWORD, create=True)
    _refresh_explorer()
    return [("显示文件扩展名 HideFileExt=0", ok, msg)]


def check_show_extensions():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    e, v = get_reg(winreg.HKEY_CURRENT_USER, path, "HideFileExt")
    if e and v == 0:
        return ("ok", "✅ 已显示")
    return ("no", "❌ 未显示")


def task_show_hidden_files():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "Hidden", 1, winreg.REG_DWORD, create=True)
    _refresh_explorer()
    return [("显示隐藏文件 Hidden=1", ok, msg)]


def check_show_hidden_files():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    e, v = get_reg(winreg.HKEY_CURRENT_USER, path, "Hidden")
    if e and v == 1:
        return ("ok", "✅ 已显示")
    return ("no", "❌ 未显示")


def task_show_system_files():
    """显示受保护的操作系统文件"""
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    results = []
    # ShowSuperHidden = 1 显示受保护的操作系统文件
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "ShowSuperHidden", 1, winreg.REG_DWORD, create=True)
    results.append(("显示受保护的系统文件 ShowSuperHidden=1", ok, msg))
    # 同时确保 Hidden = 1（显示隐藏文件）
    ok, msg = set_reg(winreg.HKEY_CURRENT_USER, path,
                      "Hidden", 1, winreg.REG_DWORD, create=True)
    results.append(("显示隐藏文件 Hidden=1", ok, msg))
    _refresh_explorer()
    return results


def check_show_system_files():
    path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    e1, v1 = get_reg(winreg.HKEY_CURRENT_USER, path, "ShowSuperHidden")
    e2, v2 = get_reg(winreg.HKEY_CURRENT_USER, path, "Hidden")
    ok1 = e1 and v1 == 1
    ok2 = e2 and v2 == 1
    if ok1 and ok2:
        return ("ok", "✅ 已显示")
    elif not ok1 and not ok2:
        return ("no", "❌ 未显示")
    return ("part", "⚠️ 部分显示")


# ================= 配置定义 =================
def _get_tasks():
    return [
    # ---- 网络共享 ----
    {
        "id": "lsa",
        "group": "网络共享",
        "title": "局域网共享免密访问",
        "short": "允许无密码访问共享文件夹（经典共享模型）",
        "icon": "🔓",
        "detail": (
            "作用：让局域网内的电脑用本地账户身份验证，而不是统一映射为 Guest。\n"
            "同时允许空密码账户通过网络访问共享。\n\n"
            "适用场景：需要无密码访问共享文件夹、打印机。\n\n"
            "生效：立即生效，无需重启。"
        ),
        "func": task_lsa_security,
        "check": check_lsa_security,
    },
    {
        "id": "lanman",
        "group": "网络共享",
        "title": "允许来宾账户访问共享",
        "short": "解决“安全策略阻止来宾访问”的提示",
        "icon": "👥",
        "detail": (
            "作用：允许 Win10/11 访问使用 SMB 来宾身份的共享设备，\n"
            "比如老式 NAS、路由器 USB 共享、部分打印机。\n\n"
            "适用场景：访问共享时提示“安全策略阻止未经身份验证的来宾访问”。\n\n"
            "生效：立即生效，建议重启一次确保稳定。"
        ),
        "func": task_lanman_guest,
        "check": check_lanman_guest,
    },
    {
        "id": "pending",
        "group": "网络共享",
        "title": "清理挂起的文件操作",
        "short": "解决软件安装时“需要重启”却无效的问题",
        "icon": "🧹",
        "detail": (
            "作用：该注册表值会让系统在下次启动时重命名/删除某些文件。\n"
            "如果残留了旧记录，可能导致软件安装提示“需要重启”却无效。\n\n"
            "适用场景：软件安装报错“重启后继续”，但重启也没用。\n\n"
            "生效：立即生效。"
        ),
        "func": task_clean_pending,
        "check": check_clean_pending,
    },
    # ---- 系统安全 ----
    {
        "id": "firewall",
        "group": "系统安全",
        "title": "关闭 Windows 防火墙",
        "short": "关闭所有网络配置文件的防火墙保护",
        "icon": "🛡",
        "detail": (
            "作用：一次性关闭域、专用、公用三种网络配置文件的防火墙。\n\n"
            "适用场景：局域网共享被防火墙拦截、调试服务端口、内网环境。\n\n"
            "⚠️ 警告：关闭防火墙会显著降低系统安全性，请仅在可信网络中使用。\n\n"
            "生效：立即生效。"
        ),
        "func": task_firewall_off,
        "check": check_firewall,
    },
    {
        "id": "wu_off",
        "group": "系统安全",
        "title": "关闭系统自动更新",
        "short": "禁用 Windows Update 自动下载安装",
        "icon": "🚫",
        "detail": (
            "作用：\n"
            "· 把 Windows Update 服务设为禁用，停止自动下载和安装更新。\n"
            "· 同时设置注册表策略，防止系统自动重启安装更新。\n\n"
            "适用场景：\n"
            "· 服务器/生产环境需要锁定版本\n"
            "· 避免更新导致驱动或软件不兼容\n"
            "· 不想被半夜自动重启打扰\n\n"
            "⚠️ 警告：\n"
            "关闭更新会错过安全补丁，系统安全性降低。\n"
            "建议定期手动检查并安装重要更新。\n\n"
            "生效：立即生效。想恢复更新，把服务改回“手动”或“自动”即可。"
        ),
        "func": task_disable_windows_update,
        "check": check_windows_update,
    },
    # ---- 系统优化 ----
    {
        "id": "power",
        "group": "系统优化",
        "title": "禁用自动睡眠和关闭屏幕",
        "short": "让电脑保持常亮常在线（显示器/睡眠/硬盘）",
        "icon": "💡",
        "detail": (
            "作用：\n"
            "· 显示器：不再自动关闭屏幕\n"
            "· 睡眠：系统不再自动进入睡眠\n"
            "· 硬盘：机械硬盘不再自动休眠（SSD 不受影响）\n\n"
            "适用场景：服务器长时间运行、挂机下载、远程桌面常连。\n\n"
            "⚠️ 注意：笔记本电池模式下会增加耗电，建议插电使用。\n\n"
            "生效：立即生效。"
        ),
        "func": task_power_saving,
        "check": check_power_saving,
    },
    {
        "id": "hibernate",
        "group": "系统优化",
        "title": "关闭休眠（释放硬盘空间）",
        "short": "释放与内存等大的硬盘空间",
        "icon": "💤",
        "detail": (
            "作用：关闭 Windows 休眠功能，释放 C 盘上约等于内存大小的 hiberfil.sys 文件。\n\n"
            "适用场景：C 盘空间紧张、不需要休眠功能。\n\n"
            "⚠️ 注意：关闭后无法使用“休眠”选项，但“睡眠”仍可用。\n\n"
            "生效：立即生效。"
        ),
        "func": task_disable_hibernate,
        "check": check_hibernate,
    },
    {
        "id": "shutdown",
        "group": "系统优化",
        "title": "优化关机速度",
        "short": "减少关机等待时间（2 秒超时）",
        "icon": "⚡",
        "detail": (
            "作用：把 WaitToKillServiceTimeout 从默认 5000ms 改为 2000ms，\n"
            "让系统更快结束无响应的服务，加快关机速度。\n\n"
            "适用场景：关机慢、卡在“正在关闭服务”。\n\n"
            "⚠️ 注意：设太短可能导致服务未正常保存数据，2000ms 是安全值。\n\n"
            "生效：下次关机时生效。"
        ),
        "func": task_shutdown_speed,
        "check": check_shutdown_speed,
    },
    {
        "id": "boost",
        "group": "系统优化",
        "title": "处理器性能提升模式 = 激进",
        "short": "让 CPU 更积极睿频，提升响应速度",
        "icon": "🚀",
        "detail": (
            "作用：把电源方案里隐藏的“处理器性能提升模式”设为激进，\n"
            "让 CPU 更积极地升频，减少卡顿，提升响应速度。\n\n"
            "适用场景：高性能台式机、需要低延迟的场景。\n\n"
            "⚠️ 注意：会增加功耗和发热，笔记本电池模式下不建议开启。\n\n"
            "生效：立即生效。"
        ),
        "func": task_processor_boost,
        "check": check_processor_boost,
    },
    {
        "id": "no_shortcut_arrow",
        "group": "系统优化",
        "title": "去除桌面图标快捷方式箭头",
        "short": "去掉快捷方式左下角的小箭头，桌面更干净",
        "icon": "🖼️",
        "detail": (
            "作用：写入注册表 Shell Icons\\29，用透明图标替换快捷方式箭头覆盖层，\n"
            "同时删除图标缓存让设置更快生效（Win10 / Win11 通用）。\n\n"
            "适用场景：想让桌面图标更干净、美观。\n\n"
            "⚠️ 注意：只是视觉调整，不影响快捷方式功能；删除 Shell Icons\\29 即可恢复箭头。\n\n"
            "生效：立即生效（自动重启资源管理器）。"
        ),
        "func": task_remove_shortcut_arrow,
        "check": check_remove_shortcut_arrow,
    },
    {
        "id": "win11_classic_ctx",
        "group": "系统优化",
        "title": "恢复 Win11 经典右键菜单",
        "short": "右键直接显示完整菜单，不再多点一次“显示更多选项”",
        "icon": "🖱️",
        "min_build": 22000,  # 仅 Win11 显示
        "detail": (
            "作用：在当前用户下创建 CLSID InprocServer32 空值项，\n"
            "让 Windows 11 右键菜单恢复为 Win10 的经典完整样式。\n\n"
            "适用场景：觉得 Win11 精简右键菜单不方便，常用功能被折叠。\n\n"
            "⚠️ 仅当前用户生效，不需要管理员权限；不影响系统其他用户。\n\n"
            "生效：立即生效（自动重启资源管理器）。"
        ),
        "func": task_win11_classic_context_menu,
        "check": check_win11_context_menu,
    },
    {
        "id": "win11_default_ctx",
        "group": "系统优化",
        "title": "还原 Win11 默认右键菜单",
        "short": "撤销经典菜单修改，回到 Win11 精简右键样式",
        "icon": "↩️",
        "min_build": 22000,  # 仅 Win11 显示
        "detail": (
            "作用：删除当前用户下的经典菜单 CLSID 项，\n"
            "把右键菜单还原成 Windows 11 默认的精简样式。\n\n"
            "适用场景：想恢复系统原生 Win11 右键菜单。\n\n"
            "生效：立即生效（自动重启资源管理器）。"
        ),
        "func": task_win11_default_context_menu,
        "check": check_win11_context_menu,
    },
    # ---- 通用性能优化（Win7/10/11） ----
    {
        "id": "best_perf",
        "group": "系统优化",
        "title": "调整为最佳性能",
        "short": "关闭窗口动画、淡入淡出、滑动菜单等视觉效果，老电脑更跟手",
        "icon": "🎯",
        "detail": (
            "作用：把视觉效果设为“调整为最佳性能”，关闭窗口动画、\n"
            "淡入淡出、滑动菜单、阴影等特效，降低资源占用。\n\n"
            "适用场景：老电脑、低配机追求流畅操作。\n\n"
            "⚠️ 注意：界面会变素（类似经典主题）；在“系统属性→性能设置”可改回。\n\n"
            "生效：注销或重启资源管理器后完全生效。"
        ),
        "func": task_best_performance,
        "check": check_best_performance,
    },
    {
        "id": "no_taskbar_thumb",
        "group": "系统优化",
        "title": "关闭任务栏缩略图预览",
        "short": "鼠标悬停任务栏不再弹预览图，减少资源占用",
        "icon": "🗔",
        "max_build": 21999,  # Win7/10 有效；Win11 效果有限，不展示
        "detail": (
            "作用：把任务栏悬停预览延迟拉到极大，等效关闭缩略图预览。\n\n"
            "适用场景：低配机减少资源占用、不喜欢悬停弹窗。\n\n"
            "⚠️ 注意：删除 ExtendedUIHoverTime 或改为小值即可恢复；Win11 效果有限故不显示本项。\n\n"
            "生效：重启资源管理器或注销后生效。"
        ),
        "func": task_disable_taskbar_thumbnails,
        "check": check_disable_taskbar_thumbnails,
    },
    {
        "id": "no_aero_shake",
        "group": "系统优化",
        "title": "关闭 Aero Shake",
        "short": "防止误拖窗口导致其他窗口最小化",
        "icon": "🫨",
        "detail": (
            "作用：禁用“摇动窗口最小化其他窗口”（Aero Shake）功能。\n\n"
            "适用场景：拖动窗口时经常误触发其他窗口全部最小化。\n\n"
            "⚠️ 注意：把 DisallowShaking 改为 0 即可恢复。\n\n"
            "生效：重新打开窗口后生效。"
        ),
        "func": task_disable_aero_shake,
        "check": check_disable_aero_shake,
    },
    {
        "id": "no_sysmain",
        "group": "系统优化",
        "title": "禁用 SysMain / Superfetch",
        "short": "关闭后台预读，SSD 用户减少磁盘占用",
        "icon": "💾",
        "detail": (
            "作用：停止并禁用系统预读服务（Win10/11 叫 SysMain，Win7 叫 Superfetch，\n"
            "程序会按系统版本自动选择对应服务名）。\n\n"
            "适用场景：SSD 用户减少后台磁盘占用、解决 SysMain 占用高。\n\n"
            "⚠️ 注意：HDD + 大内存用户可保留以加快常用程序启动；把服务改回自动即可恢复。\n\n"
            "生效：立即生效（需管理员权限）。"
        ),
        "func": task_disable_sysmain,
        "check": check_disable_sysmain,
    },
    {
        "id": "no_wsearch",
        "group": "系统优化",
        "title": "关闭 Windows Search 索引",
        "short": "关闭后台索引服务，减少磁盘读写",
        "icon": "🔍",
        "detail": (
            "作用：停止并禁用 Windows Search（WSearch）索引服务。\n\n"
            "适用场景：不常用系统搜索、想减少后台磁盘读写。\n\n"
            "⚠️ 注意：开始菜单和文件资源管理器的搜索会变慢（仍可搜索文件名）；\n"
            "把 WSearch 服务改回自动即可恢复。\n\n"
            "生效：立即生效（需管理员权限）。"
        ),
        "func": task_disable_windows_search,
        "check": check_disable_windows_search,
    },
    {
        "id": "no_autoplay",
        "group": "系统优化",
        "title": "关闭自动播放",
        "short": "插入 U 盘/光盘不再弹自动播放窗口",
        "icon": "💿",
        "detail": (
            "作用：写入 NoDriveTypeAutoRun=255，禁止所有驱动器的自动播放。\n\n"
            "适用场景：插入 U 盘/光盘/手机不想弹自动播放窗口，也更安全。\n\n"
            "⚠️ 注意：删除该值或在“设置→自动播放”改回即可恢复。\n\n"
            "生效：立即生效。"
        ),
        "func": task_disable_autoplay,
        "check": check_disable_autoplay,
    },
    {
        "id": "clean_update_cache",
        "group": "系统优化",
        "title": "清理 Windows 更新缓存",
        "short": "删除 SoftwareDistribution\\Download 里的旧更新包，释放 C 盘空间",
        "icon": "🧹",
        "detail": (
            "作用：停止更新服务 → 清空 C:\\Windows\\SoftwareDistribution\\Download\n"
            "里的旧更新包 → 恢复更新服务。安全可清，系统需要时会重新下载。\n\n"
            "适用场景：C 盘空间紧张、更新后残留大量安装包。\n\n"
            "⚠️ 注意：个别被占用的文件可能删不掉，重试或重启后再清即可（需管理员权限）。\n\n"
            "生效：立即生效。"
        ),
        "func": task_clean_update_cache,
        "check": check_clean_update_cache,
    },
    {
        "id": "fast_menu",
        "group": "系统优化",
        "title": "菜单显示延迟调低",
        "short": "开始菜单/右键菜单弹出更快",
        "icon": "⚡",
        "detail": (
            "作用：把 MenuShowDelay 从默认 400ms 改为 0，\n"
            "开始菜单、右键菜单、下拉菜单弹出不再有延迟。\n\n"
            "适用场景：追求操作跟手、不喜欢菜单弹出动画等待。\n\n"
            "⚠️ 注意：改回 400 即可恢复默认。\n\n"
            "生效：注销或重启资源管理器后生效。"
        ),
        "func": task_fast_menu_show,
        "check": check_fast_menu_show,
    },
    # ---- 区域与显示 ----
    {
        "id": "date",
        "group": "区域与显示",
        "title": "统一日期格式为 年-月-日",
        "short": "把系统短日期格式改成 yyyy-MM-dd",
        "icon": "📅",
        "detail": (
            "作用：把系统“短日期”显示格式统一改成 yyyy-MM-dd（如 2026-10-17）。\n"
            "同时写入当前用户和默认用户配置，新登录用户也会生效。\n\n"
            "适用场景：程序读取日期、日志记录、报表导出时格式不统一。\n\n"
            "生效：立即生效，部分程序需重启刷新显示。"
        ),
        "func": task_short_date,
        "check": check_short_date,
    },
    {
        "id": "time_fmt",
        "group": "区域与显示",
        "title": "时间格式 = HH:mm:ss",
        "short": "统一时间为 24 小时制 时:分:秒",
        "icon": "🕐",
        "detail": (
            "作用：把系统长时间格式改成 HH:mm:ss（24 小时制，如 14:30:05）。\n"
            "短时间格式改成 HH:mm。\n\n"
            "适用场景：习惯 24 小时制、日志时间需要精确到秒。\n\n"
            "生效：立即生效，部分程序需重启刷新显示。"
        ),
        "func": task_time_format,
        "check": check_time_format,
    },
    {
        "id": "nonunicode",
        "group": "区域与显示",
        "title": "非 Unicode 程序语言 = 简体中文",
        "short": "代码页设为 936（中国大陆）",
        "icon": "🌐",
        "detail": (
            "作用：把非 Unicode 程序的默认语言设为简体中文（代码页 936）。\n"
            "解决部分老程序显示乱码的问题。\n\n"
            "适用场景：运行日本、繁体程序后出现乱码，需要改回简体中文。\n\n"
            "⚠️ 注意：修改后必须重启电脑才能生效。\n\n"
            "生效：需要重启。"
        ),
        "func": task_non_unicode_lang,
        "check": check_non_unicode_lang,
    },
    {
        "id": "dpi",
        "group": "区域与显示",
        "title": "DPI 缩放 = 100%",
        "short": "字体和界面显示更小但更清晰",
        "icon": "🔍",
        "detail": (
            "作用：把系统 DPI 缩放设为 100%（96 DPI），\n"
            "让字体和界面元素显示更小但更清晰。\n\n"
            "适用场景：高分屏想显示更多内容、部分老程序在 125% 缩放下模糊。\n\n"
            "⚠️ 注意：修改后必须注销或重启才能生效。\n\n"
            "生效：需要注销或重启。"
        ),
        "func": task_dpi_100,
        "check": check_dpi_100,
    },
    {
        "id": "desktop_icons",
        "group": "区域与显示",
        "title": "桌面显示此电脑和控制面板",
        "short": "在桌面上添加“此电脑”和“控制面板”图标",
        "icon": "🖥",
        "detail": (
            "作用：\n"
            "· 在桌面显示“此电脑”图标（Win10/11 默认隐藏）\n"
            "· 在桌面显示“控制面板”图标\n\n"
            "适用场景：快速访问磁盘、打开控制面板。\n\n"
            "生效：立即生效。"
        ),
        "func": task_desktop_icons,
        "check": check_desktop_icons,
    },
    {
        "id": "file_ext",
        "group": "区域与显示",
        "title": "显示文件扩展名",
        "short": "让 .txt / .jpg 等后缀名在资源管理器中可见",
        "icon": "📄",
        "detail": (
            "作用：在资源管理器中显示文件的完整扩展名（如 文档.txt、照片.jpg）。\n"
            "默认 Windows 隐藏已知文件类型的扩展名，容易被伪装文件欺骗。\n\n"
            "适用场景：识别文件真实类型、批量重命名。\n\n"
            "生效：立即生效，已打开的窗口需按 F5 刷新。"
        ),
        "func": task_show_extensions,
        "check": check_show_extensions,
    },
    {
        "id": "show_hidden",
        "group": "区域与显示",
        "title": "显示隐藏文件",
        "short": "让隐藏文件和文件夹在资源管理器中可见",
        "icon": "👁",
        "detail": (
            "作用：显示被标记为“隐藏”的文件和文件夹（半透明图标）。\n"
            "不显示系统关键文件（如 bootmgr、pagefile.sys）。\n\n"
            "适用场景：清理 AppData、ProgramData 里的隐藏文件。\n\n"
            "⚠️ 注意：显示隐藏文件后，不要误删系统或软件的隐藏配置。\n\n"
            "生效：立即生效，已打开的窗口需按 F5 刷新。"
        ),
        "func": task_show_hidden_files,
        "check": check_show_hidden_files,
    },
    {
        "id": "show_system",
        "group": "区域与显示",
        "title": "显示系统文件",
        "short": "让受保护的操作系统文件也可见",
        "icon": "⚙",
        "detail": (
            "作用：\n"
            "· 关闭“隐藏受保护的操作系统文件”保护，让 bootmgr、pagefile.sys、\n"
            "  System Volume Information 等系统文件在资源管理器中可见。\n"
            "· 同时自动开启“显示隐藏文件”。\n\n"
            "适用场景：\n"
            "· 排查系统残留、病毒文件\n"
            "· 手动清理系统目录里的垃圾\n\n"
            "⚠️ 警告：\n"
            "系统文件被误删可能导致系统无法启动！\n"
            "操作前建议先备份，不确定的文件不要动。\n\n"
            "生效：立即生效，已打开的窗口需按 F5 刷新。"
        ),
        "func": task_show_system_files,
        "check": check_show_system_files,
    },
    ]


# 按系统版本过滤任务：任务可声明 min_build / max_build（内部版本号，与系统概览一致），
# 不满足当前系统版本的任务不展示（如 Win11 专属右键菜单、仅 Win7/10 有效的任务栏缩略图）
def _task_supported(task):
    build = get_build_number()
    if build <= 0:
        return True  # 版本未知时不做过滤，全部展示
    if "min_build" in task and build < task["min_build"]:
        return False
    if "max_build" in task and build > task["max_build"]:
        return False
    return True


TASKS = [t for t in _get_tasks() if _task_supported(t)]


# ================= 主界面 =================
class ConfigApp:
    def __init__(self, parent=None):
        # parent=None 独立运行（Tk + mainloop）；被主程序调用时用 Toplevel，不启动独立主循环
        self._standalone = parent is None
        self.root = tk.Tk() if self._standalone else tk.Toplevel(parent)
        self.root.title("Windows 配置助手")
        self.root.geometry("760x820")
        self.root.minsize(720, 600)
        self.root.configure(bg="#f8fafc")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        logger.info("========== Windows 配置助手启动 ==========")

        self.status_labels = {}
        self.check_vars = {}
        self.detail_frames = {}
        self.detail_shown = {}
        self.action_buttons = []
        self.group_frames = {}
        self.group_collapsed = {}
        self._detecting = False
        self._hw_loaded = False
        self._hw_loading = False

        self.admin_ok = is_admin()

        # 1. 系统概览固定顶部（不进滚动区）
        self._build_system_panel()

        # 2. 滚动区只装功能列表
        container = tk.Frame(self.root, bg="#f8fafc")
        container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(container, bg="#f8fafc", highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg="#f8fafc")
        self.body.bind("<Configure>",
                       lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.body, anchor="nw", width=730)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def on_wheel(event):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        # 仅鼠标在滚动区时接管滚轮，避免作为 Toplevel 嵌入时影响主程序窗口
        def _bind_wheel(_event=None):
            self.canvas.bind_all("<MouseWheel>", on_wheel)

        def _unbind_wheel(_event=None):
            self.canvas.unbind_all("<MouseWheel>")

        self.canvas.bind("<Enter>", _bind_wheel)
        self.canvas.bind("<Leave>", _unbind_wheel)

        # 3. 操作栏 + 任务列表 + 底部 放滚动区
        self._build_action_bar()
        self._build_task_list()
        self._build_footer()

        # 硬件检测
        if self.var_auto_hw.get():
            self._hw_loading = True
            self.hw_btn.config(state="disabled", text="检测中…")
            threading.Thread(target=self._load_hardware_async, daemon=True).start()

        self.root.after(50, self.refresh_all_status)
        logger.info(f"系统信息：{getattr(self, '_base_info', {})}")
        if self._standalone:
            self.root.mainloop()

    def _on_close(self):
        logger.info("========== Windows 配置助手关闭 ==========")
        try:
            self.root.destroy()
        except Exception:
            pass

    # ============ 系统概览（固定顶部） ============
    def _build_system_panel(self):
        panel = tk.Frame(self.root, bg="white", bd=0,
                         highlightthickness=1, highlightbackground="#e2e8f0")
        panel.pack(fill="x", padx=14, pady=(10, 4))

        inner = tk.Frame(panel, bg="white")
        inner.pack(fill="x", padx=14, pady=10)

        title_row = tk.Frame(inner, bg="white")
        title_row.pack(fill="x", pady=(0, 6))

        tk.Label(title_row, text="系统概览",
                 font=("微软雅黑", 10, "bold"),
                 bg="white", fg="#0f172a").pack(side="left")

        self.summary_label = tk.Label(title_row, text="检测中...",
                                       font=("微软雅黑", 8),
                                       bg="white", fg="#64748b")
        self.summary_label.pack(side="left", padx=10)

        self.hw_btn = tk.Button(title_row, text="检测硬件",
                  font=("微软雅黑", 8),
                  bg="white", fg="#6366f1",
                  bd=1, relief="solid",
                  padx=8, pady=2, cursor="hand2",
                  command=self.reload_hardware)
        self.hw_btn.pack(side="right", padx=(4, 0))

        self.var_auto_hw = tk.BooleanVar(value=True)
        tk.Checkbutton(title_row, text="启动时检测硬件",
                       variable=self.var_auto_hw,
                       font=("微软雅黑", 8),
                       bg="white", fg="#64748b",
                       activebackground="white",
                       bd=0, cursor="hand2",
                       selectcolor="white").pack(side="right", padx=(4, 8))

        self.info_grid = tk.Frame(inner, bg="white")
        self.info_grid.pack(fill="x")

        base_info = get_basic_info(self.admin_ok)
        self._base_info = dict(base_info)
        self._render_info(base_info)

    def reload_hardware(self):
        if not HAS_WMI:
            messagebox.showwarning("提示", "未安装 wmi 库，无法检测硬件信息")
            logger.warning("硬件检测失败：未安装 wmi 库")
            return
        if self._hw_loading:
            return
        logger.info("开始手动检测硬件信息")
        self._hw_loading = True
        self.hw_btn.config(state="disabled", text="检测中…")
        self._hw_loaded = False
        self.summary_label.config(text="检测中...", fg="#64748b")
        threading.Thread(target=self._load_hardware_async, daemon=True).start()

    def _load_hardware_async(self):
        try:
            hw = get_hardware_info()
            if hw:
                logger.info(f"硬件检测完成：{hw}")
                self.root.after(0, lambda h=hw: self._merge_info(h))
            else:
                logger.warning("硬件检测返回空结果，请检查 WMI 服务")
                self.root.after(0, lambda: self.summary_label.config(
                    text="硬件检测失败，请检查 WMI 服务", fg="#ef4444"))
        except Exception as e:
            logger.exception(f"硬件检测异常：{e}")
            self.root.after(0, lambda: self.summary_label.config(
                text="硬件检测异常", fg="#ef4444"))
        finally:
            self.root.after(0, self._hw_done)

    def _hw_done(self):
        self._hw_loading = False
        try:
            self.hw_btn.config(state="normal", text="检测硬件")
        except Exception:
            pass

    def _merge_info(self, hw):
        self._hw_loaded = True
        merged = dict(getattr(self, "_base_info", {}))
        merged.update(hw)
        self._render_info(merged)
        self._update_summary()

    def _render_info(self, info):
        for w in self.info_grid.winfo_children():
            w.destroy()

        # 两列等宽并填满可用空间（uniform 保证左右列宽一致）
        self.info_grid.grid_columnconfigure(0, weight=1, uniform="info_col")
        self.info_grid.grid_columnconfigure(1, weight=1, uniform="info_col")

        items = list(info.items())
        cols = 2
        per_col = (len(items) + cols - 1) // cols

        for i, (k, v) in enumerate(items):
            col = i // per_col
            row = i % per_col
            row_frame = tk.Frame(self.info_grid, bg="white")
            row_frame.grid(row=row, column=col, sticky="we", padx=(0, 24), pady=2)
            row_frame.grid_columnconfigure(1, weight=1)  # 值列吃掉剩余宽度

            tk.Label(row_frame, text=k + "：",
                     font=("微软雅黑", 8),
                     bg="white", fg="#94a3b8",
                     anchor="w").grid(row=0, column=0, sticky="w")
            # 长文本自动换行显示完整内容，不再按 28 字截断
            tk.Label(row_frame, text=str(v),
                     font=("微软雅黑", 8, "bold"),
                     bg="white", fg="#1e293b",
                     anchor="w", justify="left",
                     wraplength=260).grid(row=0, column=1, sticky="w")

    # ============ 操作栏 ============
    def _build_action_bar(self):
        bar = tk.Frame(self.body, bg="#f8fafc")
        bar.pack(fill="x", padx=14, pady=4)

        b1 = tk.Button(bar, text="执行已勾选",
                       font=("微软雅黑", 9, "bold"),
                       bg="#6366f1", fg="white",
                       activebackground="#4f46e5", activeforeground="white",
                       bd=0, padx=14, pady=6, cursor="hand2",
                       command=self.run_checked)
        b1.pack(side="left")

        b2 = tk.Button(bar, text="全部执行",
                       font=("微软雅黑", 9),
                       bg="#ef4444", fg="white",
                       activebackground="#dc2626", activeforeground="white",
                       bd=0, padx=12, pady=6, cursor="hand2",
                       command=self.run_all_tasks)
        b2.pack(side="left", padx=4)

        tk.Button(bar, text="全选",
                  font=("微软雅黑", 9),
                  bg="white", fg="#475569",
                  activebackground="#f1f5f9",
                  bd=1, relief="solid",
                  padx=10, pady=6, cursor="hand2",
                  command=self.select_all).pack(side="left", padx=2)

        tk.Button(bar, text="清空",
                  font=("微软雅黑", 9),
                  bg="white", fg="#475569",
                  activebackground="#f1f5f9",
                  bd=1, relief="solid",
                  padx=10, pady=6, cursor="hand2",
                  command=self.select_none).pack(side="left", padx=2)

        # 备份 / 还原（放在右侧刷新按钮左边）
        tk.Button(bar, text="💾备份当前状态",
                  font=("微软雅黑", 9),
                  bg="white", fg="#059669",
                  activebackground="#f0fdf4",
                  bd=1, relief="solid",
                  padx=10, pady=6, cursor="hand2",
                  command=self._backup_current_state).pack(side="right", padx=(0, 2))

        tk.Button(bar, text="↺还原备份",
                  font=("微软雅黑", 9),
                  bg="white", fg="#b45309",
                  activebackground="#fffbeb",
                  bd=1, relief="solid",
                  padx=10, pady=6, cursor="hand2",
                  command=self._restore_from_backup).pack(side="right", padx=(0, 2))

        b5 = tk.Button(bar, text="刷新",
                       font=("微软雅黑", 9),
                       bg="white", fg="#475569",
                       activebackground="#f1f5f9",
                       bd=1, relief="solid",
                       padx=10, pady=6, cursor="hand2",
                       command=self.refresh_all_status)
        b5.pack(side="right")

        self.action_buttons = [b1, b2, b5]

    # ============ 任务列表 ============
    def _build_task_list(self):
        wrap = tk.Frame(self.body, bg="#f8fafc")
        wrap.pack(fill="x", padx=14, pady=4)

        groups = []
        for task in TASKS:
            g = task.get("group", "其它")
            if g not in groups:
                groups.append(g)

        for group in groups:
            group_frame = tk.Frame(wrap, bg="#f8fafc")
            group_frame.pack(fill="x")

            g_header = tk.Frame(group_frame, bg="#f8fafc", cursor="hand2")
            g_header.pack(fill="x", pady=(8, 2))

            arrow_label = tk.Label(g_header, text="▾",
                                    font=("微软雅黑", 9),
                                    bg="#f8fafc", fg="#6366f1")
            arrow_label.pack(side="left", padx=(2, 4))

            title_label = tk.Label(g_header, text=group,
                                    font=("微软雅黑", 10, "bold"),
                                    bg="#f8fafc", fg="#0f172a")
            title_label.pack(side="left")

            g_body = tk.Frame(group_frame, bg="#f8fafc")
            g_body.pack(fill="x")

            self.group_frames[group] = (g_header, g_body, arrow_label)
            self.group_collapsed[group] = False

            def make_toggle(name=group):
                def toggle(event=None):
                    g_header, g_body, arrow = self.group_frames[name]
                    if self.group_collapsed[name]:
                        g_body.pack(fill="x", after=g_header)
                        arrow.config(text="▾")
                        self.group_collapsed[name] = False
                    else:
                        g_body.pack_forget()
                        arrow.config(text="▸")
                        self.group_collapsed[name] = True
                return toggle

            toggle_func = make_toggle()
            for w in [g_header, arrow_label, title_label]:
                w.bind("<Button-1>", toggle_func)

            for task in TASKS:
                if task.get("group") == group:
                    self._build_task_card(g_body, task)

    def _build_task_card(self, parent, task):
        card_container = tk.Frame(parent, bg="#f8fafc")
        card_container.pack(fill="x", pady=3)

        card = tk.Frame(card_container, bg="white", bd=0,
                        highlightthickness=1, highlightbackground="#e2e8f0")
        card.pack(fill="x")

        row = tk.Frame(card, bg="white")
        row.pack(fill="x", padx=10, pady=8)

        var = tk.BooleanVar(value=False)
        self.check_vars[task["id"]] = var
        cb = tk.Checkbutton(row, variable=var,
                            bg="white", activebackground="white",
                            bd=0, cursor="hand2",
                            selectcolor="white",
                            font=("Segoe UI Symbol", 12))
        cb.pack(side="left", padx=(0, 6))

        tk.Label(row, text=task["icon"],
                 font=("Segoe UI Emoji", 14),
                 bg="white").pack(side="left", padx=(0, 8))

        text_col = tk.Frame(row, bg="white")
        text_col.pack(side="left", fill="x", expand=True)

        tk.Label(text_col, text=task["title"],
                 font=("微软雅黑", 9, "bold"),
                 bg="white", fg="#0f172a",
                 anchor="w").pack(anchor="w")

        tk.Label(text_col, text=task["short"],
                 font=("微软雅黑", 8),
                 bg="white", fg="#64748b",
                 anchor="w").pack(anchor="w")

        status_label = tk.Label(row, text="待检测",
                                font=("微软雅黑", 8, "bold"),
                                bg="white", fg="#94a3b8",
                                padx=8)
        status_label.pack(side="right")
        self.status_labels[task["id"]] = status_label

        # 反向按钮：任务有 undo_func 时显示，点击后执行反向还原
        if UNDO_MAP.get(task["id"]) is not None:
            def _make_undo(tid=task["id"], name=task["title"]):
                def _do_undo():
                    if not self._ensure_admin():
                        return
                    if not messagebox.askyesno("反向操作",
                                               f"确定执行「{name}」的反向还原吗？\n"
                                               f"（此操作会撤销之前做的修改）"):
                        return
                    self._run_undo(tid, name)
                return _do_undo
            tk.Button(row, text="↺还原",
                      font=("微软雅黑", 8),
                      bg="white", fg="#059669",
                      activebackground="#f0fdf4",
                      bd=1, relief="solid",
                      padx=6, pady=1, cursor="hand2",
                      command=_make_undo()).pack(side="right", padx=(0, 4))

        self.detail_shown[task["id"]] = False
        detail_btn = tk.Label(row, text="ⓘ",
                              font=("Segoe UI Symbol", 11),
                              bg="white", fg="#94a3b8",
                              cursor="hand2",
                              padx=6)
        detail_btn.pack(side="right")

        detail_frame = tk.Frame(card, bg="#f8fafc")

        def toggle_detail(event=None):
            if self.detail_shown[task["id"]]:
                detail_frame.pack_forget()
                self.detail_shown[task["id"]] = False
                detail_btn.config(fg="#94a3b8")
            else:
                if not detail_frame.winfo_children():
                    txt = tk.Text(detail_frame, wrap="word",
                                  font=("微软雅黑", 9),
                                  bg="#f8fafc", fg="#334155",
                                  bd=0, padx=12, pady=10,
                                  height=6)
                    txt.pack(fill="x")
                    txt.insert("1.0", task["detail"])
                    txt.config(state="disabled")
                detail_frame.pack(fill="x")
                self.detail_shown[task["id"]] = True
                detail_btn.config(fg="#6366f1")

        detail_btn.bind("<Button-1>", toggle_detail)

    def _build_footer(self):
        footer = tk.Frame(self.body, bg="#f8fafc")
        footer.pack(fill="x", padx=14, pady=12)

        tk.Label(footer, text="部分设置需要重启或注销后生效",
                 font=("微软雅黑", 8), fg="#94a3b8",
                 bg="#f8fafc").pack(side="left")

        tk.Button(footer, text="退出",
                  font=("微软雅黑", 8),
                  bg="white", fg="#ef4444",
                  bd=1, relief="solid", padx=10, pady=3,
                  cursor="hand2",
                  command=self._on_close).pack(side="right", padx=2)

        tk.Button(footer, text="关于",
                  font=("微软雅黑", 8),
                  bg="white", fg="#64748b",
                  bd=1, relief="solid", padx=10, pady=3,
                  cursor="hand2",
                  command=self.show_about).pack(side="right", padx=2)

    def select_all(self):
        for var in self.check_vars.values():
            var.set(True)

    def select_none(self):
        for var in self.check_vars.values():
            var.set(False)

    def _get_checked_tasks(self):
        return [t for t in TASKS if self.check_vars[t["id"]].get()]

    def _ensure_admin(self):
        if self.admin_ok:
            return True
        if not self._standalone:
            # 嵌入模式：打开工具时已询问过提权，用户选择了「否」，
            # 这里不再二次打扰，直接尝试执行；无权限的项目会逐项返回失败
            logger.info("以普通权限执行（打开时已放弃提权），无权限项目将失败")
            return True
        # 独立模式的兜底：整体提权重启
        ans = messagebox.askyesno(
            "需要管理员权限",
            "该操作需要管理员权限才能执行。\n\n是否以管理员身份重新启动？")
        if ans and run_as_admin():
            self._on_close()
        return False

    def _set_action_state(self, state):
        for b in self.action_buttons:
            try:
                b.config(state=state)
            except Exception:
                pass

    def run_checked(self):
        checked = self._get_checked_tasks()
        if not checked:
            messagebox.showinfo("提示", "请先勾选要执行的功能")
            return
        if not self._ensure_admin():
            return
        ans = messagebox.askyesno(
            "确认执行",
            f"即将执行已勾选的 {len(checked)} 项：\n\n" +
            "\n".join(f"· {t['title']}" for t in checked) + "\n\n确定继续？")
        if not ans:
            return
        self._run_tasks_async(checked, "执行结果")

    def run_all_tasks(self):
        if not self._ensure_admin():
            return
        ans = messagebox.askyesno(
            "全部执行",
            f"即将执行全部 {len(TASKS)} 项：\n\n" +
            "\n".join(f"· {t['title']}" for t in TASKS) + "\n\n确定继续？")
        if not ans:
            return
        self._run_tasks_async(TASKS, "全部执行结果")

    def _run_tasks_async(self, tasks, title):
        self._set_action_state("disabled")
        self.root.config(cursor="watch")
        self.root.update_idletasks()

        def worker():
            all_results = []
            for task in tasks:
                logger.info(f"开始执行：{task['title']}")
                try:
                    results = task["func"]()
                    for d, ok, m in results:
                        all_results.append((f"[{task['title']}] {d}", ok, m))
                        if ok:
                            logger.info(f"[{task['title']}] {d} -> {m}")
                        else:
                            logger.warning(f"[{task['title']}] {d} -> {m}")
                except Exception as e:
                    logger.exception(f"[{task['title']}] 执行异常：{e}")
                    all_results.append((task["title"], False, f"异常：{e}"))
            self.root.after(0, lambda: self._on_tasks_done(title, all_results))

        threading.Thread(target=worker, daemon=True).start()

    def _show_result_dialog(self, title, summary, results):
        """执行结果弹窗：可滚动，避免项目过多时底部内容看不到"""
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.configure(bg="#f8fafc")
        dlg.transient(self.root)
        dlg.resizable(True, True)
        dlg.geometry("620x480")
        dlg.minsize(420, 300)

        # 标题栏
        ok_count = sum(1 for _, ok, _ in results if ok)
        total = len(results)
        head_color = "#10b981" if ok_count == total else ("#ef4444" if ok_count == 0 else "#f59e0b")
        head = tk.Frame(dlg, bg="white")
        head.pack(fill="x")
        tk.Label(head, text=summary, font=("微软雅黑", 11, "bold"),
                 bg="white", fg=head_color, anchor="w").pack(
            side="left", padx=14, pady=12)
        tk.Frame(dlg, bg="#e2e8f0", height=1).pack(fill="x")

        # 可滚动结果区
        body = tk.Frame(dlg, bg="#f8fafc")
        body.pack(fill="both", expand=True, padx=14, pady=10)
        sb = tk.Scrollbar(body)
        sb.pack(side="right", fill="y")
        txt = tk.Text(body, font=("微软雅黑", 9), bd=0, bg="white",
                      relief="flat", wrap="word", spacing1=2, spacing3=2,
                      yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.config(command=txt.yview)
        txt.tag_config("ok", foreground="#10b981")
        txt.tag_config("fail", foreground="#dc2626")

        for d, ok, m in results:
            icon = "✅" if ok else "❌"
            txt.insert("end", f"{icon} {d}\n", "ok" if ok else "fail")
            txt.insert("end", f"      {m}\n\n")
        txt.config(state="disabled")

        # 底部按钮
        foot = tk.Frame(dlg, bg="#f8fafc")
        foot.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(foot, text="知道了", width=12,
                  font=("微软雅黑", 9, "bold"),
                  bg="#6366f1", fg="white", activebackground="#4f46e5",
                  activeforeground="white", bd=0, pady=6, cursor="hand2",
                  command=dlg.destroy).pack(side="right")

        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        try:
            self.root.update_idletasks()
            dlg.update_idletasks()
            x = self.root.winfo_rootx() + max(
                0, (self.root.winfo_width() - dlg.winfo_width()) // 2)
            y = self.root.winfo_rooty() + 60
            dlg.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _on_tasks_done(self, title, all_results):
        self._set_action_state("normal")
        self.root.config(cursor="")
        ok_count = sum(1 for _, ok, _ in all_results if ok)
        total = len(all_results)
        logger.info(f"{title}完成：成功 {ok_count}/{total}")
        if ok_count == total:
            summary = f"全部成功（{ok_count}/{total}）"
        elif ok_count == 0:
            summary = f"全部失败（0/{total}）"
        else:
            summary = f"部分成功（{ok_count}/{total}）"
        # 用可滚动窗口展示，项目再多也能看完
        self._show_result_dialog(title, summary, all_results)
        self.refresh_all_status()

    def refresh_all_status(self):
        if self._detecting:
            return
        self._detecting = True
        for task in TASKS:
            self.status_labels[task["id"]].config(text="检测中", fg="#94a3b8")
        threading.Thread(target=self._do_refresh_all, daemon=True).start()

    def _do_refresh_all(self):
        for task in TASKS:
            try:
                level, text = task["check"]()
                color = {"ok": "#10b981", "no": "#ef4444",
                         "part": "#f59e0b", "unknown": "#94a3b8"}.get(level, "#94a3b8")
                self.root.after(0, lambda t=text, c=color, id=task["id"]:
                                self.status_labels[id].config(text=t, fg=c))
            except Exception:
                self.root.after(0, lambda id=task["id"]:
                                self.status_labels[id].config(text="未知", fg="#94a3b8"))
            self.root.update_idletasks()
        self.root.after(0, self._update_summary)
        self.root.after(0, lambda: setattr(self, "_detecting", False))

    def _update_summary(self):
        total = len(TASKS)
        ok_count = no_count = part_count = unknown_count = 0
        for task in TASKS:
            label = self.status_labels.get(task["id"])
            if not label:
                continue
            text = label.cget("text")
            if "✅" in text:
                ok_count += 1
            elif "⚠️" in text:
                part_count += 1
            elif "❌" in text:
                no_count += 1
            else:
                unknown_count += 1

        parts = []
        if ok_count:
            parts.append(f"{ok_count} 已配置")
        if part_count:
            parts.append(f"{part_count} 部分")
        if no_count:
            parts.append(f"{no_count} 未配置")
        if unknown_count:
            parts.append(f"{unknown_count} 未知")

        text = f"共 {total} 项 · " + " · ".join(parts) if parts else f"共 {total} 项"
        self.summary_label.config(text=text, fg="#64748b")

    # ============ 备份 / 还原 ============
    def _backup_current_state(self):
        """遍历所有任务，调 check 函数快照当前状态，存 JSON"""
        # check 函数是只读查询，不需要 admin 权限
        snapshot = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "tasks": {}}
        for task in TASKS:
            try:
                status, label = task["check"]()
            except Exception as e:
                logger.error(f"备份失败 {task['id']}: {e}")
                snapshot["tasks"][task["id"]] = {"state": "error", "label": str(e)}
                continue
            snapshot["tasks"][task["id"]] = {"state": status, "label": label}
        try:
            with open(_backup_file(), "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            logger.info(f"已备份 {len(snapshot['tasks'])} 个任务的当前状态 → {_backup_file()}")
            messagebox.showinfo("备份完成",
                                f"已备份 {len(snapshot['tasks'])} 个配置项的当前状态\n"
                                f"备份时间：{snapshot['timestamp']}\n"
                                f"备份文件：{_backup_file()}")
        except Exception as e:
            logger.error(f"备份保存失败：{e}")
            messagebox.showerror("备份失败", str(e))

    def _restore_from_backup(self):
        """从 JSON 快照还原：只还原备份时已修改为 ok 的任务（执行反向 undo_func）"""
        path = _backup_file()
        if not os.path.exists(path):
            messagebox.showwarning("提示", f"未找到备份文件：\n{path}")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
        except Exception as e:
            logger.error(f"读取备份失败：{e}")
            messagebox.showerror("读取失败", str(e))
            return

        # 分析：哪些任务需要还原（备份时 state=="ok" 说明已修改过）
        tasks_data = snapshot.get("tasks", {})
        to_restore = []       # 需要执行反向 undo_func 的
        no_undo = []          # 有备份但没 undo_func 的
        not_changed = []      # 备份时就是 no 的，无需还原
        for tid, data in tasks_data.items():
            st = data.get("state")
            if st == "ok" and tid in UNDO_MAP:
                to_restore.append(tid)
            elif st == "ok":
                no_undo.append(tid)
            else:
                not_changed.append(tid)

        if not to_restore and not no_undo:
            messagebox.showinfo("提示", "备份中没有需要还原的项目（备份时都是默认状态）")
            return

        # 展示还原计划让用户确认
        lines = [f"备份时间：{snapshot.get('timestamp', '未知')}\n"]
        if to_restore:
            lines.append(f"✓ 将执行反向还原（{len(to_restore)} 项）：")
            for tid in to_restore:
                task = next((t for t in TASKS if t["id"] == tid), None)
                lines.append(f"  · {task['title'] if task else tid}")
        if no_undo:
            lines.append(f"\n⚠ 以下 {len(no_undo)} 项备份时有修改但暂无自动还原逻辑，需手动处理：")
            for tid in no_undo:
                task = next((t for t in TASKS if t["id"] == tid), None)
                lines.append(f"  · {task['title'] if task else tid}")
        lines.append(f"\n其余 {len(not_changed)} 项备份时就是默认状态，无需还原。")

        ans = messagebox.askyesno("还原确认", "\n".join(lines))
        if not ans:
            return

        self._ensure_admin()
        # 执行反向还原（后台线程，避免卡 UI）
        def worker():
            results = []
            for tid in to_restore:
                task = next((t for t in TASKS if t["id"] == tid), None)
                undo_fn = UNDO_MAP.get(tid)
                if not task or not undo_fn:
                    continue
                logger.info(f"🔙 还原中：{task['title']}")
                try:
                    items = undo_fn()
                    for name, ok, msg in items:
                        logger.info(f"  {'✅' if ok else '❌'} {name}: {msg}")
                        results.append((tid, name, ok))
                except Exception as e:
                    logger.error(f"还原 {tid} 异常：{e}")
                    results.append((tid, task["title"], False))
            self.root.after(0, lambda: self._on_restore_done(results, no_undo))

        threading.Thread(target=worker, daemon=True).start()

    def _on_restore_done(self, results, no_undo):
        """还原完成回调（主线程）"""
        ok_count = sum(1 for _, _, ok in results if ok)
        fail_count = len(results) - ok_count
        self.refresh_all_status()
        msg_lines = [f"还原完成：成功 {ok_count} 项，失败 {fail_count} 项"]
        if no_undo:
            msg_lines.append(f"\n⚠️ 以下 {len(no_undo)} 项暂无自动还原逻辑，请手动处理：")
            for tid in no_undo:
                task = next((t for t in TASKS if t["id"] == tid), None)
                msg_lines.append(f"  · {task['title'] if task else tid}")
        messagebox.showinfo("还原完成", "\n".join(msg_lines))
        logger.info(f"还原完成：成功 {ok_count}，失败 {fail_count}，无自动还原 {len(no_undo)}")

    def _run_undo(self, tid, name):
        """单项反向操作（卡片上的「↺还原」按钮点击后调用，后台线程执行）"""
        undo_fn = UNDO_MAP.get(tid)
        if not undo_fn:
            return

        def worker():
            try:
                items = undo_fn()
                for item_name, ok, msg in items:
                    logger.info(f"🔙 反向 {name} / {item_name}: {'✅' if ok else '❌'} {msg}")
            except Exception as e:
                logger.error(f"反向 {name} 异常：{e}")
            self.root.after(0, self.refresh_all_status)

        threading.Thread(target=worker, daemon=True).start()

    def show_about(self):
        messagebox.showinfo(
            "关于",
            "Windows 配置助手\n\n"
            "功能分组：\n"
            "· 网络共享\n· 系统安全\n· 系统优化\n· 区域与显示\n\n"
            "点击分组标题可折叠，点击 ⓘ 查看详情。\n\n"
            "部分操作需要管理员权限。\n"
            "修改后可能需要重启或注销生效。")


# ================= 启动入口 =================
def create_config_app(parent=None):
    """供主程序调用（parent=主窗口）。
    非管理员时在「打开工具时」就询问是否提权，与独立运行行为一致：
      · 是 → 以管理员身份单独打开配置助手，不在主程序内开嵌入窗口
      · 否 → 以普通权限在主程序内打开，需要权限的项目执行时会逐项失败
    """
    if parent is not None and not is_admin():
        ans = messagebox.askyesno(
            "管理员权限",
            "本工具的大部分功能需要管理员权限。\n\n"
            "是否以管理员身份打开配置助手？\n\n"
            "· 选择「是」→ 提权后单独打开（推荐）\n"
            "· 选择「否」→ 在当前主程序窗口内打开，需要权限的项目会执行失败",
            parent=parent)
        if ans:
            if run_as_admin():
                logger.info("主程序调用：用户选择提权，已启动管理员身份的独立配置助手")
                return None
            messagebox.showwarning(
                "提示", "未获得管理员权限（UAC 被取消或启动失败），将以普通权限打开。", parent=parent)
    return ConfigApp(parent)


def main():
    logger.info("配置助手独立模式启动，检查管理员权限")
    if not is_admin():
        root = tk.Tk()
        root.withdraw()
        ans = messagebox.askyesno(
            "管理员权限",
            "本工具的部分功能需要管理员权限。\n\n"
            "是否以管理员身份运行？\n\n"
            "· 选择「是」→ 提权后运行（推荐）\n"
            "· 选择「否」→ 普通权限运行，部分功能会失败")
        root.destroy()
        if ans:
            run_as_admin()
            return
    ConfigApp()


if __name__ == "__main__":
    main()