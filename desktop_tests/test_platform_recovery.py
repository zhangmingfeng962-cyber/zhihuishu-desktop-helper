import threading
from unittest.mock import Mock
import pytest
from PIL import Image,ImageDraw
from desktop_app.domain import Profile,Rect,Span,View,Answer,CourseTask
from desktop_app.vision import Perception
from desktop_app.runner import Runner,PopupAnswered
from desktop_app.storage import Journal
from desktop_app.course import CatalogParser,CatalogScanner,CatalogPage,CatalogRow,Heading,CourseController,CourseSnapshot,completion_marker


def test_graded_popup_is_closed_without_answering_again(tmp_path):
    frame=Image.new('RGB',(800,600),'white')
    profile=Profile(regions={'popup':Rect(100,100,500,400),'player':Rect(0,80,650,500)})
    spans=[Span('【单选题】测试问题',Rect(120,180,280,30)),Span('A.甲',Rect(140,240,90,30)),Span('B.乙',Rect(140,300,90,30)),Span('正确答案：B',Rect(120,360,160,30)),Span('关闭',Rect(300,430,70,30))]
    view=Perception(Mock()).inspect(frame,profile,spans,scope_hint='video')
    assert view.popup_feedback and view.question is None
    desktop,ai=Mock(),Mock()
    runner=Runner(desktop,Mock(),ai,Journal(tmp_path/'j.db'),profile,threading.Event(),threading.Event(),Mock())
    runner.step(frame,view);runner.step(frame,view)
    desktop.click.assert_called_once_with(view.buttons['popup_done'],frame)
    ai.solve.assert_not_called()
    for _ in range(2):runner.step(frame,View([],video_visible=True,video_lesson='2.3.4'))
    assert not runner.popup_feedback_closed


def test_chapter_answer_text_cannot_close_unrelated_popup():
    profile=Profile(regions={'popup':Rect(0,0,200,200),'question':Rect(250,100,400,400)})
    spans=[Span('正确答案：B',Rect(300,220,160,30)),Span('关闭',Rect(300,300,70,30))]
    view=Perception(Mock()).inspect(Image.new('RGB',(800,600),'white'),profile,spans,scope_hint='chapter')
    assert not view.popup_feedback and 'popup_done' not in view.buttons


def test_browser_release_version_is_not_current_lesson():
    profile=Profile(regions={'player':Rect(0,350,900,600),'catalog':Rect(920,400,280,550)})
    spans=[Span('Release v0.2.1-beta.1',Rect(400,10,300,30)),Span('第二章 2.3.4、当前课程',Rect(50,300,400,35))]
    view=Perception(Mock()).inspect(Image.new('RGB',(1200,1000),'white'),profile,spans)
    assert view.video_lesson=='2.3.4'


def test_completed_feedback_during_fill_is_a_transition(tmp_path):
    from desktop_app.domain import Question
    desktop,vision=Mock(),Mock()
    frame=Image.new('RGB',(800,600),'white');desktop.capture.return_value=frame
    vision.inspect.return_value=View([],popup_feedback=True,buttons={'popup_done':Rect(300,400,100,30)})
    runner=Runner(desktop,vision,Mock(),Journal(tmp_path/'j.db'),Profile(),threading.Event(),threading.Event(),Mock())
    with pytest.raises(PopupAnswered):runner.verify_identity(Question('single','题目',1,1,'popup'))


def test_partial_progress_ring_is_unfinished():
    image=Image.new('RGB',(400,160),'white');draw=ImageDraw.Draw(image)
    draw.ellipse((360,45,384,69),outline='#3688f7',width=5)
    assert completion_marker(image,Rect(0,0,400,160),Rect(0,40,400,50)) is False


def test_play_control_above_bottom_letterbox_is_detected():
    frame=Image.new('RGB',(700,600),'black')
    draw=ImageDraw.Draw(frame)
    draw.rectangle((35,420,650,470),fill='#dddddd')
    draw.polygon([(25,430),(25,458),(48,444)],fill='white')
    box=Perception.play_triangle(frame,Rect(0,100,650,480))
    assert box is not None and 430<=box.y<445


def test_percentage_overrides_colored_icon_and_clipped_header_is_skipped():
    image=Image.new('RGB',(500,400),'white')
    spans=[Span('目录',Rect(10,5,80,30)),Span('00:06:16',Rect(120,48,120,28)),Span('2.3.4',Rect(10,150,80,30)),Span('职业兴趣',Rect(120,130,160,30)),Span('00:08:51',Rect(120,165,120,30)),Span('52%',Rect(450,165,40,30))]
    page=CatalogParser().read(image,spans,Rect(0,0,500,400),'course')
    assert len(page.rows)==1 and page.rows[0].task.code=='2.3.4'
    assert page.rows[0].done is False


def test_catalog_merges_obscured_and_wrapped_rows_across_scrolls():
    a=CourseTask('a','1','1.1','长标题...','video',30,0)
    b=CourseTask('a','1','1.1','长标题的完整文字','video',30,0)
    c=CourseTask('b','1','1.2','另一节','video',40,1)
    first=Image.new('RGB',(400,300),'white');second=Image.new('RGB',(400,300),'#dddddd')
    page1=CatalogPage([Heading('1',Rect(0,0,90,30),True)],[CatalogRow(a,Rect(10,50,150,30),None)],{'1.1'},'1')
    page2=CatalogPage([],[CatalogRow(b,Rect(10,50,150,30),False),CatalogRow(c,Rect(10,150,150,30),True)],{'1.1','1.2'},'1')
    scanner=CatalogScanner(Mock(),Mock(),Profile(regions={'catalog':Rect(0,0,400,300)}),'course')
    scanner.top=Mock();scanner.capture=Mock(side_effect=[(first,page1,View([])),(second,page2,View([])),(second,page2,View([])),(second,page2,View([]))])
    result=scanner.scan()
    assert len(result.rows)==2 and result.rows[0].task.label=='长标题的完整文字'
    assert result.rows[0].done is False and result.rows[1].done is True


def test_rescan_recovers_video_mistakenly_marked_done_and_adds_missed_lesson(tmp_path):
    controller=CourseController(Journal(tmp_path/'j.db'),'course')
    one=CourseTask('one','1','1.1','已学','video',30,0)
    two=CourseTask('two','1','1.2','漏课','video',30,1)
    controller.reconcile(CourseSnapshot([CatalogRow(one,Rect(0,0,100,30),True)],{'1'},None,True))
    controller.reconcile(CourseSnapshot([CatalogRow(one,Rect(0,0,100,30),False),CatalogRow(two,Rect(0,40,100,30),False)],{'1'},None,True))
    assert len(controller.tasks)==2 and controller.tasks[0].status=='pending'
    assert controller.choose().task_id=='one'
