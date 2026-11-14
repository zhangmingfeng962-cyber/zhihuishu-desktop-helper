"""Offline, clearly labelled test fixture. No course account or online AI is used."""
from __future__ import annotations
import tkinter as tk
from .domain import Answer, Question


class DemoAI:
    def solve(self, question: Question) -> Answer:
        values = {1: ["B"], 2: ["A", "B"], 3: ["A"], 4: ["北京", "2"]}
        return Answer(values[question.index], "离线演示固定答案，仅用于验证操作流程。")


class Demo:
    def __init__(self, root: tk.Toplevel | tk.Tk):
        self.root = root
        root.title("Autovisor 流程演示 — 离线测试，不是真实课程")
        root.geometry("1100x760+50+40")
        root.configure(bg="#edf2f7")
        self.index = 1
        self.responses = {}
        self.submit_count = 0
        self.content = tk.Frame(root, bg="white", padx=48, pady=30)
        self.content.pack(fill="both", expand=True, padx=24, pady=24)
        self.show()

    def text(self, value, size=15, bold=False):
        label = tk.Label(self.content, text=value, font=("Microsoft YaHei UI", size, "bold" if bold else "normal"),
                         bg="white", fg="#172b4d", anchor="w", justify="left", wraplength=900)
        label.pack(fill="x", pady=8)
        return label

    def clear(self):
        for child in self.content.winfo_children():
            child.destroy()

    def button(self, text, command):
        tk.Button(self.content, text=text, command=command, font=("Microsoft YaHei UI", 13),
                  bg="#1d4ed8", fg="white", padx=24, pady=10, relief="flat").pack(anchor="e", pady=20)

    def show(self):
        self.clear()
        self.text("离线流程演示 / 章节练习", 20, True)
        self.text(f"第 {self.index} 题    共 4 题", 14)
        kinds = {1: "单选题", 2: "多选题", 3: "判断题", 4: "填空题"}
        self.text(kinds[self.index], 16, True)
        stems = {1: "1 + 1 等于几？", 2: "小于 3 的正整数有哪些？", 3: "地球是一颗行星。", 4: "中华人民共和国的首都是第一个空，1 + 1 的结果是第二个空。"}
        self.text(stems[self.index], 16)
        if self.index < 4:
            labels = ["1", "2", "3"] if self.index < 3 else ["正确", "错误"]
            self.selected = set(self.responses.get(self.index, []))
            self.canvases = {}
            for n, text in enumerate(labels):
                label = chr(65+n)
                canvas = tk.Canvas(self.content, height=56, bg="white", highlightthickness=0)
                canvas.pack(fill="x", pady=5)
                canvas.create_text(60, 28, text=f"{label}. {text}", anchor="w", fill="#172b4d", font=("Microsoft YaHei UI", 16))
                self.canvases[label] = canvas
                canvas.bind("<Button-1>", lambda event, value=label: self.choose(value))
            self.paint()
        else:
            self.entries = []
            for i in range(2):
                self.text(f"第 {i+1} 空", 13)
                frame = tk.Frame(self.content, bg="#555f70", padx=1, pady=1)
                frame.pack(anchor="w", pady=5)
                entry = tk.Entry(frame, font=("Microsoft YaHei UI", 17), width=34, relief="flat", bg="white", fg="#172b4d",
                                 highlightthickness=2, highlightbackground="white", highlightcolor="#2563eb")
                entry.pack(ipady=6)
                if self.responses.get(4):
                    entry.insert(0, self.responses[4][i])
                self.entries.append(entry)
        self.button("下一题" if self.index < 4 else "提交作业", self.next if self.index < 4 else self.confirm)

    def paint(self):
        for label, canvas in self.canvases.items():
            canvas.delete("control")
            draw = canvas.create_rectangle if self.index == 2 else canvas.create_oval
            draw(13, 16, 37, 40, fill="#2563eb" if label in self.selected else "white", outline="#64748b", width=2, tags="control")

    def choose(self, label):
        if self.index == 2:
            self.selected.symmetric_difference_update({label})
        else:
            self.selected = {label}
        self.responses[self.index] = sorted(self.selected)
        self.paint()

    def next(self):
        self.responses[self.index] = sorted(self.selected)
        self.index += 1
        self.show()

    def confirm(self):
        self.responses[4] = [entry.get() for entry in self.entries]
        self.clear()
        self.text("确认提交作业", 22, True)
        if len(self.responses) != 4 or any(not values or any(not v for v in values) for values in self.responses.values()):
            self.text("还有未完成题目，请返回检查。")
            self.button("返回检查", self.show)
            return
        self.text("提交后无法修改，是否提交？", 17)
        self.button("确认提交", self.submit)

    def submit(self):
        self.submit_count += 1
        self.clear()
        self.text("提交成功", 26, True)
        expected = {1: ["B"], 2: ["A", "B"], 3: ["A"], 4: ["北京", "2"]}
        score = sum(self.responses.get(k) == v for k, v in expected.items()) * 25
        self.text(f"本次得分：{score} / 100", 20)
        self.text(f"演示提交次数：{self.submit_count}。这不是智慧树实测结果。", 14)


def main():
    from .windows import enable_dpi
    enable_dpi()
    root = tk.Tk()
    Demo(root)
    root.mainloop()


if __name__ == "__main__":
    main()
