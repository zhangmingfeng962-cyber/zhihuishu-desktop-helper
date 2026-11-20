# 来源与改造

- 上游：<https://github.com/CXRunfree/Autovisor>
- 基线提交：`5c715623030877d1daac0eaa14ac91f0abb2d003`
- 上游许可：MIT，版权声明完整保存在 `LICENSE`。
- 本地衍生版：Autovisor Desktop 0.3.0 Beta1，增加 AI 看图控制、课程任务与视频进度持久化、整课调度及两章四节视频演示，支持多选 A-Z 与缺失选项检查。

复用上游 `modules/logger.py` 日志实现、ConfigParser 配置方式和 Tkinter 界面技术基础。
`Autovisor.py` / `GUI.py` 已改为桌面版入口。新增 `desktop_app` 完成截图、OCR、在线 AI、
Unicode 输入、可视状态复核、任务日志、恢复与交卷状态管理。

发行 ZIP 和源码 ZIP 只包含桌面版所需文件。原仓库旧的浏览器操作模块不进入发行包。
项目未向上游推送，也不代表上游作者的正式发行版。

多空答案的结构化校验参考了 Siua 的公开实现思路，未复制其 C# 源码：
<https://github.com/zxiyx/Siua>。

第三方 OCR 与运行库使用各自许可；发行包附第三方许可清单。

Beta2 修复弹题结果关闭与续播、目录漏课识别，增加课程标签页自动静音与 AI 连接测试反馈；仍不使用浏览器扩展、DOM 或调试端口。

0.3.0 Beta1 新增 DeepSeek 原图识别、截图绑定动作、自动定位、批量输入、像素变化监测与图片接口测试；旧 OCR 模式及原 SQLite 记录继续保留。
