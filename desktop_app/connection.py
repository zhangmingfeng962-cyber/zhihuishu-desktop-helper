"""A small connection check, independent of Tk and course execution."""
import time
from .ai import OnlineAI
from .domain import Question,Option,Rect,PauseRequired,Stopped


def probe_connection(address,model,key,stop):
    started=time.monotonic()
    question=Question('single','1+1等于几？',1,1,'popup',options=[Option('A','1',Rect(0,0,20,20)),Option('B','2',Rect(0,30,20,20))])
    try:
        answer=OnlineAI(address,model,key,stop,request_timeout=20).solve(question)
        if stop.is_set():raise Stopped()
        status='passed' if answer.values==['B'] else 'warning'
        message='验证成功：接口可用，测试题回答正确。' if status=='passed' else '接口已连接，但测试题回答不正确，请核对所选模型。'
    except Stopped:
        status,message='cancelled','已取消测试；未把这次请求标记为验证成功。'
    except PauseRequired as exc:
        status,message='failed',str(exc)
    except Exception as exc:
        status,message='failed','连接测试未完成（'+type(exc).__name__+'），请检查配置后重试。'
    return {'status':status,'message':message,'elapsed':round(time.monotonic()-started,1)}
