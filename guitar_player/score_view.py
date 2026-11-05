"""Reusable score and interval index for an unchanged editor document."""
from bisect import bisect_left,bisect_right


class ScoreView:
    def __init__(self,document):
        self.score=document.score()
        # score() can normalize ordering and extend the final measure.
        self.state=document.snapshot();self.tempos=dict(self.score.tempo_map)
        self.cells=tuple(document.cells);self.starts=tuple(c.start for c in self.cells)
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


def score_view(editor,document,slot='single'):
    cache=getattr(editor,'_score_views',None)
    if cache is None:cache=editor._score_views={}
    view=cache.get(slot)
    if view is None or view.state!=document.snapshot():cache[slot]=view=ScoreView(document)
    return view
