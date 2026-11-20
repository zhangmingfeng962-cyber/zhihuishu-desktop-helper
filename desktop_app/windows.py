from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import threading
import time
from dataclasses import dataclass
import numpy as np
from PIL import ImageGrab, Image
from .domain import Rect, Profile, PauseRequired, Stopped, FrameChanged,InputFocusUnclear

U = C.windll.user32
K = C.windll.kernel32
U.GetForegroundWindow.restype = W.HWND
U.GetAncestor.argtypes = [W.HWND, W.UINT]
U.GetAncestor.restype = W.HWND
U.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
K.OpenProcess.restype = W.HANDLE
K.CloseHandle.argtypes = [W.HANDLE]
K.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
U.GetClientRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
U.ClientToScreen.argtypes = [W.HWND, C.POINTER(W.POINT)]
U.IsWindow.argtypes = [W.HWND]
U.IsIconic.argtypes = [W.HWND]
U.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
U.GetWindowTextLengthW.argtypes = [W.HWND]


def enable_dpi():
    try:
        U.SetProcessDpiAwarenessContext(C.c_void_p(-4))
    except (AttributeError, OSError):
        U.SetProcessDPIAware()


@dataclass
class Target:
    hwnd: int
    title: str
    process: str


def demo_target(target: Target) -> bool:
    return target.title.startswith('Autovisor 流程演示') and target.process.lower() in {'python.exe','pythonw.exe','autovisordesktop.exe'}


def validate_target_title(target: Target, title: str):
    if demo_target(target):
        if not title.startswith('Autovisor 流程演示'):
            raise PauseRequired('演示窗口发生变化，请重新绑定。')
    elif target.process.lower() not in {'msedge.exe','chrome.exe'} or not any(name in title for name in ('智慧树','知到')):
        raise PauseRequired('请切回智慧树或知到课程标签页后继续；程序不操作其他网站。')


def process_name(hwnd: int) -> str:
    pid = W.DWORD()
    U.GetWindowThreadProcessId(hwnd, C.byref(pid))
    handle = K.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = W.DWORD(4096)
        buf = C.create_unicode_buffer(size.value)
        if K.QueryFullProcessImageNameW(handle, 0, buf, C.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        K.CloseHandle(handle)


def windows() -> list[Target]:
    result = []
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    @callback_type
    def callback(hwnd, _):
        if U.IsWindowVisible(hwnd) and U.GetWindowTextLengthW(hwnd):
            buf = C.create_unicode_buffer(2048)
            U.GetWindowTextW(hwnd, buf, len(buf))
            process = process_name(hwnd)
            if process in {"msedge.exe", "chrome.exe"} or buf.value.startswith("Autovisor 流程演示"):
                result.append(Target(int(hwnd), buf.value, process))
        return True

    U.EnumWindows(callback, 0)
    return result


def client_rect(hwnd: int) -> Rect:
    rect, origin = W.RECT(), W.POINT()
    if not U.IsWindow(hwnd) or not U.GetClientRect(hwnd, C.byref(rect)) or not U.ClientToScreen(hwnd, C.byref(origin)):
        raise PauseRequired("目标窗口已关闭，请重新选择窗口。")
    return Rect(origin.x, origin.y, rect.right, rect.bottom)


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD), ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]


class INPUTUNION(C.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", W.DWORD), ("value", INPUTUNION)]


U.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
U.SendInput.restype = W.UINT


def unicode_events(text: str) -> list[INPUT]:
    encoded = text.encode("utf-16-le")
    result = []
    for i in range(0, len(encoded), 2):
        code = int.from_bytes(encoded[i:i + 2], "little")
        for flag in (0x0004, 0x0004 | 0x0002):
            result.append(INPUT(type=1, ki=KEYBDINPUT(0, code, flag, 0, 0)))
    return result


def wheel_event(notches: int) -> INPUT:
    return INPUT(type=0,mi=MOUSEINPUT(0,0,(notches*120)&0xffffffff,0x0800,0,0))


class Desktop:
    def __init__(self, target: Target, profile: Profile, stop: threading.Event, pause: threading.Event):
        validate_target_title(target,target.title)
        import pyautogui
        self.gui = pyautogui
        self.gui.FAILSAFE = True
        self.gui.PAUSE = .12
        self.target, self.profile, self.stop, self.pause = target, profile, stop, pause
        self.layout = (profile.width, profile.height)

    def activate(self):
        title=C.create_unicode_buffer(2048)
        U.GetWindowTextW(self.target.hwnd,title,len(title))
        validate_target_title(self.target,title.value)
        return bool(U.SetForegroundWindow(self.target.hwnd)) if not U.IsIconic(self.target.hwnd) else False

    def check(self) -> Rect:
        emergency = all(U.GetAsyncKeyState(key) & 0x8000 for key in (0x10,0x11,0x12,0x7B))
        if self.stop.is_set() or emergency:  # Ctrl+Alt+Shift+F12
            self.stop.set()
            raise Stopped()
        if self.pause.is_set():
            raise PauseRequired("已暂停；切回目标窗口后点继续。")
        if U.GetForegroundWindow() != self.target.hwnd or U.IsIconic(self.target.hwnd):
            raise PauseRequired("目标窗口不在前台。请切回课程窗口，再继续。")
        title=C.create_unicode_buffer(2048)
        U.GetWindowTextW(self.target.hwnd,title,len(title))
        validate_target_title(self.target,title.value)
        rect = client_rect(self.target.hwnd)
        if (rect.w, rect.h) != self.layout:
            raise PauseRequired("窗口尺寸变化，请恢复原尺寸或重新校准区域。")
        if rect.x < 0 or rect.y < 0 or rect.x + rect.w > U.GetSystemMetrics(0) or rect.y + rect.h > U.GetSystemMetrics(1):
            raise PauseRequired("请将课程窗口完整放在主屏幕上。")
        x, y = self.gui.position()
        sw, sh = self.gui.size()
        if (x, y) in {(0, 0), (0, sh-1), (sw-1, 0), (sw-1, sh-1)}:
            raise Stopped()
        return rect

    def capture(self) -> Image.Image:
        rect = self.check()
        image = ImageGrab.grab(rect.bounds)
        self.check()
        return image

    def wait(self, seconds: float):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            time.sleep(min(.1, max(0, deadline-time.monotonic())))

    def click(self, box: Rect, expected: Image.Image | None = None, button='left',strict=False):
        origin = self.check()
        if not Rect(0, 0, origin.w, origin.h).contains(box):
            raise PauseRequired("点击目标超出课程窗口，已停止该动作。")
        if expected is not None:
            current = self.capture()
            patch = box.expanded(3, origin.w, origin.h)
            a, b = np.asarray(expected.crop(patch.bounds), dtype=float), np.asarray(current.crop(patch.bounds), dtype=float)
            if a.shape != b.shape or np.mean(np.abs(a - b)) > (5 if strict else 24):
                raise FrameChanged("点击位置的画面发生变化，请重新识别。")
        self.check()
        self.gui.click(origin.x + box.center[0], origin.y + box.center[1],button=button)

    def ensure_muted(self,vision,frame,view):
        from .audio import ensure_course_muted
        return ensure_course_muted(self,vision,frame,view)

    def fast_inputs(self):
        self.gui.PAUSE=.02

    def visual_hover(self,box,expected):
        origin=self.check()
        if not Rect(0,0,origin.w,origin.h).contains(box):raise PauseRequired('悬停位置超出课程窗口。')
        self.gui.moveTo(origin.x+box.center[0]+min(4,box.w//4),origin.y+box.center[1])
        self.gui.moveTo(origin.x+box.center[0],origin.y+box.center[1]);self.wait(.12)

    def visual_scroll(self,amount,region,expected):
        if type(amount) is not int or not -3<=amount<=3 or amount==0:raise PauseRequired('视觉滚动量无效。')
        origin=self.check()
        if not Rect(0,0,origin.w,origin.h).contains(region):raise PauseRequired('滚动区域超出课程窗口。')
        a=np.asarray(expected.crop(region.bounds),dtype=float);b=np.asarray(self.capture().crop(region.bounds),dtype=float)
        if a.shape!=b.shape or np.mean(np.abs(a-b))>24:raise FrameChanged('滚动区域已经变化，已丢弃旧坐标。')
        self.gui.moveTo(origin.x+region.center[0],origin.y+region.center[1]);self.check()
        event=wheel_event(amount)
        if U.SendInput(1,C.byref(event),C.sizeof(INPUT))!=1:raise PauseRequired('Windows 滚动未完成。')
        self.wait(.15)

    def scroll(self, amount: int, region: Rect):
        origin = self.check()
        self.gui.moveTo(origin.x + region.center[0], origin.y + region.center[1])
        self.check()
        event=wheel_event(amount)
        if U.SendInput(1,C.byref(event),C.sizeof(INPUT))!=1:
            raise PauseRequired('Windows 未完成滚轮操作。')
        self.wait(.45)

    def reveal_player(self, region: Rect):
        origin=self.check()
        # Some players have a black letterbox BELOW the interactive video.
        # Hover inside the video body so its own controls are revealed.
        x=origin.x+region.x+min(70,region.w//4)
        y=origin.y+region.y+int(region.h*.8)
        self.gui.moveTo(x+min(15,region.w//10),y)
        self.gui.moveTo(x,y)
        self.wait(.35)

    def press(self,*keys):
        self.check()
        self.gui.hotkey(*keys)
        self.wait(.25)

    def replace_text(self, box: Rect, text: str, expected: Image.Image, fast=False):
        before = np.asarray(expected.crop(box.bounds), dtype=float)
        self.click(box, expected,strict=fast)
        self.wait(.08 if fast else .35)
        after = np.asarray(self.capture().crop(box.bounds), dtype=float)
        # Tab may already have focused the next blank; in that case clicking it
        # produces no image difference, so accept a visible blue focus outline.
        import cv2
        hsv=cv2.cvtColor(after.astype('uint8'),cv2.COLOR_RGB2HSV)
        blue=(hsv[...,0]>=85)&(hsv[...,0]<=135)&(hsv[...,1]>90)&(hsv[...,2]>140)
        edge=np.concatenate((blue[:6,:].ravel(),blue[-6:,:].ravel(),blue[:,:6].ravel(),blue[:,-6:].ravel()))
        focused=bool(np.mean(edge)>.10)
        if before.shape != after.shape or (np.mean(np.abs(before - after)) < .25 and not focused):
            raise InputFocusUnclear("无法确认填空框已获得焦点，未输入文字；请重新定位实际输入框，不要点击上方标签。")
        self.check()
        self.gui.hotkey("ctrl", "a")
        self.wait(.03 if fast else .1)
        for offset in range(0, len(text), 12):
            self.check()
            events = unicode_events(text[offset:offset+12])
            array = (INPUT * len(events))(*events)
            if U.SendInput(len(events), array, C.sizeof(INPUT)) != len(events):
                raise PauseRequired("Windows 未完成文字输入，请检查浏览器是否以管理员身份运行。")
        self.check()
        self.gui.press("tab")
        self.wait(.08 if fast else .4)


def emergency_monitor(stop: threading.Event, shutdown: threading.Event, notify=None):
    # Register the combination so Edge does not receive bare F12 / open DevTools.
    registered=U.RegisterHotKey(None,0xAD01,0x0001|0x0002|0x0004|0x4000,0x7B)
    if registered and notify:
        notify('log','全局停止快捷键已注册：Ctrl+Alt+Shift+F12。')
    if not registered and notify:
        notify('log','Ctrl+Alt+Shift+F12 被其他程序占用，请使用鼠标角落或停止按钮。')
    msg=W.MSG()
    try:
        while not shutdown.wait(.04):
            while U.PeekMessageW(C.byref(msg),None,0,0,1):
                if msg.message==0x0312 and msg.wParam==0xAD01:
                    stop.set()
                    if notify:
                        notify('log','收到全局停止指令。')
    finally:
        if registered:
            U.UnregisterHotKey(None,0xAD01)
