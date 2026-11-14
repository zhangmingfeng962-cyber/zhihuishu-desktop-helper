"""Idempotent course-tab mute using visible browser UI only."""
import re
from .domain import Rect,PauseRequired,normalized
from .windows import demo_target

MUTE={'将标签页静音','静音标签页','静音此标签页','将此标签页静音','mutetab'}
UNMUTE={'取消标签页静音','取消此标签页静音','取消标签页的静音','取消此标签页的静音','取消静音','unmutetab'}


def command(text):
    value=normalized(text).lower()
    # Browser menus may append a shortcut, e.g. 静音标签页 (Ctrl+M).
    return re.split(r'\(|ctrl|control|shift|⌘',value)[0].strip()


def ensure_course_muted(desktop,vision,frame,view):
    if demo_target(desktop.target):return True
    tab=view.buttons.get('course_tab')
    if not tab:
        raise PauseRequired('自动静音：未识别到智慧树课程标签。请保持标签名称可见，或取消勾选自动静音后继续。')
    left=max(0,min(tab.center[0]-30,frame.width-480))
    area=Rect(left,tab.y,min(520,frame.width-left),min(850,frame.height-tab.y))
    for attempt in range(2):
        desktop.click(tab,frame,button='right')
        desktop.wait(.4)
        menu_frame=desktop.capture()
        spans=vision.ocr.read(menu_frame,area)
        unmute=next((s for s in spans if command(s.text) in UNMUTE),None)
        mute=next((s for s in spans if command(s.text) in MUTE),None)
        if unmute:
            desktop.press('esc')
            return True
        if mute and attempt==0:
            desktop.click(mute.box,menu_frame)
            desktop.wait(.4)
            frame=desktop.capture()
            continue
        desktop.press('esc')
        raise PauseRequired('自动静音未能复核。请手动将课程标签页静音，或取消勾选自动静音后继续。')
    return False
