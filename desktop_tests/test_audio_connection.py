import json,threading,time
from unittest.mock import Mock
import httpx,pytest
from PIL import Image
from desktop_app.audio import ensure_course_muted
from desktop_app.connection import probe_connection
from desktop_app.domain import Rect,Span,View,Profile,PauseRequired
from desktop_app.windows import Target


@pytest.mark.parametrize('status,message',[(401,'Key'),(402,'余额'),(404,'模型'),(429,'频繁'),(503,'暂时')])
def test_connection_failure_explains_recovery_without_leaking_key(monkeypatch,status,message):
    real=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(status,text='unit-private-value')),**kw))
    result=probe_connection('https://example.com/v1','test-model','unit-private-value',threading.Event())
    assert result['status']=='failed' and message in result['message']
    assert 'unit-private-value' not in json.dumps(result)


def test_connection_validates_real_answer_and_reports_timeout(monkeypatch):
    real=httpx.Client
    def success(r):return httpx.Response(200,json={'choices':[{'message':{'content':'{"answers":["B"]}'}}]})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(success),**kw))
    assert probe_connection('https://example.com/v1','m','test-value',threading.Event())['status']=='passed'
    def timeout(r):raise httpx.ReadTimeout('unit-private-value')
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(timeout),**kw))
    result=probe_connection('https://example.com/v1','m','test-value',threading.Event())
    assert result['status']=='failed' and '超时' in result['message']


def mute_fixture():
    desktop,vision=Mock(),Mock()
    desktop.target=Target(1,'课程学习_智慧树','msedge.exe')
    frame=Image.new('RGB',(1200,900),'white');desktop.capture.return_value=frame
    view=View([],buttons={'course_tab':Rect(400,10,160,30)})
    return desktop,vision,frame,view


def test_already_muted_tab_is_never_unmuted():
    desktop,vision,frame,view=mute_fixture()
    vision.ocr.read.return_value=[Span('取消标签页静音',Rect(480,200,180,30))]
    assert ensure_course_muted(desktop,vision,frame,view)
    assert desktop.click.call_count==1 and desktop.click.call_args.kwargs=={'button':'right'}
    desktop.press.assert_called_once_with('esc')


def test_mute_requires_visible_confirmation():
    desktop,vision,frame,view=mute_fixture()
    vision.ocr.read.side_effect=[[Span('静音标签页 (Ctrl+M)',Rect(480,200,180,30))],[Span('取消标签页静音',Rect(480,200,180,30))]]
    assert ensure_course_muted(desktop,vision,frame,view)
    assert desktop.click.call_count==3
    desktop.press.assert_called_once_with('esc')


def test_unrecognized_mute_menu_closes_and_pauses():
    desktop,vision,frame,view=mute_fixture();vision.ocr.read.return_value=[]
    with pytest.raises(PauseRequired,match='自动静音'):
        ensure_course_muted(desktop,vision,frame,view)
    assert desktop.click.call_count==1
    desktop.press.assert_called_once_with('esc')


@pytest.fixture
def ui(tmp_path,monkeypatch,tk_root):
    import tkinter as tk
    from desktop_app import gui
    monkeypatch.setenv('AUTOVISOR_DESKTOP_DATA',str(tmp_path))
    monkeypatch.setattr(gui,'emergency_monitor',lambda *args:None)
    class LocalCredentials:
        def get(self):return ''
        def set(self,value):pass
    monkeypatch.setattr(gui,'CredentialStore',LocalCredentials)
    root=tk_root;app=gui.App(root)
    app.key_text.set('unit-private-value')
    app.base_text.set('https://example.com/v1')
    app.model_text.set('test-model')
    yield app,root,gui
    app.stop.set();app.shutdown.set();app.ai_test_stop.set()
    for job in root.tk.call('after','info'):root.after_cancel(job)
    for child in root.winfo_children():child.destroy()
    app.journal.close()


@pytest.fixture(scope='module')
def tk_root():
    import tkinter as tk
    root=tk.Tk();root.withdraw()
    yield root
    root.destroy()


def wait_ui(app,root,timeout=3):
    deadline=time.monotonic()+timeout
    while app.ai_test_running and time.monotonic()<deadline:
        root.update();time.sleep(.01)
    root.update()
    assert not app.ai_test_running


def test_ai_button_immediately_shows_progress_and_result_on_settings_page(ui,monkeypatch):
    app,root,gui=ui;release=threading.Event()
    def probe(*args):
        assert threading.current_thread() is not threading.main_thread()
        release.wait(2)
        return {'status':'passed','message':'验证成功：接口可用。','elapsed':.1}
    monkeypatch.setattr(gui,'probe_connection',probe)
    app.ai_test_button.invoke()
    assert app.ai_test_running and '正在测试' in app.ai_status.get()
    assert 'disabled' in app.ai_test_button.state()
    release.set();wait_ui(app,root)
    assert '验证成功' in app.ai_status.get() and '用时' in app.ai_status.get()
    assert app.ai_verified_signature==app.ai_signature()


def test_cancelled_test_ignores_late_success(ui,monkeypatch):
    app,root,gui=ui;release=threading.Event()
    def probe(*args):
        release.wait(2)
        return {'status':'passed','message':'验证成功','elapsed':1}
    monkeypatch.setattr(gui,'probe_connection',probe)
    app.ai_test_button.invoke();app.ai_cancel_button.invoke()
    assert '取消' in app.ai_status.get() and not app.ai_test_running
    release.set();time.sleep(.05);app.drain()
    assert '取消' in app.ai_status.get() and app.ai_verified_signature is None


def test_missing_key_and_changed_settings_never_show_false_success(ui,monkeypatch):
    app,root,gui=ui
    app.key_text.set('');app.ai_test_button.invoke()
    assert not app.ai_test_running and '请填写' in app.ai_status.get()
    app.key_text.set('unit-private-value')
    monkeypatch.setattr(gui,'probe_connection',lambda *a:{'status':'passed','message':'验证成功','elapsed':.1})
    app.ai_test_button.invoke();app.model_text.set('different-model');wait_ui(app,root)
    assert '输入已更改' in app.ai_status.get() and app.ai_verified_signature is None


def test_failure_is_visible_next_to_test_button_and_allows_retry(ui,monkeypatch):
    app,root,gui=ui
    monkeypatch.setattr(gui,'probe_connection',lambda *a:{'status':'failed','message':'HTTP 401：API Key 无效，请重新填写。','elapsed':.2})
    app.ai_test_button.invoke();wait_ui(app,root)
    assert 'Key 无效' in app.ai_status.get()
    assert 'disabled' not in app.ai_test_button.state()
    assert str(app.ai_status_label.cget('foreground'))=='#b91c1c'


def test_mute_preference_is_enabled_by_default_and_saved(ui):
    from desktop_app.storage import load_profile
    app,root,gui=ui
    assert Profile().auto_mute and app.mute_text.get()
    app.mute_text.set(False);assert app.save(False)
    assert load_profile(app.data).auto_mute is False
    assert 'unit-private-value' not in (app.data/'config.ini').read_text('utf8')
