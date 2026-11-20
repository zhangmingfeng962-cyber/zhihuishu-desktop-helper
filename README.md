# 智慧树桌面看课＋答题助手

默认流程：**课程窗口截图 → DeepSeek 看图判断 → 本地鼠标键盘执行 → 新截图复核**。
AI 负责目录检漏、播放暂停、静音、视频弹题和章节作业，本地保存进度并防止重复提交。

> **v0.3.0-beta.1 预发布。** 实际验证项目见 [测试记录](TEST-REPORT.md)。
> AI 可能误判或答错，不保证所有模板适配或平台免检测；正式考试不在适配范围。

## 下载与开始

[下载 Windows x64 运行包、源码、说明及测试报告](https://github.com/zhangmingfeng962-cyber/zhihuishu-desktop-helper/releases/tag/v0.3.0-beta.1)

1. 关闭旧版，完整解压运行包，运行 `AutovisorDesktop.exe`，保留 `_internal`。
2. 手动登录智慧树，浏览器放在主屏幕、缩放 100%，填写真实课程名称并绑定窗口。
3. 保持默认“AI 看图（自动定位）”，在 AI 设置填写自己的 Key，测试图片接口后保存。
4. 点击“开始看课＋答题”，保持课程窗口在前台。**无需先框选四区。**

原有 Key、配置和学习记录继续使用。[详细说明](README-course.md)。

## 功能与边界

- 同一稳定题面最多批量操作四个选项或空位；提交、切页、滚动单独处理。
- 画面变化时判断，稳定播放最多每 30 秒补一次复核。网络和模型仍会产生等待，不承诺所有场景都更快。
- 单选、多选 A–Z、判断、普通文本多空填空；已有选择和内容先复核，避免重复填写。
- 默认自动静音，已静音不反向开启声音。结束后核对当前课时自己的完成标记，再切下一节。
- 目录分页合并，任务结束后全量复扫；SQLite 保留任务、答案与提交状态，状态不明确时不重复交卷。
- AI 自动定位，可暂停后人工修正，也可切换“本地 OCR（旧版）”。
- 目标变化时丢弃旧动作；窗口失焦、验证提示、异常脚本或不可靠识别时暂停。
- 仅面向传统 `studyvideoh5.zhihuishu.com/stuStudy` 共享课与单题作业面板。正式考试、图片作答、复杂公式、直播、讨论及其他平台另行适配。

## 每人自行配置

每位使用者使用自己的课程账号和 DeepSeek API Key。Key 存 Windows 凭据管理器；配置、日志及学习记录在 `%LOCALAPPDATA%\AutovisorDesktop`。

**看图模式会发送绑定课程窗口截图和任务上下文给所选 AI 服务。** 不上传整个桌面，不读取浏览器 Cookie，不使用扩展、DOM 或调试端口。
原始截图默认不保存；仅显式测试采样时保存在本地，不进入公开发布包。

默认官方接口和 `deepseek-flash`，费用由各自开放平台账户承担。图片测试会验证识题、答案及控件位置，仅保存 Key 不等于连接已验证。
`Ctrl+Alt+Shift+F12`、停止按钮或鼠标移至主屏幕角落可停止。一次只运行一个任务。

## 源码与验证

Windows x64、Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe DesktopLauncher.py
.\.venv\Scripts\python.exe -m pytest desktop_tests -q
.\.venv\Scripts\python.exe build_desktop.py
```

显式原生桌面测试，需要保持演示窗口在前台：

```powershell
.\.venv\Scripts\python.exe DesktopLauncher.py --selftest-vision --resume-test --report-dir qa/vision-offline
.\.venv\Scripts\python.exe DesktopLauncher.py --selftest-vision --resume-test --online-vision-test --capture-test-samples --report-dir qa/vision-online
```

第一条为固定演示回放，不调用 AI；第二条会使用本机配置的真实 Key，发送本地演示窗口截图并产生费用。
旧版 OCR 和 Beta2 历史 Release 保留。

## 来源与许可

基于 [CXRunfree/Autovisor](https://github.com/CXRunfree/Autovisor)，保留 MIT 版权与许可证。
见 [UPSTREAM.md](UPSTREAM.md)、LICENSE 和 THIRD-PARTY.md。本项目不是智慧树官方客户端，也不是上游作者的正式发行版。
