"""Bounded desktop fixture for both offline and real image-API verification."""
from pathlib import Path
import json,threading,time,tkinter as tk
import numpy as np
from . import __version__
from .course_demo import CourseDemo
from .vision_demo import VisionDemoAI
from .vision_ai import VisionAI
from .vision_control import VisionCourseRunner
from .storage import Journal,load_profile,app_dir,CredentialStore
from .windows import enable_dpi,client_rect,Target,Desktop,U,process_name


def main(report_dir,resume_test=False,online=False,capture_samples=False):
    enable_dpi();directory=Path(report_dir).resolve();directory.mkdir(parents=True,exist_ok=True)
    root=tk.Tk();demo=CourseDemo(root,duration=6,first_duration=35 if resume_test else None)
    stop,pause=threading.Event(),threading.Event();holder={};events=[]
    result={'version':__version__,'control_mode':'vision','provider':'deepseek-live' if online else 'offline-fixture','success':False}
    log=directory/'vision-events.jsonl';log.write_text('',encoding='utf8')
    def emit(kind,value):
        if kind in {'log','paused','vision_status','completed'}:
            item={'kind':kind,'value':value}
            events.append(item)
            with log.open('a',encoding='utf8') as f:f.write(json.dumps(item,ensure_ascii=False)+'\n')
        if kind=='paused':result['error']=str(value);stop.set()
        if kind=='completed':holder['completed']=True
        current=holder.get('runner')
        if resume_test and not holder.get('restarted') and kind=='vision_status' and current:
            active=current.controller.active
            if active and active.code=='1.1' and current.journal.lesson(current.course_id,active.task_id).position>=2:
                holder['restart']=True;stop.set()
    def worker():
        nonlocal stop,pause
        started=time.monotonic();calls=0;latencies=[]
        try:
            hwnd=int(U.GetAncestor(root.winfo_id(),2))
            for _ in range(600):
                if U.GetForegroundWindow()==hwnd:break
                time.sleep(.1)
            profile=demo.profile();rect=client_rect(hwnd);profile.width,profile.height=rect.w,rect.h
            if capture_samples:(directory/'test-profile.json').write_text(json.dumps(profile.to_dict(),ensure_ascii=False,indent=2),'utf8')
            journal=Journal(directory/'vision-demo.sqlite3');journal.reset()
            config=load_profile(app_dir()) if online else None
            key=CredentialStore().get() if online else ''
            def make():
                ai=VisionAI(config.base_url,config.model,key,stop) if online else VisionDemoAI(demo)
                if online and capture_samples:
                    traces=directory/'model-responses';traces.mkdir(exist_ok=True)
                    ai.trace=lambda frame,raw:(traces/(frame+'.json')).write_text(raw,'utf8')
                desktop=Desktop(Target(hwnd,demo.title,process_name(hwnd)),profile,stop,pause)
                instance=VisionCourseRunner(desktop,ai,journal,profile,stop,pause,emit)
                if capture_samples:
                    samples=directory/'samples';samples.mkdir(exist_ok=True)
                    def record(image,obs):
                        n=len(list(samples.glob('*.png')))
                        if n<20:
                            image.save(samples/(str(n).zfill(2)+'.png'))
                            (samples/(str(n).zfill(2)+'.json')).write_text(json.dumps({'scene':obs.scene,'elapsed':obs.elapsed,'context':instance.context()},ensure_ascii=False,indent=2),'utf8')
                    instance.on_observation=record
                return instance
            runner=make();holder['runner']=runner;runner.run();calls+=runner.calls;latencies+=runner.latencies
            if holder.get('restart') and not holder.get('completed'):
                holder['restarted']=True;result['resume_exercised']=True
                stop,pause=threading.Event(),threading.Event()
                runner=make();holder['runner']=runner;runner.run();calls+=runner.calls;latencies+=runner.latencies
            metrics=demo.metrics();result.update(metrics)
            tasks=journal.course_tasks(runner.course_id)
            result['tasks']=[{'code':t.code,'chapter':t.chapter,'kind':t.kind,'status':t.status,'source':t.completion_source} for t in tasks]
            result['success']=bool(holder.get('completed')) and len(tasks)==6 and all(t.status=='done' for t in tasks) and all(n==1 for values in (metrics['video_starts'],metrics['popup_submits'],metrics['quiz_submits']) for n in values.values()) and metrics['correct_popups'] and metrics['correct_quizzes'] and (not resume_test or result.get('resume_exercised',False))
            result['calls']=calls;result['request_latencies']=latencies
            result['execution_latencies']=runner.execution_latencies
            result['median_request_seconds']=float(np.median(latencies)) if latencies else None
            result['p95_request_seconds']=float(np.percentile(latencies,95)) if latencies else None
        except Exception as exc:
            from .domain import PauseRequired
            result['error']=str(exc) if isinstance(exc,PauseRequired) else type(exc).__name__+': '+str(exc)
        finally:
            result['wall_seconds']=round(time.monotonic()-started,2)
            result['events']=events
            (directory/'vision-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
            try:holder['runner'].last_frame.save(directory/'vision-final.png')
            except Exception:pass
            root.after(0,root.destroy)
    root.after(300,lambda:(root.lift(),root.focus_force(),U.SetForegroundWindow(int(U.GetAncestor(root.winfo_id(),2)))))
    root.after(700,lambda:threading.Thread(target=worker,daemon=True).start())
    root.after(900000,stop.set)
    root.bind('<Escape>',lambda e:stop.set())
    root.mainloop()
    return 0 if result['success'] else 1
