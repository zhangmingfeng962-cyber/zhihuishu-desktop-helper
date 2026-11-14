"""Native course-mode integration test, restricted to the bundled local fixture."""
from pathlib import Path
import json
import threading
import time
import tkinter as tk
from .course_demo import CourseDemo,CourseDemoAI
from .course import CourseRunner
from .domain import PauseRequired
from .storage import Journal
from .vision import LocalOCR,Perception
from .windows import enable_dpi,Target,Desktop,client_rect,U,process_name


def main(report_dir: str,resume_test=False):
    enable_dpi()
    directory=Path(report_dir).resolve();directory.mkdir(parents=True,exist_ok=True)
    root=tk.Tk();demo=CourseDemo(root,first_duration=30 if resume_test else None)
    stop,pause=threading.Event(),threading.Event()
    from . import __version__
    result={'success':False,'version':__version__,'fixture':'two chapters / four videos / popup questions / chapter tests','events':[]}
    holder={}

    (directory/'course-events.jsonl').write_text('',encoding='utf8')
    def emit(kind,value):
        with (directory/'course-events.jsonl').open('a',encoding='utf8') as stream:
            stream.write(json.dumps({'time':time.time(),'kind':kind,'value':value if kind in {'log','paused','course','completed'} else str(type(value).__name__)},ensure_ascii=False)+'\n')
        if kind in {'log','paused','completed'}:
            result['events'].append({'kind':kind,'value':value})
        if kind=='paused':
            result['error']=str(value);stop.set()
        if kind=='completed':
            holder['completed']=True
            root.after(0,demo.summary)
        if resume_test and not holder.get('restarted') and kind=='course' and str(value.get('message','')).startswith('正在看课：'):
            runner=holder.get('runner')
            active=runner.controller.active if runner else None
            if active and active.code=='1.1' and runner.journal.lesson(runner.course_id,active.task_id).position>=2:
                holder['restart_requested']=True
                stop.set()

    def worker():
        nonlocal stop,pause
        try:
            time.sleep(.5)
            hwnd=int(U.GetAncestor(root.winfo_id(),2))
            for _ in range(600):
                if U.GetForegroundWindow()==hwnd:break
                time.sleep(.1)
            rect=client_rect(hwnd);profile=demo.profile()
            profile.width,profile.height=rect.w,rect.h
            desktop=Desktop(Target(hwnd,demo.title,process_name(hwnd)),profile,stop,pause)
            journal=Journal(directory/'course-demo-test.sqlite3');journal.reset()
            vision=Perception(LocalOCR())
            runner=CourseRunner(desktop,vision,CourseDemoAI(),journal,profile,stop,pause,emit)
            holder['runner']=runner;holder['desktop']=desktop
            runner.run()
            if holder.get('restart_requested') and not holder.get('completed'):
                holder['restarted']=True
                result['resume_exercised']=True
                stop,pause=threading.Event(),threading.Event()
                desktop=Desktop(Target(hwnd,demo.title,process_name(hwnd)),profile,stop,pause)
                runner=CourseRunner(desktop,vision,CourseDemoAI(),journal,profile,stop,pause,emit)
                holder['runner']=runner;holder['desktop']=desktop
                runner.run()
            metrics=demo.metrics();result.update(metrics)
            tasks=journal.course_tasks(runner.course_id)
            result['tasks']=[{'chapter':t.chapter,'code':t.code,'kind':t.kind,'status':t.status,'source':t.completion_source} for t in tasks]
            result['success']=bool(holder.get('completed')) and len(tasks)==6 and all(t.status=='done' for t in tasks) and all(n==1 for n in metrics['video_starts'].values()) and all(n==1 for n in metrics['popup_submits'].values()) and all(n==1 for n in metrics['quiz_submits'].values()) and metrics['correct_popups'] and (not resume_test or result.get('resume_exercised',False))
            if not result['success'] and 'error' not in result:
                result['error']='Course completion counters did not match the expected fixture.'
        except Exception as exc:
            result['error']=str(exc) if isinstance(exc,PauseRequired) else type(exc).__name__+': '+str(exc)
        finally:
            if holder.get('runner') and hasattr(holder['runner'].scanner,'last_frame'):
                holder['runner'].scanner.last_frame.save(directory/'course-catalog-last.png')
                region=holder['runner'].profile.regions['catalog']
                result['last_catalog_spans']=[{'text':s.text,'box':s.box.__dict__} for s in holder['runner'].scanner.last_spans if region.contains(s.box)]
            try:
                holder['desktop'].capture().save(directory/'course-final.png')
            except Exception:
                pass
            (directory/'course-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
            root.after(300,root.destroy)

    def foreground():
        U.SetForegroundWindow(int(U.GetAncestor(root.winfo_id(),2)));root.focus_force()
    root.after(300,foreground)
    root.after(700,lambda:threading.Thread(target=worker,daemon=True).start())
    def timeout():
        result['error']='Integration test exceeded 15 minutes.';stop.set()
    root.after(900000,timeout)
    root.mainloop()
    return 0 if result['success'] else 1
