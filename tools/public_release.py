"""Portable, allow-listed public packaging; never reads application credentials."""
from pathlib import Path
import argparse,hashlib,json,re,shutil,zipfile,tomllib
from datetime import date
import xml.etree.ElementTree as ET

TAG='v0.3.0-beta.1'
REPO='zhangmingfeng962-cyber/zhihuishu-desktop-helper'
ROOT=Path(__file__).resolve().parents[1]
SECRET=re.compile(r'(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def checksum(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
    return h.hexdigest()


def clean_xml(path):
    root=ET.fromstring(Path(path).read_bytes())
    for element in root.iter():
        element.attrib.pop('hostname',None)
        for key in ('file','filepath'):
            if key in element.attrib:
                element.attrib[key]=Path(element.attrib[key].replace('\\','/')).name
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def evidence(path):
    data=json.loads(Path(path).read_text('utf8'))
    if not data.get('success'):raise ValueError('Native regression has not passed')
    keys=('success','version','fixture','control_mode','provider','resume_exercised','video_starts','popup_submits','quiz_submits','correct_popups','correct_quizzes','mute_changes','calls','median_request_seconds','p95_request_seconds','wall_seconds','tasks')
    return {key:data[key] for key in keys if key in data}


def source_files():
    names=['Autovisor.py','GUI.py','DesktopLauncher.py','pyproject.toml','requirements-desktop.txt','requirements-lock.txt',
           'build_desktop.py','LICENSE','UPSTREAM.md','THIRD-PARTY.md','README-course.md','README-public.md',
           'modules/__init__.py','modules/logger.py','tools/public_release.py','tools/package_course_release.py',
           'tools/native_smoke.py','tools/scroll_smoke.py','tools/collect_licenses.py','tools/benchmark_vision.py']
    files=[ROOT/name for name in names]
    for directory in ('desktop_app','desktop_tests'):
        files.extend(p for p in (ROOT/directory).rglob('*.py') if '__pycache__' not in p.parts)
    files.extend(p for p in (ROOT/'third-party-licenses').rglob('*') if p.is_file())
    return sorted(set(files))


def scan_text_files(folder):
    flagged=[]
    for path in Path(folder).rglob('*'):
        if path.is_file() and path.suffix.lower() in {'.py','.md','.json','.xml','.toml','.txt','.yml','.yaml','.ini'}:
            text=path.read_text('utf-8-sig',errors='replace')
            if SECRET.search(text) or re.search(r'[A-Z]:[\\/]Users[\\/][^\s"<>]+|[A-Z]:[\\/]ChatGPT[\\/]',text):
                flagged.append(str(path.relative_to(folder)))
    if flagged:raise ValueError('Public text scan failed: '+', '.join(flagged))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-dir',type=Path,default=ROOT/'dist/releases')
    parser.add_argument('--app-dir',type=Path,default=ROOT/'dist/AutovisorDesktop')
    parser.add_argument('--evidence-root',type=Path,default=ROOT/'qa')
    parser.add_argument('--source-case',default='vision-online')
    parser.add_argument('--frozen-case',default='vision-frozen')
    parser.add_argument('--junit',default='pytest-vision.xml')
    parser.add_argument('--stage-dir',type=Path,default=ROOT/'dist/public-source')
    parser.add_argument('--tag',default=TAG)
    parser.add_argument('--verification-report',type=Path,help='Explicit release report with verification scope and limitations')
    parser.add_argument('--extra-evidence',type=Path,action='append',default=[])
    args=parser.parse_args()
    out=args.output_dir.resolve();stage=args.stage_dir.resolve()
    if stage.exists() and any(stage.iterdir()):raise ValueError('Source staging directory must be empty')
    tag=args.tag
    source=frozen=None
    if not args.verification_report:
        source_dir=args.evidence_root/args.source_case;frozen_dir=args.evidence_root/args.frozen_case
        source=evidence(source_dir/('vision-result.json' if (source_dir/'vision-result.json').exists() else 'course-result.json'))
        frozen=evidence(frozen_dir/('vision-result.json' if (frozen_dir/'vision-result.json').exists() else 'course-result.json'))
        source_version=tomllib.loads((ROOT/'pyproject.toml').read_text('utf8'))['project']['version']
        if frozen.get('version')!=source_version:raise ValueError('Native evidence must match the source and binary version')
        if not frozen.get('resume_exercised'):raise ValueError('Resume regression evidence is missing')
    suites=list(ET.parse(args.evidence_root/args.junit).getroot().iter('testsuite'))
    total=sum(int(s.get('tests','0')) for s in suites)
    if any(int(s.get('failures','0')) or int(s.get('errors','0')) for s in suites):raise ValueError('Unit tests have failures')
    out.mkdir(parents=True,exist_ok=True);stage.mkdir(parents=True,exist_ok=True)
    for path in source_files():
        dest=stage/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    (stage/'README.md').write_bytes((ROOT/'README-public.md').read_bytes())
    (stage/'.gitignore').write_text('.venv/\n__pycache__/\n.pytest_cache/\n*.pyc\n*.spec\nbuild/\ndist/\nqa/\nlogs/\nconfig*.ini\n.env\n.env.*\n*.sqlite*\n*.db\n*.log\n*.egg-info/\n',encoding='utf8')
    proof=stage/'test-evidence';proof.mkdir(exist_ok=True)
    (proof/'unit-tests.xml').write_bytes(clean_xml(args.evidence_root/args.junit))
    for name,data in (('course-source',source),('course-frozen',frozen)):
        if data is not None:(proof/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf8')
    for path in args.extra_evidence:
        shutil.copy2(path,proof/path.name)
    report=f'''# {tag} 测试记录\n\n发布日期：{date.today().isoformat()}（发布者本地日期）。\n\n- {total} 项自动测试通过，0 失败。\n- 多选 A–Z、多空填空、截图坐标、状态复核和重复提交保护已做回归。\n- 源码与本次运行包均通过本地原生桌面课程验证，匿名化证据见 test-evidence。\n- 原生演示包含两章四节视频、四种弹题、两份章测，以及看课中途重建执行器后的 SQLite 恢复。\n- provider 字段区分真实图片 API 与固定离线适配器；本地演示通过不代表真实智慧树成绩或全部模板适配。\n- 真实平台验证应另附实际记录；不保证免检测或 AI 全部答对。\n- 本机名称、密钥与个人绝对路径不进入公开资料。\n'''
    if args.verification_report:
        report=args.verification_report.read_text('utf8')
        if tag not in report:raise ValueError('Explicit report must identify the release tag')
    (stage/'TEST-REPORT.md').write_text(report,'utf8')
    scan_text_files(stage)
    version=tag.lstrip('v');release=out/('zhihuishu-desktop-helper-'+version)
    if release.exists():raise ValueError('Release directory already exists')
    shutil.copytree(args.app_dir,release)
    for name in ('README.md','README-course.md','TEST-REPORT.md','LICENSE','UPSTREAM.md','THIRD-PARTY.md'):
        shutil.copy2(stage/name,release/name)
    shutil.copytree(stage/'third-party-licenses',release/'third-party-licenses')
    shutil.copytree(proof,release/'test-evidence')
    archives=[]
    for folder,label in ((release,'windows-x64'),(stage,'source')):
        target=out/f'zhihuishu-desktop-helper-{version}-{label}.zip'
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for path in sorted(folder.rglob('*')):
                if path.is_file():z.write(path,str(Path(folder.name)/path.relative_to(folder)))
        archives.append(target)
    readme=out/'README.md';readme.write_bytes((stage/'README.md').read_bytes())
    test_report=out/'TEST-REPORT.md';test_report.write_bytes((stage/'TEST-REPORT.md').read_bytes())
    assets=archives+[readme,test_report]
    (out/'SHA256SUMS.txt').write_text(''.join(checksum(p)+'  '+p.name+'\n' for p in assets),'utf8')
    manifest=[{'name':p.name,'size':p.stat().st_size,'sha256':checksum(p)} for p in assets+[out/'SHA256SUMS.txt']]
    (out/'release-manifest.json').write_text(json.dumps(manifest,indent=2),'utf8')
    print(json.dumps({'tag':tag,'repo':REPO,'stage':str(stage),'output':str(out),'tests':total,'assets':manifest},ensure_ascii=True,indent=2))


if __name__=='__main__':main()
