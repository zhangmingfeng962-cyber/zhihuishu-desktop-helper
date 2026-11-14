"""Visual course inventory, durable scheduling and video/quiz coordination."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
import cv2
import numpy as np
from PIL import Image
from .domain import CourseTask, LessonProgress, Rect, Span, View, Profile, PauseRequired, Stopped, fingerprint, normalized
from .runner import Runner


def time_seconds(value: str) -> int:
    result=0
    for piece in value.split(':'):
        result=result*60+int(piece)
    return result


def chapter_number(value: str) -> str:
    if value.isdigit():
        return str(int(value))
    digits={'零':0,'〇':0,'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    if '十' in value:
        left,right=value.split('十',1)
        return str((digits.get(left,1) if left else 1)*10+digits.get(right,0))
    return str(digits.get(value,0))


@dataclass
class Heading:
    chapter: str
    box: Rect
    expanded: bool | None


@dataclass
class CatalogRow:
    task: CourseTask
    box: Rect
    done: bool | None
    selected: bool = False


@dataclass
class CatalogPage:
    headings: list[Heading] = field(default_factory=list)
    rows: list[CatalogRow] = field(default_factory=list)
    numbered: set[str] = field(default_factory=set)
    chapter_context: str = ''


@dataclass
class CourseSnapshot:
    rows: list[CatalogRow]
    chapters: set[str]
    selected: str | None
    complete: bool


def completion_marker(image: Image.Image, region: Rect, row: Rect) -> bool | None:
    top=max(region.y,row.y-6)
    band=Rect(max(region.x,region.x+region.w-75),top,min(75,region.w),max(1,min(region.y+region.h-top,max(60,row.h+12))))
    rgb=np.asarray(image.crop(band.bounds).convert('RGB'))
    hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
    colored=(((hsv[...,0]>85)&(hsv[...,0]<135))|((hsv[...,0]>35)&(hsv[...,0]<85)))&(hsv[...,1]>90)&(hsv[...,2]>90)
    contours,_=cv2.findContours(colored.astype('uint8')*255,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    uncertain=False
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if not 12<=w<=42 or not 12<=h<=42 or cv2.contourArea(contour)<70:
            continue
        patch=hsv[y:y+h,x:x+w]
        white=(patch[...,1]<65)&(patch[...,2]>215)
        columns=[(j,float(np.mean(np.where(white[:,j])[0]))) for j in range(w) if np.any(white[:,j])]
        if len(columns)>=5:
            ys=[item[1] for item in columns]
            peak=int(np.argmax(ys))
            if 0<peak<len(ys)-1 and ys[peak]-min(ys[:peak])>1 and ys[peak]-min(ys[peak+1:])>1.5:
                return True
        uncertain=True
    return None if uncertain else False


def heading_expanded(image: Image.Image, region: Rect, box: Rect) -> bool | None:
    # Chapter chevrons sit in the first catalog column, next to 第N章.
    x=region.x+min(70,int(region.w*.12))
    area=Rect(x,max(region.y,box.y-5),min(115,region.x+region.w-x),min(box.h+10,region.y+region.h-max(region.y,box.y-5)))
    if area.w<5 or area.h<5:
        return None
    gray=cv2.cvtColor(np.asarray(image.crop(area.bounds).convert('RGB')),cv2.COLOR_RGB2GRAY)
    contours,_=cv2.findContours((gray<145).astype('uint8')*255,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        xx,yy,w,h=cv2.boundingRect(contour)
        poly=cv2.approxPolyDP(contour,.07*cv2.arcLength(contour,True),True)
        if len(poly)==3 and 6<=w<=22 and 6<=h<=22:
            pts=poly[:,0,:]
            if np.count_nonzero(pts[:,0]<xx+w*.4)==2:
                return False
            if np.count_nonzero(pts[:,1]<yy+h*.4)==2:
                return True
    return None


class CatalogParser:
    time_pattern=re.compile(r'\d{1,2}:\d{2}(?::\d{2})?')
    code_pattern=re.compile(r'(?<!\d)(\d+\.\d+(?:\.\d+)*)(?!\d)')
    quiz_pattern=re.compile(r'(?:章节|单元|平时).*(?:测试|测验|作业)|章测')

    def read(self,image: Image.Image,spans: list[Span],region: Rect,course: str,chapter: str='') -> CatalogPage:
        items=[s for s in spans if region.contains(s.box) and s.score>=.65]
        page=CatalogPage(chapter_context=chapter)
        for span in items:
            match=re.search(r'第([一二三四五六七八九十零〇\d]+)章',normalized(span.text))
            if match:
                page.headings.append(Heading(chapter_number(match[1]),span.box,heading_expanded(image,region,span.box)))
            code=self.code_pattern.search(normalized(span.text))
            if code and ':' not in span.text:
                page.numbered.add(code[1])
        events=[]
        for h in page.headings:
            events.append((h.box.y,'heading',h))
        for stamp in items:
            match=self.time_pattern.search(normalized(stamp.text))
            if not match:
                continue
            near=[s for s in items if stamp.box.y-65<=s.box.y<=stamp.box.y+20]
            code=next((m[1] for s in sorted(near,key=lambda s:s.box.x) if (m:=self.code_pattern.search(normalized(s.text))) and ':' not in s.text),'')
            before=stamp.text[:stamp.text.find(match[0])] if match[0] in stamp.text else ''
            title_spans=[s for s in near if s.box.y<stamp.box.y-2 and len(normalized(s.text))>=2 and not self.time_pattern.search(s.text) and not re.fullmatch(r'[\d.]+',normalized(s.text)) and not re.search(r'第.+章',s.text) and not self.quiz_pattern.search(normalized(s.text))]
            title=max(title_spans,key=lambda s:s.box.y,default=None)
            label=before.strip() if len(normalized(before))>3 else (title.text if title else '')
            label=re.sub(r'^\s*\d+(?:\.\d+)+[.、．\s]*','',label).strip()
            if not label:
                if stamp.box.y<region.y+70:
                    continue
                raise PauseRequired('目录课时标题没有完整识别，请调整目录区域。')
            anchor=Rect(region.x,min(title.box.y if title else stamp.box.y,stamp.box.y),region.w,max(45,stamp.box.y+stamp.box.h-(title.box.y if title else stamp.box.y)))
            events.append((anchor.y,'video',(code,label,time_seconds(match[0]),anchor,title.box if title else stamp.box)))
        for span in items:
            if self.quiz_pattern.search(normalized(span.text)):
                events.append((span.box.y,'quiz',span))
        seen=set()
        first_heading=min(page.headings,key=lambda h:h.box.y,default=None)
        explicit_before=False
        for _,kind,data in sorted(events,key=lambda item:(item[0],0 if item[1]=='heading' else 1)):
            if kind=='heading':
                chapter=data.chapter
                explicit_before=True
                continue
            if kind=='video':
                code,label,duration,anchor,clickbox=data
                chapter_for_row=code.split('.')[0] if code else chapter
                if code:
                    chapter=chapter_for_row;explicit_before=True
            else:
                code,label,duration,anchor,clickbox='',data.text,0,data.box,data.box
                # Overlapping scroll pages can start with the previous chapter's
                # quiz ABOVE the next heading. The previous page's end-context
                # is not the context of this new page's top.
                chapter_for_row=str(max(0,int(first_heading.chapter)-1)) if first_heading and data.box.y<first_heading.box.y and not explicit_before else chapter
            if not chapter_for_row:
                raise PauseRequired('目录资源缺少可确认的章节编号，请从目录顶部开始。')
            identity=fingerprint(course+'|'+kind+'|'+chapter_for_row+'|'+(code or normalized(label)))
            if identity in seen:
                continue
            seen.add(identity)
            task=CourseTask(identity,chapter_for_row,code,label,kind,duration,0)
            done=completion_marker(image,region,anchor)
            crop=np.asarray(image.crop(anchor.bounds).convert('RGB'))
            hsv=cv2.cvtColor(crop,cv2.COLOR_RGB2HSV)
            selected=float(np.mean((hsv[...,0]>=85)&(hsv[...,0]<=135)&(hsv[...,1]>8)&(hsv[...,1]<90)&(hsv[...,2]>180)))>.25
            page.rows.append(CatalogRow(task,clickbox,done,selected))
        page.chapter_context=chapter
        return page


class CatalogInterrupted(Exception):
    def __init__(self,frame,view):
        self.frame,self.view=frame,view


class CatalogScanner:
    def __init__(self,desktop,vision,profile,course):
        self.desktop,self.vision,self.profile,self.course=desktop,vision,profile,course
        self.region=profile.regions['catalog']
        self.parser=CatalogParser()

    def capture(self,chapter=''):
        frame=self.desktop.capture()
        self.last_frame=frame
        view=self.vision.inspect(frame,self.profile,scope_hint='video')
        self.last_spans=view.spans
        if view.blocked:
            raise PauseRequired(view.blocked)
        if view.question:
            raise CatalogInterrupted(frame,view)
        return frame,self.parser.read(frame,view.spans,self.region,self.course,chapter),view

    @staticmethod
    def same(a,b):
        return np.mean(np.abs(np.asarray(a,dtype=float)-np.asarray(b,dtype=float)))<1.5

    def top(self):
        previous=self.desktop.capture().crop(self.region.bounds)
        stable=0
        for _ in range(50):
            self.desktop.scroll(8,self.region)
            frame,_,_=self.capture()
            current=frame.crop(self.region.bounds)
            stable=stable+1 if self.same(previous,current) else 0
            if stable>=2:
                return
            previous=current
        raise PauseRequired('未能定位到课程目录顶部。')

    def scan(self) -> CourseSnapshot:
        for _ in range(64):
            self.top()
            chapter='';rows={};chapters=set();numbered=set();selected=None;stable=0
            previous=None;restart=False
            for _ in range(120):
                frame,page,view=self.capture(chapter)
                chapter=page.chapter_context
                collapsed=next((h for h in page.headings if h.expanded is False),None)
                if collapsed:
                    self.desktop.click(collapsed.box,frame)
                    self.desktop.wait(.45)
                    restart=True
                    break
                chapters.update(h.chapter for h in page.headings)
                numbered.update(page.numbered)
                for row in page.rows:
                    if row.done is None:
                        raise PauseRequired('目录中出现不能确认的完成图标，请预览并调整目录区域。')
                    if row.task.task_id not in rows:
                        row.task.order=len(rows)
                        rows[row.task.task_id]=row
                    elif rows[row.task.task_id].task.duration!=row.task.duration or normalized(rows[row.task.task_id].task.label)!=normalized(row.task.label):
                        raise PauseRequired('同一课时编号的标题或时长识别不一致。')
                    if row.selected:
                        selected=row.task.task_id
                current=frame.crop(self.region.bounds)
                if previous is not None:
                    stable=stable+1 if self.same(previous,current) else 0
                if stable>=2:
                    break
                previous=current
                self.desktop.scroll(-3,self.region)
            else:
                raise PauseRequired('目录超过扫描长度或无法确认底部。')
            if restart:
                continue
            if not rows or not chapters:
                raise PauseRequired('没有识别到完整章节和课时目录，请重新校准。')
            actual_codes={r.task.code for r in rows.values() if r.task.code}
            unknown=[code for code in numbered if code not in actual_codes and not any(c.startswith(code+'.') for c in actual_codes)]
            if unknown:
                raise PauseRequired('目录编号 '+unknown[0]+' 的资源类型或时长没有完整识别。')
            if any(not any(r.task.chapter==ch for r in rows.values()) for ch in chapters):
                raise PauseRequired('存在未展开或无法识别资源的章节。')
            return CourseSnapshot(list(rows.values()),chapters,selected,True)
        raise PauseRequired('目录展开次数异常，请检查页面。')

    def locate(self,task_id: str):
        self.top();chapter='';previous=None;stable=0
        for _ in range(120):
            frame,page,view=self.capture(chapter)
            chapter=page.chapter_context
            row=next((r for r in page.rows if r.task.task_id==task_id),None)
            if row:
                return frame,row,view
            current=frame.crop(self.region.bounds)
            stable=stable+1 if previous is not None and self.same(previous,current) else 0
            if stable>=2:
                break
            previous=current
            self.desktop.scroll(-3,self.region)
        raise PauseRequired('当前任务未能在目录中重新定位，请重新扫描课程。')


class CourseController:
    def __init__(self,journal,course,clock=time.time):
        self.journal,self.course,self.clock=journal,course,clock
        self.session=journal.course_session(course)
        self.tasks=journal.course_tasks(course)
        self.last_advance=clock();self.recovery=False;self.completion_wait=None

    @property
    def active(self):
        return next((t for t in self.tasks if t.task_id==self.session.get('active')),None)

    def save(self):
        self.journal.save_session(self.course,self.session)

    def reconcile(self,snapshot: CourseSnapshot):
        if not snapshot.complete:
            raise PauseRequired('目录扫描尚未完成。')
        old={t.task_id:t for t in self.tasks}
        if self.session.get('inventory_complete') and set(old)!=set(r.task.task_id for r in snapshot.rows):
            raise PauseRequired('课程目录发生变化，请核对后重新建立课程任务。')
        tasks=[]
        for row in snapshot.rows:
            task=old.get(row.task.task_id,row.task)
            task.order=row.task.order
            if task.status=='done' and not row.done:
                raise PauseRequired('平台完成状态与本地记录不一致，已暂停重做。')
            if row.done:
                if task.status!='done':
                    task.completion_source='existing'
                task.status='done'
            self.journal.save_task(self.course,task);tasks.append(task)
        self.tasks=tasks
        if self.active and self.active.status=='done' and self.session.get('phase')!='returning':
            self.session['active']=None
        self.session['inventory_complete']=True
        self.session['selected']=snapshot.selected
        self.save()

    def choose(self):
        active=self.active
        if active and active.status!='done':
            return active
        selected=next((t for t in self.tasks if t.task_id==self.session.get('selected') and t.kind=='video' and t.status!='done'),None)
        if selected:
            return selected
        for task in self.tasks:
            if task.status=='done':
                continue
            if task.kind=='quiz' and any(t.kind=='video' and t.chapter==task.chapter and t.status!='done' for t in self.tasks):
                continue
            return task
        return None

    def activate(self,task):
        self.session.update(active=task.task_id,phase='watching' if task.kind=='video' else 'opening_quiz')
        task.status='running';self.journal.save_task(self.course,task)
        self.last_advance=self.clock();self.recovery=False;self.completion_wait=None
        self.save()

    def done(self,task,source='session'):
        task.status='done';task.completion_source=source
        self.journal.save_task(self.course,task)
        self.session.update(active=None,phase='scanning');self.save()

    def video(self,view: View,current_done: bool | None):
        task=self.active
        if not task or task.kind!='video':
            raise PauseRequired('当前课时状态丢失。')
        if view.video_lesson and task.code and view.video_lesson!=task.code:
            raise PauseRequired('播放器课时与当前任务不一致，已停止切课。')
        now=self.clock()
        progress=self.journal.lesson(self.course,task.task_id)
        if view.video_position is not None:
            if view.video_position!=progress.position:
                self.last_advance=now;self.recovery=False
            progress.position=view.video_position
            progress.duration=view.video_duration or task.duration
            progress.last_advanced_at=self.last_advance
            self.journal.save_lesson(self.course,progress)
        if view.video_finished:
            if current_done is True:
                self.done(task)
                return 'done'
            self.completion_wait=self.completion_wait or now
            if now-self.completion_wait>60:
                raise PauseRequired('视频结束后平台仍未确认当前课时完成。')
            return 'waiting_completion'
        if view.video_paused:
            return 'play'
        if now-self.last_advance>60:
            if self.recovery:
                raise PauseRequired('视频持续无进展，续播检查后仍未恢复。')
            self.recovery=True;self.last_advance=now
            return 'recover'
        return 'watching'


class CourseRunner(Runner):
    def __init__(self,*args,clock=time.time,**kwargs):
        super().__init__(*args,**kwargs)
        required={'player','catalog','popup','question'}
        if not required<=self.profile.regions.keys():
            raise PauseRequired('整课模式需要校准视频、目录、视频弹题和章节题目四个区域。')
        self.course_id=fingerprint(self.profile.task_name)
        self.controller=CourseController(self.journal,self.course_id,clock)
        self.scanner=CatalogScanner(self.desktop,self.vision,self.profile,self.course_id)
        active=self.controller.active
        self.quiz=active.quiz_ref if active and active.kind=='quiz' else None
        self.opened_at=0;self.initialized=False;self.complete=False

    def inspect(self,frame):
        task=self.controller.active
        hint='chapter' if task and task.kind=='quiz' or self.controller.session.get('phase')=='returning' else 'video'
        return self.vision.inspect(frame,self.profile,scope_hint=hint)

    def on_resume(self):
        self.controller.last_advance=self.controller.clock()
        self.controller.completion_wait=None
        self.controller.recovery=False

    def quiz_for(self,q):
        task=self.controller.active
        q.title=task.task_id if task else q.title
        if q.scope=='popup':
            context=task.task_id if task else self.controller.session.get('initial_lesson','')
            if not context:
                raise PauseRequired('无法确认弹题所属课时，请确保视频标题与编号可见。')
            quiz='popup:'+fingerprint(self.course_id+context+q.key)
            self.journal.begin(quiz,1)
            return quiz
        quiz=super().quiz_for(q)
        if task:
            task.quiz_ref=quiz;self.journal.save_task(self.course_id,task)
        return quiz

    def _status(self,message):
        tasks=self.controller.tasks
        self.emit('course',{'message':message,'done':sum(t.status=='done' for t in tasks),'total':len(tasks),'active':self.controller.active.label if self.controller.active else ''})

    def _course_visible(self,view):
        text=normalized(''.join(s.text for s in view.spans))
        return not view.submitted and not view.question and not view.confirmation and normalized(self.profile.task_name) in text and any(re.search(r'第[一二三四五六七八九十\d]+章',s.text) for s in view.spans if self.profile.regions['catalog'].contains(s.box))

    def _return(self,frame,view):
        if self._course_visible(view):
            self.controller.session.update(phase='scanning',active=None,return_sent=False)
            self.controller.save();self.quiz=None;self.initialized=False
            self._status('已返回课程，正在安排下一项任务。')
            return
        if self.controller.session.get('return_sent'):
            if self.controller.clock()-self.controller.session.get('return_at',0)>20:
                raise PauseRequired('返回课程后未能核对课程名称，请切回原课程页面继续。')
            return
        if 'return' in view.buttons:
            self.desktop.click(view.buttons['return'],frame)
        elif 'course_tab' in view.buttons:
            self.desktop.click(view.buttons['course_tab'],frame)
        elif self.controller.session.get('new_test_tab'):
            self.desktop.press('ctrl','w')
        elif self.controller.session.get('same_test_tab'):
            self.desktop.press('alt','left')
        else:
            raise PauseRequired('已提交作业，但不能可靠定位原课程标签页。请切回课程继续。')
        self.controller.session.update(return_sent=True,return_at=self.controller.clock())
        self.controller.save();self.desktop.wait(.6)

    def pending_submission(self,frame,view):
        task=self.controller.active
        if task and task.kind=='quiz' and self.quiz and view.submitted:
            chapters={chapter_number(m[1]) for s in view.spans if not self.profile.regions['catalog'].contains(s.box) and (m:=re.search(r'第([一二三四五六七八九十零〇\d]+)章',normalized(s.text)))}
            if task.chapter not in chapters:
                raise PauseRequired('结果页未确认对应章节，已暂停以避免把其他作业结果当作本次提交。')
            state=self.journal.state(self.quiz)
            if state not in {'submit_sent','confirm_sent','success'}:
                raise PauseRequired('结果页不属于本次提交，请核对作业名称。')
            if state!='success':
                self.journal.transition(self.quiz,state,'success')
            task.status='done';task.completion_source='session'
            self.journal.save_task(self.course_id,task)
            self.controller.session.update(phase='returning',return_sent=False)
            self.controller.save();self.finished_jobs+=1
            self.note('章节作业提交成功，正在返回课程。')
            self._return(frame,view)
            return True
        popup=bool(self.quiz and self.quiz.startswith('popup:'))
        result=super().pending_submission(frame,view)
        if popup and not self.quiz:
            self.on_resume()
        return result

    def run(self):
        original=self.emit
        def filtered(kind,value):
            if kind=='stopped' and self.complete:
                return
            original(kind,value)
        self.emit=filtered
        try:
            super().run()
        finally:
            self.emit=original

    def _scan(self):
        self._status('正在展开并扫描整门课程目录。')
        snapshot=self.scanner.scan()
        self.controller.reconcile(snapshot)
        self.initialized=True
        return snapshot

    def step(self,frame=None,view=None):
        frame=self.desktop.capture() if frame is None else frame
        view=self.inspect(frame) if view is None else view
        if view.blocked:
            raise PauseRequired(view.blocked)
        visible_text=normalized(''.join(s.text for s in view.spans))
        if normalized(self.profile.task_name) not in visible_text:
            raise PauseRequired('未确认当前课程名称，请切回所选课程，课程名称应与页面显示一致。')
        self.last_view,self.last_frame=view,frame
        self.emit('view',view)
        if view.video_lesson:
            self.controller.session['initial_lesson']=view.video_lesson
            self.controller.save()
        if self.controller.session.get('phase')=='returning':
            self._return(frame,view);return
        if self.quiz and self.journal.state(self.quiz) in {'submit_sent','confirm_sent'}:
            self.pending_submission(frame,view);return
        if view.question:
            task=self.controller.active
            if task and task.kind=='quiz':
                view.question.scope='chapter'
            else:
                view.question.scope='popup';view.question.index=1;view.question.total=1
            self._status('正在自动处理视频弹题。' if view.question.scope=='popup' else f'正在完成章节作业第 {view.question.index} 题。')
            super().step(frame,view);return
        task=self.controller.active
        if task and task.kind=='quiz':
            if view.submitted:
                if task.quiz_ref and self.journal.state(task.quiz_ref)=='success':
                    self.controller.session.update(phase='returning',return_sent=False);self.controller.save();self._return(frame,view);return
                # Opening an already-completed result is a read-only discovery.
                task.status='done';task.completion_source='existing';self.journal.save_task(self.course_id,task)
                self.controller.session.update(phase='returning',return_sent=False);self.controller.save();self._return(frame,view);return
            if 'start_quiz' in view.buttons:
                self.desktop.click(view.buttons['start_quiz'],frame);self.desktop.wait(.6);return
            if self.opened_at and self.controller.clock()-self.opened_at>25:
                raise PauseRequired('章节作业打开后未识别到题目，请核对题目区域。')
            return
        try:
            if not self.initialized:
                self._scan()
                task=self.controller.active
            if not task:
                chosen=self.controller.choose()
                if chosen is None:
                    # A second complete scan is mandatory before declaring completion.
                    snapshot=self._scan()
                    if not snapshot.rows or not all(r.done is True for r in snapshot.rows):
                        raise PauseRequired('结束复核发现尚未完成的课程任务。')
                    self.complete=True;self.controller.session['phase']='complete';self.controller.save()
                    self._status('整门课程完成：所有视频和章节作业均已复核。')
                    self.emit('completed',{'course':self.profile.task_name,'tasks':len(snapshot.rows)})
                    self.stop.set();return
                self.controller.activate(chosen)
                frame,row,current=self.scanner.locate(chosen.task_id)
                if row.done is True:
                    self.controller.done(chosen,'existing');self.initialized=False;return
                if chosen.kind=='video':
                    if current.video_lesson!=chosen.code or not row.selected:
                        self.desktop.click(row.box,frame);self.desktop.wait(.6)
                    self._status('正在看课：'+chosen.code+' '+chosen.label)
                else:
                    self.controller.session['tabs_before']=view.tab_titles
                    self.controller.save()
                    self.desktop.click(row.box,frame);self.desktop.wait(.7)
                    after=self.inspect(self.desktop.capture())
                    before=self.controller.session.get('tabs_before',[])
                    before_set={normalized(t) for t in before};after_set={normalized(t) for t in after.tab_titles}
                    verified_new=bool(before_set) and before_set<=after_set and len(after.tab_titles)>len(before)
                    self.controller.session.update(new_test_tab=verified_new,same_test_tab=bool(before) and len(after.tab_titles)==len(before))
                    self.controller.save();self.opened_at=self.controller.clock()
                return
            # Current playback task must be selected and identified, not merely any blue check.
            frame,row,current=self.scanner.locate(task.task_id)
            if not row.selected and current.video_lesson!=task.code:
                self.desktop.click(row.box,frame);self.desktop.wait(.6);return
            if not current.video_visible:
                self.desktop.reveal_player(self.profile.regions['player'])
                frame=self.desktop.capture();current=self.inspect(frame)
            action=self.controller.video(current,row.done)
            if action=='done':
                self.note('当前课时已完成，并已核对平台目录标记。');self.initialized=False
            elif action=='play':
                if 'play' not in current.buttons:
                    raise PauseRequired('播放器已暂停，但播放按钮未识别。')
                self.desktop.click(current.buttons['play'],frame)
                self._status('已续播当前课时。')
            elif action=='recover':
                self.desktop.reveal_player(self.profile.regions['player'])
                self._status('视频暂时无进展，正在重新检查暂停和弹题。')
            else:
                progress=f'{current.video_position or 0}/{current.video_duration or task.duration} 秒'
                self._status(('等待平台确认本节完成：' if action=='waiting_completion' else '正在看课：')+task.code+' '+progress)
        except CatalogInterrupted as interruption:
            if interruption.view.video_lesson:
                self.controller.session['initial_lesson']=interruption.view.video_lesson;self.controller.save()
            self._status('目录扫描遇到视频弹题，先完成弹题后继续扫描。')
            super().step(interruption.frame,interruption.view)
