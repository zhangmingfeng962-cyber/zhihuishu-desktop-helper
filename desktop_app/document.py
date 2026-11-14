"""Bounded visual scrolling and document coordinates; no DOM or browser protocol."""
from __future__ import annotations
import cv2
import numpy as np
from PIL import Image
from .domain import Rect, Profile, PauseRequired


def vertical_shift(before: Image.Image, after: Image.Image) -> int:
    """Find overlap in the middle strip, avoiding fixed headers and footers."""
    a = cv2.cvtColor(np.asarray(before.convert('RGB')), cv2.COLOR_RGB2GRAY)
    b = cv2.cvtColor(np.asarray(after.convert('RGB')), cv2.COLOR_RGB2GRAY)
    if a.shape != b.shape:
        raise PauseRequired("滚动时题目区域尺寸变化。")
    h, w = a.shape
    y, length = h // 4, max(35, h // 4)
    left, right = min(15,w//10), max(min(15,w//10)+10,w-20)
    needle = b[y:y+length,left:right]
    if np.std(needle) < 5:
        raise PauseRequired("长题滚动缺少可定位的文字或图形，请缩小题目区域后重试。")
    result = cv2.matchTemplate(a[:,left:right],needle,cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(result)
    shift = loc[1]-y
    if score < .87 or abs(shift) < 6 or abs(shift) > h*.65:
        raise PauseRequired("无法可靠确认题目滚动偏移，已暂停以避免坐标错位。")
    return shift


class QuestionDocument:
    def __init__(self, desktop, vision, profile, seed, region):
        self.desktop,self.vision,self.profile,self.seed,self.region=desktop,vision,profile,seed,region
        self.offset=0
        self.question=None

    @classmethod
    def collect(cls, desktop, vision, profile, frame, seed):
        if seed.scope!='chapter' or 'question' not in profile.regions:
            return None
        region=profile.regions['question']
        doc=cls(desktop,vision,profile,seed,region)
        first=frame.crop(region.bounds)
        chunks=[(0,first)]
        previous=first
        for _ in range(14):
            desktop.scroll(-2,region)
            image=desktop.capture()
            current=image.crop(region.bounds)
            if np.mean(np.abs(np.asarray(previous,dtype=float)-np.asarray(current,dtype=float)))<1.5:
                break
            shift=vertical_shift(previous,current)
            if shift<=0:
                raise PauseRequired('题目滚动方向与预期不一致，请重新校准题目区域。')
            doc.offset+=shift
            chunks.append((doc.offset,current))
            previous=current
        else:
            raise PauseRequired('题目超过可扫描长度，请人工检查页面布局。')
        if len(chunks)==1:
            return None
        height=chunks[-1][0]+region.h
        canvas=Image.new('RGB',(region.w,height),'white')
        canvas.paste(first,(0,0))
        previous_offset=0
        for offset,image in chunks[1:]:
            delta=offset-previous_offset
            canvas.paste(image.crop((0,region.h-delta,region.w,region.h)),(0,offset+region.h-delta))
            previous_offset=offset
        spans=vision.ocr.read(canvas)
        doc.image=canvas
        temporary=Profile()
        q=vision.question(canvas,spans,Rect(0,0,canvas.width,canvas.height),temporary,'chapter')
        if not q:
            raise PauseRequired('长题拼接后未能识别题型。')
        q.index,q.total=seed.index,seed.total
        doc.question=q
        doc.top()
        return doc

    def move(self, amount):
        before=self.desktop.capture().crop(self.region.bounds)
        self.desktop.scroll(amount,self.region)
        after=self.desktop.capture().crop(self.region.bounds)
        if np.mean(np.abs(np.asarray(before,dtype=float)-np.asarray(after,dtype=float)))<1.5:
            return False
        self.offset=max(0,self.offset+vertical_shift(before,after))
        return True

    def top(self):
        for _ in range(35):
            if self.offset<=3:
                self.offset=0
                return
            if not self.move(2):
                self.offset=0
                return
        raise PauseRequired('未能回到题目顶部。')

    def locate(self, rect: Rect) -> Rect:
        for _ in range(35):
            if rect.y>=self.offset+5 and rect.y+rect.h<=self.offset+self.region.h-5:
                return rect.moved(self.region.x,self.region.y-self.offset)
            amount=-2 if rect.y+rect.h>self.offset+self.region.h-5 else 2
            if not self.move(amount):
                break
        raise PauseRequired('滚动后未找到完整可见的答案控件，请检查题目区域。')
