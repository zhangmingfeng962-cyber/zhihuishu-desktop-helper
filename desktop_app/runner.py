from __future__ import annotations

import threading
import time
from PIL import Image
from .domain import Answer, Profile, Question, View, PauseRequired, Stopped, normalized, fingerprint
from .storage import Journal
from .vision import Perception, selection_state


class PopupAnswered(Exception):
    def __init__(self, frame, view):
        self.frame, self.view = frame, view


class Runner:
    def __init__(self, desktop, perception: Perception, ai, journal: Journal, profile: Profile,
                 stop: threading.Event, pause: threading.Event, emit):
        self.desktop, self.vision, self.ai, self.journal, self.profile = desktop, perception, ai, journal, profile
        self.stop, self.pause, self.emit = stop, pause, emit
        active = journal.active()
        self.quiz = active if active and active.startswith("chapter:") else None
        self.last_question = None
        self.navigation = None
        self.submit_deadline = None
        self.saved = set()
        self.catalog_seen = set()
        self.catalog_repeats = 0
        self.last_view = None
        self.last_frame = None
        self.document = None
        self.finished_jobs = 0
        self.started_at = time.monotonic()
        self.popup_absent_frames = 0
        self.popup_feedback_closed = False
        self.popup_close_deadline = None

    def note(self, message: str):
        self.emit("log", message)

    def inspect(self, frame):
        return self.vision.inspect(frame,self.profile)

    def on_resume(self):
        self.popup_feedback_closed = False
        self.popup_close_deadline = None

    def quiz_for(self, q: Question) -> str:
        total = q.total
        if q.scope == "popup":
            quiz = "popup:" + fingerprint(self.profile.task_name + q.key)
            self.journal.begin(quiz, 1)
            return quiz
        if total is None or not 1 <= q.index <= total <= 300:
            raise PauseRequired("未能确认当前题号和总题数。题目区域需包含“第 N 题 / 共 N 题”等文字。")
        if q.index == 1:
            quiz = "chapter:" + fingerprint(self.profile.task_name + q.title + q.key)
            self.quiz = quiz
        elif self.quiz:
            quiz = self.quiz
        else:
            raise PauseRequired("首次开始请打开作业第 1 题；已有运行记录的作业可从中断题继续。")
        self.journal.begin(quiz, total)
        return quiz

    def validate_question(self, q: Question):
        if q.kind == "blank":
            if not q.blanks:
                raise PauseRequired("没有可靠定位到填空框。请暂停后用“标注填空”逐框校准。")
        elif (not q.option_rows_complete or len(q.options) < 2 or [o.label for o in q.options] != [chr(65+i) for i in range(len(q.options))]
              or any(o.control is None or o.selected is None for o in q.options)):
            raise PauseRequired("选项或选中状态识别不完整，请调整题目区域并预览。")

    def verify_identity(self, q: Question) -> tuple[Image.Image, View]:
        expected_key = q.key
        if self.document and self.document.question and self.document.question.key == q.key:
            self.document.top()
            expected_key = self.document.seed.key
        frame = self.desktop.capture()
        view = self.inspect(frame)
        if view.blocked:
            raise PauseRequired(view.blocked)
        if q.scope == 'popup' and view.popup_feedback:
            raise PopupAnswered(frame, view)
        if not view.question or view.question.key != expected_key:
            raise PauseRequired("题目在分析期间发生变化，已取消旧答案的填写。")
        if not self.document:
            self.validate_question(view.question)
        return frame, view

    def apply(self, q: Question, answer: Answer):
        self.validate_question(q)
        for i, box in enumerate(q.blanks) if q.kind == "blank" else []:
            frame, _ = self.verify_identity(q)
            if self.document:
                box = self.document.locate(box)
                frame = self.desktop.capture()
            wanted = normalized(answer.values[i])
            existing = normalized(self.vision.field_text(frame, box))
            if existing == wanted:
                continue
            if existing:
                raise PauseRequired(f"第 {q.index} 题第 {i+1} 空已有不同内容；请检查并清空该框后继续。")
            self.desktop.replace_text(box, answer.values[i], frame)
            actual = normalized(self.vision.field_text(self.desktop.capture(), box))
            if actual != wanted:
                raise PauseRequired(f"第 {q.index} 题第 {i+1} 空输入后未能完整复核；请检查可见内容。")
        if q.kind != "blank":
            for option in q.options:
                desired = option.label in answer.values
                frame, current = self.verify_identity(q)
                control = self.document.locate(option.control) if self.document else option.control
                frame = self.desktop.capture()
                selected = selection_state(frame,control)
                if selected is None:
                    raise PauseRequired("无法确认选项状态，已停止点击。")
                if selected != desired:
                    # Single-choice controls cannot be deselected by clicking; select the desired item only.
                    if q.kind != "multiple" and not desired:
                        continue
                    self.desktop.click(control, frame)
                    self.desktop.wait(.35)
                    state = selection_state(self.desktop.capture(), control)
                    if state is not desired:
                        raise PauseRequired("点击后未确认选中状态，请检查页面。")
            selected = set()
            self.verify_identity(q)
            for option in q.options:
                control = self.document.locate(option.control) if self.document else option.control
                state = selection_state(self.desktop.capture(),control)
                if state is None:
                    raise PauseRequired('最终选项状态无法复核。')
                if state:
                    selected.add(option.label)
            if selected != set(answer.values):
                raise PauseRequired("最终选项状态与答案不一致。")

    def handle_popup_feedback(self, frame, view):
        if view.popup_feedback:
            if not self.popup_feedback_closed:
                if 'popup_done' not in view.buttons:
                    raise PauseRequired('弹题已显示结果，但未识别到弹窗内的关闭按钮，请调整视频弹题区域。')
                self.desktop.click(view.buttons['popup_done'],frame)
                self.popup_feedback_closed = True
                self.popup_close_deadline = time.monotonic()+45
                self.popup_absent_frames = 0
                self.note('弹题已显示作答结果，正在关闭并恢复原视频。')
            elif time.monotonic() > self.popup_close_deadline:
                raise PauseRequired('点击关闭后弹题结果仍未消失，请检查弹窗并继续。')
            return True
        if not self.popup_feedback_closed:
            return False
        if view.blocked:
            raise PauseRequired(view.blocked)
        if not view.question and not view.confirmation:
            if not view.video_visible and self.profile.regions.get('player'):
                self.desktop.reveal_player(self.profile.regions['player'])
                frame=self.desktop.capture();view=self.inspect(frame)
                if view.blocked:raise PauseRequired(view.blocked)
            if (view.video_visible or view.video_lesson) and not view.question and not view.popup_feedback:
                self.popup_absent_frames += 1
                if self.popup_absent_frames >= 2:
                    if self.quiz and self.quiz.startswith('popup:'):
                        state=self.journal.state(self.quiz)
                        self.journal.transition(self.quiz,state,'success')
                        self.quiz=None;self.submit_deadline=None
                    self.popup_feedback_closed=False;self.popup_close_deadline=None
                    self.popup_absent_frames=0;self.on_resume()
                    self.note('已确认弹题关闭，继续当前视频。')
                return True
        if time.monotonic() > self.popup_close_deadline:
            raise PauseRequired('弹题关闭后未确认返回播放器，请检查视频区域。')
        return True

    def pending_submission(self, frame: Image.Image, view: View) -> bool:
        if not self.quiz:
            return False
        state = self.journal.state(self.quiz)
        if state not in {"submit_sent", "confirm_sent"}:
            return False
        if view.unfinished:
            raise PauseRequired("页面提示存在未完成题目，已暂停交卷。请检查题目或清除本次记录后重做。")
        if self.quiz.startswith('popup:'):
            if self.handle_popup_feedback(frame,view):return True
            if not view.question and not view.confirmation and view.video_visible:
                self.popup_absent_frames += 1
                if self.popup_absent_frames >= 2:
                    self.journal.transition(self.quiz,state,'success')
                    self.note('已确认弹题关闭，并返回视频画面。')
                    self.quiz,self.submit_deadline = None,None
                    self.popup_absent_frames = 0
                return True
        if view.submitted:
            self.journal.transition(self.quiz, state, "success")
            self.finished_jobs += 1
            self.note("已通过结果页确认提交成功。")
            if "return" in view.buttons:
                self.desktop.click(view.buttons["return"], frame)
                self.desktop.wait(1.)
            elif 'course_tab' in view.buttons:
                self.desktop.click(view.buttons['course_tab'],frame)
                self.desktop.wait(1.)
            else:
                raise PauseRequired("作业提交成功；未找到返回课程按钮，请回到课程页面后继续。")
            self.quiz, self.submit_deadline = None, None
            return True
        if view.confirmation and state == "submit_sent":
            if "confirm" not in view.buttons:
                raise PauseRequired("交卷确认按钮未识别，请校准确认弹窗区域。")
            # Persist BEFORE the irreversible click. An ambiguous crash is never retried.
            if self.journal.transition(self.quiz, "submit_sent", "confirm_sent"):
                self.desktop.click(view.buttons["confirm"], frame)
                self.submit_deadline = time.monotonic() + 18
                self.note("已确认提交，正在检查结果页。")
            return True
        if self.submit_deadline is None:
            self.submit_deadline = time.monotonic() + 18
        if time.monotonic() > self.submit_deadline:
            raise PauseRequired("提交状态未确认，程序不会重复交卷。请检查页面；确认尚未提交时可清除记录后重新开始。")
        return True

    def step(self, frame: Image.Image | None = None, view: View | None = None):
        try:
            return self._step(frame,view)
        except PopupAnswered as result:
            self.handle_popup_feedback(result.frame,result.view)

    def _step(self, frame: Image.Image | None = None, view: View | None = None):
        frame = self.desktop.capture() if frame is None else frame
        view = self.inspect(frame) if view is None else view
        self.last_view, self.last_frame = view, frame
        self.emit("view", view)
        if view.blocked:
            raise PauseRequired(view.blocked)
        if self.handle_popup_feedback(frame,view):
            return
        if self.pending_submission(frame, view):
            return
        if view.confirmation:
            raise PauseRequired("出现未关联本次交卷记录的确认弹窗，请检查页面。")
        q = view.question
        if q:
            if self.navigation:
                old_key, deadline = self.navigation
                if q.key == old_key:
                    if time.monotonic() > deadline:
                        raise PauseRequired("点击下一题后页面未切换，请检查网络与页面状态。")
                    return
                self.navigation = None
            from .document import QuestionDocument
            self.document = QuestionDocument.collect(self.desktop,self.vision,self.profile,frame,q)
            seed_key = q.key
            if self.document:
                q = self.document.question
            quiz = self.quiz_for(q)
            if q.scope == "popup":
                self.quiz = quiz
                self.popup_feedback_closed = False
            state = self.journal.state(quiz)
            if state == "success":
                if q.scope == "popup" and "popup_done" in view.buttons:
                    self.desktop.click(view.buttons["popup_done"], frame)
                    return
                raise PauseRequired("这份作业已有提交成功记录，已暂停重复作答。")
            self.validate_question(q)
            cached = self.journal.answer(quiz, q.index, q.key)
            if cached:
                answer = cached[0]
            else:
                self.note(f"正在分析第 {q.index} 题（{q.kind}）。")
                answer = self.ai.solve(q)
                answer = Answer.parse(__import__("json").dumps({"answers": answer.values, "explanation": answer.explanation}, ensure_ascii=False), q)
                self.journal.record(quiz, q.index, q.key, answer, False)
            self.apply(q, answer)
            self.journal.record(quiz, q.index, q.key, answer, True)
            self.last_question = q
            self.emit("answer", (q, answer))
            self.note(f"第 {q.index} 题已填写并复核。")
            self.desktop.wait(self.profile.answer_interval)
            frame, current = self.verify_identity(q)
            if q.scope == "chapter" and q.index < q.total:
                if "next" not in current.buttons:
                    if "next" not in self.seek_buttons(q):
                        raise PauseRequired("未找到下一题按钮，请确保题目区域与翻页按钮可见。")
                    return
                self.desktop.click(current.buttons["next"], frame)
                self.navigation = (seed_key, time.monotonic() + 12)
                return
            if not self.journal.complete(quiz):
                raise PauseRequired("作答记录存在漏题，已停止提交；请返回未完成题目继续。")
            if self.document and not ({'save','submit'} & current.buttons.keys()):
                frame,current=self.find_submission_controls()
            save_key = (quiz, q.key)
            if "save" in current.buttons and save_key not in self.saved:
                self.desktop.click(current.buttons["save"], frame)
                self.saved.add(save_key)
                self.desktop.wait(.7)
                if self.document:
                    self.document.top()
                return
            if "submit" not in current.buttons:
                if q.scope == "popup" and "confirm" in current.buttons:
                    current.buttons["submit"] = current.buttons["confirm"]
                else:
                    raise PauseRequired("全部题目已填写，但未找到提交按钮，请检查页面或调整区域。")
            self.quiz = quiz
            if self.journal.transition(quiz, "working", "submit_sent"):
                self.desktop.click(current.buttons["submit"], frame)
                self.submit_deadline = time.monotonic() + 18
                self.note("已发出提交，等待页面确认。")
            return
        if view.submitted:
            raise PauseRequired("当前处于结果页。请返回课程页面，或先核对已有提交状态。")
        if "start_quiz" in view.buttons:
            self.desktop.click(view.buttons["start_quiz"], frame)
            self.desktop.wait(1.)
            return
        if self.profile.regions.get('player') and not view.video_visible and not view.video_finished:
            self.desktop.reveal_player(self.profile.regions['player'])
            frame=self.desktop.capture()
            view=self.inspect(frame)
            if view.blocked:
                raise PauseRequired(view.blocked)
        if view.video_finished:
            self.advance_catalog(frame, view)
        elif view.video_paused and "play" in view.buttons:
            self.desktop.click(view.buttons["play"], frame)
            self.desktop.wait(1.)
        elif not self.profile.regions.get("player"):
            raise PauseRequired("未检测到题目，请先校准题目区域，或配置视频与目录区域。")

    def seek_buttons(self, q: Question) -> dict:
        region = self.profile.regions.get("question")
        if not region:
            return {}
        # Bounded scrolling; never carry old coordinates across a scroll.
        for _ in range(6):
            self.desktop.scroll(-3, region)
            frame = self.desktop.capture()
            view = self.inspect(frame)
            if view.blocked:
                raise PauseRequired(view.blocked)
            if "next" in view.buttons:
                self.desktop.click(view.buttons["next"], frame)
                key = self.document.seed.key if self.document else q.key
                self.navigation = (key, time.monotonic() + 12)
                return view.buttons
        return {}

    def find_submission_controls(self):
        region=self.profile.regions['question']
        for _ in range(16):
            frame=self.desktop.capture()
            view=self.inspect(frame)
            if view.blocked:
                raise PauseRequired(view.blocked)
            if {'save','submit'} & view.buttons.keys():
                return frame,view
            self.desktop.scroll(-2,region)
        raise PauseRequired('未找到完整可见的保存或提交按钮。')

    def advance_catalog(self, frame: Image.Image, view: View):
        region = self.profile.regions.get("catalog")
        if not region:
            raise PauseRequired("视频已结束，请校准课时目录区域以继续切换。")
        rows = self.vision.catalog_rows(frame, self.profile, view.spans)
        if not rows or not any(done for _, _, done in rows):
            raise PauseRequired("视频已结束，但未确认目录完成标记；请检查平台进度。")
        pending = [(title, box) for title, box, done in rows if not done]
        if pending:
            title, box = pending[0]
            self.desktop.click(box, frame)
            self.note("正在切换到目录中的未完成课时。")
            self.catalog_repeats = 0
            self.desktop.wait(1.5)
            return
        tests=self.vision.catalog_tests(frame,self.profile,view.spans)
        for box,done in tests:
            if not done:
                self.desktop.click(box,frame)
                self.note('正在进入目录中的未完成章节测试。')
                self.desktop.wait(1.5)
                return
        signature = fingerprint("|".join(t for t, _, _ in rows))
        if signature in self.catalog_seen:
            self.catalog_repeats += 1
        self.catalog_seen.add(signature)
        if self.catalog_repeats >= 2:
            raise PauseRequired("已检查到目录底部，可见视频均已完成。请核对章节测试或切换课程。")
        self.desktop.scroll(-5, region)

    def run(self):
        self.note("5 秒后开始，请切回绑定的课程窗口。Ctrl+Alt+Shift+F12 可紧急停止。")
        if self.stop.wait(5):
            return
        while not self.stop.is_set():
            try:
                if self.profile.max_minutes and time.monotonic()-self.started_at > self.profile.max_minutes * 60:
                    raise Stopped()
                self.step()
                self.desktop.wait(self.profile.poll_interval)
            except PauseRequired as exc:
                self.pause.set()
                self.emit("paused", str(exc))
                while self.pause.is_set() and not self.stop.wait(.1):
                    pass
                if not self.stop.is_set():
                    self.on_resume()
                    self.note("3 秒后恢复，请切回课程窗口。")
                    if self.stop.wait(3):
                        break
            except Stopped:
                break
            except Exception as exc:
                self.pause.set()
                self.emit("paused", f"运行出现 {type(exc).__name__}，已暂停。请查看日志后继续或停止。")
                while self.pause.is_set() and not self.stop.wait(.1):
                    pass
        self.emit("stopped", "已停止。")
