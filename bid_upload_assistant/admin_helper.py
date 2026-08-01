"""仅用于管理员权限下关闭/启动编制工具，绝不终止进程或操作投标文件。"""
from __future__ import annotations

import argparse
import ctypes
import time
from ctypes import wintypes
from pathlib import Path

import psutil


WM_CLOSE = 0x0010


def running_pids() -> set[int]:
    return {
        proc.pid
        for proc in psutil.process_iter(["name"])
        if (proc.info.get("name") or "").lower() == "tenderbidapp.exe"
    }


def close_gracefully() -> int:
    pids = running_pids()
    if not pids:
        print("编制工具未运行")
        return 0
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    sent: list[int] = []

    @callback_type
    def callback(hwnd: int, _lparam: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            sent.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if not sent:
        print("未找到可关闭的编制工具主窗口；未强制结束进程")
        return 2
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not running_pids():
            print("编制工具已正常关闭")
            return 0
        time.sleep(0.5)
    print("软件没有在等待时间内关闭；未强制结束进程")
    return 3


def launch_debug(exe_path: Path) -> int:
    if running_pids():
        print("编制工具仍在运行，拒绝启动第二个实例")
        return 2
    if not exe_path.is_file():
        print(f"找不到编制工具: {exe_path}")
        return 2
    args = "--remote-debugging-port=9222 --remote-allow-origins=http://127.0.0.1:9222"
    result = ctypes.windll.shell32.ShellExecuteW(None, "open", str(exe_path), args, str(exe_path.parent), 1)
    if result <= 32:
        print(f"启动失败: {result}")
        return 3
    print("已请求以 CEF 调试模式启动编制工具")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("close", "launch-debug"))
    parser.add_argument("--exe", default=r"C:\Program Files\投标文件编制工具\tenderBidApp.exe")
    args = parser.parse_args()
    return close_gracefully() if args.action == "close" else launch_debug(Path(args.exe))


if __name__ == "__main__":
    raise SystemExit(main())
