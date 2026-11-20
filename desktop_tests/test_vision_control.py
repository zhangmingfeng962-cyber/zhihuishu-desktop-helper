import copy,json,threading
from unittest.mock import Mock
import httpx,pytest
from PIL import Image,ImageDraw
from desktop_app.domain import Profile,Rect,CourseTask,Answer,PauseRequired,Stopped
from desktop_app.storage import Journal
from desktop_app.vision_ai import Observation,VisionAI,Box
from desktop_app.vision_control import VisionCourseRunner,VisualWatch,VisionExecutor


def payload(scene='course',**changes):
    value={'frame_id':'frame','scene':scene,'course_name':'测试课程','chapter':'1','lesson_code':'1.1','message':'test',
           'player':{'state':'playing','position':10,'duration':30,'muted':True},'regions':{'player':[0,200,700,900],'catalog':[750,200,1000,950]},
           'catalog':{'at_top':False,'at_bottom':False,'chapters':[],'rows':[]},'question':None,'actions':[]}
    value.update(changes);return value


def question(selected=False):
    return {'kind':'single','stem':'1+1等于几？','index':1,'total':1,'complete':True,'option_count':2,'blank_count':0,
            'options':[{'label':'A','text':'1','selected':False,'box':[100,300,150,350]},
                       {'label':'B','text':'2','selected':selected,'box':[100,400,150,450]}],
            'blanks':[],'answers':['B'],'explanation':'test'}


def observation(data):return Observation.parse(json.dumps(data,ensure_ascii=False),data['frame_id'])


def runner(tmp_path,data=None,profile=None):
    desktop=Mock();image=Image.new('RGB',(1000,800),'white');desktop.capture.return_value=image
    ai=Mock();queue=[data or payload()]
    def observe(image,context,frame_id):
        value=copy.deepcopy(queue.pop(0) if len(queue)>1 else queue[0]);value['frame_id']=frame_id
        return observation(value)
    ai.observe.side_effect=observe
    journal=Journal(tmp_path/'j.db')
    profile=profile or Profile(task_name='测试课程',auto_mute=False)
    instance=VisionCourseRunner(desktop,ai,journal,profile,threading.Event(),threading.Event(),Mock())
    return instance,desktop,ai,journal,image,queue


@pytest.mark.parametrize('value',[[0,0,1001,100],[100,50,20,100],[True,0,100,100],[0,0,0,100],['0',0,100,100]])
def test_invalid_visual_boxes_are_never_executed(value):
    with pytest.raises(PauseRequired):Box.parse(value)


def test_normalized_coordinates_scale_to_original_dpi_and_singleton_metadata():
    assert Box.parse([500,250,750,500]).pixels((2560,1440))==Rect(1280,360,640,360)
    assert Box.parse([[100,100,200,200]]).pixels((1000,800))==Rect(100,80,100,80)


def test_pixel_contract_is_normalized_without_guessing_units():
    data=payload(actions=[{'kind':'hover','intent':'inspect','box':[1000,200,1500,400]}],regions={'player':[0,100,2000,900]})
    data.update(coordinate_space='pixels',image_size=[2000,1000])
    obs=Observation.parse(json.dumps(data),'frame',(2000,1000))
    assert obs.actions[0].box.json()==[500,200,750,400]
    with pytest.raises(PauseRequired):Observation.parse(json.dumps(data),'frame',(1000,800))
    data.pop('coordinate_space')
    with pytest.raises(PauseRequired):Observation.parse(json.dumps(data),'frame',(2000,1000))


def test_detail_coordinates_map_only_through_the_current_screenshot_view():
    data=payload(actions=[{'kind':'click','intent':'mute','box_reference':'player_right','box':[200,100,260,160]}],regions={})
    data.update(coordinate_space='pixels',image_size=[2000,1000])
    views={'player_right':{'bounds':[1200,800,1800,1000],'size':[1200,400]}}
    result=Observation.parse(json.dumps(data),'frame',(2000,1000),views)
    assert result.actions[0].box.pixels((2000,1000))==Rect(1300,850,30,30)
    with pytest.raises(PauseRequired,match='不存在'):Observation.parse(json.dumps(data),'frame',(2000,1000))
    data['actions'][0]['box']=[200,100,1300,160]
    with pytest.raises(PauseRequired,match='超出'):Observation.parse(json.dumps(data),'frame',(2000,1000),views)
    data['actions'][0].update(intent='submit',box=[200,100,260,160])
    with pytest.raises(PauseRequired,match='仅用于播放器'):Observation.parse(json.dumps(data),'frame',(2000,1000),views)


def test_null_quiz_duration_and_inferred_quiz_number_do_not_change_identity():
    item={'kind':'quiz','chapter':'1','code':'1.3','label':'章节测试','duration':None,'done':False,'selected':False,'box':[750,200,990,260]}
    result=observation(payload(catalog={'rows':[item]})).catalog['rows'][0]
    assert result['code']=='' and result['duration']==0


@pytest.mark.parametrize('duration',[None,0,'00:09:24'])
def test_group_titles_and_unparsed_durations_are_not_video_tasks(duration):
    item={'kind':'video','chapter':'3','code':'3.2','label':'职业世界地图','duration':duration,'done':None,'selected':False,'box':[750,200,990,260]}
    with pytest.raises(PauseRequired,match='分组标题'):
        observation(payload(catalog={'rows':[item]}))


def test_unseen_player_and_region_control_lists_are_safe_optional_metadata():
    data=payload('chapter_question',player={'state':None,'muted':None},regions={'question':[[100,200,300,240],[100,300,300,340]]})
    data.update(coordinate_space='pixels',image_size=[1000,800])
    result=Observation.parse(json.dumps(data),'frame',(1000,800))
    assert result.player['state']=='unknown' and result.regions['question'].json()==[100,250,300,425]


def test_hidden_controls_cannot_claim_video_is_playing():
    result=observation(payload(player={'state':'playing','position':None,'duration':None,'muted':None}))
    assert result.player['state']=='unknown'


def test_wrong_frame_and_unsupported_actions_are_rejected():
    with pytest.raises(PauseRequired):Observation.parse(json.dumps(payload()),'new-frame')
    for action in [{'kind':'shell','intent':'inspect','value':'anything'},
                   {'kind':'key','intent':'inspect','value':'ctrl+l'},
                   {'kind':'input','intent':'inspect','box':[0,0,100,100],'value':'anything'}]:
        with pytest.raises(PauseRequired):observation(payload(actions=[action]))


def test_navigations_cannot_hide_inside_selection_batches():
    actions=[{'kind':'click','intent':'submit','box':[0,0,100,100]}, {'kind':'click','intent':'select_option','label':'A','desired':True,'box':[0,0,100,100]}]
    result=observation(payload(actions=actions))
    assert len(result.actions)==1 and result.actions[0].intent=='submit'


@pytest.mark.parametrize('zoom,images',[(None,1),([600,100,800,500],2),([-10,100,800,500],1)])
def test_visual_http_contains_inline_image_and_frame_identity(monkeypatch,zoom,images):
    real=httpx.Client
    def respond(request):
        data=json.loads(request.content)
        blocks=data['messages'][1]['content'];context=json.loads(blocks[0]['text'])
        assert blocks[1]['type']=='image_url' and blocks[1]['image_url']['url'].startswith('data:image/jpeg;base64,')
        assert sum(b['type']=='image_url' for b in blocks)==images
        assert context['image_size']==[800,500]
        assert 'catalog_zoom_native' not in context['context']
        assert data['thinking']=={'type':'disabled'}
        value=payload();value['frame_id']=context['frame_id']
        value['coordinate_space']='pixels';value['image_size']=context['image_size'];value['regions']={}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(value)}}],'usage':{'total_tokens':99}})
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real(transport=httpx.MockTransport(respond),**kwargs))
    ai=VisionAI('https://api.deepseek.com/chat/completions','deepseek-flash','unit-key',threading.Event())
    try:
        result=ai.observe(Image.new('RGB',(800,500),'white'),{'goal':'test','catalog_zoom_native':zoom},'specific-frame')
        assert result.frame_id=='specific-frame' and ai.tokens==99 and ai.calls==1
    finally:ai.close()


@pytest.mark.parametrize('phase',['scan','watch','resume','navigate'])
def test_only_validated_inventory_phases_ignore_unrelated_catalog_rows(monkeypatch,phase):
    real=httpx.Client
    def respond(request):
        meta=json.loads(json.loads(request.content)['messages'][1]['content'][0]['text'])
        item=row('1.1')
        unrelated={'kind':'quiz','chapter':'2','code':'','label':'章节测试','duration':0,'done':True,'selected':False,'box':[750,300,990,360]}
        value=payload(catalog={'rows':[item,unrelated,copy.deepcopy(unrelated)]},regions={})
        value.update(frame_id=meta['frame_id'],coordinate_space='pixels',image_size=meta['image_size'])
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(value)}}]})
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real(transport=httpx.MockTransport(respond),**kwargs))
    ai=VisionAI('https://api.deepseek.com/chat/completions','deepseek-flash','unit-key',threading.Event())
    context={'phase':phase,'active_task':{'kind':'video','chapter':'1','code':'1.1'}}
    try:
        if phase=='scan':
            with pytest.raises(PauseRequired,match='重复资源'):ai.observe(Image.new('RGB',(1000,800)),context)
        else:
            result=ai.observe(Image.new('RGB',(1000,800)),context)
            assert [r['code'] for r in result.catalog['rows']]==['1.1']
    finally:ai.close()


def test_new_mode_does_not_require_calibration_and_preserves_legacy_records(tmp_path):
    instance,desktop,ai,journal,image,queue=runner(tmp_path)
    journal.begin('chapter:old',1);journal.record('chapter:old',1,'key',Answer(['A']),True)
    assert instance.profile.regions=={} and journal.complete('chapter:old')
    desktop.fast_inputs.assert_called_once()
    assert Profile.from_dict({'task_name':'旧配置'}).control_mode=='vision'


def test_stop_or_stale_reply_cannot_dispatch_clicks(tmp_path):
    value=payload(actions=[{'kind':'hover','intent':'inspect','box':[100,200,200,300]}])
    instance,desktop,ai,*_=runner(tmp_path,value)
    ai.observe.side_effect=lambda *args:observation(value)
    with pytest.raises(PauseRequired,match='旧截图'):instance.step()
    desktop.click.assert_not_called()
    def late(*args):
        instance.stop.set();return observation(value)
    ai.observe.side_effect=late
    with pytest.raises(Stopped):instance.step()
    desktop.visual_hover.assert_not_called()


def test_wrong_course_and_blocked_scene_do_not_click(tmp_path):
    instance,desktop,ai,journal,image,queue=runner(tmp_path,payload(scene='blocked',message='人机验证'))
    with pytest.raises(PauseRequired):instance.step()
    queue[0]=payload(course_name='另一个课程')
    with pytest.raises(PauseRequired):instance.step()
    desktop.click.assert_not_called()


def test_existing_selection_is_skipped_and_submit_only_happens_once(tmp_path):
    value=payload('chapter_question',question=question(True),actions=[{'kind':'click','intent':'submit','box':[400,700,600,760]}])
    profile=Profile(task_name='测试课程',mode='quiz',auto_mute=False)
    instance,desktop,ai,journal,image,queue=runner(tmp_path,value,profile)
    instance.step()
    quiz=instance.state['quiz_id']
    assert journal.complete(quiz) and journal.state(quiz)=='submit_sent'
    desktop.click.assert_called_once()
    instance.force=True;instance.step()
    assert desktop.click.call_count==1


def test_partial_question_or_missed_option_prevents_filling(tmp_path):
    q=question(False);q['complete']=False
    value=payload('popup_question',question=q,actions=[{'kind':'click','intent':'select_option','label':'B','desired':True,'box':[100,400,150,450]}])
    instance,desktop,ai,journal,image,queue=runner(tmp_path,value)
    instance.step()
    assert instance.force and '完整识别' in instance.state['last_error']
    q=question(False);q['options'][1]['label']='C'
    queue[0]=payload('popup_question',question=q)
    instance.force=True
    with pytest.raises(PauseRequired):instance.step()
    desktop.click.assert_not_called()


def test_multiple_choice_batch_is_verified_on_next_image(tmp_path):
    q=question(False);q['kind']='multiple';q['answers']=['A','B']
    first=payload('popup_question',question=q,actions=[{'kind':'click','intent':'select_option','label':label,'desired':True,'box':box} for label,box in [('A',[100,300,150,350]),('B',[100,400,150,450])]])
    instance,desktop,ai,journal,image,queue=runner(tmp_path,first)
    instance.step();quiz=instance.state['quiz_id']
    assert desktop.click.call_count==2 and not journal.complete(quiz)
    q2=copy.deepcopy(q)
    for o in q2['options']:o['selected']=True
    queue[0]=payload('popup_question',question=q2,actions=[{'kind':'click','intent':'submit','box':[400,700,600,760]}])
    instance.step()
    assert journal.complete(quiz) and journal.state(quiz)=='submit_sent'
    assert desktop.click.call_count==3


def test_omitted_desired_is_derived_from_the_ai_answer_not_toggled_blindly(tmp_path):
    action={'kind':'click','intent':'select_option','label':'B','box':[100,400,150,450]}
    instance,desktop,ai,journal,image,queue=runner(tmp_path,payload('popup_question',question=question(),actions=[action]))
    instance.step();assert desktop.click.call_count==1
    q=question(True);queue[0]=payload('popup_question',question=q,actions=[action]);instance.step()
    assert desktop.click.call_count==1


def test_stable_selection_prefix_is_batched_before_navigation():
    actions=[{'kind':'click','intent':'select_option','label':label,'desired':True,'box':[100,200,150,250]} for label in 'ABCD']
    actions.append({'kind':'click','intent':'submit','box':[300,400,500,450]})
    parsed=observation(payload(actions=actions)).actions
    assert len(parsed)==4 and all(a.intent=='select_option' for a in parsed)


def test_changed_target_before_submit_is_reobserved_without_duplicate_submit(tmp_path):
    from desktop_app.domain import FrameChanged
    data=payload('chapter_question',question=question(True),actions=[{'kind':'click','intent':'submit','box':[400,700,600,760]}])
    instance,desktop,ai,journal,image,queue=runner(tmp_path,data,Profile(task_name='测试课程',mode='quiz',auto_mute=False))
    desktop.click.side_effect=FrameChanged('target changed before click')
    instance.step();quiz=instance.state['quiz_id']
    assert journal.state(quiz)=='working' and instance.force


def test_unknown_error_after_preparing_submit_does_not_retry(tmp_path):
    data=payload('chapter_question',question=question(True),actions=[{'kind':'click','intent':'submit','box':[400,700,600,760]}])
    instance,desktop,ai,journal,image,queue=runner(tmp_path,data,Profile(task_name='测试课程',mode='quiz',auto_mute=False))
    desktop.click.side_effect=Stopped()
    with pytest.raises(Stopped):instance.step()
    assert journal.state(instance.state['quiz_id'])=='submit_sent'


def test_fill_order_and_existing_different_text_are_guarded(tmp_path):
    q={'kind':'blank','stem':'填写两空','index':1,'total':1,'complete':True,'option_count':0,'blank_count':2,'options':[],
       'blanks':[{'index':1,'value':'北京','box':[100,300,500,350]},{'index':2,'value':'','box':[100,400,500,450]}],'answers':['北京','2'],'explanation':''}
    actions=[{'kind':'input','intent':'fill_blank','index':1,'value':'北京','box':[100,300,500,350]},
             {'kind':'input','intent':'fill_blank','index':2,'value':'2','box':[100,400,500,450]}]
    instance,desktop,ai,journal,image,queue=runner(tmp_path,payload('popup_question',question=q,actions=actions))
    instance.step()
    desktop.replace_text.assert_called_once_with(Rect(100,320,400,40),'2',image,fast=True)


def test_popup_auto_grade_can_return_without_an_extra_submit(tmp_path):
    instance,desktop,ai,journal,image,queue=runner(tmp_path,payload('popup_question',question=question(True),actions=[{'kind':'click','intent':'submit','box':[400,700,600,760]}]))
    instance.step();quiz=instance.state['quiz_id']
    queue[0]=payload();instance.step()
    assert journal.state(quiz)=='success' and instance.state['question'] is None
    assert desktop.click.call_count==1


def row(code,done=False):
    return {'kind':'video','chapter':'1','code':code,'label':'同名课时','duration':30,'done':done,'selected':False,'box':[750,200,990,260]}


def test_complete_inventory_is_merged_and_final_scan_is_required(tmp_path):
    instance,desktop,ai,journal,image,queue=runner(tmp_path)
    instance.last_frame=image
    instance.scan_top=True;instance.state['scroll_feedback']={'intent':'scroll_catalog','amount':-3,'unchanged_count':2}
    data=payload(catalog={'at_top':True,'at_bottom':True,'chapters':[{'chapter':'1','expanded':True,'box':[750,100,990,150]}],'rows':[row('1.1',True),row('1.2',True)]})
    obs=observation(data)
    assert instance.scan(obs) and not instance.controller.tasks
    instance.scan(obs)
    assert len(instance.controller.tasks)==2 and not instance.complete
    instance.reset_scan(True);instance.scan_top=True
    instance.state['scroll_feedback']={'intent':'scroll_catalog','amount':-3,'unchanged_count':2}
    instance.scan(obs);instance.scan(obs)
    assert instance.complete and instance.stop.is_set()


def test_catalog_number_gap_and_partial_percentage_cannot_claim_complete(tmp_path):
    instance,desktop,ai,journal,image,queue=runner(tmp_path);instance.last_frame=image
    instance.scan_top=True;instance.state['scroll_feedback']={'intent':'scroll_catalog','amount':-3,'unchanged_count':2}
    data=payload(catalog={'at_top':True,'at_bottom':True,'chapters':[{'chapter':'1','expanded':True,'box':[750,100,990,150]}],'rows':[row('1.1',True),row('1.3',True)]})
    obs=observation(data);instance.scan(obs)
    with pytest.raises(PauseRequired,match='1.2'):instance.scan(obs)
    r=row('1.1',True);r['progress']=52
    assert observation(payload(catalog={'rows':[r]})).catalog['rows'][0]['done'] is False


def test_different_visible_duration_prevents_opening_a_clipped_wrong_lesson(tmp_path):
    instance,desktop,ai,journal,image,queue=runner(tmp_path)
    task=instance.row_task(row('1.1'));task.duration=35
    instance.controller.tasks=[task];instance.controller.activate(task);instance.state['phase']='watch'
    data=payload(lesson_code='',player={'state':'paused','position':0,'duration':6,'muted':True},catalog={'rows':[row('1.1')]},actions=[{'kind':'click','intent':'open_lesson','box':[750,200,990,260]}])
    queue[0]=data;instance.step()
    assert '时长不匹配' in instance.state['last_error']
    desktop.click.assert_not_called()


def test_current_pending_lesson_can_be_adopted_but_completed_lesson_cannot(tmp_path):
    instance,desktop,ai,journal,image,queue=runner(tmp_path)
    first=CourseTask('a','1','1.1','同名课时','video',35,0)
    second=CourseTask('b','1','1.2','同名课时','video',30,1)
    instance.controller.tasks=[first,second];instance.controller.activate(first);instance.state['phase']='watch'
    current=row('1.2');current['selected']=True
    data=payload(lesson_code='1.2',catalog={'rows':[current]})
    instance.advance_state(observation(data))
    assert instance.controller.active is second and first.status=='pending'
    instance.controller.activate(first);second.status='done'
    data=payload('popup_question',lesson_code='1.2',question=question(),catalog={'rows':[current]})
    with pytest.raises(PauseRequired):instance.advance_state(observation(data))


def test_manual_regions_are_persisted_and_limit_visual_actions(tmp_path):
    from desktop_app.storage import save_profile,load_profile
    profile=Profile(task_name='测试课程',auto_mute=False,vision_region_overrides={'popup':Rect(500,100,400,500)})
    save_profile(tmp_path,profile)
    assert load_profile(tmp_path).vision_region_overrides==profile.vision_region_overrides
    data=payload('popup_question',question=question(),actions=[{'kind':'click','intent':'select_option','label':'B','desired':True,'box':[100,400,150,450]}])
    instance,desktop,ai,journal,image,queue=runner(tmp_path,data,profile)
    instance.step();assert '人工修正' in instance.state['last_error']
    desktop.click.assert_not_called()


def test_static_playback_reuses_state_until_heartbeat(tmp_path):
    instance,desktop,ai,journal,image,queue=runner(tmp_path)
    task=CourseTask('a','1','1.1','课时','video',30,0)
    instance.controller.tasks=[task];instance.controller.activate(task);instance.state['phase']='watch'
    instance.step();instance.step()
    assert ai.observe.call_count==1
    instance.watch.checked-=31;instance.step()
    assert ai.observe.call_count==2


def test_scroll_boundaries_require_physical_feedback_not_just_ai_flags(tmp_path):
    instance,desktop,ai,journal,image,queue=runner(tmp_path);instance.last_frame=image
    data=payload(catalog={'at_top':True,'at_bottom':True,'chapters':[{'chapter':'1','expanded':True,'box':[750,100,990,150]}],'rows':[row('1.1',True)]})
    obs=observation(data)
    assert not instance.scan(obs) and not instance.scan_top and not instance.scan_rows
    instance.state['scroll_feedback']={'intent':'scroll_catalog','amount':3,'unchanged_count':2}
    instance.scan(obs)
    assert instance.scan_top and not instance.controller.tasks


def test_receipt_taxonomy_uses_the_known_submission_and_matching_chapter(tmp_path):
    instance,desktop,ai,journal,image,queue=runner(tmp_path)
    task=CourseTask('q','1','','章节测试','quiz',0,0,quiz_ref='chapter:q')
    instance.controller.tasks=[task];instance.controller.activate(task)
    journal.begin(task.quiz_ref,1);journal.transition(task.quiz_ref,'working','submit_sent');journal.transition(task.quiz_ref,'submit_sent','confirm_sent')
    data=payload('popup_result',chapter='1',actions=[{'kind':'click','intent':'return_course','box':[300,400,500,450]}])
    obs=observation(data);instance.canonicalize_receipt(obs)
    assert obs.scene=='chapter_result'
    other=observation({**data,'chapter':'2'});instance.canonicalize_receipt(other)
    assert other.scene=='popup_result'


def test_video_end_waits_for_its_marker_instead_of_replaying_or_fast_pausing(tmp_path):
    instance,desktop,ai,journal,image,queue=runner(tmp_path)
    task=instance.row_task(row('1.1'));instance.controller.tasks=[task];instance.controller.activate(task);instance.state['phase']='watch'
    data=payload(player={'state':'paused','position':30,'duration':30,'muted':True},catalog={'rows':[row('1.1',False)]})
    queue[0]=data;instance.step()
    assert instance.observation.player['state']=='ended' and instance.state['completion_waiting']
    assert instance.idle_count==0 and task.status=='running'
    desktop.click.assert_not_called()


def test_pause_button_text_means_currently_playing_and_mute_is_idempotent():
    data=payload(player={'state':'paused','position':5,'duration':30,'muted':False,'button_text':'暂停','mute_button_text':'已静音'})
    result=observation(data)
    assert result.player['state']=='playing' and result.player['muted'] is True


def test_lesson_number_with_visible_title_remains_the_same_identity():
    data=payload(lesson_code='2.1 同名课时')
    assert observation(data).lesson_code=='2.1'


def test_visual_field_box_is_refined_without_reading_text():
    image=Image.new('RGB',(900,650),'white');draw=ImageDraw.Draw(image)
    draw.text((90,325),'Field 1',fill='black')
    draw.rectangle((90,350,640,404),outline='#64748b',width=2)
    draw.rectangle((90,450,640,504),outline='#64748b',width=2)
    first=VisionExecutor.input_box(image,Rect(90,300,550,80))
    assert 348<=first.y<=352 and 52<=first.h<=57
    second=VisionExecutor.input_box(image,Rect(90,430,550,70))
    assert 448<=second.y<=452 and 52<=second.h<=57


def test_confirmation_button_grounding_is_refined_before_irreversible_click():
    image=Image.new('RGB',(900,650),'white');draw=ImageDraw.Draw(image)
    draw.rectangle((350,460,530,518),fill='#1d4ed8')
    target=VisionExecutor.button_box(image,Rect(350,400,180,80))
    assert target.contains(Rect(440,489,1,1)) and target.y>=458


def test_changing_a_number_in_a_long_question_never_reuses_the_old_answer(tmp_path):
    q=question();q['stem']='x'*200+'1+1等于几'
    instance,desktop,ai,journal,image,queue=runner(tmp_path,payload('popup_question',question=q))
    instance.step();instance.force=True
    q2=copy.deepcopy(q);q2['stem']='x'*200+'1+2等于几';queue[0]=payload('popup_question',question=q2)
    with pytest.raises(PauseRequired,match='题干发生变化'):instance.step()


def test_watcher_ignores_video_animation_but_has_heartbeat():
    image=Image.new('RGB',(1000,800),'#333333')
    obs=observation(payload())
    watch=VisualWatch();watch.remember(image,obs,0)
    changed=image.copy();ImageDraw.Draw(changed).rectangle((50,200,600,650),fill='#b53a99')
    assert watch.reason(changed,obs,6) is None
    assert watch.reason(changed,obs,31)=='定期状态复核'


def test_watcher_detects_a_white_popup_before_idle_heartbeat():
    image=Image.new('RGB',(1000,800),'#333333');obs=observation(payload())
    watch=VisualWatch();watch.remember(image,obs,0)
    changed=image.copy();ImageDraw.Draw(changed).rectangle((80,200,640,690),fill='white')
    assert watch.reason(changed,obs,1)=='播放器出现新的覆盖面板'


def test_vision_connection_probe_is_selected_by_gui_mode(monkeypatch):
    from desktop_app import connection
    called=[]
    monkeypatch.setattr('desktop_app.vision_ai.visual_probe',lambda *args:(called.append(True) or True,1.2))
    result=connection.probe_connection('https://example.com','m','key',threading.Event(),True)
    assert result['status']=='passed' and '图片' in result['message'] and called
