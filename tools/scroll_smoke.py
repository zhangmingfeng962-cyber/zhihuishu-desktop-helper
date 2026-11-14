"""Actual mouse-wheel + OCR test against a long, disposable fixture window."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json,threading,time,traceback
import tkinter as tk
from desktop_app.windows import enable_dpi,Target,Desktop,client_rect,U
enable_dpi()
from desktop_app.domain import Profile,Rect,PauseRequired
from desktop_app.vision import Perception,LocalOCR
from desktop_app.runner import Runner
from desktop_app.storage import Journal
from desktop_app.demo import DemoAI
qa=Path(__file__).resolve().parents[1]/'qa'
qa.mkdir(exist_ok=True)
root=tk.Tk()
root.title('Autovisor 流程演示 — 长题滚动测试')
root.geometry('1050x720+80+30')
canvas=tk.Canvas(root,bg='white',highlightthickness=0,scrollregion=(0,0,1020,2100),yscrollincrement=20)
bar=tk.Scrollbar(root,command=canvas.yview)
bar.pack(side='right',fill='y')
canvas.pack(side='left',fill='both',expand=True)
canvas.configure(yscrollcommand=bar.set)
canvas.bind_all('<MouseWheel>',lambda e:canvas.yview_scroll(-int(e.delta/120)*3,'units'))
font=('Microsoft YaHei UI',15)
canvas.create_text(50,45,text='第 1 题    共 1 题',anchor='w',font=font,fill='#172b4d')
canvas.create_text(50,95,text='单选题',anchor='w',font=font,fill='#172b4d')
canvas.create_text(50,145,text='请选出数值为 2 的选项。此题用于离线滚动测试。',anchor='w',font=font,fill='#172b4d')
for i in range(20):
    canvas.create_text(50,205+i*85,text=f'滚动定位参考文字，第 {i+1} 行，保持清晰可辨认的上下文。',anchor='w',font=font,fill='#475569')
selected=set()
response={'submit_count':0,'selected':[]}
def choose(label):
    selected.clear();selected.add(label)
    for k in 'ABC':
        canvas.itemconfigure('circle'+k,fill='#2563eb' if k==label else 'white')
for i,(label,y) in enumerate(zip('ABC',(335,930,1520))):
    canvas.create_oval(48,y-12,72,y+12,fill='white',outline='#64748b',width=2,tags=('circle'+label,label))
    canvas.create_text(100,y,text=f'{label}. {i+1}',anchor='w',font=font,fill='#172b4d',tags=label)
    canvas.tag_bind(label,'<Button-1>',lambda e,label=label:choose(label))
def submit():
    response['submit_count']+=1
    response['selected']=sorted(selected)
    canvas.delete('all')
    canvas.configure(scrollregion=(0,0,1020,700))
    canvas.yview_moveto(0)
    canvas.create_text(50,100,text='提交成功',anchor='w',font=font,fill='#172b4d')
button=tk.Button(canvas,text='提交作业',command=submit,font=font,bg='#1d4ed8',fg='white')
canvas.create_window(800,1980,window=button,width=160,height=65)
result={'success':False,'kind':'long-question native fixture'}
def work():
    try:
        time.sleep(.7)
        hwnd=int(U.GetAncestor(root.winfo_id(),2));r=client_rect(hwnd)
        print('WAITING_FOR_FOREGROUND',hwnd,flush=True)
        for _ in range(600):
            if U.GetForegroundWindow()==hwnd:
                break
            time.sleep(.1)
        profile=Profile(width=r.w,height=r.h,regions={'question':Rect(5,5,r.w-35,r.h-10)},task_name='long-native')
        stop,pause=threading.Event(),threading.Event()
        desktop=Desktop(Target(hwnd,'Autovisor 流程演示','python.exe'),profile,stop,pause)
        result['geometry']={'rect':r.__dict__,'canvas':[canvas.winfo_rootx(),canvas.winfo_rooty(),canvas.winfo_width(),canvas.winfo_height()]}
        result['scroll_before']=canvas.yview()
        desktop.capture().save(qa/'scroll-debug-before.png')
        desktop.scroll(-2,profile.regions['question'])
        result['scroll_after']=canvas.yview()
        desktop.capture().save(qa/'scroll-debug-after.png')
        desktop.scroll(2,profile.regions['question'])
        vision=Perception(LocalOCR());journal=Journal(qa/'scroll.sqlite3');journal.reset()
        runner=Runner(desktop,vision,DemoAI(),journal,profile,stop,pause,lambda k,v:print(str(v) if k=='log' else k,flush=True))
        runner.step()
        frame=desktop.capture();view=vision.inspect(frame,profile)
        frame.save(qa/'scroll-result.png')
        result.update(response)
        result['success']=view.submitted and response['selected']==['B'] and response['submit_count']==1
    except Exception as exc:
        result['error']=type(exc).__name__+': '+str(exc)
        result['traceback']=traceback.format_exc()
        if runner.document:
            if hasattr(runner.document,'image'):
                runner.document.image.save(qa/'long-document.png')
            q=runner.document.question
            result['question']={'text':q.text,'options':[{'label':o.label,'text':o.text,'control':o.control.__dict__ if o.control else None,'selected':o.selected} for o in q.options]}
        else:
            result['document']='no scroll detected'
        print(result['traceback'],flush=True)
    finally:
        (qa/'scroll-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
        root.after(200,root.destroy)
def foreground_fixture():
    U.SetForegroundWindow(int(U.GetAncestor(root.winfo_id(),2)))
    root.focus_force()
root.after(200,foreground_fixture)
root.after(500,lambda:threading.Thread(target=work,daemon=True).start())
root.after(360000,root.destroy)
root.mainloop()
sys.exit(0 if result['success'] else 1)
