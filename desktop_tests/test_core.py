import json
import threading
from dataclasses import replace
from unittest.mock import Mock
import pytest
from PIL import Image, ImageDraw
from desktop_app.domain import Rect, Span, Question, Option, Answer, Profile, View, PauseRequired, normalized
from desktop_app.storage import Journal, save_profile, load_profile
from desktop_app.vision import Perception, selection_state, controls, reading_order
from desktop_app.runner import Runner
from desktop_app.ai import OnlineAI


def question(kind="single", index=1, total=1):
    return Question(kind, "测试题目", index, total, "chapter", options=[Option("A", "甲", Rect(60,100,90,25),Rect(20,100,24,24),False),Option("B", "乙", Rect(60,160,90,25),Rect(20,160,24,24),False)], blanks=[Rect(20,220,200,40),Rect(20,280,200,40)] if kind=="blank" else [])


@pytest.mark.parametrize("kind,raw,expected", [
 ("single", '{"answers":["B"]}', ["B"]),
 ("multiple", '{"answers":["A","B"]}', ["A","B"]),
 ("judge", '["A"]', ["A"]),
 ("blank", '{"answers":["甲，乙","3.14"]}', ["甲，乙","3.14"]),
 ("blank", '```json\n["北京","2"]\n```', ["北京","2"]),
])
def test_answer_formats(kind,raw,expected):
    assert Answer.parse(raw,question(kind)).values == expected


@pytest.mark.parametrize("kind,raw", [("single",'["A","B"]'),("single",'["C"]'),("multiple",'["A","A"]'),("blank",'["北京"]'),("blank",'["", "2"]'),("blank",'[1,2]'),("blank",'["甲\\n乙","2"]'),("judge",'unstructured text'),("single",'{"answers":[]}')])
def test_invalid_answers_stop(kind,raw):
    with pytest.raises(PauseRequired):
        Answer.parse(raw,question(kind))


def test_journal_completeness_and_durable_submit(tmp_path):
    path=tmp_path/'journal.sqlite3'
    journal=Journal(path)
    journal.begin('chapter:test',2)
    journal.record('chapter:test',1,'q1',Answer(['A']),True)
    assert not journal.complete('chapter:test')
    journal.record('chapter:test',2,'q2',Answer(['B']),False)
    assert not journal.complete('chapter:test')
    journal.record('chapter:test',2,'q2',Answer(['B']),True)
    assert journal.complete('chapter:test')
    assert journal.transition('chapter:test','working','submit_sent')
    assert not journal.transition('chapter:test','working','submit_sent')
    journal.close()
    restored=Journal(path)
    assert restored.state('chapter:test')=='submit_sent'
    assert restored.answer('chapter:test',2,'q2')[1]
    assert restored.answer('chapter:test',2,'different') is None


def test_popup_does_not_replace_chapter_checkpoint(tmp_path):
    journal=Journal(tmp_path/'j.db')
    journal.begin('chapter:one',3)
    journal.begin('popup:two',1)
    assert journal.active()=='chapter:one'


def test_config_roundtrip_has_no_key(tmp_path):
    profile=Profile(width=1000,height=700,regions={'question':Rect(10,10,500,600)})
    save_profile(tmp_path,profile)
    assert load_profile(tmp_path)==profile
    assert 'ApiKey' not in (tmp_path/'config.ini').read_text('utf8')


def test_selected_ring_is_not_selected():
    image=Image.new('RGB',(100,100),'white')
    draw=ImageDraw.Draw(image)
    box=Rect(20,20,25,25)
    draw.ellipse(box.bounds,outline='#2563eb',width=2)
    assert selection_state(image,box) is False
    draw.ellipse(box.bounds,fill='#2563eb')
    assert selection_state(image,box) is True
    assert selection_state(image,None) is None


def test_reading_order_with_small_vertical_offsets():
    assert reading_order([Rect(300,110,100,40),Rect(10,100,100,40),Rect(5,220,100,40)]) == [Rect(10,100,100,40),Rect(300,110,100,40),Rect(5,220,100,40)]


def test_input_contours_deduplicate_and_preserve_two_blanks():
    image=Image.new('RGB',(600,350),'white')
    draw=ImageDraw.Draw(image)
    draw.rectangle((20,80,300,122),outline='#555555',width=2)
    draw.rectangle((20,160,300,202),outline='#555555',width=2)
    found=controls(image,Rect(0,0,600,350),65,580)
    assert len(found)==2


def test_radio_controls_are_detected():
    image=Image.new('RGB',(200,100),'white')
    draw=ImageDraw.Draw(image)
    draw.ellipse((20,20,44,44),outline='#64748b',width=2)
    found=controls(image,Rect(0,0,200,100),14,45,max_height=45)
    assert len(found)==1


def test_detection_blocks_before_actions():
    vision=Perception(Mock())
    frame=Image.new('RGB',(800,600),'white')
    view=vision.inspect(frame,Profile(),[Span('检测到异常脚本',Rect(20,20,200,30))])
    assert view.blocked and not view.question


def runner_fixture(tmp_path):
    desktop=Mock()
    frame=Image.new('RGB',(800,600),'white')
    desktop.capture.return_value=frame
    vision=Mock()
    journal=Journal(tmp_path/'j.db')
    events=[]
    runner=Runner(desktop,vision,Mock(),journal,Profile(),threading.Event(),threading.Event(),lambda *args:events.append(args))
    return runner,desktop,vision,journal,frame


def test_missing_question_prevents_submit(tmp_path):
    runner,desktop,vision,journal,frame=runner_fixture(tmp_path)
    q=question(index=2,total=2)
    view=View([],q,{'submit':Rect(10,400,100,30)})
    runner.quiz='chapter:partial'
    runner.apply=Mock()
    runner.verify_identity=Mock(return_value=(frame,view))
    runner.ai.solve.return_value=Answer(['A'])
    with pytest.raises(PauseRequired,match='漏题'):
        runner.step(frame,view)
    desktop.click.assert_not_called()


def test_submit_once_confirm_once_then_result(tmp_path):
    runner,desktop,vision,journal,frame=runner_fixture(tmp_path)
    q=question()
    view=View([],q,{'submit':Rect(20,400,80,30)})
    runner.apply=Mock()
    runner.verify_identity=Mock(return_value=(frame,view))
    runner.ai.solve.return_value=Answer(['B'])
    runner.step(frame,view)
    quiz=runner.quiz
    assert journal.state(quiz)=='submit_sent'
    runner.step(frame,view)
    assert desktop.click.call_count==1
    confirm=View([],buttons={'confirm':Rect(30,300,80,30)},confirmation=True)
    runner.step(frame,confirm)
    runner.step(frame,confirm)
    assert desktop.click.call_count==2
    assert journal.state(quiz)=='confirm_sent'
    success=View([],buttons={'return':Rect(50,500,80,30)},submitted=True)
    runner.step(frame,success)
    assert journal.state(quiz)=='success'
    assert runner.finished_jobs==1


def test_unknown_submit_status_never_retries(tmp_path,monkeypatch):
    runner,desktop,vision,journal,frame=runner_fixture(tmp_path)
    journal.begin('chapter:x',1)
    journal.transition('chapter:x','working','submit_sent')
    runner.quiz='chapter:x'
    runner.submit_deadline=0
    with pytest.raises(PauseRequired,match='不会重复'):
        runner.step(frame,View([]))
    desktop.click.assert_not_called()


def test_unfinished_confirmation_is_not_confirmed(tmp_path):
    runner,desktop,vision,journal,frame=runner_fixture(tmp_path)
    journal.begin('chapter:x',1)
    journal.transition('chapter:x','working','submit_sent')
    runner.quiz='chapter:x'
    with pytest.raises(PauseRequired,match='未完成'):
        runner.step(frame,View([],confirmation=True,unfinished=True,buttons={'confirm':Rect(10,10,80,30)}))
    desktop.click.assert_not_called()


def test_ai_error_does_not_leak_provider_body(monkeypatch):
    import httpx
    real_client=httpx.Client
    def respond(request):
        return httpx.Response(401,text='secret-test-value')
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(respond),**kwargs))
    ai=OnlineAI('https://example.com/v1','model','secret-test-value',threading.Event())
    with pytest.raises(PauseRequired) as error:
        ai.solve(question())
    assert 'secret-test-value' not in str(error.value)


def test_ai_real_json_protocol_with_mock_transport(monkeypatch):
    import httpx
    real_client=httpx.Client
    def respond(request):
        assert request.url.path=='/v1/chat/completions'
        payload=json.loads(request.content)
        assert payload['model']=='configured-model'
        assert json.loads(payload['messages'][1]['content'])['blank_count']==2
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"answers":["北京","2"],"explanation":"test"}'}}]})
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(respond),**kwargs))
    ai=OnlineAI('https://example.com/v1','configured-model','test-key',threading.Event())
    assert ai.solve(question('blank')).values==['北京','2']


def test_native_unicode_encodes_surrogate_pairs():
    from desktop_app.windows import unicode_events, INPUT
    import ctypes
    events=unicode_events('北京2😀')
    assert len(events)==10
    assert events[0].ki.wScan==ord('北')
    assert events[1].ki.dwFlags==6
    assert ctypes.sizeof(INPUT)==40


def test_wheel_uses_native_120_unit_notches():
    from desktop_app.windows import wheel_event
    event=wheel_event(-2)
    assert event.type==0
    assert event.mi.dwFlags==0x0800
    assert event.mi.mouseData==(-240)&0xffffffff


def test_blank_fingerprint_ignores_filled_text_with_ocr_padding():
    vision=Perception(Mock())
    image=Image.new('RGB',(600,350),'white')
    ImageDraw.Draw(image).rectangle((25,150,400,195),outline='#555555',width=2)
    profile=Profile(regions={'question':Rect(0,0,600,350)})
    common=[Span('第1题 共1题',Rect(10,10,180,25)),Span('填空题',Rect(10,45,80,25)),Span('请填写城市名称',Rect(10,90,230,25))]
    before=vision.inspect(image,profile,common).question
    after=vision.inspect(image,profile,common+[Span('北京',Rect(22,155,65,32))]).question
    assert len(before.blanks)==1
    assert before.key==after.key


def test_scroll_shift_tracks_pixels_both_directions():
    from desktop_app.document import vertical_shift
    import numpy as np
    rng=np.random.default_rng(1234)
    full=Image.fromarray(rng.integers(0,256,(1200,400,3),dtype=np.uint8))
    first=full.crop((0,0,400,600))
    second=full.crop((0,100,400,700))
    assert vertical_shift(first,second)==100
    assert vertical_shift(second,first)==-100


def test_deepseek_current_model_and_structured_payload(monkeypatch):
    import httpx
    real_client=httpx.Client
    def respond(request):
        assert str(request.url)=='https://api.deepseek.com/chat/completions'
        payload=json.loads(request.content)
        assert payload['model']=='deepseek-flash'
        assert payload['response_format']=={'type':'json_object'}
        assert payload['thinking']=={'type':'disabled'}
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"answers":["B"]}'}}]})
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(respond),**kwargs))
    profile=Profile()
    assert OnlineAI(profile.base_url,profile.model,'test-key',threading.Event()).solve(question()).values==['B']


@pytest.mark.parametrize('address',['http://localhost.evil.example/v1','http://127.0.0.1.evil.example/v1','file:///tmp/test'])
def test_insecure_or_misleading_loopback_url_is_rejected(address):
    with pytest.raises(PauseRequired):
        OnlineAI(address,'model','test-key',threading.Event())


def test_popup_disappearance_requires_two_video_observations(tmp_path):
    runner,desktop,vision,journal,frame=runner_fixture(tmp_path)
    journal.begin('popup:x',1)
    journal.transition('popup:x','working','submit_sent')
    runner.quiz='popup:x'
    runner.step(frame,View([],video_visible=True))
    assert journal.state('popup:x')=='submit_sent'
    runner.step(frame,View([],video_visible=True))
    assert journal.state('popup:x')=='success'
    desktop.click.assert_not_called()


def test_ocr_radio_ring_prefix_does_not_hide_option():
    vision=Perception(Mock())
    image=Image.new('RGB',(600,350),'white')
    draw=ImageDraw.Draw(image)
    draw.ellipse((30,130,54,154),outline='#64748b',width=2)
    draw.ellipse((30,190,54,214),outline='#64748b',width=2)
    common=[Span('第1题 共1题',Rect(10,10,180,25)),Span('单选题',Rect(10,45,80,25)),Span('请选择',Rect(10,90,100,25))]
    spans=common+[Span('O A. 甲',Rect(30,125,180,35)),Span('OB.乙',Rect(30,185,180,35))]
    q=vision.inspect(image,Profile(regions={'question':Rect(0,0,600,350)}),spans).question
    assert [o.label for o in q.options]==['A','B']
    assert all(o.control for o in q.options)


def test_video_resume_and_next_unfinished_lesson(tmp_path):
    runner,desktop,vision,journal,frame=runner_fixture(tmp_path)
    runner.profile.regions={'player':Rect(0,0,600,450),'catalog':Rect(610,0,190,600)}
    play=Rect(10,400,30,30)
    runner.step(frame,View([],buttons={'play':play},video_paused=True,video_visible=True))
    desktop.click.assert_called_once_with(play,frame)
    desktop.click.reset_mock()
    next_lesson=Rect(650,150,100,25)
    vision.catalog_rows.return_value=[('one',Rect(650,50,100,25),True),('two',next_lesson,False)]
    runner.step(frame,View([],video_finished=True,video_visible=True))
    desktop.click.assert_called_once_with(next_lesson,frame)


def test_completed_lesson_checks_pending_chapter_test(tmp_path):
    runner,desktop,vision,journal,frame=runner_fixture(tmp_path)
    runner.profile.regions={'catalog':Rect(610,0,190,600)}
    vision.catalog_rows.return_value=[('one',Rect(650,50,100,25),True)]
    test_button=Rect(650,150,100,25)
    vision.catalog_tests.return_value=[(test_button,False)]
    runner.step(frame,View([],video_finished=True,video_visible=True))
    desktop.click.assert_called_once_with(test_button,frame)


def test_windows_credential_roundtrip_isolated_namespace():
    from desktop_app.storage import CredentialStore
    import uuid
    store=CredentialStore()
    store.service='AutovisorDesktop.UnitTest.'+uuid.uuid4().hex
    try:
        store.set('unit-test-not-an-api-key')
        assert store.get()=='unit-test-not-an-api-key'
    finally:
        store.set('')
    assert store.get()==''


@pytest.mark.parametrize('artifact',['1','A'])
def test_selected_checkbox_ocr_digit_is_not_part_of_question(artifact):
    vision=Perception(Mock())
    image=Image.new('RGB',(600,350),'white')
    draw=ImageDraw.Draw(image)
    for y in (130,190):
        draw.rectangle((30,y,54,y+24),outline='#64748b',width=2)
    profile=Profile(regions={'question':Rect(0,0,600,350)})
    spans=[Span('第1题 共1题',Rect(10,10,180,25)),Span('多选题',Rect(10,45,80,25)),Span('请选择',Rect(10,90,100,25)),Span('A.甲',Rect(80,125,150,35)),Span('B.乙',Rect(80,185,150,35))]
    before=vision.inspect(image,profile,spans).question
    after=vision.inspect(image,profile,spans+[Span(artifact,Rect(30,190,24,24))]).question
    assert before.key==after.key
    assert [o.text for o in before.options]==[o.text for o in after.options]


def test_platform_bracket_heading_and_total_metadata():
    vision=Perception(Mock())
    image=Image.new('RGB',(600,350),'white')
    spans=[Span('总题数：5',Rect(10,10,180,25)),Span('1【单选题】',Rect(10,45,160,25)),Span('请选择',Rect(10,90,100,25))]
    q=vision.inspect(image,Profile(regions={'question':Rect(0,0,600,350)}),spans).question
    assert q.index==1 and q.total==5


def test_cursor_decoration_outside_option_column_does_not_change_identity():
    vision=Perception(Mock())
    image=Image.new('RGB',(700,350),'white')
    draw=ImageDraw.Draw(image)
    for y in (130,190):
        draw.rectangle((30,y,54,y+24),outline='#64748b',width=2)
    profile=Profile(regions={'question':Rect(0,0,700,350)})
    spans=[Span('第1题 共1题',Rect(10,10,180,25)),Span('多选题',Rect(10,45,80,25)),Span('请选择',Rect(10,90,100,25)),Span('A.甲',Rect(80,125,150,35)),Span('B.乙',Rect(80,185,150,35))]
    before=vision.inspect(image,profile,spans).question
    after=vision.inspect(image,profile,spans+[Span('1',Rect(500,225,28,28))]).question
    assert before.key==after.key
    assert [o.text for o in before.options]==[o.text for o in after.options]


@pytest.mark.parametrize('title,process',[('其他网站的表单','msedge.exe'),('Autovisor 流程演示','msedge.exe'),('记事本','notepad.exe')])
def test_unrelated_pages_and_false_demo_titles_are_rejected(title,process):
    from desktop_app.windows import Target,validate_target_title
    with pytest.raises(PauseRequired):
        validate_target_title(Target(1,title,process),title)


def test_all_letter_labels_and_multiple_answer_sets():
    q=question('multiple')
    q.options=[Option(chr(65+i),'选项'+chr(65+i),Rect(60,100+i*45,100,25),Rect(20,100+i*45,24,24),False) for i in range(26)]
    assert Answer.parse('{"answers":["A","J","Z"]}',q).values==['A','J','Z']


def test_labels_i_to_z_are_recognized_without_a_to_h_cap():
    vision=Perception(Mock())
    image=Image.new('RGB',(600,1450),'white');draw=ImageDraw.Draw(image)
    spans=[Span('第1题 共1题',Rect(10,10,180,25)),Span('多选题',Rect(10,45,80,25)),Span('选择全部正确项',Rect(10,90,230,25))]
    for i in range(26):
        y=130+i*45
        draw.rectangle((30,y,54,y+24),outline='#64748b',width=2)
        spans.append(Span(chr(65+i)+'. 选项'+chr(65+i),Rect(80,y-5,200,35)))
    q=vision.inspect(image,Profile(regions={'question':Rect(0,0,600,1450)}),spans).question
    assert [o.label for o in q.options]==list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    assert q.option_rows_complete


def test_unrecognized_trailing_checkbox_causes_pause(tmp_path):
    vision=Perception(Mock())
    image=Image.new('RGB',(600,400),'white');draw=ImageDraw.Draw(image)
    spans=[Span('第1题 共1题',Rect(10,10,180,25)),Span('多选题',Rect(10,45,80,25)),Span('请选择',Rect(10,90,230,25))]
    for i in range(3):
        y=130+i*60
        draw.rectangle((30,y,54,y+24),outline='#64748b',width=2)
        spans.append(Span(chr(65+i)+'. 选项' if i<2 else '模糊内容',Rect(80,y-5,200,35)))
    q=vision.inspect(image,Profile(regions={'question':Rect(0,0,600,400)}),spans).question
    assert not q.option_rows_complete
    runner,*_=runner_fixture(tmp_path)
    with pytest.raises(PauseRequired,match='识别不完整'):
        runner.validate_question(q)


@pytest.mark.parametrize('wanted,expected_clicks',[(['A','B'],1),(['B'],2)])
def test_multiple_choice_preserves_selection_and_resume_is_idempotent(tmp_path,wanted,expected_clicks):
    runner,desktop,vision,journal,frame=runner_fixture(tmp_path)
    q=question('multiple')
    vision.inspect.return_value=View([],q)
    draw=ImageDraw.Draw(frame)
    draw.rectangle(q.options[0].control.bounds,fill='#2563eb')
    def toggle(box,previous):
        color='white' if selection_state(frame,box) else '#2563eb'
        draw.rectangle(box.bounds,fill=color)
    desktop.click.side_effect=toggle
    runner.apply(q,Answer(wanted))
    assert desktop.click.call_count==expected_clicks
    runner.apply(q,Answer(wanted))
    assert desktop.click.call_count==expected_clicks
    assert [o.label for o in q.options if selection_state(frame,o.control)]==wanted
