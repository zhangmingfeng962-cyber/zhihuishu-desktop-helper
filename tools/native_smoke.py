"""Run the real desktop/OCR/Unicode engine ONLY against the bundled fixture window."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import threading
import time
import traceback
import tkinter as tk
from desktop_app.windows import enable_dpi, Target, Desktop, client_rect, U
enable_dpi()
from desktop_app.demo import Demo, DemoAI
from desktop_app.domain import Profile, Rect, PauseRequired
from desktop_app.vision import Perception, LocalOCR
from desktop_app.storage import Journal
from desktop_app.runner import Runner
from desktop_app.domain import PauseRequired

qa=Path(__file__).resolve().parents[1]/'qa'
qa.mkdir(exist_ok=True)
root=tk.Tk()
demo=Demo(root)
events=[]
done=threading.Event()
result={'kind':'native fixture only','success':False,'events':events}
interrupt_test='--interrupt' in sys.argv
interruption={'triggered':False,'resumed':False}


def run():
    try:
        time.sleep(.7)
        hwnd=int(U.GetAncestor(root.winfo_id(),2))
        print('WAITING_FOR_FOREGROUND',hwnd,flush=True)
        for _ in range(600):
            if U.GetForegroundWindow()==hwnd:
                break
            time.sleep(.1)
        target=Target(hwnd,'Autovisor 流程演示 — 离线测试，不是真实课程','python.exe')
        rect=client_rect(hwnd)
        profile=Profile(width=rect.w,height=rect.h,regions={'question':Rect(25,25,rect.w-50,rect.h-50)},task_name='native-smoke')
        stop,pause=threading.Event(),threading.Event()
        desktop=Desktop(target,profile,stop,pause)
        perception=Perception(LocalOCR())
        journal=Journal(qa/'native.sqlite3')
        journal.reset()
        def emit(kind,value):
            if kind=='log':
                events.append(value)
                print(value,flush=True)
        runner=Runner(desktop,perception,DemoAI(),journal,profile,stop,pause,emit)
        original_click=desktop.click
        def interrupt_click(box,expected=None):
            original_click(box,expected)
            if interrupt_test and demo.index==2 and demo.selected=={'A'} and not interruption['triggered']:
                interruption['triggered']=True
                pause.set()
        desktop.click=interrupt_click
        for step in range(25):
            image=desktop.capture()
            image.save(qa/f'native-{step:02d}.png')
            view=perception.inspect(image,profile)
            details={'step':step,'buttons':list(view.buttons),'confirmation':view.confirmation,'submitted':view.submitted,'spans':[s.text for s in view.spans]}
            if view.question:
                q=view.question
                details['question']={'kind':q.kind,'index':q.index,'total':q.total,'key':q.key,'blanks':[b.__dict__ for b in q.blanks],
                    'options':[{'label':o.label,'text':o.text,'control':o.control.__dict__ if o.control else None,'selected':o.selected} for o in q.options]}
            (qa/f'native-{step:02d}.json').write_text(json.dumps(details,ensure_ascii=False,indent=2),'utf8')
            print(json.dumps(details,ensure_ascii=True),flush=True)
            if view.submitted:
                result['success']=demo.submit_count==1 and demo.responses=={1:['B'],2:['A','B'],3:['A'],4:['北京','2']}
                result['responses']=demo.responses
                result['submit_count']=demo.submit_count
                if runner.quiz:
                    try:
                        runner.step(image,view)
                    except PauseRequired as exc:
                        if '提交成功' not in str(exc):
                            raise
                        result['terminal_pause']=str(exc)
                break
            try:
                runner.step(image,view)
            except PauseRequired:
                if interruption['triggered'] and not interruption['resumed']:
                    interruption['resumed']=True
                    pause.clear()
                    runner=Runner(desktop,perception,DemoAI(),journal,profile,stop,pause,emit)
                    events.append('已模拟在多选题第一项后中断，并从持久记录恢复。')
                    continue
                raise
            desktop.wait(.5)
    except Exception as exc:
        result['error']=type(exc).__name__+': '+str(exc)
        result['traceback']=traceback.format_exc()
        try:
            failure=desktop.capture()
            failure.save(qa/'native-error.png')
            failed_view=perception.inspect(failure,profile)
            result['failure_spans']=[s.text for s in failed_view.spans]
            if failed_view.question:
                result['failure_question']={'text':failed_view.question.text,'key':failed_view.question.key,'options':[(o.label,o.text) for o in failed_view.question.options]}
        except Exception:
            pass
        print(result['traceback'],flush=True)
    finally:
        result['interruption']=interruption
        destination='native-resume-result.json' if interrupt_test else 'native-result.json'
        if interrupt_test:
            result['success']=result['success'] and interruption['triggered'] and interruption['resumed']
        (qa/destination).write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
        done.set()
        root.after(300,root.destroy)


root.after(300,lambda:root.focus_force())
root.after(700,lambda:threading.Thread(target=run,daemon=True).start())
root.after(540000,root.destroy)
root.mainloop()
sys.exit(0 if result['success'] else 1)
