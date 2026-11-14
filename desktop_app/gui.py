from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from tkinter import font as tkfont
from PIL import ImageGrab, ImageTk
from .domain import Profile, Rect, PauseRequired, fingerprint
from .storage import app_dir, load_profile, save_profile, CredentialStore, Journal
from .windows import enable_dpi, windows, client_rect, Desktop, emergency_monitor, demo_target,Target,U,process_name
from .vision import LocalOCR, Perception
from .ai import OnlineAI,DeferredOnlineAI
from .runner import Runner
from .course import CourseRunner
from .connection import probe_connection


REGIONS = {"章节题目区域": "question", "视频弹题区域": "popup", "视频画面及控制条": "player",
           "右侧课时目录": "catalog", "交卷确认弹窗（可选）": "dialog"}


class Selector:
    def __init__(self, app, image, origin, callback, multiple=False):
        self.top = tk.Toplevel(app.root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.geometry(f"{image.width}x{image.height}+{origin.x}+{origin.y}")
        self.image = ImageTk.PhotoImage(image)
        self.canvas = tk.Canvas(self.top, width=image.width, height=image.height, highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self.image)
        self.canvas.create_rectangle(0, 0, image.width, 42, fill="#172b4d", outline="")
        self.canvas.create_text(18, 21, anchor="w", text="拖动框选区域；Enter 保存；Esc 取消" + ("；可连续框选多个空，按阅读顺序" if multiple else ""), fill="white", font=("Microsoft YaHei UI", 12))
        self.start = None
        self.drag = None
        self.rects = []
        self.callback, self.multiple, self.app = callback, multiple, app
        self.canvas.bind("<ButtonPress-1>", self.down)
        self.canvas.bind("<B1-Motion>", self.move)
        self.canvas.bind("<ButtonRelease-1>", self.up)
        self.top.bind("<Return>", self.done)
        self.top.bind("<Escape>", lambda e: self.cancel())
        self.top.focus_force()

    def down(self, event):
        if not self.multiple:
            self.canvas.delete("selection")
            self.rects.clear()
        self.start = (event.x, event.y)
        self.drag = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#22c55e", width=3, tags="selection")

    def move(self, event):
        if self.start and self.drag:
            self.canvas.coords(self.drag, *self.start, event.x, event.y)

    def up(self, event):
        if self.start:
            x, y = self.start
            rect = Rect(min(x, event.x), min(y, event.y), abs(event.x-x), abs(event.y-y))
            if rect.w >= 15 and rect.h >= 15:
                self.rects.append(rect)
                self.canvas.create_text(rect.x+8, rect.y+8, anchor="nw", text=str(len(self.rects)), fill="#b91c1c", font=("Microsoft YaHei UI", 14, "bold"), tags="selection")
            self.start = None

    def done(self, event=None):
        if self.rects:
            self.top.destroy()
            self.app.root.deiconify()
            self.callback(self.rects)

    def cancel(self):
        self.top.destroy()
        self.app.root.deiconify()


class App:
    def __init__(self, root):
        self.root = root
        self.data = app_dir()
        from modules.logger import Logger
        Logger.get_runtime_root = staticmethod(lambda: str(self.data))
        Logger._instance = None
        self.logger = Logger()
        try:
            self.profile = load_profile(self.data)
        except PauseRequired as exc:
            self.profile = Profile()
            messagebox.showwarning("配置需要修复", str(exc))
        self.credentials = CredentialStore()
        self.journal = Journal(self.data / "journal.sqlite3")
        self.vision = Perception(LocalOCR())
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.shutdown = threading.Event()
        self.messages = queue.Queue()
        self.worker = None
        self.runner = None
        self.last_question = None
        self.targets = []
        self.target = None
        self.busy = False
        self.demo_running=False
        self.status = tk.StringVar(value="待配置 · 自动提交已启用")
        self.target_text = tk.StringVar()
        self.task_text = tk.StringVar(value=self.profile.task_name)
        self.base_text = tk.StringVar(value=self.profile.base_url)
        self.model_text = tk.StringVar(value=self.profile.model)
        self.key_text = tk.StringVar()
        self.mode_text=tk.StringVar(value='仅答题' if self.profile.mode=='quiz' else '看课＋答题（整门课程）')
        self.mute_text=tk.BooleanVar(value=self.profile.auto_mute)
        self.ai_status=tk.StringVar(value='尚未验证连接。填写 Key 后点击“测试连接”。')
        self.ai_test_running=False
        self.ai_test_id=0
        self.ai_test_stop=threading.Event()
        self.ai_verified_signature=None
        try:
            self.key_text.set(self.credentials.get())
        except Exception:
            pass
        self.region_name = tk.StringVar(value=next(iter(REGIONS)))
        self.root.title("智慧树桌面助手 · Autovisor Desktop 0.2.1 Beta2")
        self.root.geometry(f"1120x{min(1080,self.root.winfo_screenheight()-120)}")
        self.root.minsize(850, 680)
        self.root.configure(bg="#f3f5f8")
        for name in ('TkDefaultFont','TkTextFont','TkMenuFont'):
            tkfont.nametofont(name).configure(family='Microsoft YaHei UI',size=11)
        style = ttk.Style()
        try:
            import sv_ttk
            sv_ttk.set_theme("light")
        except ImportError:
            style.theme_use("clam")
        style.configure('TLabel',font=('Microsoft YaHei UI',11))
        style.configure('TButton',font=('Microsoft YaHei UI',11),padding=(12,7))
        style.configure('Accent.TButton',font=('Microsoft YaHei UI',11),padding=(12,7))
        style.configure('TNotebook.Tab',font=('Microsoft YaHei UI',11),padding=(14,7))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 23, "bold"))
        style.configure("Status.TLabel", foreground="#1d4ed8", font=("Microsoft YaHei UI", 12, "bold"))
        outer = ttk.Frame(root, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="智慧树桌面助手", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="正常播放 / 视频弹题 / 自动切课 / 章节作业自动答题与提交", foreground="#475569").pack(anchor="w", pady=(6,16))
        self.tabs = ttk.Notebook(outer)
        self.run_tab, self.cal_tab, self.ai_tab, self.help_tab = [ttk.Frame(self.tabs, padding=20) for _ in range(4)]
        for tab, text in zip((self.run_tab, self.cal_tab, self.ai_tab, self.help_tab), ("运行", "区域校准", "AI 设置", "使用说明")):
            self.tabs.add(tab, text=text)
        self.build_run()
        self.build_calibration()
        self.build_ai()
        self.build_help()
        ttk.Label(outer, text="Ctrl+Alt+Shift+F12 紧急停止  ·  鼠标移至主屏幕角落也可停止  ·  保持课程窗口在前台", foreground="#475569",wraplength=1000).pack(side='bottom',anchor="w", pady=(16,0))
        self.tabs.pack(fill="both", expand=True)
        threading.Thread(target=emergency_monitor, args=(self.stop,self.shutdown,self.emit), daemon=True).start()
        self.root.after(100, self.drain)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    def build_run(self):
        tab = self.run_tab
        ttk.Label(tab, textvariable=self.status, style="Status.TLabel", wraplength=850).pack(fill="x", pady=(0,18))
        modes=ttk.Frame(tab);modes.pack(fill='x',pady=(0,12))
        ttk.Label(modes,text='运行模式').pack(side='left')
        chooser=ttk.Combobox(modes,textvariable=self.mode_text,values=['看课＋答题（整门课程）','仅答题'],state='readonly',font=('Microsoft YaHei UI',11),width=28)
        chooser.pack(side='left',padx=16)
        chooser.bind('<<ComboboxSelected>>',lambda e:self.start_button.configure(text='开始自动答题' if self.mode_text.get()=='仅答题' else '开始看课＋答题'))
        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Label(row, text="课程 / 作业名称").pack(side="left")
        ttk.Entry(row, textvariable=self.task_text,font=('Microsoft YaHei UI',11)).pack(side="left", fill="x", expand=True, padx=(16,0))
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=14)
        ttk.Label(row, text="目标窗口").pack(side="left")
        self.targets_box = ttk.Combobox(row, textvariable=self.target_text, state="readonly",font=('Microsoft YaHei UI',11))
        self.targets_box.pack(side="left", fill="x", expand=True, padx=16)
        ttk.Button(row, text="刷新", command=self.refresh).pack(side="left")
        ttk.Button(row, text="绑定窗口", command=self.bind).pack(side="left", padx=(8,0))
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(4,20))
        self.start_button = ttk.Button(row, text="开始自动答题" if self.profile.mode=='quiz' else '开始看课＋答题', command=self.start,style='Accent.TButton')
        self.start_button.pack(side="left")
        ttk.Button(row, text="暂停", command=lambda: self.pause.set()).pack(side="left", padx=8)
        ttk.Button(row, text="继续", command=self.resume).pack(side="left")
        ttk.Button(row, text="停止", command=self.stop.set).pack(side="left", padx=8)
        ttk.Button(row, text="离线演示", command=self.demo).pack(side="right")
        ttk.Checkbutton(tab,text='自动静音课程标签页（默认开启，已静音时不会反向开启声音）',variable=self.mute_text).pack(anchor='w',pady=(0,12))
        ttk.Label(tab, text="当前题目与答案", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        self.answer_text = scrolledtext.ScrolledText(tab, height=7, wrap="word", font=("Microsoft YaHei UI", 11), relief="flat", bg="#f1f5f9", fg="#172b4d", padx=12, pady=10)
        self.answer_text.pack(fill="both", expand=True, pady=(8,16))
        self.answer_text.configure(state="disabled")
        ttk.Label(tab, text="运行记录", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        self.logs = scrolledtext.ScrolledText(tab, height=6, wrap="word", font=("Microsoft YaHei UI", 10), relief="flat", bg="#f8fafc", fg="#334155", padx=12, pady=8)
        self.logs.pack(fill="both", expand=True, pady=(8,0))
        self.logs.configure(state="disabled")

    def build_calibration(self):
        tab = self.cal_tab
        ttk.Label(tab, text="先绑定窗口，再分别框选需要自动操作的区域。", wraplength=800).pack(anchor="w")
        ttk.Label(tab, text="整课模式需要视频、目录、视频弹题和章节题目四个区域。题目需包含题型、题号和全部输入控件。\n目录会自动展开。点击校准后有 3 秒切回课程窗口；拖动框选后按 Enter 保存。", foreground="#475569", wraplength=800).pack(anchor="w", pady=12)
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=16)
        ttk.Combobox(row, textvariable=self.region_name, values=list(REGIONS), state="readonly", width=28,font=('Microsoft YaHei UI',11)).pack(side="left")
        ttk.Button(row, text="框选区域", command=self.calibrate).pack(side="left", padx=8)
        ttk.Button(row, text="识别预览", command=self.preview).pack(side="left")
        ttk.Button(row, text="标注填空", command=self.calibrate_blanks).pack(side="left", padx=8)
        self.regions_text = tk.StringVar()
        ttk.Label(tab, textvariable=self.regions_text, justify="left", foreground="#334155").pack(anchor="w", pady=12)
        ttk.Separator(tab).pack(fill="x", pady=12)
        ttk.Label(tab, text="提交状态不明确时，先在浏览器检查已有提交结果。\n确认需要重新运行后，可清除运行记录；此操作不会修改平台上的答案。", wraplength=800).pack(anchor="w", pady=8)
        ttk.Button(tab, text="清除本地运行记录", command=self.reset_journal).pack(anchor="w", pady=8)
        self.refresh_regions()

    def build_ai(self):
        tab = self.ai_tab
        for text, var, hint, secret in (("接口地址", self.base_text, "默认已配置 DeepSeek 官方接口；也支持其他完整 /chat/completions 地址", False),
                                      ("模型名称", self.model_text, "填写你的服务支持的准确模型 ID", False),
                                      ("API Key", self.key_text, "保存到 Windows 凭据管理器，普通配置文件不保存密钥", True)):
            ttk.Label(tab, text=text, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=(12,6))
            entry = ttk.Entry(tab, textvariable=var, show="•" if secret else "",font=('Microsoft YaHei UI',11))
            entry.pack(fill="x")
            ttk.Label(tab, text=hint, foreground="#475569", wraplength=800).pack(anchor="w", pady=(5,4))
        ttk.Label(tab, text="测试会发送一道简单题目，只验证当前输入。使用前请点“保存配置”；仅保存 Key 不代表连接已经验证。", wraplength=800).pack(anchor="w", pady=12)
        actions=ttk.Frame(tab);actions.pack(fill='x',pady=(4,12))
        ttk.Button(actions,text='保存配置',command=self.save).pack(side='left')
        self.ai_test_button=ttk.Button(actions,text='测试连接',command=self.test_ai,style='Accent.TButton')
        self.ai_test_button.pack(side='left',padx=10)
        self.ai_cancel_button=ttk.Button(actions,text='取消测试',command=self.cancel_ai_test,state='disabled')
        self.ai_cancel_button.pack(side='left')
        self.ai_progress=ttk.Progressbar(tab,mode='indeterminate',maximum=100)
        self.ai_progress.pack(fill='x',pady=(0,10))
        self.ai_status_label=ttk.Label(tab,textvariable=self.ai_status,foreground='#334155',wraplength=800,font=('Microsoft YaHei UI',11,'bold'))
        self.ai_status_label.pack(anchor='w',fill='x')
        for variable in (self.base_text,self.model_text,self.key_text):
            variable.trace_add('write',self.ai_config_changed)

    def build_help(self):
        text = """首次使用
1. 在浏览器里手动登录智慧树，打开当前课程，缩放设为 100%。
2. 运行页刷新并绑定正确的 Edge / Chrome 窗口，填写课程或作业名称。
3. 整课模式校准视频、目录、视频弹题和章节题目四个区域；视频弹题可框选整个视频内容区。
4. “识别预览”检查题型、题号、总题数、选项和填空框数量。识别不出填空框时使用“标注填空”。
5. 配置在线 AI，验证接口后点“开始看课＋答题”。程序会尝试切到课程窗口。

运行行为
整课模式会扫描并展开目录，正常播放未完成课时，处理视频弹题，再完成章节作业并返回课程。
视频结束后还会核对当前课时的完成标记，最后重新扫描目录确认视频和章测全部完成。
章节作业首次从第 1 题开始，程序逐题填写与复核，全部完成后自动提交并确认。
保留本地记录可从中断题恢复；提交状态不明确时不会自动重交。
填空支持普通文本框。窗口尺寸变化、页面验证、未知控件或识别不完整时暂停。
需要窗口始终可见；仅支持主屏幕。正式考试、图片作答和公式编辑器不在本版适配范围。

离线演示
整课模式点击“离线演示”，会自动运行两章四节视频、弹题和两份章节作业，无需密钥和手动校准。
演示采用本地计时与固定答案，不连接智慧树，不调用在线 AI。仅答题模式保留原来的四题演示。

状态与数据
配置、运行记录和日志在 %LOCALAPPDATA%\\AutovisorDesktop。
程序不读取浏览器 Cookie，不使用扩展、网页脚本或浏览器调试端口。
AI 可能答错；程序不保证免检测。真实课程适配和正确率需分别验证。

来源：CXRunfree/Autovisor（MIT）桌面视觉衍生版；原作者版权与许可见 LICENSE。
"""
        box = scrolledtext.ScrolledText(self.help_tab, wrap="word", font=("Microsoft YaHei UI", 11), relief="flat", padx=12, pady=10)
        box.pack(fill="both", expand=True)
        box.insert("1.0", text)
        box.configure(state="disabled")

    def emit(self, kind, value):
        self.messages.put((kind, value))

    def drain(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind in {"log", "paused", "stopped", "error"}:
                    self.logs.configure(state="normal")
                    self.logs.insert("end", str(value)+"\n")
                    self.logs.see("end")
                    self.logs.configure(state="disabled")
                    self.logger.write_log(str(value)+"\n", level="WARN" if kind in {"paused", "error"} else "INFO")
                    if kind != "log":
                        self.status.set(("已暂停 · " if kind == "paused" else "") + str(value))
                if kind=='course':
                    self.status.set(('离线演示 · ' if self.demo_running else '')+value['message']+f"  [{value['done']}/{value['total']}]")
                elif kind=='completed':
                    self.status.set(('离线演示完成' if self.demo_running else '整门课程完成')+f" · 已复核 {value['tasks']} 项任务")
                    if self.demo_running and hasattr(self,'demo_instance'):
                        self.demo_instance.summary()
                if kind == "view":
                    if value.question:
                        self.last_question = value.question
                        q = value.question
                        self.status.set(f"第 {q.index} / {q.total or '?'} 题 · {q.kind} · {len(q.blanks) if q.kind=='blank' else len(q.options)} 个{'空' if q.kind=='blank' else '选项'}")
                elif kind == "answer":
                    q, answer = value
                    self.answer_text.configure(state="normal")
                    self.answer_text.delete("1.0", "end")
                    self.answer_text.insert("end", f"第 {q.index} 题\n{q.text}\n\n答案：{json.dumps(answer.values, ensure_ascii=False)}\n{answer.explanation}")
                    self.answer_text.configure(state="disabled")
                elif kind == "callback":
                    value()
                elif kind == 'ai_test_result':
                    self.finish_ai_test(*value)
        except queue.Empty:
            pass
        if not self.shutdown.is_set():
            self.root.after(100, self.drain)

    def refresh(self):
        self.targets = windows()
        self.targets_box["values"] = [f"{t.title}  [{t.hwnd}]" for t in self.targets]
        if self.targets:
            self.targets_box.current(0)

    def bind(self):
        idx = self.targets_box.current()
        if idx < 0:
            messagebox.showinfo("选择窗口", "请先打开课程或演示窗口，然后刷新。")
            return
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("任务仍在运行", "请先停止当前任务，再绑定新窗口。")
            return
        self.target = self.targets[idx]
        rect = client_rect(self.target.hwnd)
        if (self.profile.width, self.profile.height) != (rect.w, rect.h):
            self.profile.regions.clear()
            self.profile.blank_overrides.clear()
        self.profile.width, self.profile.height = rect.w, rect.h
        self.status.set("已绑定 · " + self.target.title)
        self.refresh_regions()
        self.save(False)

    def save(self, inform=True):
        if self.worker and self.worker.is_alive():
            self.status.set('请先停止任务，再修改 AI 配置或作业名称。')
            return False
        self.profile.task_name = self.task_text.get().strip() or "当前课程"
        self.profile.base_url = self.base_text.get().strip()
        self.profile.model = self.model_text.get().strip()
        self.profile.mode='quiz' if self.mode_text.get()=='仅答题' else 'course'
        self.profile.auto_mute=self.mute_text.get()
        try:
            self.credentials.set(self.key_text.get().strip())
            save_profile(self.data, self.profile)
            if inform:
                self.status.set("配置已保存。")
                self.ai_status.set('配置已保存。'+('当前配置已验证可用。' if self.ai_verified_signature==self.ai_signature() else '连接尚未验证，请点击“测试连接”。'))
        except Exception:
            messagebox.showerror("配置未保存", "Windows 凭据管理器或配置文件写入失败。请检查当前用户权限。")
            return False
        return True

    def refresh_regions(self):
        if hasattr(self, "regions_text"):
            values = [f"窗口：{self.profile.width} × {self.profile.height}"]
            for label, key in REGIONS.items():
                r = self.profile.regions.get(key)
                values.append(f"{label}：" + (f"已校准 ({r.x}, {r.y}, {r.w}, {r.h})" if r else "未校准"))
            self.regions_text.set("\n".join(values))

    def delayed_capture(self, callback):
        if not self.target:
            messagebox.showinfo("尚未绑定", "请先在运行页绑定课程窗口。")
            return
        if self.worker and self.worker.is_alive() and not self.pause.is_set():
            messagebox.showinfo("请先暂停", "先暂停任务，再进行预览或区域校准。")
            return
        self.status.set("3 秒后截图，请切回目标窗口。")
        self.root.iconify()
        def capture():
            try:
                desktop = Desktop(self.target, self.profile, threading.Event(), threading.Event())
                image = desktop.capture()
                callback(image, client_rect(self.target.hwnd))
            except Exception as exc:
                self.root.deiconify()
                self.status.set(str(exc) if isinstance(exc, PauseRequired) else "截图失败，请检查目标窗口。")
        self.root.after(3000, capture)

    def calibrate(self):
        name = REGIONS[self.region_name.get()]
        def received(image, origin):
            def selected(rects):
                self.profile.regions[name] = rects[0]
                save_profile(self.data, self.profile)
                self.refresh_regions()
                self.status.set("区域已保存，请进行识别预览。")
            Selector(self, image, origin, selected)
        self.delayed_capture(received)

    def calibrate_blanks(self):
        if not self.last_question or self.last_question.kind != "blank":
            messagebox.showinfo("先预览填空题", "请打开填空题，执行识别预览后再标注。")
            return
        key = getattr(self.last_question, "override_key", None)
        if not key:
            return
        def received(image, origin):
            def selected(rects):
                self.profile.blank_overrides[key] = rects
                save_profile(self.data, self.profile)
                self.status.set(f"已标注 {len(rects)} 个填空框，请再次预览。")
            Selector(self, image, origin, selected, multiple=True)
        self.delayed_capture(received)

    def preview(self):
        if self.busy:
            self.status.set('正在识别或测试接口，请等待当前操作完成。')
            return
        def received(image, origin):
            self.root.deiconify()
            self.status.set("正在识别，首次启动可能需要下载模型……")
            self.busy=True
            def work():
                try:
                    view = self.vision.inspect(image, self.profile)
                    self.emit("view", view)
                    if view.blocked:
                        self.emit("paused", view.blocked)
                    elif view.question:
                        q = view.question
                        details = f"预览：{q.kind}，题号 {q.index}，共 {q.total or '未识别'} 题；{len(q.blanks)} 个空，{len(q.options)} 个选项。"
                        self.emit("log", details)
                        from .domain import Answer
                        self.emit("answer", (q, Answer([], "这是识别预览，尚未发送给 AI 或执行点击。")))
                    else:
                        self.emit("log", "当前未识别到题目；请检查题目区域与页面题型文字。")
                except Exception as exc:
                    self.emit("paused", str(exc) if isinstance(exc, PauseRequired) else f"识别失败：{type(exc).__name__}")
                finally:
                    self.emit('callback',lambda:setattr(self,'busy',False))
            threading.Thread(target=work, daemon=True).start()
        self.delayed_capture(received)

    def start(self):
        if self.busy:
            self.status.set('请等待识别或接口测试结束后再开始。')
            return
        if self.worker and self.worker.is_alive():
            self.status.set("当前任务仍在运行；暂停后可点继续。")
            return
        if not self.target or not self.profile.regions:
            messagebox.showinfo("先完成准备", "请先绑定窗口并校准题目或视频区域。")
            return
        if not self.save(False):
            return
        self.stop.clear()
        self.pause.clear()
        try:
            if demo_target(self.target):
                from .demo import DemoAI
                ai = DemoAI()
                self.profile.task_name = "离线演示"
            else:
                if self.profile.mode=='course' and self.profile.task_name in {'当前课程','离线演示'}:
                    raise PauseRequired('请填写页面上显示的真实课程名称，再开始整课任务。')
                ai = DeferredOnlineAI(self.profile,self.credentials.get,self.stop) if self.profile.mode=='course' else OnlineAI(self.profile.base_url,self.profile.model,self.key_text.get().strip(),self.stop)
            run_profile=Profile.from_dict(self.profile.to_dict())
            desktop = Desktop(self.target, run_profile, self.stop, self.pause)
            runner_type=CourseRunner if run_profile.mode=='course' else Runner
            self.runner = runner_type(desktop, self.vision, ai, self.journal, run_profile, self.stop, self.pause, self.emit)
            self.worker = threading.Thread(target=self.runner.run, daemon=True)
            self.worker.start()
            self.status.set("准备开始 · 请在 5 秒内切回课程窗口")
            try:
                desktop.activate()
            except PauseRequired:
                pass
        except PauseRequired as exc:
            messagebox.showerror("无法开始", str(exc))

    def resume(self):
        if self.worker and self.worker.is_alive():
            if not self.demo_running:
                self.runner.profile.regions=dict(self.profile.regions)
                self.runner.document=None
                if isinstance(self.runner,CourseRunner):
                    self.runner.scanner.region=self.runner.profile.regions['catalog']
            self.pause.clear()
            self.status.set("3 秒后继续，请切回课程窗口。")
            try:
                self.runner.desktop.activate()
            except PauseRequired:
                pass
        else:
            self.start()

    def reset_journal(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("请先停止", "请先停止任务，再清除运行记录。")
            return
        if messagebox.askyesno("清除本地记录", "这会清除本地作答与提交记录。请先检查浏览器里的提交结果，确认需要重新开始。"):
            self.journal.reset()
            self.status.set("本地记录已清除。")

    def ai_signature(self):
        return fingerprint(self.base_text.get().strip()+'\0'+self.model_text.get().strip()+'\0'+self.key_text.get().strip())

    def ai_config_changed(self,*args):
        self.ai_verified_signature=None
        if not self.ai_test_running:
            self.ai_status.set('配置已更改，尚未验证。点击“测试连接”检查当前输入。')
            self.ai_status_label.configure(foreground='#334155')

    def test_ai(self):
        if self.busy or self.worker and self.worker.is_alive():
            self.ai_status.set('请先停止看课任务，或等待当前识别结束，再测试连接。')
            return
        address,model,key=self.base_text.get().strip(),self.model_text.get().strip(),self.key_text.get().strip()
        if not key or not model:
            self.ai_status.set('尚未开始测试：请填写 API Key 和模型名称。')
            self.ai_status_label.configure(foreground='#b91c1c')
            return
        self.ai_test_id+=1;token=self.ai_test_id
        self.ai_test_signature=self.ai_signature()
        self.ai_started_at=time.monotonic()
        self.ai_test_stop=threading.Event();stop=self.ai_test_stop
        self.ai_test_running=True;self.busy=True
        self.ai_test_button.configure(text='测试中…',state='disabled')
        self.ai_cancel_button.configure(state='normal')
        self.ai_progress.start(12)
        self.ai_status_label.configure(foreground='#1d4ed8')
        self.ai_status.set('正在测试连接 · 0 秒；结果会显示在这里。')
        self.root.after(500,lambda:self.tick_ai_test(token))
        def work():
            result=probe_connection(address,model,key,stop)
            self.emit('ai_test_result',(token,result))
        threading.Thread(target=work,daemon=True).start()

    def tick_ai_test(self,token):
        if not self.ai_test_running or token!=self.ai_test_id:return
        elapsed=int(time.monotonic()-self.ai_started_at)
        if elapsed>=45:
            self.ai_test_stop.set()
            self.finish_ai_test(token,{'status':'failed','message':'测试等待超时，请检查网络、接口地址后重试。','elapsed':elapsed})
            return
        self.ai_status.set(f'正在测试连接 · 已等待 {elapsed} 秒；可点击“取消测试”。')
        self.root.after(500,lambda:self.tick_ai_test(token))

    def cancel_ai_test(self):
        if not self.ai_test_running:return
        self.ai_test_stop.set()
        self.finish_ai_test(self.ai_test_id,{'status':'cancelled','message':'已取消等待；正在进行的请求会按网络超时结束。','elapsed':round(time.monotonic()-self.ai_started_at,1)})

    def finish_ai_test(self,token,result):
        if token!=self.ai_test_id or not self.ai_test_running:return
        self.ai_test_running=False;self.busy=False
        self.ai_progress.stop()
        self.ai_test_button.configure(text='重新测试',state='normal')
        self.ai_cancel_button.configure(state='disabled')
        changed=self.ai_test_signature!=self.ai_signature()
        if changed and result['status'] in {'passed','warning'}:
            result={**result,'status':'warning','message':'本次请求已连接，但输入已更改，请重新测试当前配置。'}
        if result['status']=='passed':self.ai_verified_signature=self.ai_signature()
        color={'passed':'#166534','failed':'#b91c1c','warning':'#92400e','cancelled':'#475569'}[result['status']]
        self.ai_status_label.configure(foreground=color)
        message=result['message']+f"  用时 {result['elapsed']} 秒。"
        self.ai_status.set(message);self.status.set(message)
        self.emit('log',message)

    def demo(self):
        if self.worker and self.worker.is_alive():
            self.status.set('请先停止当前任务，再打开离线演示。')
            return
        if self.mode_text.get()!='仅答题':
            from .course_demo import CourseDemo,CourseDemoAI
            if hasattr(self,'demo_instance') and self.demo_instance.root.winfo_exists():
                self.demo_instance.root.destroy()
            top=tk.Toplevel(self.root)
            self.demo_instance=CourseDemo(top)
            top.update_idletasks()
            hwnd=int(U.GetAncestor(top.winfo_id(),2))
            profile=self.demo_instance.profile()
            actual=client_rect(hwnd);profile.width,profile.height=actual.w,actual.h
            self.stop.clear();self.pause.clear();self.demo_running=True
            journal=Journal(self.data/'demo-course.sqlite3');journal.reset()
            desktop=Desktop(Target(hwnd,CourseDemo.title,process_name(hwnd)),profile,self.stop,self.pause)
            self.runner=CourseRunner(desktop,self.vision,CourseDemoAI(),journal,profile,self.stop,self.pause,self.emit)
            def work():
                try:self.runner.run()
                finally:
                    journal.close()
                    self.emit('callback',lambda:setattr(self,'demo_running',False))
            self.worker=threading.Thread(target=work,daemon=True);self.worker.start()
            top.protocol('WM_DELETE_WINDOW',lambda:(self.stop.set(),top.destroy()))
            top.after(100,lambda:(top.lift(),top.focus_force(),desktop.activate()))
            self.status.set('整课离线演示已自动绑定，将正常播放四段视频并处理弹题和章测。')
            return
        from .demo import Demo
        self.demo_instance = Demo(tk.Toplevel(self.root))
        self.status.set("演示已打开。刷新并绑定演示窗口，再框选其白色题目面板。")

    def close(self):
        self.ai_test_stop.set()
        self.stop.set()
        self.shutdown.set()
        self.root.destroy()


def main():
    enable_dpi()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
