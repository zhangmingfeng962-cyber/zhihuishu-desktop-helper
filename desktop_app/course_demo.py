"""Two chapters, four timed videos, four popup types and two chapter tests.

This disposable local fixture never connects to the teaching platform or an AI API.
"""
from __future__ import annotations
import tkinter as tk
from .domain import Answer,Rect,Profile


class CourseDemoAI:
    def solve(self,question):
        values={'single':['B'],'multiple':['A','B'],'judge':['A'],'blank':['北京','2']}
        return Answer(values[question.kind],'离线固定答案，只验证看课与答题联动。')


class CourseDemo:
    title='Autovisor 流程演示 — 整课看课＋答题'
    course_name='整课离线演示'

    def __init__(self,root,duration=4,completion_delay=1800,first_duration=None):
        self.root=root;root.title(self.title);root.geometry('1500x1080+25+20')
        root.configure(bg='#eef2f7')
        self.duration,self.completion_delay=duration,completion_delay
        self.durations={code:duration for code in ('1.1','1.2','2.1','2.2')}
        if first_duration:self.durations['1.1']=first_duration
        self.expanded={'1':False,'2':False}
        self.done=set();self.current=None;self.position=0;self.playing=False
        self.popup=None;self.popup_asked=set();self.stage='course';self.quiz_chapter=None;self.quiz_index=1
        self.quiz_answers={};self.selected=set();self.entries=[];self.widgets=[]
        self.video_starts={code:0 for code in ('1.1','1.2','2.1','2.2')}
        self.popup_submits={code:0 for code in self.video_starts}
        self.popup_answers={};self.quiz_submits={'1':0,'2':0}
        self.page=tk.Canvas(root,bg='white',highlightthickness=0)
        self.page.place(x=20,y=20,width=980,height=1020)
        self.catalog=tk.Canvas(root,bg='white',highlightthickness=0,yscrollincrement=20)
        self.catalog.place(x=1030,y=150,width=440,height=480)
        self.scrollbar=tk.Scrollbar(root,command=self.catalog.yview)
        self.scrollbar.place(x=1471,y=150,width=23,height=480)
        self.catalog.configure(yscrollcommand=self.scrollbar.set)
        self.header=tk.Label(root,text='目录：自动展开、滚动、核对完成标记',font=('Microsoft YaHei UI',13),bg='#eef2f7',fg='#172b4d')
        self.header.place(x=1030,y=100)
        self.detail=tk.Label(root,text='离线演示 / 固定答案\n普通速度计时\n完成标记延迟更新',justify='left',font=('Microsoft YaHei UI',12),bg='#eef2f7',fg='#475569')
        self.detail.place(x=1040,y=680)
        root.bind_all('<MouseWheel>',self.wheel,add='+')
        self.draw_page();self.draw_catalog();root.after(1000,self.tick)

    def profile(self):
        return Profile(width=1500,height=1080,task_name=self.course_name,mode='course',answer_interval=.35,poll_interval=.5,
                       regions={'player':Rect(30,110,950,650),'catalog':Rect(1030,150,440,480),
                                'popup':Rect(165,155,755,555),'question':Rect(35,110,930,800)})

    def text(self,x,y,value,size=15,bold=False,tag=None,width=880):
        return self.page.create_text(x,y,text=value,anchor='nw',font=('Microsoft YaHei UI',size,'bold' if bold else 'normal'),
                                     fill='#172b4d',width=width,tags=tag or '')

    def button(self,x,y,label,command,width=180):
        tag='button_'+str(len(self.page.find_all()))
        self.page.create_rectangle(x,y,x+width,y+58,fill='#1d4ed8',outline='',tags=tag)
        self.page.create_text(x+width/2,y+29,text=label,font=('Microsoft YaHei UI',14),fill='white',tags=tag)
        self.page.tag_bind(tag,'<Button-1>',lambda e:command())

    def clear(self):
        for widget in self.widgets:
            widget.destroy()
        self.widgets=[];self.entries=[];self.page.delete('all')

    def draw_page(self):
        self.clear()
        self.text(20,15,self.course_name,20,True)
        if self.stage=='course':
            self.text(20,85,'当前课时 '+(self.current+'、同名课时' if self.current else '尚未选择'),15,True)
            self.page.create_rectangle(20,140,950,640,fill='#edf5ff',outline='#cbd5e1')
            self.text(60,220,'本地视频计时演示\n正常播放 → 弹题 → 续播 → 完成标记',21,True)
            self.text(60,375,'真实课程需另外登录验证；此处不是智慧树成绩。',13)
            duration=self.durations.get(self.current,self.duration)
            self.text(150,685,f'00:{self.position:02d} / 00:{duration:02d}',16,True)
            if self.current and self.position>=duration:
                self.text(25,675,'播放结束',14)
            else:
                self.button(25,665,'暂停' if self.playing else '播放',self.toggle_play,110)
            if self.popup:
                self.page.create_rectangle(145,130,925,705,fill='white',outline='#94a3b8',width=2)
                self.draw_question(self.popup,True)
        elif self.stage=='quiz':
            self.text(20,85,'第'+self.quiz_chapter+'章章节测试',17,True)
            self.draw_question('judge' if self.quiz_index==1 else 'blank',False)
        elif self.stage=='confirm':
            self.text(20,85,'第'+self.quiz_chapter+'章章节测试',17,True)
            self.text(180,210,'确认提交作业',24,True)
            self.text(180,300,'提交后无法修改，是否提交？',17)
            self.button(330,440,'确认提交',self.submit_quiz)
        elif self.stage=='result':
            self.text(20,85,'第'+self.quiz_chapter+'章章节测试',17,True)
            self.text(180,210,'提交成功',25,True)
            self.text(180,295,'本次得分：100 / 100',19)
            self.text(180,350,'章节测验演示提交次数：'+str(self.quiz_submits[self.quiz_chapter]),14)
            self.button(330,440,'返回课程',self.return_course)
        elif self.stage=='complete':
            self.text(100,210,'整门课程完成',27,True)
            self.text(100,300,'视频 4 / 4    视频弹题 4 / 4    章节作业 2 / 2',18)
            self.text(100,380,'离线测试结果，不代表真实智慧树学习记录。',14)

    def draw_question(self,kind,popup):
        x=180 if popup else 70
        y=175 if popup else 170
        if not popup:
            self.text(x,y-40,f'第 {self.quiz_index} 题   共 2 题',14)
        names={'single':'单选题','multiple':'多选题','judge':'判断题','blank':'填空题'}
        self.text(x,y,names[kind],17,True)
        stems={'single':'1 + 1 等于几？','multiple':'小于 3 的正整数有哪些？','judge':'地球是一颗行星。',
               'blank':'第一个空填写中华人民共和国的首都，第二个空填写 1 + 1 的结果。'}
        self.text(x,y+55,stems[kind],15,width=700)
        if kind=='blank':
            old=[] if popup else self.quiz_answers.get(self.quiz_index,[])
            for i in range(2):
                self.text(x,y+130+i*100,f'第 {i+1} 空',12)
                frame=tk.Frame(self.page,bg='#64748b',padx=1,pady=1)
                entry=tk.Entry(frame,font=('Microsoft YaHei UI',17),relief='flat',highlightthickness=2,highlightbackground='white',highlightcolor='#2563eb')
                entry.pack(fill='both',expand=True)
                if old:entry.insert(0,old[i])
                self.page.create_window(x,y+160+i*100,window=frame,anchor='nw',width=550,height=54)
                self.widgets.append(frame);self.entries.append(entry)
        else:
            labels=['正确','错误'] if kind=='judge' else ['1','2','3']
            for i,value in enumerate(labels):
                label=chr(65+i);yy=y+150+i*65;tag='option_'+label
                draw=self.page.create_rectangle if kind=='multiple' else self.page.create_oval
                draw(x,yy,x+24,yy+24,fill='#2563eb' if label in self.selected else 'white',outline='#64748b',width=2,tags=tag)
                self.page.create_text(x+48,yy+12,text=label+'. '+value,anchor='w',font=('Microsoft YaHei UI',16),fill='#172b4d',tags=tag)
                self.page.tag_bind(tag,'<Button-1>',lambda e,label=label:self.choose(label))
        if popup:
            self.button(x+250,y+430,'提交答案',self.submit_popup)
        else:
            self.button(x+470,y+500,'下一题' if self.quiz_index==1 else '提交作业',self.next_question if self.quiz_index==1 else self.confirm_quiz)

    def choose(self,label):
        kind=self.popup or ('judge' if self.quiz_index==1 else 'blank')
        if kind=='multiple':self.selected.symmetric_difference_update({label})
        else:self.selected={label}
        self.draw_page()

    def values(self):
        return [e.get() for e in self.entries] if self.entries else sorted(self.selected)

    def submit_popup(self):
        values=self.values()
        if not values or any(not value for value in values):return
        self.popup_submits[self.current]+=1;self.popup_answers[self.current]=values
        self.popup=None;self.selected=set();self.playing=True;self.draw_page()

    def next_question(self):
        if not self.selected:return
        self.quiz_answers[self.quiz_index]=sorted(self.selected)
        self.quiz_index+=1;self.selected=set();self.draw_page()

    def confirm_quiz(self):
        values=self.values()
        if len(values)!=2 or any(not value for value in values):return
        self.quiz_answers[self.quiz_index]=values;self.stage='confirm';self.draw_page()

    def submit_quiz(self):
        self.quiz_submits[self.quiz_chapter]+=1
        self.done.add('q'+self.quiz_chapter)
        self.stage='result';self.draw_page();self.draw_catalog()

    def return_course(self):
        self.stage='course';self.quiz_chapter=None;self.selected=set();self.draw_page()

    def toggle_play(self):
        if self.current and self.position<self.durations[self.current]:
            self.playing=not self.playing;self.draw_page()

    def tick(self):
        if self.stage=='course' and self.playing and self.current and not self.popup:
            self.position+=1
            if self.position==1 and self.current not in self.popup_asked:
                kinds=dict(zip(('1.1','1.2','2.1','2.2'),('single','multiple','judge','blank')))
                self.popup=kinds[self.current];self.popup_asked.add(self.current);self.selected=set();self.playing=False
            if self.position>=self.durations[self.current]:
                self.position=self.durations[self.current];self.playing=False
                code=self.current
                self.root.after(self.completion_delay,lambda code=code:self.mark_video_done(code))
            self.draw_page()
        self.root.after(1000,self.tick)

    def mark_video_done(self,code):
        self.done.add(code);self.draw_catalog()

    def select_video(self,code):
        if self.current!=code:
            self.current=code;self.position=0;self.video_starts[code]+=1
        self.stage='course';self.playing=self.position<self.durations[self.current];self.popup=None;self.selected=set()
        self.draw_page();self.draw_catalog()

    def select_quiz(self,ch):
        self.quiz_chapter=ch
        self.stage='result' if 'q'+ch in self.done else 'quiz'
        self.quiz_index=1;self.quiz_answers={};self.selected=set();self.playing=False
        self.draw_page()

    def toggle_chapter(self,ch):
        self.expanded[ch]=not self.expanded[ch];self.draw_catalog()

    def draw_catalog(self):
        old=self.catalog.yview()[0]
        self.catalog.delete('all');y=12
        for ch,name in (('1','第一章'),('2','第二章')):
            tag='heading'+ch
            self.catalog.create_text(12,y+18,text=name,anchor='w',font=('Microsoft YaHei UI',14,'bold'),fill='#172b4d',tags=tag)
            pts=(108,y+13,120,y+13,114,y+23) if self.expanded[ch] else (108,y+10,108,y+24,119,y+17)
            self.catalog.create_polygon(*pts,fill='#475569',tags=tag)
            self.catalog.tag_bind(tag,'<Button-1>',lambda e,ch=ch:self.toggle_chapter(ch))
            y+=54
            if self.expanded[ch]:
                for n in (1,2):
                    code=ch+'.'+str(n);tag='lesson'+code
                    if self.current==code:
                        self.catalog.create_rectangle(0,y,440,y+88,fill='#e9f2ff',outline='',tags=tag)
                    self.catalog.create_text(18,y+18,text=code+' 同名课时',anchor='w',font=('Microsoft YaHei UI',14),fill='#172b4d',tags=tag)
                    self.catalog.create_text(38,y+52,text='00:'+str(self.durations[code]).zfill(2),anchor='w',font=('Microsoft YaHei UI',13),fill='#475569',tags=tag)
                    self.draw_done(code,y+31)
                    self.catalog.tag_bind(tag,'<Button-1>',lambda e,code=code:self.select_video(code))
                    y+=98
                tag='quiz'+ch
                self.catalog.create_text(18,y+24,text='章节测试',anchor='w',font=('Microsoft YaHei UI',14),fill='#172b4d',tags=tag)
                self.draw_done('q'+ch,y+24)
                self.catalog.tag_bind(tag,'<Button-1>',lambda e,ch=ch:self.select_quiz(ch))
                y+=80
        self.catalog.configure(scrollregion=(0,0,440,max(480,y)))
        self.catalog.yview_moveto(old)

    def draw_done(self,key,y):
        if key in self.done:
            self.catalog.create_oval(394,y-12,418,y+12,fill='#3688f7',outline='')
            self.catalog.create_line(400,y,405,y+5,414,y-5,fill='white',width=3)

    def wheel(self,event):
        x=self.root.winfo_pointerx()-self.root.winfo_rootx();y=self.root.winfo_pointery()-self.root.winfo_rooty()
        if 1030<=x<=1470 and 150<=y<=630:
            self.catalog.yview_scroll(-int(event.delta/120)*2,'units')

    def summary(self):
        self.stage='complete';self.draw_page()

    def metrics(self):
        expected={'1.1':['B'],'1.2':['A','B'],'2.1':['A'],'2.2':['北京','2']}
        return {'video_starts':self.video_starts,'popup_submits':self.popup_submits,'quiz_submits':self.quiz_submits,
                'popup_answers':self.popup_answers,'done':sorted(self.done),'correct_popups':self.popup_answers==expected}
