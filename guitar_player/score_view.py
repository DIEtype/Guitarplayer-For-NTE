"""Reusable score and interval index for an unchanged editor document."""
from bisect import bisect_left,bisect_right


class ScoreView:
    def __init__(self,document):
        self.score=document.score()
        # score() can normalize ordering and extend the final measure.
        self.state=document.snapshot();self.tempos=dict(self.score.tempo_map)
        self.cells=tuple(document.cells);self.starts=tuple(c.start for c in self.cells)
        self.marks=tuple(sorted(getattr(document,'chord_changes',{}).items()))
        self.mark_starts=tuple(at for at,_ in self.marks)
        ends=[];end=0
        for cell in self.cells:end=max(end,cell.end);ends.append(end)
        self.ends=tuple(ends)

    def between(self,start,end):
        stop=bisect_left(self.starts,end);first=bisect_right(self.ends,start,0,stop)
        return tuple(c for c in self.cells[first:stop] if c.end>start)

    def rests(self,start,end):
        cursor=start;result=[]
        for cell in self.between(start,end):
            if cursor<cell.start:result.append((cursor,cell.start-cursor))
            cursor=max(cursor,min(end,cell.end))
        if cursor<end:result.append((cursor,end-cursor))
        return result

    def chord_marks(self,start,end):
        first=bisect_left(self.mark_starts,start);stop=bisect_left(self.mark_starts,end)
        prior=self.marks[first-1][1] if first else None
        return prior,self.marks[first:stop]

    def bar_key(self,document,index,selection):
        bar=document.bar(index);start,end=bar.start,bar.end
        selected=selection is not None and selection[0]<=index<=selection[1]
        repeat=index in getattr(document,'measure_repeats',())
        sections=tuple(section for section in getattr(document,'repeat_sections',()) if index in section[:2])
        return (bar,self.between(start,end),self.chord_marks(start,end),document.bar_changes.get(index),
                getattr(document,'tuning',None),document.capo,document.default_timbre,document.bpm,
                self.score.tempo_map,self.tempos.get(start),selected,repeat,sections)


def score_view(editor,document,slot='single'):
    cache=getattr(editor,'_score_views',None)
    if cache is None:cache=editor._score_views={}
    view=cache.get(slot)
    if view is None or view.state!=document.snapshot():cache[slot]=view=ScoreView(document)
    return view
