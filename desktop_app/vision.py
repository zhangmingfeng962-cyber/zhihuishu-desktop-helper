from __future__ import annotations

import re
import os
from dataclasses import dataclass
import cv2
import numpy as np
from PIL import Image
from .domain import Rect, Span, Option, Question, View, Profile, PauseRequired, normalized, fingerprint


BUTTONS = {
    "next": {"下一题", "保存并下一题", "下一步"},
    "previous": {"上一题"},
    "save": {"保存", "保存答案"},
    "submit": {"提交作业", "提交试卷", "交卷", "提交答案"},
    "confirm": {"确定", "确认", "确认提交", "确定提交", "确认交卷"},
    "return": {"返回课程", "返回学习", "继续学习", "返回目录"},
    "start_quiz": {"开始答题", "去答题", "进入测试", "开始测试"},
    "play": {"播放", "继续播放", "恢复播放"},
    "popup_done": {"知道了", "完成答题", "继续学习", "关闭"},
}


def overlap(a: Rect, b: Rect) -> float:
    w = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    h = max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    return w * h / max(1, min(a.w * a.h, b.w * b.h))


def reading_order(rects: list[Rect]) -> list[Rect]:
    rows: list[list[Rect]] = []
    for rect in sorted(rects, key=lambda r: (r.y, r.x)):
        match = next((row for row in rows if abs(row[0].center[1] - rect.center[1]) < min(row[0].h, rect.h) * .5), None)
        if match is None:
            rows.append([rect])
        else:
            match.append(rect)
    return [r for row in rows for r in sorted(row, key=lambda r: r.x)]


def controls(image: Image.Image, region: Rect, min_width: int, max_width: int, max_height=90) -> list[Rect]:
    crop = np.asarray(image.crop(region.bounds).convert("RGB"))
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 45, 150)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    found: list[Rect] = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if not min_width <= w <= max_width or not (12 if max_width<=45 else 18) <= h <= max_height:
            continue
        if cv2.arcLength(c, True) < .55 * (w + h) * 2:
            continue
        if max_width <= 45 and cv2.contourArea(c) < .52*w*h:
            continue
        if max_width > 45:
            patch = edges[y:y+h,x:x+w]
            coverage = [np.mean(np.any(patch[:3,:]>0,axis=0)),np.mean(np.any(patch[-3:,:]>0,axis=0)),
                        np.mean(np.any(patch[:,:3]>0,axis=1)),np.mean(np.any(patch[:,-3:]>0,axis=1))]
            if min(coverage)<.75:
                continue
        rect = Rect(x + region.x, y + region.y, w, h)
        if not any(overlap(rect, r) > .85 for r in found):
            found.append(rect)
    return reading_order(found)


def selection_state(image: Image.Image, rect: Rect | None) -> bool | None:
    if rect is None:
        return None
    # Ignore outlines: unchecked blue rings are not selected controls.
    pad = max(3, min(rect.w, rect.h) // 4)
    inner = Rect(rect.x + pad, rect.y + pad, max(1, rect.w - 2 * pad), max(1, rect.h - 2 * pad))
    rgb = np.asarray(image.crop(inner.bounds).convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    blue = (hsv[..., 0] >= 85) & (hsv[..., 0] <= 135) & (hsv[..., 1] > 75) & (hsv[..., 2] > 70)
    green = (hsv[..., 0] >= 35) & (hsv[..., 0] <= 85) & (hsv[..., 1] > 80)
    if np.mean(blue | green) > .13:
        return True
    if np.mean(hsv[..., 1] < 55) > .65:
        return False
    return None


class LocalOCR:
    def __init__(self):
        self.engine = None

    def read(self, image: Image.Image, region: Rect | None = None) -> list[Span]:
        if self.engine is None:
            try:
                from rapidocr import RapidOCR
                self.engine = RapidOCR(params={"Global.text_score": .60, "Global.use_cls": False,
                    "EngineConfig.onnxruntime.intra_op_num_threads": min(2,os.cpu_count() or 1),
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1})
            except Exception as exc:
                raise PauseRequired("OCR 初始化失败，请检查模型文件及运行日志。首次启动需要下载模型。") from exc
        region = region or Rect(0, 0, *image.size)
        if region.h > 1800:
            merged = []
            for y in range(region.y,region.y+region.h,1280):
                tile=Rect(region.x,y,region.w,min(1440,region.y+region.h-y))
                for span in self.read(image,tile):
                    if not any(normalized(span.text)==normalized(old.text) and overlap(span.box,old.box)>.5 for old in merged):
                        merged.append(span)
            return sorted(merged,key=lambda s:(s.box.y,s.box.x))
        crop = image.crop(region.bounds).convert("RGB")
        scale = min(1., 1800 / max(crop.size))
        if scale < 1:
            crop = crop.resize((round(crop.width * scale), round(crop.height * scale)))
        try:
            result = self.engine(cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2BGR))
        except Exception as exc:
            raise PauseRequired("OCR 识别失败，请检查模型文件或重新启动。") from exc
        if result.boxes is None or result.txts is None:
            return []
        spans = []
        for points, text, score in zip(result.boxes, result.txts, result.scores):
            points = np.asarray(points) / scale
            x, y = np.floor(points.min(axis=0)).astype(int)
            right, bottom = np.ceil(points.max(axis=0)).astype(int)
            spans.append(Span(text, Rect(int(x) + region.x, int(y) + region.y, int(right - x), int(bottom - y)), float(score)))
        return sorted(spans, key=lambda s: (s.box.y, s.box.x))


class Perception:
    def __init__(self, ocr: LocalOCR):
        self.ocr = ocr

    def video_spans(self,image,profile):
        player=profile.regions.get('player')
        if not player:return self.ocr.read(image)
        regions=[Rect(0,0,image.width,min(image.height,player.y+65)),
                 Rect(player.x,max(player.y,player.y+player.h-170),player.w,min(170,player.h))]
        popup=profile.regions.get('popup')
        if popup:
            rgb=np.asarray(image.crop(popup.bounds).convert('RGB'))
            # A white modal covers the lecture image. Avoid repeatedly OCRing
            # hundreds of words inside the lecture while no popup is present.
            white=np.all(rgb>225,axis=2)&((rgb.max(axis=2)-rgb.min(axis=2))<30)
            if np.mean(white)>.55:regions.append(popup)
        spans=[]
        for region in regions:
            for span in self.ocr.read(image,region):
                if not any(normalized(span.text)==normalized(old.text) and overlap(span.box,old.box)>.6 for old in spans):
                    spans.append(span)
        return sorted(spans,key=lambda s:(s.box.y,s.box.x))

    def inspect(self, image: Image.Image, profile: Profile, spans: list[Span] | None = None, scope_hint: str = 'auto') -> View:
        spans = (self.video_spans(image,profile) if scope_hint=='video' else self.ocr.read(image)) if spans is None else spans
        view = View(spans)
        text = normalized("\n".join(s.text for s in spans))
        header=normalized(''.join(s.text for s in spans if s.box.y<max(220,image.height*.2)))
        guard = re.search(r"异常脚本|异常学习行为|安全验证|人机验证|身份验证|请输入验证码|请完成验证|人脸核验", text) or re.search(r'期末考试|考试须知|监考',header)
        if guard:
            view.blocked = "检测到“" + guard.group() + "”，请在浏览器检查；程序已暂停。"
            return view
        view.unfinished = bool(re.search(r"还有.*(?:未答|未完成)|存在未作答|尚未完成|漏答", text))
        popup = profile.regions.get('popup')
        popup_text = normalized(''.join(s.text for s in spans if popup and popup.contains(s.box)))
        # A graded video popup can retain all options. It is a result, not a new
        # question. Restrict this signal and its close control to that popup.
        view.popup_feedback = scope_hint != 'chapter' and bool(re.search(r'回答正确|回答错误|答案正确|答案错误|正确答案[:：]',popup_text))
        view.confirmation = bool(re.search(r"确认提交|确定交卷|确认交卷|是否提交|确定要提交|提交后.*(?:修改|撤回)|确认要提交", text))
        for name, labels in BUTTONS.items():
            candidates = [s for s in spans if normalized(s.text).strip("。！!？?：:") in labels and s.score >= .65]
            if name == 'popup_done':
                candidates = [s for s in candidates if popup and popup.contains(s.box)]
            if name == "confirm" and view.confirmation and profile.regions.get("dialog"):
                candidates = [s for s in candidates if profile.regions["dialog"].contains(s.box)]
            if candidates:
                view.buttons[name] = max(candidates, key=lambda s: s.box.y).box
        view.tab_titles=[s.text for s in spans if s.box.y<85 and re.search(r'课程学习|作业|在线学堂|单元测试|章节测试',s.text) and len(s.text)<65]
        tab=next((s for s in spans if '课程学习' in s.text and ('智慧树' in s.text or '知到' in s.text) and s.box.y<85),None)
        if tab:
            view.buttons['course_tab']=tab.box
        content_text=normalized(''.join(s.text for s in spans if s.box.y>max(100,image.height*.08)))
        if re.search(r'本题的得分|作业结果|答题结果',content_text) and 'submit' not in view.buttons:
            view.submitted = True
            tab = next((s for s in spans if '课程学习' in s.text and s.box.y<85),None)
            if tab:
                view.buttons['course_tab']=tab.box
            return view
        for scope, region_name in (("popup", "popup"), ("chapter", "question")):
            if scope_hint=='video' and scope!='popup' or scope_hint=='chapter' and scope!='chapter':
                continue
            region = profile.regions.get(region_name)
            if not region:
                continue
            if scope == 'popup' and view.popup_feedback:
                break
            question = self.question(image, spans, region, profile, scope)
            if question:
                view.question = question
                break
        view.submitted = view.question is None and bool(re.search(r"提交成功|已成功提交|作业已提交|作答完成|本次得分|提交时间[:：]|已提交作业", text))
        times = re.findall(r"(\d{1,2}:\d{2}(?::\d{2})?)/(\d{1,2}:\d{2}(?::\d{2})?)", text)
        player = profile.regions.get("player")
        if player:
            player_text = normalized("".join(s.text for s in spans if player.contains(s.box)))
            times = re.findall(r"(\d{1,2}:\d{2}(?::\d{2})?)/(\d{1,2}:\d{2}(?::\d{2})?)", player_text)
            if times:
                def seconds(value):
                    result=0
                    for piece in value.split(':'):
                        result=result*60+int(piece)
                    return result
                view.video_position=seconds(times[-1][0])
                view.video_duration=seconds(times[-1][1])
            codes=[re.search(r'(?<!\d)(\d+\.\d+(?:\.\d+)*)(?:[、.．\s]|[^\d.:])',s.text) for s in spans if not profile.regions.get('catalog',Rect(0,0,0,0)).contains(s.box) and max(90,player.y-110)<=s.box.y<player.y+65]
            view.video_lesson=next((m[1] for m in codes if m),'')
            view.video_visible = bool(times) or 'play' in view.buttons
            view.video_finished = any(a == b and a not in {"00:00", "00:00:00", "0:00"} for a, b in times)
            view.video_finished |= "播放结束" in player_text or "重新播放" in player_text
            view.video_paused = bool(re.search(r"^(?:播放|继续播放|恢复播放)$", player_text)) or "play" in view.buttons
            view.video_playing=any(normalized(s.text) in {'暂停','暂停播放','正在播放'} for s in spans if player.contains(s.box))
            if view.video_playing:
                view.video_paused=False
            if not view.video_finished and not view.video_playing and "play" not in view.buttons:
                triangle = self.play_triangle(image, player)
                if triangle:
                    view.buttons["play"] = triangle
                    view.video_paused = True
        return view

    @staticmethod
    def play_triangle(image: Image.Image, player: Rect) -> Rect | None:
        # Only the bottom-left player control. Never infer play from an entire video.
        # The calibrated player can include a bottom letterbox. The actual
        # control bar may be 100+ pixels above that region's bottom edge.
        region = Rect(player.x, max(player.y, player.y + player.h - 220), min(95, player.w), min(220, player.h))
        gray = cv2.cvtColor(np.asarray(image.crop(region.bounds).convert("RGB")), cv2.COLOR_RGB2GRAY)
        _, binary = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            polygon = cv2.approxPolyDP(c, .05 * cv2.arcLength(c, True), True)
            if len(polygon) == 3 and 10 <= w <= 34 and 12 <= h <= 40:
                pts = polygon[:, 0, :]
                if np.count_nonzero(pts[:, 0] < x + w * .4) == 2:
                    return Rect(x + region.x, y + region.y, w, h)
        return None

    def question(self, image: Image.Image, spans: list[Span], region: Rect, profile: Profile, scope: str) -> Question | None:
        relevant = [s for s in spans if region.contains(s.box)]
        text = "\n".join(s.text for s in relevant)
        kinds = {"单选题": "single", "多选题": "multiple", "判断题": "judge", "填空题": "blank"}
        hits = [(s, name, kind) for s in relevant for name, kind in kinds.items() if name in normalized(s.text)]
        if not hits:
            return None
        if len(hits) > 1:
            raise PauseRequired("题目区域出现多道题，请把题目区域校准为当前单题面板。")
        heading, _, kind = hits[0]
        if heading.score < .70:
            raise PauseRequired("题型识别置信度不足，请调整页面缩放后重新预览。")
        index_match = re.search(r"第\s*(\d+)\s*题|(?:^|\n)\s*(\d+)\s*(?:[.、．]|[【\[(（]?(?:单选题|多选题|判断题|填空题))|题号\s*[:：]?\s*(\d+)", text)
        index = next((int(v) for v in index_match.groups() if v), 1) if index_match else 0
        if not index:
            nearby=[s for s in relevant if re.fullmatch(r'\d{1,3}',s.text.strip()) and s.box.x<=heading.box.x+15 and abs(s.box.center[1]-heading.box.center[1])<45]
            if len(nearby)==1:
                index=int(nearby[0].text.strip())
        all_text = '|'.join(normalized(s.text) for s in spans)
        totals={int(v) for hit in re.findall(r"共[|]?(\d+)[|]?题|(?:题目总数|总题数|题目数)[:：|]*(\d+)|第\d+题[|]?[/／][|]?(\d+)",all_text) for v in hit if v}
        if len(totals)>1:
            raise PauseRequired('画面上的题目总数不一致，请检查是否存在多个部分或多个作业。')
        total=next(iter(totals),None)
        if scope == "popup":
            index, total = 1, 1
        button_words = {word for labels in BUTTONS.values() for word in labels}
        body = [s for s in relevant if s.box.y >= heading.box.y-3 and normalized(s.text) not in button_words and not re.fullmatch(r"共\d+题", normalized(s.text))
                and not re.fullmatch(r"[■□☐☑✓✔√●○◎◉]+", normalized(s.text))]
        # OCR can merge an empty radio ring into the label as 'O A.' / 'OB.'.
        # Canonicalize only a decoration immediately preceding a valid label.
        body = [Span(re.sub(r'^\s*[O0o○●□■☐☑✓✔√◎◉]+\s*(?=[A-Z]\s*[.、．:：)）])','',s.text),s.box,s.score) for s in body]
        if kind == "blank":
            boxes = controls(image, region, min_width=65, max_width=max(65, region.w - 10), max_height=85)
            boxes = [b for b in boxes if b.center[1] > heading.box.y + heading.box.h and not any(normalized(s.text) in button_words and overlap(s.box, b) > .5 for s in relevant)]
            # A calibrated override is tied to the visible heading/stem, not a global coordinate list.
            stem_lines = [s.text for s in body if s.box.y > heading.box.y+heading.box.h*.7 and not re.fullmatch(r'第\d+空',normalized(s.text)) and not any(overlap(s.box,b)>.6 for b in boxes)]
            stem = heading.text + (stem_lines[0] if stem_lines else '')
            override_key = fingerprint(str(index) + stem)
            boxes = profile.blank_overrides.get(override_key, boxes)
            body = [s for s in body if not any(overlap(s.box,b)>.6 for b in boxes)]
            question = Question(kind, "\n".join(s.text for s in body), index, total, scope, blanks=reading_order(boxes))
            question.override_key = override_key
            return question
        options: list[Option] = []
        small = controls(image, region, 14, 45, max_height=45)
        option_spans = []
        for span in body:
            marker = re.match(r"^\s*([A-Z])\s*[.、．:：)）\s]\s*(.*)$", span.text)
            if marker:
                option_spans.append((span, marker[1], marker[2]))
            elif kind == "judge" and normalized(span.text) in {"对", "错", "正确", "错误", "是", "否"}:
                option_spans.append((span, chr(65 + len(option_spans)), span.text))
        control_pool=[]
        for span,_,_ in option_spans:
            candidates=[c for c in small if .70<=c.w/c.h<=1.4 and abs(c.center[1]-span.box.center[1])<18 and span.box.x-65<=c.center[0]<=span.box.x+35]
            if candidates:
                control_pool.append(min(candidates,key=lambda c:c.x))
        labels=[item[0] for item in option_spans]
        body=[s for s in body if s in labels or not (len(normalized(s.text))<=2 and any(overlap(s.box,c)>.55 for c in control_pool))]
        for n, (span, label, tail) in enumerate(option_spans):
            next_y = option_spans[n + 1][0].box.y if n + 1 < len(option_spans) else region.y + region.h
            continuations = [s.text for s in body if s.box.y > span.box.y + span.box.h * .6 and s.box.y < next_y - 3 and span.box.x <= s.box.x <= span.box.x+100 and not any(name in s.text for name in kinds)]
            candidates = [c for c in small if .70 <= c.w/c.h <= 1.4 and abs(c.center[1] - span.box.center[1]) < 18 and span.box.x - 65 <= c.center[0] <= span.box.x + 35]
            # Prefer the control LEFT of the text, not the dark-blue glyph 'A'.
            control = min(candidates, key=lambda c: c.x, default=None)
            options.append(Option(label, tail + " ".join(continuations), span.box, control, selection_state(image, control)))
        first_option_y=min((item[0].box.y for item in option_spans),default=region.y+region.h)
        stem_text='\n'.join(s.text for s in body if s.box.y<first_option_y-3)
        known=[o.control for o in options if o.control]
        complete=True
        if known:
            bottom=min((s.box.y for s in relevant if normalized(s.text) in button_words and s.box.y>first_option_y),default=region.y+region.h)
            for candidate in small:
                if not .70<=candidate.w/candidate.h<=1.4 or candidate.center[1]<first_option_y-15 or candidate.center[1]>=bottom:
                    continue
                if any(overlap(candidate,s.box)>.45 for s in labels):
                    continue
                if any(abs(candidate.center[0]-control.center[0])<10 for control in known) and not any(abs(candidate.center[1]-control.center[1])<14 for control in known):
                    complete=False
        return Question(kind, stem_text, index, total, scope, options=options,option_rows_complete=complete)

    def field_text(self, image: Image.Image, rect: Rect) -> str:
        inner = Rect(rect.x + 3, rect.y + 3, max(1, rect.w - 6), max(1, rect.h - 6))
        return "".join(s.text for s in self.ocr.read(image, inner))

    def catalog_rows(self, image: Image.Image, profile: Profile, spans: list[Span]) -> list[tuple[str, Rect, bool]]:
        region = profile.regions.get("catalog")
        if not region:
            return []
        times = [s for s in spans if region.contains(s.box) and re.search(r"\d{1,2}:\d{2}", s.text)]
        rows = []
        rgb = np.asarray(image.convert("RGB"))
        for span in times:
            titles = [s for s in spans if region.contains(s.box) and span.box.y - 60 <= s.box.y < span.box.y - 3 and not re.search(r"\d{1,2}:\d{2}", s.text)]
            title = max(titles, key=lambda s: s.box.y, default=span)
            band = Rect(max(region.x, region.x + region.w - 80), max(region.y, title.box.y - 8), min(80, region.w), min(65, region.y + region.h - max(region.y, title.box.y - 8)))
            hsv = cv2.cvtColor(rgb[band.y:band.y+band.h, band.x:band.x+band.w], cv2.COLOR_RGB2HSV)
            blue = ((hsv[..., 0] > 85) & (hsv[..., 0] < 135) & (hsv[..., 1] > 95)).astype("uint8") * 255
            components, _ = cv2.findContours(blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            done = any(12 <= cv2.boundingRect(c)[2] <= 40 and 12 <= cv2.boundingRect(c)[3] <= 40 and cv2.contourArea(c) > 90 for c in components)
            rows.append((title.text, title.box, done))
        return rows

    def catalog_tests(self,image: Image.Image,profile: Profile,spans: list[Span]) -> list[tuple[Rect,bool]]:
        region=profile.regions.get('catalog')
        if not region:
            return []
        result=[]
        for span in spans:
            if not region.contains(span.box) or not re.fullmatch(r'(?:平时测试|章节测试|章测试|第.{1,5}章测试)',normalized(span.text)):
                continue
            band=Rect(max(region.x,region.x+region.w-80),max(region.y,span.box.y-10),min(80,region.w),min(65,region.y+region.h-max(region.y,span.box.y-10)))
            rgb=np.asarray(image.crop(band.bounds).convert('RGB'))
            hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
            blue=((hsv[...,0]>85)&(hsv[...,0]<135)&(hsv[...,1]>95)).astype('uint8')*255
            cs,_=cv2.findContours(blue,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            done=any(12<=cv2.boundingRect(c)[2]<=40 and 12<=cv2.boundingRect(c)[3]<=40 and cv2.contourArea(c)>90 for c in cs)
            result.append((span.box,done))
        return result
