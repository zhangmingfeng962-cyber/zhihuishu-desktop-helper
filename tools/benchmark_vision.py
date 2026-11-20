"""Read-only replay: legacy OCR + cached text answers versus image decisions.

Input consists only of explicitly recorded local fixture images. No UI inputs.
"""
from pathlib import Path
import argparse,hashlib,json,sys,threading,time,zipfile
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_app.domain import Profile,PauseRequired
from desktop_app.storage import CredentialStore,load_profile,app_dir
from desktop_app.vision import LocalOCR,Perception
from desktop_app.course import CatalogParser
from desktop_app.ai import OnlineAI


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--samples',type=Path,required=True)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--baseline-source',type=Path)
    parser.add_argument('--online-text',action='store_true')
    args=parser.parse_args()
    profile=Profile.from_dict(json.loads(args.profile.read_text('utf8')))
    if args.baseline_source:
        with zipfile.ZipFile(args.baseline_source) as z:
            name=next(n for n in z.namelist() if n.endswith('/desktop_app/vision.py'))
            assert hashlib.sha256(z.read(name)).digest()==hashlib.sha256((Path(__file__).resolve().parents[1]/'desktop_app/vision.py').read_bytes()).digest(),'Legacy OCR differs from the supplied baseline'
    ocr=LocalOCR();vision=Perception(ocr);cache=set();samples=[]
    config=load_profile(app_dir()) if args.online_text else None
    key=CredentialStore().get() if args.online_text else ''
    for path in sorted(args.samples.glob('*.png')):
        metadata=json.loads(path.with_suffix('.json').read_text('utf8'));image=Image.open(path).convert('RGB')
        started=time.monotonic();error=None;text_seconds=0
        try:
            scene=metadata['scene'];hint='chapter' if scene.startswith('chapter_') else 'video'
            view=vision.inspect(image,profile,scope_hint=hint)
            if scene=='course':
                spans=ocr.read(image,profile.regions['catalog'])
                CatalogParser().read(image,spans,profile.regions['catalog'],'fixture')
            q=view.question
            if q and args.online_text:
                context=metadata.get('context',{});task=context.get('active_task') or {}
                identity=(q.scope,q.key,task.get('task_id'))
                if identity not in cache:
                    request_started=time.monotonic()
                    OnlineAI(config.base_url,config.model,key,threading.Event()).solve(q)
                    text_seconds=time.monotonic()-request_started;cache.add(identity)
        except Exception as exc:
            error=str(exc) if isinstance(exc,PauseRequired) else type(exc).__name__
        elapsed=time.monotonic()-started
        samples.append({'sample':path.name,'scene':metadata['scene'],'legacy_decision_seconds':round(elapsed,3),'legacy_text_request_seconds':round(text_seconds,3),
                        'vision_request_seconds':metadata['elapsed'],'legacy_error':error})
        print(json.dumps(samples[-1],ensure_ascii=True),flush=True)
    def stats(key):
        values=[s[key] for s in samples if s['legacy_error'] is None]
        return {'median':round(float(np.median(values)),3),'p95':round(float(np.percentile(values,95)),3)} if values else None
    report={'method':'Same recorded local fixture images; legacy OCR plus cached text answer requests when enabled versus observed image API latency. No mouse execution or video time included.',
            'sample_count':len(samples),'comparable_count':sum(s['legacy_error'] is None for s in samples),'online_text':args.online_text,'legacy_errors':sum(s['legacy_error'] is not None for s in samples),
            'legacy_decision_seconds':stats('legacy_decision_seconds'),'vision_request_seconds':stats('vision_request_seconds'),'samples':samples}
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='samples'},ensure_ascii=True))


if __name__=='__main__':main()
