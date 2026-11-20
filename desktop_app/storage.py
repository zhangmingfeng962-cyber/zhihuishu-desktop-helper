from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import sqlite3
import threading
from .domain import Answer, Profile, PauseRequired, CourseTask, LessonProgress
from dataclasses import asdict


def app_dir() -> Path:
    root = Path(os.environ.get("AUTOVISOR_DESKTOP_DATA", Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AutovisorDesktop"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_profile(root: Path) -> Profile:
    p = root / "config.ini"
    if not p.exists():
        return Profile()
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read(p, encoding="utf-8")
        return Profile.from_dict(json.loads(ini["desktop"]["profile"]))
    except (ValueError, KeyError, TypeError, configparser.Error) as exc:
        raise PauseRequired(f"配置文件损坏，请保留备份并重新校准：{p}") from exc


def save_profile(root: Path, profile: Profile) -> None:
    # ConfigParser is retained from upstream; credentials never enter the file.
    ini = configparser.ConfigParser(interpolation=None)
    ini["desktop"] = {"profile": json.dumps(profile.to_dict(), ensure_ascii=False)}
    tmp = root / "config.ini.tmp"
    with tmp.open("w", encoding="utf-8") as stream:
        ini.write(stream)
    tmp.replace(root / "config.ini")


class CredentialStore:
    service = "AutovisorDesktop.OnlineAI"

    def get(self) -> str:
        import keyring
        return keyring.get_password(self.service, "api-key") or ""

    def set(self, value: str) -> None:
        import keyring
        if value:
            keyring.set_password(self.service, "api-key", value)
        elif self.get():
            keyring.delete_password(self.service, "api-key")


class Journal:
    """Durable checkpoints; pending submissions are never retried blindly."""

    def __init__(self, path: Path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS quiz(id TEXT PRIMARY KEY, total INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'working');
        CREATE TABLE IF NOT EXISTS answer(quiz TEXT, idx INTEGER, qkey TEXT, payload TEXT, verified INTEGER DEFAULT 0,
          PRIMARY KEY(quiz,idx));
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS course_task(course TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(course,id));
        CREATE TABLE IF NOT EXISTS lesson_progress(course TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(course,id));
        CREATE TABLE IF NOT EXISTS course_session(course TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)
        self.db.commit()

    def begin(self, quiz: str, total: int):
        with self.lock, self.db:
            old = self.db.execute("SELECT total FROM quiz WHERE id=?", (quiz,)).fetchone()
            if old and old[0] != total:
                raise PauseRequired("同一作业的题目总数发生变化，请检查后清除该作业记录。")
            self.db.execute("INSERT OR IGNORE INTO quiz(id,total) VALUES(?,?)", (quiz, total))
            if quiz.startswith("chapter:"):
                self.db.execute("INSERT OR REPLACE INTO meta VALUES('active',?)", (quiz,))

    def active(self) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key='active'").fetchone()
        return row[0] if row else None

    def record(self, quiz: str, index: int, qkey: str, answer: Answer, verified: bool):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO answer VALUES(?,?,?,?,?)", (quiz, index, qkey, json.dumps(answer.__dict__, ensure_ascii=False), int(verified)))

    def answer(self, quiz: str, index: int, qkey: str) -> tuple[Answer, bool] | None:
        row = self.db.execute("SELECT qkey,payload,verified FROM answer WHERE quiz=? AND idx=?", (quiz, index)).fetchone()
        if not row or row[0] != qkey:
            return None
        return Answer(**json.loads(row[1])), bool(row[2])

    def complete(self, quiz: str) -> bool:
        row = self.db.execute("SELECT total FROM quiz WHERE id=?", (quiz,)).fetchone()
        if not row:
            return False
        actual = {r[0] for r in self.db.execute("SELECT idx FROM answer WHERE quiz=? AND verified=1", (quiz,))}
        return actual == set(range(1, row[0] + 1))

    def verified_indices(self,quiz: str) -> list[int]:
        with self.lock:
            return [r[0] for r in self.db.execute('SELECT idx FROM answer WHERE quiz=? AND verified=1 ORDER BY idx',(quiz,))]

    def state(self, quiz: str) -> str:
        row = self.db.execute("SELECT state FROM quiz WHERE id=?", (quiz,)).fetchone()
        return row[0] if row else "working"

    def transition(self, quiz: str, old: str, new: str) -> bool:
        with self.lock, self.db:
            return self.db.execute("UPDATE quiz SET state=? WHERE id=? AND state=?", (new, quiz, old)).rowcount == 1

    def reset(self):
        with self.lock, self.db:
            self.db.executescript("DELETE FROM answer; DELETE FROM quiz; DELETE FROM meta; DELETE FROM course_task; DELETE FROM lesson_progress; DELETE FROM course_session;")

    def course_tasks(self, course: str) -> list[CourseTask]:
        with self.lock:
            return sorted((CourseTask(**json.loads(r[0])) for r in self.db.execute('SELECT payload FROM course_task WHERE course=?',(course,))),key=lambda t:t.order)

    def save_task(self, course: str, task: CourseTask):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO course_task VALUES(?,?,?)',(course,task.task_id,json.dumps(asdict(task),ensure_ascii=False)))

    def course_session(self, course: str) -> dict:
        with self.lock:
            row=self.db.execute('SELECT payload FROM course_session WHERE course=?',(course,)).fetchone()
            return json.loads(row[0]) if row else {}

    def save_session(self, course: str, session: dict):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO course_session VALUES(?,?)',(course,json.dumps(session,ensure_ascii=False)))

    def lesson(self, course: str, task: str) -> LessonProgress:
        with self.lock:
            row=self.db.execute('SELECT payload FROM lesson_progress WHERE course=? AND id=?',(course,task)).fetchone()
            return LessonProgress(**json.loads(row[0])) if row else LessonProgress(task)

    def save_lesson(self, course: str, progress: LessonProgress):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO lesson_progress VALUES(?,?,?)',(course,progress.task_id,json.dumps(asdict(progress))))

    def close(self):
        self.db.close()
