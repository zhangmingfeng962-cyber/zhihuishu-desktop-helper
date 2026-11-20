"""Course state and screenshot-bound execution for the image-only backend."""
from __future__ import annotations
import copy,difflib,hashlib,json,re,threading,time,uuid
from dataclasses import asdict
import cv2,numpy as np
from PIL import Image,ImageDraw
from .domain import Answer,CourseTask,LessonProgress,Option,PauseRequired,Profile,Question,Rect,Stopped,FrameChanged,InputFocusUnclear,TargetUnclear,fingerprint,normalized
from .course import CourseController,CourseSnapshot,CatalogRow
from .vision_ai import Box,Observation,VisionProtocolError
from .vision import controls


def serial(value):
    if isinstance(value,Box):return value.json()
    if isinstance(value,dict):return {k:serial(v) for k,v in value.items()}
    if isinstance(value,list):return [serial(v) for v in value]
    return value


class VisualWatch:
    """Cheap UI-change sensing; video pixels themselves never drive polling."""
    def __init__(self):self.reference=None;self.panel=None;self.checked=0

    @staticmethod
    def sample(image,regions,scene):
        canvas=image.convert('RGB').resize((320,200))
        player=regions.get('player')
        panel=None
        if player and scene=='course':
            box=player.pixels(image.size)
            crop=np.asarray(image.crop(box.bounds).convert('RGB').resize((240,160)))
            white=(np.min(crop,axis=2)>225).astype('uint8')*255
            contours,_=cv2.findContours(white,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            candidates=[]
            for c in contours:
                x,y,w,h=cv2.boundingRect(c)
                polygon=cv2.approxPolyDP(c,.025*cv2.arcLength(c,True),True)
                if len(polygon)==4 and w>75 and h>40 and cv2.contourArea(c)>.7*w*h:candidates.append((x//4,y//4,w//4,h//4))
            # White dialogs can touch the white page background, so a contour
            # need not remain a standalone rectangle. Coverage catches that case.
            panel=(tuple(sorted(candidates)),int(np.mean(white>0)*4))
            rect=player.pixels(canvas.size)
            # Keep top lesson identity; hide clock/timeline and lecture animation.
            ImageDraw.Draw(canvas).rectangle(rect.bounds,fill='black')
        return np.asarray(canvas,dtype=np.int16),panel

    def remember(self,image,obs,now):
        self.reference,self.panel=self.sample(image,obs.regions,obs.scene);self.checked=now

    def reason(self,image,obs,now):
        current,panel=self.sample(image,obs.regions,obs.scene)
        if self.reference is None:return '首次观察'
        if panel!=self.panel:return '播放器出现新的覆盖面板'
        if now-self.checked>=30:return '定期状态复核'
        difference=float(np.mean(np.abs(current-self.reference)))
        if difference>10:return '课程界面出现明显变化'
        if now-self.checked>=5 and difference>2.5:return '课程界面发生变化'
        return None


class VisionExecutor:
    def __init__(self,desktop,stop):self.desktop,self.stop=desktop,stop

    @staticmethod
    def input_box(image,proposed):
        search=proposed.expanded(min(80,max(35,proposed.h)),*image.size)
        candidates=controls(image,search,min_width=max(65,int(proposed.w*.5)),max_width=min(search.w,int(proposed.w*1.6)),max_height=min(120,max(60,proposed.h*2)))
        ranked=[]
        for candidate in candidates:
            dx=abs(candidate.center[0]-proposed.center[0])/max(1,proposed.w)
            dy=abs(candidate.center[1]-proposed.center[1])/max(35,proposed.h)
            if dx>.3 or abs(candidate.center[1]-proposed.center[1])>max(65,proposed.h):continue
            score=dx+dy+abs(candidate.w-proposed.w)/max(1,proposed.w)
            ranked.append((score,candidate))
        ranked.sort(key=lambda item:item[0])
        if not ranked or len(ranked)>1 and ranked[1][0]-ranked[0][0]<.2:return proposed
        return ranked[0][1]

    @staticmethod
    def button_box(image,proposed):
        search=proposed.expanded(min(90,max(40,proposed.h)),*image.size)
        rgb=np.asarray(image.crop(search.bounds).convert('RGB'));hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
        mask=((hsv[...,0]>=85)&(hsv[...,0]<=135)&(hsv[...,1]>75)&(hsv[...,2]>100)).astype('uint8')*255
        contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        candidates=[]
        for contour in contours:
            x,y,w,h=cv2.boundingRect(contour)
            if w<max(40,proposed.w*.35) or not 24<=h<=120 or w>max(400,proposed.w*2.5):continue
            if np.mean(mask[y:y+h,x:x+w]>0)<.65:continue
            candidates.append(Rect(x+search.x,y+search.y,w,h))
        if not candidates:
            candidates=controls(image,search,min_width=max(45,int(proposed.w*.35)),max_width=min(search.w,max(250,int(proposed.w*2.5))),max_height=120)
        ranked=[]
        for box in candidates:
            overlap_w=max(0,min(box.x+box.w,proposed.x+proposed.w)-max(box.x,proposed.x))
            overlap_h=max(0,min(box.y+box.h,proposed.y+proposed.h)-max(box.y,proposed.y))
            if overlap_w*overlap_h<.15*min(box.w*box.h,proposed.w*proposed.h):continue
            score=abs(box.center[0]-proposed.center[0])/max(40,proposed.w)+abs(box.center[1]-proposed.center[1])/max(40,proposed.h)
            ranked.append((score,box))
        ranked.sort(key=lambda item:item[0])
        if len(ranked)>1 and ranked[1][0]-ranked[0][0]<.15:raise TargetUnclear('附近存在多个相似按钮，请重新精确定位目标。')
        return ranked[0][1] if ranked else proposed

    def execute(self,image,obs):
        for action in obs.actions:
            if self.stop.is_set():raise Stopped()
            self.desktop.check()
            box=action.box.pixels(image.size) if action.box else None
            if box and not Rect(0,0,*image.size).contains(box):raise PauseRequired('AI 控件位置超出课程窗口。')
            if action.kind=='click':
                if action.intent in {'submit','confirm_submit','return_course','next_question'}:box=self.button_box(image,box)
                if action.intent in {'play','pause','mute'} and 'player' in obs.regions:
                    self.desktop.reveal_player(obs.regions['player'].pixels(image.size))
                self.desktop.click(box,image,button=action.button,strict=True)
            elif action.kind=='input':
                box=self.input_box(image,box)
                area='popup' if obs.scene.startswith('popup_') else 'question'
                override=self.desktop.profile.vision_region_overrides.get(area) if isinstance(getattr(self.desktop,'profile',None),Profile) else None
                if override and not override.contains(box):raise InputFocusUnclear('可输入边框超出人工修正范围，请重新确认。')
                self.desktop.replace_text(box,action.value,image,fast=True)
            elif action.kind=='hover':self.desktop.visual_hover(box,image)
            elif action.kind=='scroll':self.desktop.visual_scroll(action.amount,box,image)
            elif action.kind=='key':self.desktop.press(*action.value.split('+'))
            elif action.kind=='wait':self.desktop.wait(min(1,max(.1,action.amount)))
            self.desktop.wait(.05)


class VisionCourseRunner:
    def __init__(self,desktop,ai,journal,profile,stop,pause,emit,clock=time.time):
        self.desktop,self.ai,self.journal,self.profile=desktop,ai,journal,profile
        self.stop,self.pause,self.emit,self.clock=stop,pause,emit,clock
        self.course_id=fingerprint(profile.task_name)
        self.controller=CourseController(journal,self.course_id,clock)
        self.state=self.controller.session.setdefault('vision',{})
        self.state.setdefault('question',None)
        previous_phase=self.state.get('phase')
        self.state['phase']=('return' if previous_phase=='return' else 'quiz') if profile.mode=='quiz' or self.controller.active and self.controller.active.kind=='quiz' else ('resume' if self.controller.active and self.state.get('inventory_validated') else 'scan')
        self.state['mute_verified']=False
        self.executor=VisionExecutor(desktop,stop);self.watch=VisualWatch()
        self.observation=None;self.force=True;self.calls=0;self.tokens=0;self.latencies=[]
        self.last_frame=None;self.complete=False;self.started_at=time.monotonic()
        self.submission_deadline=None;self.action_started=0
        self.scan_rows={};self.scan_chapters={};self.scan_top=False;self.scan_bottom=None
        self.scan_final=False;self.scan_selected=None
        self.waiting_after=None
        self.idle_count=0
        self.last_proposal=None;self.repeated_proposal=0
        self.on_observation=None
        self.review_count=0
        self.pending_scroll=None
        if self.state['phase']=='scan':self.state['scroll_feedback']=None
        self.execution_latencies=[]
        self.next_observation_at=0
        self.desktop.fast_inputs()

    def note(self,message):self.emit('log',message)
    def save(self):self.controller.save()

    def on_resume(self):
        self.force=True;self.observation=None;self.submission_deadline=None
        self.review_count=0;self.state['last_error']=None
        self.state['mute_verified']=False
        # A half-collected inventory cannot be treated as an entire course.
        if self.state['phase']=='scan':self.reset_scan(self.scan_final)

    def reset_scan(self,final=False):
        self.state['phase']='scan';self.scan_rows={};self.scan_chapters={};self.scan_top=False
        self.scan_bottom=None;self.scan_final=final;self.scan_selected=None;self.force=True
        self.state['scroll_feedback']=None;self.pending_scroll=None

    def quiz_id(self,scope,q=None,obs=None):
        active=self.controller.active
        if scope=='chapter':
            if active and active.kind=='quiz':
                if not active.quiz_ref:
                    active.quiz_ref='chapter:'+fingerprint(self.course_id+'|visual|'+active.task_id)
                    self.journal.save_task(self.course_id,active)
                return active.quiz_ref
            return self.state.get('quiz_id') or 'chapter:'+fingerprint(self.course_id+'|visual-only')
        code=obs.lesson_code if obs else ''
        code=code or (active.code if active and active.kind=='video' else '')
        if not code:raise PauseRequired('AI 未确认弹题所属课时，已停止作答。')
        return 'popup:'+fingerprint(self.course_id+'|'+code+'|'+self.question_key(q))

    @staticmethod
    def stem_identity(value):return re.sub(r'[，,。.!?？：:【】\[\]（）()]','',normalized(value))

    @classmethod
    def question_key(cls,q):return fingerprint(str(q['index'])+'|'+q['kind']+'|'+cls.stem_identity(q['stem']))

    def context(self):
        phase=self.state['phase'];task=self.controller.active;memory=self.state.get('question')
        goals={
            'scan':'完整扫描目录。先确认目录顶部，再展开每个章节并向下滚动；每屏读取资源，保留重叠条目；到底后不要点击课程，留空动作由本地检查。',
            'navigate':'按active_task定位并进入任务；当前课时已选中时不要再次点击重播，可滚动目录寻找目标。不要重新学习done=true的其他资源。',
            'watch':'保持active_task正常播放并按auto_mute设置静音。显示并识别控件；处理弹题，结束后等当前课时自己的完成标记，不提前切课。',
            'quiz':'完成当前章节作业。先完整识题，按required_answer填写、复核，按题号逐题完成。仅在本地verified为true时提交。',
            'return':'确认交卷结果后返回原课程，优先点击返回课程或原课程标签，不关闭其他标签。'}
        goals['resume']='重启恢复：先核对画面中的当前课时编号和时长是否匹配active_task，不要重复点击已选中的课时；匹配后由本地继续watch，弹题仍优先处理。'
        if phase=='scan':
            if not self.scan_top:goals['scan']='优先校验目录顶部：先向上滚动目录，直到scroll_feedback显示同方向连续两次changed=false，然后返回at_top=true。不能用目标编号填补裁切资源。视频正常播放也必须完成目录扫描。'
            elif self.scan_bottom is not None:goals['scan']='复核目录末尾资源是否全部可见并读取，返回at_bottom=true且actions=[]；本地将合并检查完整目录。'
            else:goals['scan']='继续完整扫描目录：展开每个章节并向下滚动，保留重叠。即使认为已到底，也需向下滚动至scroll_feedback连续两次changed=false后再返回at_bottom=true，不能因视频在播放而停止扫描。'
        goal=goals.get(phase,goals['watch'])
        if self.profile.auto_mute and not self.state.get('mute_verified'):
            goal='最高优先：当前弹题先处理；没有弹题时先确认静音。若声音控件不可见，仅hover播放器下方显示控件；若未静音，点击静音；若图标已静音则返回muted=true。完成此项后再继续目录扫描或任务导航。后续目标：'+goal
        return {
            'goal':goal+' 当前弹题优先处理：新题作答，结果页关闭，然后续播。若auto_mute且未确认静音，优先显示控件并设置静音；已经静音不要切换。',
            'phase':phase,'course_name':self.profile.task_name,'mode':self.profile.mode,'auto_mute':self.profile.auto_mute,
            'mute_verified':self.state.get('mute_verified',False),'active_task':asdict(task) if task else None,
            'tasks':[{'chapter':t.chapter,'code':t.code,'kind':t.kind,'status':t.status,'label':t.label if t.kind=='quiz' else ''} for t in self.controller.tasks],
            'manual_regions_native':{key:list(value.bounds) for key,value in self.profile.vision_region_overrides.items()},
            'catalog_zoom_native':list(self.observation.regions['catalog'].pixels(self.last_frame.size).bounds) if self.observation and self.last_frame and 'catalog' in self.observation.regions else None,
            'player_zoom_native':list(self.observation.regions['player'].pixels(self.last_frame.size).bounds) if self.observation and self.last_frame and 'player' in self.observation.regions else None,
            'scan':{'top_seen':self.scan_top,'final':self.scan_final,'seen_codes':[r.task.code for r in self.scan_rows.values()]},
            'question_memory':memory,'required_answer':memory.get('answers') if memory else None,
            'question_verified':bool(memory and memory.get('verified')),
            'all_answers_verified':bool(memory and self.journal.complete(memory['quiz'])),
            'verified_question_indices':self.journal.verified_indices(memory['quiz']) if memory else [],
            'submission_state':self.journal.state(memory['quiz']) if memory else None,
            'last_action':self.state.get('last_action'),
            'last_error':self.state.get('last_error'),
            'next_required':self.state.get('next_required'),
            'scroll_feedback':self.state.get('scroll_feedback'),
            'previous_player':self.state.get('player')}

    def guard(self,obs):
        if obs.scene in {'blocked','unknown'}:raise PauseRequired(obs.message or 'AI 无法可靠确认当前页面，已暂停。')
        if obs.scene=='probe':raise PauseRequired('连接测试画面不能用于课程执行。')
        if re.search('期末考试|监考|考试须知',obs.course_name):raise PauseRequired('当前不属于章节练习，已暂停操作。')
        active=self.controller.active
        if normalized(obs.course_name)!=normalized(self.profile.task_name):
            if not (active and active.kind=='quiz' and obs.scene.startswith('chapter_') and obs.chapter==active.chapter):
                raise PauseRequired('AI 未确认所选课程名称，请切回绑定课程。')
        if active and active.kind=='quiz' and obs.scene.startswith('chapter_') and obs.chapter!=active.chapter:
            raise PauseRequired('AI 识别到的作业章节与当前任务不一致。')

    def canonicalize_receipt(self,obs):
        task=self.controller.active
        if task and task.kind=='quiz' and obs.scene=='popup_result' and obs.chapter==task.chapter and task.quiz_ref and self.journal.state(task.quiz_ref) in {'submit_sent','confirm_sent','success'}:
            if any(a.intent in {'return_course','close_popup'} for a in obs.actions):
                obs.scene='chapter_result'
                for action in obs.actions:
                    if action.intent=='close_popup':action.intent='return_course'

    def prepare_question(self,obs):
        q=obs.question
        if not q:return
        scope='popup' if obs.scene=='popup_question' else 'chapter'
        key=self.question_key(q);old=copy.deepcopy(self.state.get('question'))
        if old and old['key']!=key and old['scope']==scope and old['index']==q['index']:
            if self.stem_identity(old['stem'])!=self.stem_identity(q['stem']):raise PauseRequired('同一题号的题干发生变化，旧动作已取消。')
            key=old['key']
        quiz=self.quiz_id(scope,q,obs)
        self.journal.begin(quiz,q['total'])
        if self.journal.state(quiz)=='success':raise PauseRequired('这份题目已有成功提交记录，不重复作答。')
        if not old or old['key']!=key or old['quiz']!=quiz:
            old={'key':key,'quiz':quiz,'scope':scope,'kind':q['kind'],'stem':q['stem'],'index':q['index'],'total':q['total'],
                 'options':{},'blanks':{},'answers':[],'verified':False,'dirty':[],'lesson':obs.lesson_code or (self.controller.active.code if self.controller.active and self.controller.active.kind=='video' else '')}
            cached=self.journal.answer(quiz,q['index'],key)
            if cached:old['answers']=cached[0].values
        for item in q['options']:
            previous=old['options'].get(item['label'])
            if previous and normalized(previous['text'])!=normalized(item['text']):raise PauseRequired('同一选项标签对应的文字发生变化。')
            old['options'][item['label']]={'text':item['text'],'selected':item['selected']}
            if item['selected'] is not None and item['label'] in old['dirty']:old['dirty'].remove(item['label'])
        for item in q['blanks']:
            old['blanks'][str(item['index'])]=item['value']
            token='blank:'+str(item['index'])
            if item['value'] is not None and token in old['dirty']:old['dirty'].remove(token)
        if q['option_count'] and len(old['options'])>q['option_count'] or q['blank_count'] and len(old['blanks'])>q['blank_count']:
            raise PauseRequired('同一题目的控件总数前后不一致。')
        old['complete']=q['complete'] and (len(old['blanks'])==q['blank_count']>0 if q['kind']=='blank' else len(old['options'])==q['option_count']>=2)
        if old['complete']:
            if q['kind']=='blank' and {int(k) for k in old['blanks']}!=set(range(1,q['blank_count']+1)):raise PauseRequired('填空序号不连续，不能填写。')
            if q['kind']!='blank' and sorted(old['options'])!=[chr(65+i) for i in range(q['option_count'])]:raise PauseRequired('选项标签不连续，可能存在漏识别。')
        if old['complete'] and not old['answers']:
            options=[Option(label,data['text'],Rect(0,0,1,1)) for label,data in sorted(old['options'].items())]
            legacy=Question(q['kind'],q['stem'],q['index'],q['total'],scope,options=options,blanks=[Rect(0,0,1,1)]*q['blank_count'])
            old['answers']=Answer.parse(json.dumps({'answers':q['answers']}),legacy).values
            self.journal.record(quiz,q['index'],key,Answer(old['answers'],q['explanation']),False)
        correct=False
        if old['complete'] and old['answers'] and not old['dirty']:
            if q['kind']=='blank':correct=all(old['blanks'].get(str(i+1)) is not None and normalized(old['blanks'][str(i+1)])==normalized(v) for i,v in enumerate(old['answers']))
            else:correct=all(v['selected'] is not None for v in old['options'].values()) and {k for k,v in old['options'].items() if v['selected']}==set(old['answers'])
        old['verified']=correct;self.state['question']=old;self.state['quiz_id']=quiz
        if correct:self.journal.record(quiz,q['index'],key,Answer(old['answers'],q['explanation']),True)
        options=[Option(i['label'],i['text'],i['box'].pixels(self.last_frame.size),i['box'].pixels(self.last_frame.size),i['selected']) for i in q['options']]
        legacy=Question(q['kind'],q['stem'],q['index'],q['total'],scope,options=options,blanks=[i['box'].pixels(self.last_frame.size) for i in q['blanks']])
        self.emit('answer',(legacy,Answer(old['answers'],q['explanation'])))

    def row_task(self,row):
        label=row['label'];code=row['code'];chapter=row['chapter'];kind=row['kind']
        identity=fingerprint(self.course_id+'|'+kind+'|'+chapter+'|'+(code or normalized(label)))
        if kind=='quiz':
            existing=[t for t in self.controller.tasks if t.chapter==chapter and t.kind=='quiz']
            if len(existing)==1:identity=existing[0].task_id
        return CourseTask(identity,chapter,code,label,kind,row['duration'],len(self.scan_rows))

    def scan(self,obs):
        cat=obs.catalog
        feedback=self.state.get('scroll_feedback') or {}
        still=feedback.get('intent')=='scroll_catalog' and feedback.get('unchanged_count',0)>=2
        top_evidence=feedback.get('intent')=='scroll_catalog' and feedback.get('amount',0)>0 and feedback.get('unchanged_count',0)>=1 and cat['at_top']
        if top_evidence or still and feedback.get('amount',0)>0:self.scan_top=True
        if not self.scan_top:return False
        for h in cat['chapters']:self.scan_chapters[h['chapter']]=h['expanded']
        for source in cat['rows']:
            task=self.row_task(source)
            if task.kind=='video' and task.duration<=0:raise PauseRequired('目录视频时长不完整。')
            row=CatalogRow(task,source['box'].pixels(self.last_frame.size),source['done'],source['selected'])
            previous=self.scan_rows.get(task.task_id)
            if previous:
                if previous.task.duration!=task.duration:raise PauseRequired('同一课时的时长识别不一致。')
                if len(task.label)>len(previous.task.label):previous.task.label=task.label
                if row.done is not None:previous.done=row.done
                previous.selected|=row.selected
            else:self.scan_rows[task.task_id]=row
            if row.selected:self.scan_selected=task.task_id
        bottom_verified=still and feedback.get('amount',0)<0
        if not (cat['at_bottom'] or bottom_verified) or any(not v for v in self.scan_chapters.values()):
            self.scan_bottom=None;return False
        if not bottom_verified:
            self.force=True;return False
        signature=tuple((r['chapter'],r['code'],r['label'],r['kind']) for r in cat['rows'])
        if self.scan_bottom!=signature:
            self.scan_bottom=signature;self.force=True;return True
        rows=list(self.scan_rows.values())
        if not rows or not self.scan_chapters:raise PauseRequired('目录扫描没有完整章节与资源。')
        if any(r.done is None for r in rows):raise PauseRequired('部分目录完成标记不明确，请修正区域或移开浮层后继续。')
        numbered_chapters={int(ch) for ch in self.scan_chapters if ch.isdigit()}
        if numbered_chapters:
            start=0 if 0 in numbered_chapters else 1
            missing_chapters=set(range(start,max(numbered_chapters)+1))-numbered_chapters
            if missing_chapters:raise PauseRequired('目录中缺少第 '+str(min(missing_chapters))+' 章，不能结束扫描。')
        for chapter in self.scan_chapters:
            if not any(r.task.chapter==chapter for r in rows):raise PauseRequired('存在未完整识别的章节。')
        groups={}
        for row in rows:
            if row.task.code and re.fullmatch(r'\d+(?:\.\d+)+',row.task.code):
                prefix,last=row.task.code.rsplit('.',1);groups.setdefault(prefix,set()).add(int(last))
        for prefix,values in groups.items():
            missing=set(range(1,max(values)+1))-values
            if missing:raise PauseRequired('目录中缺少课时 '+prefix+'.'+str(min(missing))+'，已暂停避免漏课。')
        missing_known=set(t.task_id for t in self.controller.tasks)-set(self.scan_rows)
        if missing_known:raise PauseRequired('本次扫描仍缺少已知课程资源，不能声明完整目录。请重新检查滚动区域。')
        self.controller.reconcile(CourseSnapshot(rows,set(self.scan_chapters),self.scan_selected,True))
        self.state['inventory_validated']=True
        pending=[t.code or t.label for t in self.controller.tasks if t.status!='done']
        self.note('AI 目录核对完成：'+str(len(rows))+' 项；待完成 '+str(len(pending))+' 项'+('：'+'、'.join(pending) if pending else '。'))
        if self.scan_final and not pending:
            self.complete=True;self.stop.set();self.state['phase']='complete';self.save()
            self.emit('completed',{'course':self.profile.task_name,'tasks':len(rows)})
        else:self.state['phase']='navigate';self.force=True
        return True

    def advance_state(self,obs):
        active=self.controller.active
        if active and active.kind=='video' and obs.lesson_code and obs.lesson_code!=active.code:
            actual=next((t for t in self.controller.tasks if t.kind=='video' and t.code==obs.lesson_code and t.status!='done'),None)
            selected=next((r for r in obs.catalog['rows'] if r['kind']=='video' and r['code']==obs.lesson_code and r['selected'] and actual and r['duration']==actual.duration),None)
            if actual and selected:
                active.status='pending';self.journal.save_task(self.course_id,active)
                self.controller.activate(actual);self.state['phase']='watch';self.state['question']=None
                self.note('当前已进入待学课时 '+actual.code+'，先续播，稍后补齐 '+active.code+'。')
            elif obs.scene=='popup_question':raise PauseRequired('弹题所属视频与当前待学任务未能可靠关联。')
        task=self.controller.active;phase=self.state['phase']
        closing=self.state.get('popup_closing')
        memory=self.state.get('question')
        if memory and memory['scope']=='popup' and obs.scene=='course' and self.journal.state(memory['quiz']) in {'submit_sent','confirm_sent'}:
            if not obs.lesson_code or obs.lesson_code!=memory.get('lesson'):raise PauseRequired('弹题提交后未确认返回原课时。')
            self.journal.transition(memory['quiz'],self.journal.state(memory['quiz']),'success')
            self.state['question']=None;self.state['quiz_id']=None
            self.controller.last_advance=self.clock()
            self.note('AI 已确认弹题提交后返回原视频。')
        if closing and obs.scene=='course':
            if obs.lesson_code and closing['lesson'] and obs.lesson_code!=closing['lesson']:raise PauseRequired('弹题关闭后返回了其他课时。')
            quiz=closing.get('quiz')
            if quiz:self.journal.transition(quiz,self.journal.state(quiz),'success')
            self.state['popup_closing']=None;self.state['question']=None
            self.controller.last_advance=self.clock()
            self.note('AI 已确认弹题关闭，继续原视频。')
        if obs.player.get('muted') is True:self.state['mute_verified']=True
        elif obs.player.get('muted') is False:self.state['mute_verified']=False
        if obs.scene=='popup_result':return False
        if obs.scene in {'popup_question','chapter_question'}:
            memory=self.state.get('question')
            pending=memory and self.journal.state(memory['quiz']) in {'submit_sent','confirm_sent'}
            if pending:
                self.submission_deadline=self.submission_deadline or self.clock()+90
                if self.clock()>self.submission_deadline:raise PauseRequired('交卷状态不明确，不会重复提交。请核对页面。')
                obs.actions=[];self.force=True;return True
            self.prepare_question(obs);return False
        if obs.scene=='chapter_result':
            if task and task.kind=='quiz':
                quiz=task.quiz_ref;status=self.journal.state(quiz) if quiz else None
                if status in {'submit_sent','confirm_sent','success'}:
                    self.journal.transition(quiz,status,'success');task.completion_source='session'
                elif self.state.get('last_action',{}).get('intent')=='open_quiz':task.completion_source='existing'
                else:raise PauseRequired('结果页未关联本次作业，已停止重复提交。')
                task.status='done';self.journal.save_task(self.course_id,task);self.state['phase']='return';self.state['question']=None
                self.note('AI 已确认对应章节提交结果，正在返回课程。')
            elif self.profile.mode=='quiz':
                memory=self.state.get('question');quiz=memory['quiz'] if memory else self.state.get('quiz_id')
                if not quiz or self.journal.state(quiz) not in {'submit_sent','confirm_sent','success'}:raise PauseRequired('未找到本次交卷记录。')
                self.journal.transition(quiz,self.journal.state(quiz),'success');self.stop.set();self.note('AI 已确认作业提交成功。')
            return False
        if obs.scene=='chapter_confirmation':
            quiz=task.quiz_ref if task and task.kind=='quiz' else self.state.get('quiz_id')
            if quiz and self.journal.state(quiz)=='confirm_sent':
                self.submission_deadline=self.submission_deadline or self.clock()+90
                if self.clock()>self.submission_deadline:raise PauseRequired('交卷确认已发送但未显示结果，不会重复确认。请检查平台状态。')
                self.force=True;self.next_observation_at=time.monotonic()+2
                return True
            return False
        if phase=='return' and obs.scene=='course':
            self.controller.session['active']=None;self.state['phase']='navigate';self.state['quiz_id']=None;self.force=True
            self.note('AI 已确认返回所选课程。');return True
        if obs.scene not in {'course','menu'}:return False
        if phase=='resume' and obs.scene=='course':
            if task and obs.lesson_code==task.code and obs.player.get('duration')==task.duration:
                self.state['phase']='watch';self.force=True;return True
            self.state['phase']='navigate';self.force=True;return True
        if phase=='scan' and obs.scene=='course':return self.scan(obs)
        if phase=='navigate':
            chosen=self.controller.choose()
            if chosen is None:self.reset_scan(True);return True
            self.controller.activate(chosen);self.state['phase']='watch' if chosen.kind=='video' else 'quiz';self.state['question']=None
            self.force=True;return True
        if phase=='watch' and task and task.kind=='video' and obs.scene=='course':
            if obs.lesson_code and obs.lesson_code!=task.code:return False
            position=obs.player.get('position');duration=obs.player.get('duration') or task.duration
            if position is not None:
                progress=self.journal.lesson(self.course_id,task.task_id)
                if position!=progress.position:
                    self.controller.last_advance=self.clock();self.state['stall_recheck']=None
                progress.position=position;progress.duration=duration;self.journal.save_lesson(self.course_id,progress)
            own=next((r for r in obs.catalog['rows'] if self.row_task(r).task_id==task.task_id),None)
            if obs.player['state']=='ended' and own and own['done'] is True:
                self.state['completion_waiting']=False;self.state['completion_wait_since']=None
                self.controller.done(task);self.state['phase']='navigate';self.state['question']=None;self.force=True
                self.note('AI 已核对当前课时结束及其自身完成标记。');return True
            if obs.player['state']=='ended':
                self.state['completion_waiting']=True
                self.state['completion_wait_since']=self.state.get('completion_wait_since') or self.clock()
                if self.clock()-self.state['completion_wait_since']>60:raise PauseRequired('视频结束后仍未确认当前课时自己的完成标记。')
            else:self.state['completion_waiting']=False;self.state['completion_wait_since']=None
            if obs.player['state']=='playing' and self.clock()-self.controller.last_advance>60:
                recheck=self.state.get('stall_recheck')
                if recheck and self.clock()-recheck>30:raise PauseRequired('进度停滞检查后仍未恢复，已暂停。')
                if not recheck:
                    self.state['stall_recheck']=self.clock();self.state['last_error']='视频时间超过60秒未前进，请显示播放器控件，检查暂停、加载或弹题后恢复。';self.force=True
        return False

    @staticmethod
    def same_box(a,b,size):
        aa,bb=a.pixels(size),b.pixels(size)
        return aa.expanded(8,*size).contains(bb) or bb.expanded(8,*size).contains(aa)

    def validate_actions(self,obs):
        phase=self.state['phase'];task=self.controller.active;memory=self.state.get('question')
        actions=[]
        if phase=='scan' and obs.scene=='course' and not obs.actions:
            if not self.scan_top:
                self.state['next_required']={'intent':'scroll_catalog','amount':3,'reason':'需要完成目录顶部确认'}
                raise PauseRequired('目录顶部尚未确认，请再向上滚动一次，不要仅等待。')
            folded=next((h for h in obs.catalog['chapters'] if not h['expanded']),None)
            if folded:
                self.state['next_required']={'intent':'expand_chapter','chapter':folded['chapter']}
                raise PauseRequired('顶部已核实，请展开第 '+folded['chapter']+' 章，不能停在折叠目录。')
            feedback=self.state.get('scroll_feedback') or {}
            if not (feedback.get('intent')=='scroll_catalog' and feedback.get('amount',0)<0 and feedback.get('unchanged_count',0)>=2):
                self.state['next_required']={'intent':'scroll_catalog','amount':-3,'reason':'仍需向下滚动校验底部'}
                raise PauseRequired('请继续向下滚动目录完成底部校验，不要仅等待视频。')
        for a in obs.actions:
            area_key='popup' if obs.scene.startswith('popup_') else 'question'
            if a.intent in {'scroll_catalog','expand_chapter','open_lesson','open_quiz'}:area_key='catalog'
            elif a.intent in {'play','pause','mute'} and obs.scene=='course':area_key='player'
            elif a.intent not in {'select_option','fill_blank','submit','next_question','close_popup','scroll_question'}:area_key=None
            override=self.profile.vision_region_overrides.get(area_key)
            if override and a.box and not override.contains(a.box.pixels(self.last_frame.size)):raise PauseRequired('AI 动作超出人工修正的区域。')
            if phase=='scan' and obs.scene in {'course','menu'} and a.intent not in {'scroll_catalog','expand_chapter','inspect','menu','mute'}:raise PauseRequired('目录扫描期间 AI 提议了不相关操作。')
            if a.intent in {'select_option','fill_blank'}:
                if not memory or not memory.get('complete') or not memory.get('answers') or not obs.question:raise PauseRequired('题目尚未完整识别，不能填写。')
                if a.intent=='select_option':
                    option=next((i for i in obs.question['options'] if i['label']==a.label),None)
                    desired=a.label in memory['answers']
                    if a.desired is None:a.desired=desired
                    if not option or a.kind!='click' or a.desired is not desired or not self.same_box(a.box,option['box'],self.last_frame.size):raise PauseRequired('选项动作与已核对答案或控件位置不一致。')
                    if option['selected'] is None:raise PauseRequired('当前选中状态不明确。')
                    if option['selected']==desired:continue
                    if memory['kind']!='multiple' and not desired:raise PauseRequired('单选题不能用点击错误项取消选择。')
                else:
                    if not a.index:
                        matched=[i for i in obs.question['blanks'] if self.same_box(a.box,i['box'],self.last_frame.size)]
                        if len(matched)==1:a.index=matched[0]['index']
                    blank=next((i for i in obs.question['blanks'] if i['index']==a.index),None)
                    if not blank or a.kind!='input' or a.index>len(memory['answers']) or a.value!=memory['answers'][a.index-1] or not self.same_box(a.box,blank['box'],self.last_frame.size):raise PauseRequired('填空动作与空位顺序或答案不一致。')
                    if blank['value'] is None:raise PauseRequired('填空现有内容无法确认。')
                    if normalized(blank['value'])==normalized(a.value):continue
                    if blank['value']:raise PauseRequired('该填空已有不同内容，请核对后继续。')
            if a.intent in {'submit','next_question'}:
                if not memory or not memory.get('verified'):raise PauseRequired('当前题目尚未复核，不能翻页或提交。')
                if a.intent=='submit' and not self.journal.complete(memory['quiz']):raise PauseRequired('仍有未完成题目，已阻止交卷。')
                if self.journal.state(memory['quiz'])!='working':raise PauseRequired('已发送提交，不会重复交卷。')
            if a.intent=='confirm_submit':
                quiz=task.quiz_ref if task and task.kind=='quiz' else self.state.get('quiz_id')
                if obs.scene!='chapter_confirmation' or not quiz or self.journal.state(quiz)!='submit_sent':raise PauseRequired('确认弹窗未关联本次提交。')
            if a.intent=='close_popup' and obs.scene!='popup_result':raise PauseRequired('不会关闭尚未作答的弹题。')
            if a.intent in {'open_lesson','open_quiz'}:
                if not task:raise PauseRequired('没有待进入的课程任务。')
                if obs.scene!='course':raise PauseRequired('请先完成当前弹题或返回课程，再切换资源。')
                if (a.intent=='open_lesson')!=(task.kind=='video'):raise PauseRequired('目录任务类型与操作不一致。')
                row=next((r for r in obs.catalog['rows'] if self.row_task(r).task_id==task.task_id),None)
                if not row or row['done'] is not False or not self.same_box(a.box,row['box'],self.last_frame.size):raise PauseRequired('进入目标不是已确认的未完成任务。')
                if task.kind=='video' and row['duration']!=task.duration:raise PauseRequired('目标 '+task.code+' 的时长不匹配，可能把裁切行误认成目标，请滚动显示完整编号和标题再识别。')
                if a.box.pixels(self.last_frame.size).h>180:raise PauseRequired('资源点击框过大，请只定位目标课时这一行。')
                if a.intent=='open_lesson' and obs.lesson_code==task.code and row['selected']:continue
            if a.intent=='expand_chapter':
                heading=next((h for h in obs.catalog['chapters'] if self.same_box(a.box,h['box'],self.last_frame.size)),None)
                if not heading:raise PauseRequired('展开目标未关联可见章节标题。')
                if heading['expanded']:continue
                if a.box.pixels(self.last_frame.size).h>120:raise PauseRequired('章节标题框包含了下面资源，请只框章节标题再展开。')
            if a.intent=='mute':
                if obs.player.get('muted') is True:continue
                if obs.player.get('muted') is None:raise PauseRequired('静音状态不明确，不盲目切换声音。')
            if a.intent in {'play','pause'}:
                wanted='playing' if a.intent=='play' else 'paused'
                if obs.player['state']==wanted:continue
                if obs.player['state']=='unknown':raise PauseRequired('播放状态不明确，先显示控件再继续。')
                if a.intent=='play' and obs.player['state']=='ended':raise PauseRequired('当前视频已结束，请等待自身完成标记，不要重播。')
                previous=self.state.get('player') or {}
                if a.intent=='play' and not obs.player.get('button_text') and previous.get('lesson_code')==obs.lesson_code and previous.get('position') is not None and obs.player.get('position') is not None and obs.player['position']>previous['position']:
                    raise PauseRequired('播放时间正在前进，请核对“暂停”按钮表示正在播放，不要误点成暂停。')
                if a.intent=='play' and self.profile.auto_mute and not self.state.get('mute_verified'):raise PauseRequired('尚未确认静音，请先显示声音控件并设置静音。')
            if a.kind=='click' and a.intent in {'inspect','scroll_catalog','scroll_question','fill_blank'}:raise PauseRequired('AI 动作类型与用途不匹配。')
            if a.intent=='return_course' and obs.scene not in {'chapter_result','course'}:raise PauseRequired('当前不属于返回课程流程。')
            actions.append(a)
        return actions

    def before_action(self,obs):
        proposal=[serial(asdict(a)) for a in obs.actions]
        signature={'scene':obs.scene,'lesson':obs.lesson_code,'player':obs.player,'question':serial(obs.question),
                   'catalog':[(r['chapter'],r['code'],r['kind'],r['done'],r['selected'],r['box'].json()) for r in obs.catalog['rows']],'actions':proposal}
        marker=fingerprint(json.dumps(signature,ensure_ascii=False,sort_keys=True))
        self.repeated_proposal=self.repeated_proposal+1 if marker==self.last_proposal else 0
        self.last_proposal=marker
        if self.repeated_proposal>=2:raise PauseRequired('同一操作重复两次仍未观察到效果，已停止旧动作。')
        for a in obs.actions:
            memory=self.state.get('question')
            if a.kind=='scroll':
                area=a.box.pixels(self.last_frame.size)
                before=np.asarray(self.last_frame.crop(area.bounds).convert('RGB').resize((120,160)),dtype=np.int16)
                self.pending_scroll=(area,before,a.amount,a.intent)
            if a.intent=='submit':
                if not self.journal.transition(memory['quiz'],'working','submit_sent'):raise PauseRequired('提交记录已经变化，未重复点击。')
                self.submission_deadline=self.clock()+90
            if a.intent=='confirm_submit':
                task=self.controller.active;quiz=task.quiz_ref if task and task.kind=='quiz' else self.state['quiz_id']
                if not self.journal.transition(quiz,'submit_sent','confirm_sent'):raise PauseRequired('确认记录已经变化，未重复点击。')
            if a.intent=='close_popup':
                self.state['popup_closing']={'quiz':memory['quiz'] if memory and memory['scope']=='popup' else None,'lesson':obs.lesson_code or (self.controller.active.code if self.controller.active else '')}
            if a.intent=='select_option':memory['dirty'].append(a.label);memory['verified']=False
            if a.intent=='fill_blank':memory['dirty'].append('blank:'+str(a.index));memory['verified']=False
            self.state['last_action']={'intent':a.intent,'kind':a.kind,'value':a.value if a.intent in {'open_lesson','open_quiz'} else '', 'at':self.clock()}
        self.save()

    def request_review(self,error):
        self.review_count+=1
        if self.review_count>=3:raise PauseRequired('AI 多次复核仍未通过：'+str(error))
        self.state['last_error']=str(error);self.force=True
        self.note('AI 提议未通过校验，重新看图：'+str(error))

    def step(self):
        frame=self.desktop.capture();self.last_frame=frame
        if time.monotonic()<self.next_observation_at:return
        if self.pending_scroll:
            area,before,amount,intent=self.pending_scroll;self.pending_scroll=None
            after=np.asarray(frame.crop(area.bounds).convert('RGB').resize((120,160)),dtype=np.int16)
            changed=float(np.mean(np.abs(after-before)))>1.2
            previous=self.state.get('scroll_feedback') or {}
            count=previous.get('unchanged_count',0)+1 if not changed and previous.get('amount',0)*amount>0 and previous.get('intent')==intent else int(not changed)
            self.state['scroll_feedback']={'changed':changed,'unchanged_count':count,'amount':amount,'intent':intent,
                                           'direction':'up' if amount>0 else 'down','boundary_hint':('top_candidate' if amount>0 else 'bottom_candidate') if count>=2 else None}
        if not self.force and self.observation and not self.watch.reason(frame,self.observation,time.monotonic()):return
        self.force=False
        self.emit('vision_status',{'message':'正在请求 AI 看图判断…','scene':'requesting','calls':self.calls,'elapsed':None,'tokens':self.tokens})
        if self.stop.is_set():raise Stopped()
        frame_id=uuid.uuid4().hex
        self.calls+=1
        try:obs=self.ai.observe(frame,self.context(),frame_id)
        except VisionProtocolError as exc:
            self.request_review(exc);return
        if self.stop.is_set():raise Stopped()
        if obs.frame_id!=frame_id:raise PauseRequired('AI 回复属于旧截图，已丢弃。')
        self.desktop.check();self.canonicalize_receipt(obs);self.guard(obs)
        self.tokens=self.ai.tokens if type(getattr(self.ai,'tokens',None)) is int else self.tokens+int(obs.usage.get('total_tokens',0))
        self.latencies.append(obs.elapsed)
        self.observation=obs;self.watch.remember(frame,obs,time.monotonic())
        if self.on_observation:self.on_observation(frame,obs)
        self.emit('vision_status',{'message':obs.message,'scene':obs.scene,'calls':self.calls,'elapsed':obs.elapsed,'tokens':self.tokens})
        if self.advance_state(obs):self.save();return
        try:obs.actions=self.validate_actions(obs)
        except PauseRequired as exc:
            self.request_review(exc);return
        self.review_count=0;self.state['last_error']=None;self.state['next_required']=None
        if obs.actions:
            self.idle_count=0
            self.before_action(obs)
            labels='、'.join(a.intent for a in obs.actions)
            self.note('AI 操作：'+labels)
            self.emit('vision_status',{'message':'正在执行：'+labels,'scene':obs.scene,'calls':self.calls,'elapsed':obs.elapsed,'tokens':self.tokens})
            execution_started=time.monotonic()
            try:self.executor.execute(frame,obs)
            except TargetUnclear as exc:
                self.rollback_prepared(obs);self.request_review(exc)
            except InputFocusUnclear as exc:
                self.request_review(exc)
            except FrameChanged:
                # This exception is raised by the desktop's BEFORE-input image
                # check. A single submit click definitely was not sent.
                self.rollback_prepared(obs)
                self.note('目标画面在输入前变化，已丢弃旧动作并重新看图。')
            finally:self.execution_latencies.append(round(time.monotonic()-execution_started,4))
            self.force=True
            if all(a.kind=='wait' for a in obs.actions):self.next_observation_at=time.monotonic()+2
        elif self.state.get('completion_waiting'):
            self.idle_count=0;self.force=True;self.next_observation_at=time.monotonic()+2
        elif obs.scene in {'popup_question','popup_result','chapter_question','chapter_confirmation','chapter_result','menu'} or self.state['phase']!='watch' or obs.player['state']!='playing' or self.profile.auto_mute and not self.state.get('mute_verified'):
            self.idle_count+=1
            if self.idle_count>=3:raise PauseRequired('AI 多次观察仍没有可执行的下一步，请检查页面或手动修正区域。')
            self.waiting_after=self.waiting_after or self.clock()
            if self.clock()-self.waiting_after>90:raise PauseRequired('AI 连续观察仍未给出可执行动作，请检查页面或修正区域。')
            self.force=True
        else:self.waiting_after=None;self.idle_count=0
        self.state['player']={'lesson_code':obs.lesson_code,**obs.player}
        self.save()

    def rollback_prepared(self,obs):
        for action in obs.actions:
            if action.intent=='submit':self.journal.transition(self.state['question']['quiz'],'submit_sent','working')
            if action.intent=='confirm_submit':
                task=self.controller.active;quiz=task.quiz_ref if task and task.kind=='quiz' else self.state['quiz_id']
                self.journal.transition(quiz,'confirm_sent','submit_sent')

    def run(self):
        self.note('AI 看图模式将在 5 秒后开始；仅处理绑定课程窗口。')
        if self.stop.wait(5):
            if hasattr(self.ai,'close'):self.ai.close()
            self.emit('stopped','已停止 AI 看图模式。');return
        try:
            while not self.stop.is_set():
                try:
                    if self.profile.max_minutes and time.monotonic()-self.started_at>self.profile.max_minutes*60:raise Stopped()
                    self.step();self.desktop.wait(.25)
                except PauseRequired as exc:
                    self.pause.set();self.emit('paused',str(exc))
                    while self.pause.is_set() and not self.stop.wait(.1):pass
                    if not self.stop.is_set():
                        self.on_resume()
                        if self.stop.wait(3):break
                except Stopped:break
                except Exception as exc:
                    self.pause.set();self.emit('paused','AI 看图模式发生 '+type(exc).__name__+'，已暂停；不会执行未确认动作。')
                    while self.pause.is_set() and not self.stop.wait(.1):pass
                    if not self.stop.is_set():self.on_resume()
        finally:
            if hasattr(self.ai,'close'):self.ai.close()
            if not self.complete:self.emit('stopped','已停止 AI 看图模式。')
