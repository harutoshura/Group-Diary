# -*- coding: utf-8 -*-
"""
群日记 · 启动器

直接双击本文件即可运行（Windows 会把 .pyw 关联到 pythonw.exe）：
不会弹出黑色命令行窗口，出错时会弹一个中文对话框告诉你原因。

和 启动群日记.bat 的区别：
    .pyw  —— 完全绕开 cmd.exe，不存在批处理编码问题，推荐日常使用
    .bat  —— 兜底方案，万一 .pyw 的文件关联被破坏时还能用
"""

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))

# 双击时的工作目录不一定是我们这个文件夹，统一切过来
try:
    os.chdir(HERE)
except Exception:
    pass
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def show_error(title, text):
    """尽量用图形对话框报错；没有 tkinter 就退回写日志。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(title, text)
        root.destroy()
        return
    except Exception:
        pass
    try:
        with open(os.path.join(HERE, "logs", "launcher-error.log"), "a", encoding="utf-8") as fh:
            fh.write("\n" + "=" * 60 + "\n" + title + "\n" + text + "\n")
    except Exception:
        pass


def main():
    try:
        import app
    except Exception:
        show_error(
            "群日记 · 启动失败",
            "无法加载同目录下的 app.py。\n\n"
            "请确认 app.py 和本文件在同一个文件夹里，且没有被杀毒软件隔离。\n\n"
            + traceback.format_exc(),
        )
        return 1

    try:
        app.main()
    except SystemExit:
        raise
    except Exception:
        show_error("群日记 · 运行出错", traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
