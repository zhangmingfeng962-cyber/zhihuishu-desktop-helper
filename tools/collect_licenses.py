from importlib import metadata
from pathlib import Path
import json,re

root=Path(__file__).resolve().parents[1]
out=root/'third-party-licenses'
out.mkdir(exist_ok=True)
packages=[]
for dist in metadata.distributions():
    name=dist.metadata['Name'] or 'unknown'
    record={'name':name,'version':dist.version,'license':dist.metadata.get('License-Expression') or dist.metadata.get('License',''),'files':[]}
    for file in dist.files or []:
        if re.search(r'(?i)(?:^|/)(?:licen[cs]e|notice|copying|authors)(?:[._/-]|$)',str(file)) and '.dist-info' in str(file):
            original=Path(dist.locate_file(file))
            if original.is_file():
                target=out/re.sub(r'[^A-Za-z0-9._-]','_',name)/Path(str(file).split('.dist-info/',1)[-1])
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(original.read_bytes())
                record['files'].append(str(target.relative_to(out)).replace('\\','/'))
    packages.append(record)
(out/'packages.json').write_text(json.dumps(sorted(packages,key=lambda p:p['name'].lower()),ensure_ascii=False,indent=2),'utf8')
(root/'THIRD-PARTY.md').write_text('# 第三方依赖与许可\n\n依赖版本与可用许可全文见 third-party-licenses/packages.json 及同目录子文件夹。\n\n'+ '\n'.join(f"- {p['name']} {p['version']}" for p in sorted(packages,key=lambda p:p['name'].lower()))+'\n','utf8')
print(f'Collected {len(packages)} distributions and {sum(len(p["files"]) for p in packages)} license files.')
