import threading
from unittest.mock import Mock
import pytest
from PIL import Image,ImageDraw
from desktop_app.domain import CourseTask,LessonProgress,Rect,Span,View,Profile,Answer,PauseRequired
from desktop_app.storage import Journal
from desktop_app.course import CourseController,CourseSnapshot,CatalogRow,CatalogParser,CourseRunner,completion_marker


def task(identifier,chapter='1',kind='video',order=0,code=None):
    return CourseTask(identifier,chapter,code or (chapter+'.'+str(order+1) if kind=='video' else ''),'同名课时' if kind=='video' else '章节测试',kind,4 if kind=='video' else 0,order)


def snapshot(tasks,done=(),selected=None):
    return CourseSnapshot([CatalogRow(t,Rect(0,0,100,30),t.task_id in done,t.task_id==selected) for t in tasks],{t.chapter for t in tasks},selected,True)


def controller(tmp_path):
    now=[100.]
    j=Journal(tmp_path/'course.db')
    return CourseController(j,'course',lambda:now[0]),now,j


def test_schedule_video_then_chapter_quiz_then_next_chapter(tmp_path):
    c,now,j=controller(tmp_path)
    tasks=[task('a'),task('b',order=1),task('q',kind='quiz',order=2),task('c','2',order=3)]
    c.reconcile(snapshot(tasks))
    assert c.choose().task_id=='a'
    c.done(tasks[0]);assert c.choose().task_id=='b'
    c.done(tasks[1]);assert c.choose().task_id=='q'
    c.done(tasks[2]);assert c.choose().task_id=='c'


def test_resume_keeps_task_and_position(tmp_path):
    c,now,j=controller(tmp_path)
    tasks=[task('a'),task('b',order=1)]
    c.reconcile(snapshot(tasks,selected='b'))
    c.activate(c.choose())
    c.video(View([],video_visible=True,video_lesson='1.2',video_position=2,video_duration=4),False)
    restored=CourseController(j,'course',lambda:now[0]+500)
    assert restored.choose().task_id=='b'
    assert j.lesson('course','b').position==2
    assert restored.last_advance==600


def test_other_completed_row_never_completes_current_video(tmp_path):
    c,now,j=controller(tmp_path)
    a,b=task('a'),task('b',order=1)
    c.reconcile(snapshot([a,b],done=['a']))
    c.activate(b)
    view=View([],video_lesson='1.2',video_position=4,video_duration=4,video_finished=True)
    assert c.video(view,False)=='waiting_completion'
    assert b.status=='running'
    now[0]+=20
    assert c.video(view,True)=='done'
    assert b.status=='done'


def test_delayed_platform_confirmation_times_out(tmp_path):
    c,now,j=controller(tmp_path)
    a=task('a');c.reconcile(snapshot([a]));c.activate(a)
    v=View([],video_lesson='1.1',video_position=4,video_duration=4,video_finished=True)
    assert c.video(v,False)=='waiting_completion'
    now[0]+=61
    with pytest.raises(PauseRequired,match='仍未确认'):
        c.video(v,False)


def test_stall_rechecks_once_then_pauses(tmp_path):
    c,now,j=controller(tmp_path)
    a=task('a');c.reconcile(snapshot([a]));c.activate(a)
    v=View([],video_lesson='1.1',video_position=0,video_duration=4,video_visible=True)
    now[0]+=61
    assert c.video(v,False)=='recover'
    now[0]+=61
    with pytest.raises(PauseRequired,match='持续无进展'):
        c.video(v,False)


def test_paused_player_resumes_and_mismatched_lesson_pauses(tmp_path):
    c,now,j=controller(tmp_path)
    a=task('a');c.reconcile(snapshot([a]));c.activate(a)
    assert c.video(View([],video_lesson='1.1',video_paused=True),False)=='play'
    with pytest.raises(PauseRequired,match='不一致'):
        c.video(View([],video_lesson='2.1'),True)


def test_existing_completion_not_counted_as_new_and_scan_required(tmp_path):
    c,now,j=controller(tmp_path)
    a=task('a');c.reconcile(snapshot([a],done=['a']))
    assert a.status=='done' and a.completion_source=='existing'
    assert c.choose() is None
    bad=snapshot([a]);bad.complete=False
    with pytest.raises(PauseRequired):c.reconcile(bad)


def test_completed_marker_is_check_not_merely_blue_circle():
    image=Image.new('RGB',(300,120),'white');draw=ImageDraw.Draw(image)
    region=Rect(0,0,300,120);row=Rect(0,20,300,40)
    draw.ellipse((260,25,284,49),fill='#3688f7')
    assert completion_marker(image,region,row) is None
    draw.line((266,37,271,42,280,32),fill='white',width=3)
    assert completion_marker(image,region,row) is True


def test_same_name_videos_keep_different_numbered_ids():
    image=Image.new('RGB',(400,400),'white')
    spans=[Span('第一章',Rect(10,10,80,25)),Span('1.1 同名课时',Rect(10,80,180,25)),Span('00:04',Rect(20,115,80,25)),Span('1.2 同名课时',Rect(10,190,180,25)),Span('00:04',Rect(20,225,80,25))]
    page=CatalogParser().read(image,spans,Rect(0,0,400,400),'course')
    assert [r.task.code for r in page.rows]==['1.1','1.2']
    assert len({r.task.task_id for r in page.rows})==2


def test_course_tables_preserve_legacy_answer_records(tmp_path):
    j=Journal(tmp_path/'j.db');j.begin('chapter:old',1);j.record('chapter:old',1,'key',Answer(['A']),True)
    c=CourseController(j,'new');c.reconcile(snapshot([task('v')]))
    assert j.complete('chapter:old')


def runner_fixture(tmp_path):
    j=Journal(tmp_path/'j.db')
    profile=Profile(task_name='测试课程',auto_mute=False,regions={key:Rect(0,0,440,500) for key in ('player','question','popup')})
    profile.regions['catalog']=Rect(450,0,150,500)
    runner=CourseRunner(Mock(),Mock(),Mock(),j,profile,threading.Event(),threading.Event(),Mock())
    return runner,j,Image.new('RGB',(600,500),'white')


def test_course_submit_returns_without_duplicate_submit(tmp_path):
    runner,j,frame=runner_fixture(tmp_path)
    q=task('quiz',kind='quiz');runner.controller.reconcile(snapshot([q]));runner.controller.activate(q)
    runner.quiz='chapter:test';q.quiz_ref=runner.quiz;j.begin(runner.quiz,1);j.transition(runner.quiz,'working','submit_sent')
    view=View([Span('第一章章节测试',Rect(10,60,150,30))],submitted=True,buttons={'return':Rect(20,30,90,30)})
    assert runner.pending_submission(frame,view)
    assert j.state('chapter:test')=='success'
    assert q.status=='done' and runner.controller.session['phase']=='returning'
    runner._return(frame,view)
    assert runner.desktop.click.call_count==1


def test_whole_course_needs_final_second_scan(tmp_path):
    runner,j,frame=runner_fixture(tmp_path)
    tasks=[task('v')]
    runner.scanner=Mock()
    runner.scanner.scan.return_value=snapshot(tasks,done=['v'])
    view=View([Span('测试课程',Rect(10,10,100,20)),Span('第一章',Rect(10,40,100,20))])
    runner.step(frame,view)
    assert runner.scanner.scan.call_count==2
    assert runner.complete and runner.stop.is_set()


def test_missing_course_calibration_never_silently_runs_answer_only(tmp_path):
    with pytest.raises(PauseRequired,match='四个区域'):
        CourseRunner(Mock(),Mock(),Mock(),Journal(tmp_path/'j.db'),Profile(regions={'question':Rect(0,0,100,100)}),threading.Event(),threading.Event(),Mock())


def test_overlap_quiz_above_next_heading_belongs_to_previous_chapter():
    image=Image.new('RGB',(400,400),'white')
    spans=[Span('章节测试',Rect(20,30,150,25)),Span('第二章',Rect(10,130,80,25)),Span('2.1 同名课时',Rect(10,200,180,25)),Span('00:04',Rect(20,240,80,25))]
    page=CatalogParser().read(image,spans,Rect(0,0,400,400),'course',chapter='2')
    assert page.rows[0].task.kind=='quiz' and page.rows[0].task.chapter=='1'
    assert page.rows[1].task.chapter=='2'


def test_result_from_different_chapter_does_not_complete_task(tmp_path):
    runner,j,frame=runner_fixture(tmp_path)
    q=task('quiz',kind='quiz');runner.controller.reconcile(snapshot([q]));runner.controller.activate(q)
    runner.quiz='chapter:test';q.quiz_ref=runner.quiz;j.begin(runner.quiz,1);j.transition(runner.quiz,'working','submit_sent')
    with pytest.raises(PauseRequired,match='对应章节'):
        runner.pending_submission(frame,View([Span('第二章章节测试',Rect(10,60,150,30))],submitted=True))
    assert q.status!='done' and j.state(runner.quiz)=='submit_sent'
