"""Structured first blockers for each key, without treating one key as all keys."""
from collections import Counter

from .score import pitch_name

CAUSE_NAMES={'range_high':'高音超界','range_low':'低音超界','range_span':'高低音同时超界',
             'protected_conflict':'同拍指定音无法齐奏','sweep':'扫弦无等音按法',
             'bm_sweep':'Bm 异常弦规避','other':'其他限制'}


class TranslationFailure(ValueError):
    def __init__(self,message,detail):
        super().__init__(message);self.detail=dict(detail)


def event_failure(score,event,protected,sweep=False,bm=False):
    from .guitar import event_position
    pitches=sorted(protected)
    low=any(p<40 for p in pitches);high=any(p>88 for p in pitches)
    kind=('bm_sweep' if bm else 'sweep') if sweep else (
        'range_span' if low and high else 'range_low' if low else 'range_high' if high else 'protected_conflict')
    position=event_position(score,event);names='、'.join(pitch_name(p) for p in pitches)
    reason=names+' 超出标准品格 E2～E6' if low or high else names+' 没有可同时保留的按法'
    if sweep:reason='Bm 扫弦触及临时规避的 2 弦' if bm else '扫弦没有等音的游戏按法'
    return {'kind':kind,'beat':str(event.start),'position':position,'seconds':score.seconds_at(event.start),
            'pitches':pitches,'short':f'{position}，{score.seconds_at(event.start):.3f} 秒：{reason}'}


def summarize_failures(candidates):
    failed=[row for row in candidates if not row.get('valid')]
    if not failed:return {'counts':{},'samples':[],'summary':''}
    counts=Counter(row.get('failure',{}).get('kind','other') for row in failed)
    by_shift={row['transpose_semitones']:row for row in failed};first=by_shift.get(0,failed[0]);selected=[first]
    detail=first.get('failure',{});pitches=detail.get('pitches',())
    # Show what actually blocks the simple correction suggested by the first
    # note, e.g. G6 -> lower 3 -> an E2 bass then falls below range.
    if pitches:
        proposed=[]
        if max(pitches)>88:proposed.append(first['transpose_semitones']+88-max(pitches))
        if min(pitches)<40:proposed.append(first['transpose_semitones']+40-min(pitches))
        for shift in proposed:
            row=by_shift.get(shift)
            if row is not None and row not in selected:selected.append(row)
    for row in sorted(failed,key=lambda r:abs(r['transpose_semitones'])):
        if len(selected)>=3:break
        kind=row.get('failure',{}).get('kind','other')
        if kind not in {r.get('failure',{}).get('kind','other') for r in selected}:selected.append(row)
    samples=[]
    for row in selected:
        shift=row['transpose_semitones'];label='原调' if shift==0 else f'{"降" if shift<0 else "升"}{abs(shift)}半音'
        description=row.get('failure',{}).get('short')
        if not description:
            description=row['error'].split('请在转译窗口')[0].split('可尝试整曲降调')[0].rstrip('；。 ')
        samples.append({'transpose_semitones':shift,'label':label,'cause':row.get('failure',{}).get('kind','other'),
                        'description':description})
    count_text='、'.join(f'{CAUSE_NAMES.get(kind,kind)} {number} 个' for kind,number in counts.items())
    summary='各方案首个阻碍：'+count_text+'。'+'；'.join(f'{r["label"]}：{r["description"]}' for r in samples)+'。'
    return {'counts':dict(counts),'samples':samples,'summary':summary}
