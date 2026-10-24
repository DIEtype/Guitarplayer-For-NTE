"""Estimate host input overhead. Logs cannot establish game note acceptance."""
from dataclasses import dataclass, replace
import json
import math
from pathlib import Path

from .timing import ADAPTIVE

ACTIONS = {'chord_move', 'chord_down', 'chord_up', 'page', 'release', 'note', 'timbre_down', 'timbre_up'}


def percentile(values, fraction=.95):
    values = sorted(values)
    return values[min(len(values) - 1, math.ceil(len(values) * fraction) - 1)] if values else 0.


def number(value, low=0., high=150.):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


@dataclass(frozen=True)
class LogEstimate:
    files: int = 0
    samples: int = 0
    margin: float = ADAPTIVE.jitter_margin
    wake_ms: float = 0.
    rejected_files: int = 0
    select_margin: float|None = None
    page_margin: float = 0.
    release_ms: float = 0.

    @property
    def ready(self):
        return self.samples >= 20

    def timing(self):
        return replace(ADAPTIVE, jitter_margin=self.margin,select_margin=self.select_margin,page_margin=self.page_margin) if self.ready else ADAPTIVE

    def description(self):
        if not self.ready:
            return f'有效耗时样本 {self.samples}/20；暂用默认准备余量 {ADAPTIVE.jitter_margin * 1000:.0f} ms'
        return (f'{self.files} 份日志、{self.samples} 个耗时样本；同页选品余量 {(self.select_margin if self.select_margin is not None else self.margin) * 1000:.0f} ms，'
                f'松开耗时 P95 {self.release_ms:.1f} ms，调度唤醒 P95 {self.wake_ms:.1f} ms；不代表游戏已发声')


def analyze_logs(directory):
    directory = Path(directory)
    costs = {action: [] for action in ACTIONS}
    wakes = []
    accepted = rejected = 0
    paths = sorted(directory.glob('input-*.jsonl'), key=lambda path: path.stat().st_mtime, reverse=True)[:20]
    for path in paths:
        try:
            if path.stat().st_size > 8_000_000:
                continue
            rows = []
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict):
                            rows.append(row)
                    except ValueError:
                        continue
        except OSError:
            continue
        # Never train on previews, other applications, or rejected input.
        if not any(row.get('event') == 'session' and row.get('purpose') == 'auto_play' for row in rows):
            continue
        if not any(row.get('event') == 'target' and ('异环' in str(row.get('title', '')) or
                   'nte' in str(row.get('title', '')).lower()) for row in rows):
            continue
        if any(row.get('event') == 'send_input' and row.get('accepted') != row.get('requested') or
               row.get('event') == 'pointer' and row.get('set_cursor_ok') is False for row in rows):
            rejected += 1
            continue
        explicit = any(row.get('event') == 'operation' for row in rows)
        pending = None
        file_samples = 0
        for row in rows:
            event, action = row.get('event'), row.get('action')
            if event == 'schedule':
                if number(row.get('actual'), -1000, 100000) and number(row.get('guard_until'), -1000, 100000):
                    wake = (row['actual'] - row['guard_until']) * 1000
                    if number(wake, 0, 100):
                        wakes.append(wake)
                pending = (action, row.get('perf_ns')) if action in ACTIONS else None
            if explicit:
                if event == 'operation' and action in ACTIONS and number(row.get('duration_ms')):
                    costs[action].append(row['duration_ms'])
                    file_samples += 1
            elif pending:
                action, started = pending
                terminal = 'pointer' if action == 'chord_move' else 'send_input'
                if event == terminal:
                    if number(started, 0, 1e20) and number(row.get('perf_ns'), 0, 1e20):
                        duration = (row['perf_ns'] - started) / 1e6
                        if number(duration):
                            costs[action].append(duration)
                            file_samples += 1
                    pending = None
        if file_samples:
            accepted += 1
    count = sum(map(len, costs.values()))
    wake = percentile(wakes)
    select = sum(percentile(costs[action]) for action in ('chord_move', 'chord_down', 'chord_up'))
    pages = 2 * (percentile(costs['page']) + percentile(costs['release']))
    tone = sum(percentile(costs[action]) for action in ('timbre_down', 'timbre_up'))
    # Advance preparation, keep holds and musical timestamps intact; never tune
    # game response delays from SendInput success. Bound the host-only margin.
    margin = min(.090, max(ADAPTIVE.jitter_margin, (max(select + pages, tone) + 4 * wake + 3) / 1000))
    keys=percentile(costs['note'])+percentile(costs['release'])
    select_margin=min(.090,max(ADAPTIVE.jitter_margin,(select+keys+2*wake+3)/1000))
    margin=max(margin,select_margin)
    page_margin=min(.090,(pages/2+2*wake+2)/1000)
    return LogEstimate(accepted,count,round(margin,4),wake,rejected,round(select_margin,4),round(page_margin,4),
                       percentile(costs['release']))
