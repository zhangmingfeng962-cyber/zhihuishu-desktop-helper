# 智慧树桌面看课＋答题助手

Windows 桌面视觉自动化工具，基于 Autovisor MIT 项目改造。默认流程：
**扫描目录 → 正常播放视频 → 处理视频弹题 → 切换课时 → 完成章测并提交 → 返回课程 → 结束复核**。

> **修复版源码 v0.2.1-beta.2。** 91 项自动测试通过；真实页面已局部验证弹题关闭和续播，真实 DeepSeek 测试请求通过。全目录、真实静音菜单和新版完整整课流程尚未验收。
> 不保证所有页面模板适配、答案全部正确或自动化不会被平台检测。正式考试不在适配范围。

## 下载与开始

GitHub Releases 当前仍是 [Beta1 旧运行包](https://github.com/zhangmingfeng962-cyber/zhihuishu-desktop-helper/releases/tag/v0.2.1-beta.1)，包含本次已修复的已知问题。Beta2 运行包尚未同步到 Releases；当前 main 分支为 Beta2 源码，可按下方命令构建。

完整解压 `windows-x64.zip` 后运行 `AutovisorDesktop.exe`，保留 `_internal` 文件夹。
无需安装 Python 或浏览器扩展。可先点“离线演示”自动运行两章四节视频、弹题和章测，不需要 API Key。

实际使用先手动登录智慧树，将 Edge 放在主屏幕并最大化、浏览器缩放 100%，
填写页面显示的课程名称，绑定窗口并校准视频、目录、弹题和章测四个区域。
然后本地填写自己的 DeepSeek API Key，点击“测试连接”。本页会立即显示测试中，再显示成功或失败原因及耗时。测试只验证当前输入，点“保存配置”后回到课程页点“开始看课＋答题”。
详细步骤见 [使用说明](README-course.md)。

## 功能与边界

- 默认自动静音课程标签页，已静音不会反向开启声音；菜单未识别时暂停。
- 正常播放与暂停续播；同时确认当前视频结束及其目录完成标记后才切课。
- 单选、多选、判断、普通文本填空及多空填写；多选标签支持 A–Z，发现选项缺失或无法复核时暂停。
- 本章视频完成后自动做章测、检查漏题、提交并核对结果，再返回课程。
- 课程与作答记录保存在本地，支持中断恢复，提交状态不明确时不重复交卷。
- 当前适配传统 `studyvideoh5.zhihuishu.com/stuStudy` 共享课及单题面板；其他模板需要另外验证。
- 需要课程窗口持续在前台；验证码、异常脚本提示、失焦、布局变化和不完整识别会暂停。
- 文字或控件识别不能保证 100% 准确，整卷多题、图片作答、复杂公式、文档/直播/讨论任务未纳入本版适配。

## 每位使用者的配置

每人使用自己的课程账号和 API Key，项目不内置任何人的密钥或学习记录。
密钥存 Windows 凭据管理器；配置、日志及 SQLite 位于 `%LOCALAPPDATA%\AutovisorDesktop`。
题干与选项文字会发给你配置的 AI 服务，默认不上传整屏截图。
默认 DeepSeek 官方接口及 `deepseek-flash`，接口调用由你自己的开放平台账户计费。

`Ctrl+Alt+Shift+F12` 全局停止；也可用程序按钮或将鼠标移至主屏幕角落。
请同一时间只运行一个自动化任务。

## 源码运行与构建

需要 Windows x64、Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe DesktopLauncher.py
.\.venv\Scripts\python.exe -m pytest desktop_tests -q
.\.venv\Scripts\python.exe build_desktop.py
```

原生整课演示测试（需要保持测试窗口在前台）：

```powershell
.\.venv\Scripts\python.exe DesktopLauncher.py --selftest-course --resume-test --report-dir qa/share-frozen
```

测试状态见 [TEST-REPORT.md](TEST-REPORT.md)。发布打包脚本支持 `--output-dir`、`--app-dir`、
`--evidence-root`、`--stage-dir`，不依赖发布者的电脑目录。

## 来源与许可

[CXRunfree/Autovisor](https://github.com/CXRunfree/Autovisor) 的桌面视觉衍生版，
保留原作者 MIT 版权和许可证。来源及修改范围见 [UPSTREAM.md](UPSTREAM.md)，第三方许可见 THIRD-PARTY.md。
本项目不是智慧树官方客户端，也不代表上游作者的正式发行版。
