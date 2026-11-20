"""Explicit offline fixture adapter. Never used for a real browser target."""
import json,time
from .vision_ai import Observation


class VisionDemoAI:
    def __init__(self,demo):self.demo=demo
    def close(self):pass

    def observe(self,image,context,frame_id):
        started=time.monotonic();d=self.demo;w,h=image.size
        def box(x,y,width,height):return [round(x/w*1000),round(y/h*1000),round((x+width)/w*1000),round((y+height)/h*1000)]
        def action(intent,target=None,kind='click',**kwargs):
            return {'kind':kind,'intent':intent,**({'box':target} if target else {}),**kwargs}
        catalog=d.catalog;offset=catalog.canvasy(0)
        chapters=[];rows=[]
        for ch in ('1','2'):
            bounds=catalog.bbox('heading'+ch)
            if bounds and bounds[1]>=offset and bounds[3]<=offset+480:
                chapters.append({'chapter':ch,'expanded':d.expanded[ch],'box':box(1030+bounds[0],150+bounds[1]-offset,bounds[2]-bounds[0],bounds[3]-bounds[1])})
            if d.expanded[ch]:
                for code in (ch+'.1',ch+'.2','q'+ch):
                    tag='quiz'+ch if code.startswith('q') else 'lesson'+code
                    bounds=catalog.bbox(tag)
                    if not bounds or bounds[1]<offset or bounds[3]>offset+480:continue
                    rows.append({'chapter':ch,'code':'' if code.startswith('q') else code,'kind':'quiz' if code.startswith('q') else 'video','label':'章节测试' if code.startswith('q') else '同名课时',
                                 'duration':0 if code.startswith('q') else d.durations[code],'done':code in d.done,'selected':d.current==code,
                                 'box':box(1030+max(0,bounds[0]),150+bounds[1]-offset,min(440,bounds[2])-max(0,bounds[0]),bounds[3]-bounds[1])})
        state='ended' if d.current and d.position>=d.durations[d.current] else 'playing' if d.playing else 'paused'
        result={'frame_id':frame_id,'scene':'course','course_name':d.course_name,'chapter':d.quiz_chapter or (d.current or '').split('.')[0],'lesson_code':d.current or '',
                'message':'离线固定视觉回放，不调用在线 AI','player':{'state':state,'position':d.position,'duration':d.durations.get(d.current,d.duration),'muted':d.muted},
                'regions':{'player':box(30,110,950,650),'catalog':box(1030,150,440,480),'popup':box(165,155,755,555),'question':box(35,110,930,800)},
                'catalog':{'at_top':catalog.yview()[0]<.001,'at_bottom':catalog.yview()[1]>.999,'chapters':chapters,'rows':rows},'question':None,'actions':[]}
        if d.popup or d.stage=='quiz':
            popup=bool(d.popup);kind=d.popup if popup else 'judge' if d.quiz_index==1 else 'blank'
            x,y=(200,195) if popup else (90,190)
            answers={'single':['B'],'multiple':['A','B'],'judge':['A'],'blank':['北京','2']}[kind]
            options=[];blanks=[]
            if kind=='blank':
                for i,entry in enumerate(d.entries):blanks.append({'index':i+1,'value':entry.get(),'box':box(x,y+160+i*100,550,54)})
            else:
                for i,value in enumerate(['正确','错误'] if kind=='judge' else ['1','2','3']):
                    label=chr(65+i);options.append({'label':label,'text':value,'selected':label in d.selected,'box':box(x,y+150+i*65,24,24)})
            result['scene']='popup_question' if popup else 'chapter_question'
            stems={'single':'1 + 1 等于几？','multiple':'小于 3 的正整数有哪些？','judge':'地球是一颗行星。','blank':'第一个空填写中华人民共和国的首都，第二个空填写 1 + 1 的结果。'}
            result['question']={'kind':kind,'stem':stems[kind],'index':1 if popup else d.quiz_index,'total':1 if popup else 2,'complete':True,'option_count':len(options),'blank_count':len(blanks),'options':options,'blanks':blanks,'answers':answers,'explanation':'固定演示答案'}
            pending=[]
            for item in options:
                desired=item['label'] in answers
                if desired!=item['selected'] and (kind=='multiple' or desired):pending.append(action('select_option',item['box'],label=item['label'],desired=desired))
            for item in blanks:
                if item['value']!=answers[item['index']-1]:pending.append(action('fill_blank',item['box'],kind='input',index=item['index'],value=answers[item['index']-1]))
            if pending:result['actions']=pending[:4]
            elif popup:result['actions']=[action('submit',box(x+250,y+430,180,58))]
            elif d.quiz_index==1:result['actions']=[action('next_question',box(x+470,y+500,180,58))]
            else:result['actions']=[action('submit',box(x+470,y+500,180,58))]
        elif d.stage=='confirm':
            result['scene']='chapter_confirmation';result['actions']=[action('confirm_submit',box(350,460,180,58))]
        elif d.stage=='result':
            result['scene']='chapter_result';result['actions']=[action('return_course',box(350,460,180,58))]
        elif context['phase']=='scan':
            if context['auto_mute'] and not d.muted:result['actions']=[action('mute',box(720,685,200,58))]
            elif not context['scan']['top_seen'] and not (context.get('scroll_feedback') or {}).get('unchanged_count',0)>=2:result['actions']=[action('scroll_catalog',result['regions']['catalog'],kind='scroll',amount=3)]
            elif any(not c['expanded'] for c in chapters):result['actions']=[action('expand_chapter',next(c['box'] for c in chapters if not c['expanded']))]
            elif not result['catalog']['at_bottom'] or not ((context.get('scroll_feedback') or {}).get('amount',0)<0 and (context.get('scroll_feedback') or {}).get('unchanged_count',0)>=2):result['actions']=[action('scroll_catalog',result['regions']['catalog'],kind='scroll',amount=-3)]
        elif context.get('active_task'):
            task=context['active_task'];target=next((r for r in rows if r['chapter']==task['chapter'] and r['code']==task['code'] and r['kind']==task['kind']),None)
            if task['kind']=='video' and d.current==task['code']:
                if state=='paused':result['actions']=[action('play',box(45,685,110,58))]
                elif state=='ended' and not target:result['actions']=[action('scroll_catalog',result['regions']['catalog'],kind='scroll',amount=3 if rows and task['chapter']<=rows[0]['chapter'] else -3)]
            elif target:result['actions']=[action('open_lesson' if task['kind']=='video' else 'open_quiz',target['box'])]
            else:result['actions']=[action('scroll_catalog',result['regions']['catalog'],kind='scroll',amount=3 if rows and task['chapter']<=rows[0]['chapter'] else -3)]
        obs=Observation.parse(json.dumps(result,ensure_ascii=False),frame_id);obs.elapsed=round(time.monotonic()-started,4)
        return obs
