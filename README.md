# �ǻ������濴�Σ���������

Windows �����Ӿ��Զ������ߣ����� Autovisor MIT ��Ŀ���졣Ĭ�����̣�
**ɨ��Ŀ¼ �� ����������Ƶ �� ������Ƶ���� �� �л���ʱ �� ����²Ⲣ�ύ �� ���ؿγ� �� ��������**��

> **Ԥ���� v0.2.1-beta.1��** ����������ʾ���Զ�������ͨ������ʵ�ǻ����γ̺���ʵ����ģ����δ������ա�
> ����֤����ҳ��ģ�����䡢��ȫ����ȷ���Զ������ᱻƽ̨��⡣��ʽ���Բ������䷶Χ��

## �����뿪ʼ

[���� Windows x64 ���а����鿴�汾˵��](https://github.com/zhangmingfeng962-cyber/zhihuishu-desktop-helper/releases/tag/v0.2.1-beta.1)

������ѹ `windows-x64.zip` ������ `AutovisorDesktop.exe`������ `_internal` �ļ��С�
���谲װ Python ���������չ�����ȵ㡰������ʾ���Զ����������Ľ���Ƶ��������²⣬����Ҫ API Key��

ʵ��ʹ�����ֶ���¼�ǻ������� Edge ��������Ļ����󻯡���������� 100%��
��дҳ����ʾ�Ŀγ����ƣ��󶨴��ڲ�У׼��Ƶ��Ŀ¼��������²��ĸ�����
Ȼ�󱾵���д�Լ��� DeepSeek API Key�����沢���Խӿڣ��ص��γ�ҳ�㡰��ʼ���Σ����⡱��
��ϸ����� [ʹ��˵��](README-course.md)��

## ������߽�

- ������������ͣ������ͬʱȷ�ϵ�ǰ��Ƶ��������Ŀ¼��ɱ�Ǻ���пΡ�
- ��ѡ����ѡ���жϡ���ͨ�ı���ռ������д����ѡ��ǩ֧�� A�CZ������ѡ��ȱʧ���޷�����ʱ��ͣ��
- ������Ƶ��ɺ��Զ����²⡢���©�⡢�ύ���˶Խ�����ٷ��ؿγ̡�
- �γ��������¼�����ڱ��أ�֧���жϻָ����ύ״̬����ȷʱ���ظ�������
- ��ǰ���䴫ͳ `studyvideoh5.zhihuishu.com/stuStudy` �����μ�������壻����ģ����Ҫ������֤��
- ��Ҫ�γ̴��ڳ�����ǰ̨����֤�롢�쳣�ű���ʾ��ʧ�������ֱ仯�Ͳ�����ʶ�����ͣ��
- ���ֻ�ؼ�ʶ���ܱ�֤ 100% ׼ȷ���������⡢ͼƬ���𡢸��ӹ�ʽ���ĵ�/ֱ��/��������δ���뱾�����䡣

## ÿλʹ���ߵ�����

ÿ��ʹ���Լ��Ŀγ��˺ź� API Key����Ŀ�������κ��˵���Կ��ѧϰ��¼��
��Կ�� Windows ƾ�ݹ����������á���־�� SQLite λ�� `%LOCALAPPDATA%\AutovisorDesktop`��
�����ѡ�����ֻᷢ�������õ� AI ����Ĭ�ϲ��ϴ�������ͼ��
Ĭ�� DeepSeek �ٷ��ӿڼ� `deepseek-flash`���ӿڵ��������Լ��Ŀ���ƽ̨�˻��Ʒѡ�

`Ctrl+Alt+Shift+F12` ȫ��ֹͣ��Ҳ���ó���ť�������������Ļ���䡣
��ͬһʱ��ֻ����һ���Զ�������

## Դ�������빹��

��Ҫ Windows x64��Python 3.12��

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe DesktopLauncher.py
.\.venv\Scripts\python.exe -m pytest desktop_tests -q
.\.venv\Scripts\python.exe build_desktop.py
```

ԭ��������ʾ���ԣ���Ҫ���ֲ��Դ�����ǰ̨����

```powershell
.\.venv\Scripts\python.exe DesktopLauncher.py --selftest-course --resume-test --report-dir qa/share-frozen
```

����״̬�� [TEST-REPORT.md](TEST-REPORT.md)����������ű�֧�� `--output-dir`��`--app-dir`��
`--evidence-root`��`--stage-dir`�������������ߵĵ���Ŀ¼��

## ��Դ������

[CXRunfree/Autovisor](https://github.com/CXRunfree/Autovisor) �������Ӿ������棬
����ԭ���� MIT ��Ȩ������֤����Դ���޸ķ�Χ�� [UPSTREAM.md](UPSTREAM.md)�����������ɼ� THIRD-PARTY.md��
����Ŀ�����ǻ����ٷ��ͻ��ˣ�Ҳ�������������ߵ���ʽ���а档
