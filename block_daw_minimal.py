from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math, random, wave
import numpy as np
import dearpygui.dearpygui as dpg

SR, TPQ = 44100, 960
random.seed(0)


@dataclass
class Meter:
    numerator: int = 4
    denominator: int = 4


@dataclass
class Instrument:
    waveform: str = "sine"
    attack: float = 0.0
    decay: float = 0.1
    sustain: float = 0.0
    release: float = 0.0
    pitch_drop: float = 0.0
    noise: float = 0.0
    click: float = 0.0


@dataclass
class Note:
    start_tick: int
    length_ticks: int
    pitch: int
    velocity: float = 1.0


@dataclass
class Pattern:
    name: str
    length_ticks: int
    notes: list[Note] = field(default_factory=list)


@dataclass
class PianoRoll:
    name: str
    length_ticks: int
    notes: list[Note] = field(default_factory=list)


@dataclass
class Recording:
    name: str
    length_ticks: int


class BlockKind(str, Enum):
    PATTERN = "pattern"
    PIANO_ROLL = "piano_roll"
    RECORDING = "recording"


@dataclass
class BlockRef:
    kind: BlockKind
    object_id: int


@dataclass
class Block:
    ref: BlockRef
    start_tick: int
    length_ticks: int | None = None
    loop: bool = False
    muted: bool = False


@dataclass
class Track:
    name: str
    instrument: Instrument
    selected: bool = True
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Song:
    bpm: float = 142.0
    meter: Meter = field(default_factory=Meter)
    patterns: dict[int, Pattern] = field(default_factory=dict)
    piano_rolls: dict[int, PianoRoll] = field(default_factory=dict)
    recordings: dict[int, Recording] = field(default_factory=dict)
    tracks: list[Track] = field(default_factory=list)


@dataclass
class Interaction:
    focused_block: tuple[int, int] | None = None
    focused_track: int | None = None
    hover_block: tuple[int, int] | None = None
    mode: str | None = None
    mouse_down: bool = False
    drag_origin_tick: int = 0
    drag_origin_start: int = 0
    drag_origin_len: int = 0
    drag_origin_track: int | None = None
    drag_offset_tick: int = 0
    preview_track: int | None = None
    preview_start: int | None = None
    preview_len: int | None = None


@dataclass
class DAW:
    song: Song
    px_per_beat: float = 48.0
    track_height: int = 96
    timeline_beats: int = 2048
    undo: list[list[tuple]] = field(default_factory=list)
    redo: list[list[tuple]] = field(default_factory=list)
    dirty: bool = True
    ui: Interaction = field(default_factory=Interaction)


ROOT, ROWS, SCROLLER, DRAWLIST = "root", "rows", "scroll", "draw"
LEFT_PAD, TOP_PAD, HEADER_H, TRACK_GAP, EDGE_PX = 12, 12, 34, 10, 8
LANE_BG, LANE_BORDER = (22, 22, 24, 255), (70, 70, 76, 255)
GRID_MINOR, GRID_MAJOR = (45, 45, 48, 255), (214, 178, 42, 255)
TEXT, TEXT_SOFT = (238, 214, 96, 255), (220, 220, 220, 255)
FILL = {BlockKind.PATTERN: (184, 146, 28, 255), BlockKind.PIANO_ROLL: (214, 178, 42, 255), BlockKind.RECORDING: (140, 114, 30, 255)}


# ---------- time / path ----------
def ticks_per_beat(song: Song) -> int: return TPQ * 4 // song.meter.denominator
def ticks_per_bar(song: Song) -> int: return song.meter.numerator * ticks_per_beat(song)
def seconds_per_tick(song: Song) -> float: return (60.0 / song.bpm) / TPQ
def ticks_to_seconds(song: Song, ticks: int) -> float: return ticks * seconds_per_tick(song)
def px_per_tick(daw: DAW) -> float: return daw.px_per_beat / ticks_per_beat(daw.song)
def lane_top(i: int, daw: DAW) -> float: return HEADER_H + TOP_PAD + i * (daw.track_height + TRACK_GAP)
def access(root, key): return getattr(root, key) if isinstance(key, str) else root[key]

def seq(root, path):
    for key in path: root = access(root, key)
    return root

def parent_key(root, path):
    return seq(root, path[:-1]), path[-1]


# ---------- history ----------
def apply_ops(song: Song, ops: list[tuple], undo: bool = False) -> None:
    for kind, *rest in reversed(ops) if undo else ops:
        if kind == "set":
            path, old, new = rest
            parent, key = parent_key(song, path)
            value = old if undo else new
            setattr(parent, key, value) if isinstance(key, str) else parent.__setitem__(key, value)
        else:
            path, index, value = rest
            xs = seq(song, path)
            (xs.insert(index, value) if (kind == "delete") == undo else xs.pop(index))

def do(daw: DAW, ops: list[tuple], msg: str = "") -> None:
    if not ops: return
    apply_ops(daw.song, ops)
    daw.undo.append(ops)
    daw.redo.clear()
    daw.dirty = True
    if msg and dpg.does_item_exist("status"): dpg.set_value("status", msg)

def step_history(daw: DAW, src: str, dst: str, undo_mode: bool = False) -> None:
    stack = getattr(daw, src)
    if not stack: return
    ops = stack.pop()
    apply_ops(daw.song, ops, undo=undo_mode)
    getattr(daw, dst).append(ops)
    daw.dirty = True

def undo(daw: DAW) -> None: step_history(daw, "undo", "redo", True)
def redo(daw: DAW) -> None: step_history(daw, "redo", "undo", False)


# ---------- content ----------
def resolve(song: Song, ref: BlockRef):
    return {BlockKind.PATTERN: song.patterns, BlockKind.PIANO_ROLL: song.piano_rolls, BlockKind.RECORDING: song.recordings}[ref.kind][ref.object_id]

def block_len(song: Song, block: Block) -> int: return block.length_ticks if block.length_ticks is not None else resolve(song, block.ref).length_ticks
def selected_tracks(song: Song) -> list[Track]: return [t for t in song.tracks if t.selected] or song.tracks
def next_pattern_id(song: Song) -> int: return max(song.patterns.keys() | {0}) + 1
def step_ticks(song: Song) -> int: return ticks_per_beat(song) // 4

def ensure_blank_pattern(song: Song) -> int:
    for pid, pattern in song.patterns.items():
        if pattern.name == "blank" and not pattern.notes: return pid
    pid = next_pattern_id(song)
    song.patterns[pid] = Pattern("blank", ticks_per_bar(song), [])
    return pid


# ---------- demo ----------
def make_demo_daw() -> DAW:
    song, s = Song(), step_ticks(Song())
    bar = 16 * s
    patt = lambda name, notes: Pattern(name, bar, notes)
    song.patterns |= {
        0: patt("kick", [Note(0 * s, 1 * s, 36), Note(6 * s, 1 * s, 36), Note(11 * s, 1 * s, 36)]),
        1: patt("snare", [Note(4 * s, 1 * s, 38, 0.95), Note(12 * s, 1 * s, 38, 0.95)]),
        2: patt("hat", [Note(i * s, 1 * s, 42, 0.35 if i % 4 else 0.65) for i in range(16)]),
    }
    song.piano_rolls[0] = PianoRoll("808", bar, [Note(0 * s, 4 * s, 36), Note(6 * s, 2 * s, 36), Note(8 * s, 4 * s, 34), Note(12 * s, 4 * s, 31)])
    prog = [([41, 48, 55, 60, 64], 65), ([43, 50, 57, 62], 69), ([45, 52, 57, 60, 64], 72), ([36, 43, 50, 55, 62], 67)]
    song.piano_rolls[1] = PianoRoll(
        "future organ", 4 * bar,
        [Note(i * bar + off, 14 * s if off == 0 else 2 * s, pitch, vel)
         for i, (chord, lead) in enumerate(prog)
         for pitch, off, vel in [(p, 0, 0.38 if j < len(chord) - 1 else 0.26) for j, p in enumerate(chord)] + [(lead, 8 * s, 0.22), (lead + 7, 12 * s, 0.16)]]
    )
    kick = Instrument("sine", decay=0.10, pitch_drop=24.0, click=0.20)
    snare = Instrument("square", decay=0.05, noise=0.65, click=0.08)
    hat = Instrument("noise", decay=0.02, noise=0.85)
    bass = Instrument("sine", decay=0.20, sustain=0.85, release=0.08, pitch_drop=7.0, click=0.02)
    organ = Instrument("saw", attack=0.03, decay=0.55, sustain=0.72, release=0.42, noise=0.025, click=0.01)
    bars = lambda ref, n, length=bar: [Block(ref, i * bar, length) for i in range(n)]
    song.tracks = [
        Track("kick", kick, True, bars(BlockRef(BlockKind.PATTERN, 0), 4)),
        Track("snare", snare, True, bars(BlockRef(BlockKind.PATTERN, 1), 4)),
        Track("hat", hat, True, bars(BlockRef(BlockKind.PATTERN, 2), 4)),
        Track("808", bass, True, bars(BlockRef(BlockKind.PIANO_ROLL, 0), 4)),
        Track("future organ", organ, True, [Block(BlockRef(BlockKind.PIANO_ROLL, 1), 0, 4 * bar)]),
    ]
    return DAW(song)


# ---------- audio ----------
def midi_to_hz(m: int) -> float: return 440.0 * 2.0 ** ((m - 69) / 12.0)

def osc_array(w: str, phase: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    x = np.mod(phase, 1.0)
    return (np.sin(2 * np.pi * x) if w == "sine" else np.where(x < 0.5, 1.0, -1.0) if w == "square" else 2.0 * x - 1.0 if w == "saw" else rng.uniform(-1.0, 1.0, x.shape)).astype(np.float32)

def adsr_array(inst: Instrument, t: np.ndarray, hold: float) -> np.ndarray:
    a, d, s, r = inst.attack, inst.decay, inst.sustain, inst.release
    return np.select(
        [(a > 0) & (t < a), (d > 0) & (t >= a) & (t < a + d), t < hold, (r > 0) & (t >= hold) & (t < hold + r)],
        [t / max(a, 1e-9), 1.0 + (s - 1.0) * ((t - a) / max(d, 1e-9)), s, s * (1.0 - (t - hold) / max(r, 1e-9))],
        default=0.0,
    ).astype(np.float32)

def render_note(song: Song, inst: Instrument, note: Note, rng: np.random.Generator) -> np.ndarray:
    hold = max(0.0, ticks_to_seconds(song, note.length_ticks))
    total = hold + inst.release
    t = np.arange(max(1, int(total * SR)), dtype=np.float32) / SR
    base = np.float32(midi_to_hz(note.pitch))
    hz = np.full(len(t), base, np.float32) if inst.pitch_drop <= 0.0 else (base * 2.0 ** ((inst.pitch_drop * (1.0 - np.minimum(1.0, t / max(min(0.08, total), 1e-9)))) / 12.0)).astype(np.float32)
    tonal = osc_array(inst.waveform, np.cumsum(hz / SR, dtype=np.float32), rng)
    noise = rng.uniform(-1.0, 1.0, len(t)).astype(np.float32) * np.float32(inst.noise)
    click = (np.maximum(0.0, 1.0 - t / 0.004) * np.float32(inst.click) * (t < 0.004)).astype(np.float32)
    return (adsr_array(inst, t, hold) * np.float32(note.velocity) * (0.9 * tonal + noise + click)).astype(np.float32)

def iter_notes(song: Song, block: Block):
    src = resolve(song, block.ref)
    if isinstance(src, Recording): return
    clip, src_len = block_len(song, block), max(1, src.length_ticks)
    if block.loop and clip > src_len:
        for off in range(0, clip, src_len):
            yield from ((off + n.start_tick, n) for n in src.notes if off + n.start_tick < clip)
    else:
        yield from ((n.start_tick, n) for n in src.notes if n.start_tick < clip)

def note_events(song: Song) -> list[tuple[int, Instrument, Note]]:
    return [(int(ticks_to_seconds(song, b.start_tick + rel) * SR), tr.instrument, note) for tr in selected_tracks(song) for b in tr.blocks if not b.muted for rel, note in iter_notes(song, b)]

def song_end_tick(song: Song) -> int:
    return max([ticks_per_bar(song) * 2] + [b.start_tick + block_len(song, b) for tr in selected_tracks(song) for b in tr.blocks])

def note_key(inst: Instrument, note: Note):
    return (inst.waveform, inst.attack, inst.decay, inst.sustain, inst.release, inst.pitch_drop, inst.noise, inst.click, note.pitch, note.length_ticks, note.velocity)

def accumulate_events(song: Song, events: list[tuple[int, Instrument, Note]] | None = None) -> np.ndarray:
    total = max(1, int((ticks_to_seconds(song, song_end_tick(song)) + 0.25) * SR))
    mix, rng, cache = np.zeros(total, np.float32), np.random.default_rng(0), {}
    for start, inst, note in note_events(song) if events is None else events:
        key, deterministic = note_key(inst, note), inst.noise == 0.0 and inst.waveform != "noise"
        audio = cache[key] if deterministic and key in cache else render_note(song, inst, note, rng)
        if deterministic and key not in cache: cache[key] = audio
        end = min(total, start + len(audio))
        if start < total: mix[start:end] += audio[:end - start]
    return mix

def master_signal(x: np.ndarray, mode: str = "normalize") -> np.ndarray:
    if mode == "none": return x.astype(np.float32, copy=False)
    if mode == "tanh": return np.tanh(1.4 * x).astype(np.float32)
    peak = np.max(np.abs(x), initial=1.0)
    return (x / peak).astype(np.float32) if peak > 1.0 else x.astype(np.float32, copy=False)

def render_song(song: Song, master: str = "normalize") -> np.ndarray: return master_signal(accumulate_events(song), master)

def write_wav(path: str, samples) -> None:
    pcm = (np.clip(np.asarray(samples, np.float32), -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR); wf.writeframes(pcm.tobytes())


# ---------- ops / selection ----------
def track_blocks(song: Song, i: int) -> list[Block]: return song.tracks[i].blocks
def set_op(path, old, new): return ("set", path, old, new)
def insert_op(path, index, value): return ("insert", path, index, value)
def delete_op(path, index, value): return ("delete", path, index, value)

def focused_block_ref(daw: DAW):
    fb = daw.ui.focused_block
    if fb is None: return None
    i, j = fb
    return (i, j, daw.song.tracks[i].blocks[j]) if 0 <= i < len(daw.song.tracks) and 0 <= j < len(daw.song.tracks[i].blocks) else None

def block_state(daw: DAW, i: int, j: int, block: Block) -> tuple[int, int, int]:
    preview = daw.ui.mouse_down and daw.ui.mode in {"move", "resize_left", "resize_right"} and daw.ui.focused_block == (i, j)
    return (
        daw.ui.preview_track if preview and daw.ui.preview_track is not None else i,
        daw.ui.preview_start if preview and daw.ui.preview_start is not None else block.start_tick,
        daw.ui.preview_len if preview and daw.ui.preview_len is not None else block_len(daw.song, block),
    )

def block_rect(daw: DAW, i: int, j: int, block: Block) -> tuple[float, float, float, float]:
    track_i, start_tick, length_ticks = block_state(daw, i, j, block)
    y = lane_top(track_i, daw)
    x0 = LEFT_PAD + start_tick * px_per_tick(daw)
    return x0, y + 6, x0 + length_ticks * px_per_tick(daw), y + daw.track_height - 6

def pick_block(daw: DAW, x: float, y: float):
    for i, track in enumerate(daw.song.tracks):
        for j in range(len(track.blocks) - 1, -1, -1):
            x0, y0, x1, y1 = block_rect(daw, i, j, track.blocks[j])
            if x0 <= x <= x1 and y0 <= y <= y1:
                mode = "resize_left" if abs(x - x0) <= EDGE_PX else "resize_right" if abs(x - x1) <= EDGE_PX else "move"
                return mode, i, j
    return None


# ---------- ui helpers ----------
def y_to_track(daw: DAW, y: float) -> int | None:
    for i in range(len(daw.song.tracks)):
        if lane_top(i, daw) <= y <= lane_top(i, daw) + daw.track_height: return i
    return None

def x_to_tick(daw: DAW, x: float) -> int: return max(0, int(round((x - LEFT_PAD) / px_per_tick(daw))))
def snap_tick_to_beat(song: Song, tick: int) -> int: return max(0, round(tick / ticks_per_beat(song)) * ticks_per_beat(song))

def mouse_in_timeline() -> tuple[float, float] | None:
    try:
        mx, my = dpg.get_mouse_pos(local=False)
        state = dpg.get_item_state(SCROLLER) if dpg.does_item_exist(SCROLLER) else None
        wx, wy = state["rect_min"] if state and "rect_min" in state else state["pos"] if state and "pos" in state else dpg.get_item_pos(SCROLLER)
        return mx - wx + dpg.get_x_scroll(SCROLLER), my - wy + dpg.get_y_scroll(SCROLLER)
    except Exception:
        return None

def focus_block(daw: DAW, i: int, j: int):
    daw.ui.focused_track, daw.ui.focused_block, daw.dirty = i, (i, j), True
    block = daw.song.tracks[i].blocks[j]
    print("focused block", {"track": i, "block": j, "kind": block.ref.kind, "object_id": block.ref.object_id, "start_tick": block.start_tick, "length_ticks": block_len(daw.song, block)})

def reset_drag_preview(daw: DAW):
    for name, value in {"mode": None, "mouse_down": False, "drag_origin_track": None, "drag_offset_tick": 0, "preview_track": None, "preview_start": None, "preview_len": None}.items():
        setattr(daw.ui, name, value)

def begin_drag(daw: DAW, mode: str, i: int, j: int, tick: int):
    focus_block(daw, i, j)
    block = daw.song.tracks[i].blocks[j]
    daw.ui.mode, daw.ui.mouse_down = mode, True
    daw.ui.drag_origin_tick, daw.ui.drag_origin_track = tick, i
    daw.ui.drag_origin_start, daw.ui.drag_origin_len = block.start_tick, block_len(daw.song, block)
    daw.ui.drag_offset_tick = tick - block.start_tick
    daw.ui.preview_track, daw.ui.preview_start, daw.ui.preview_len = i, block.start_tick, block_len(daw.song, block)

def update_drag_preview(daw: DAW, tick: int, y: float):
    if daw.ui.focused_block is None or daw.ui.mode is None: return
    origin_track = daw.ui.drag_origin_track if daw.ui.drag_origin_track is not None else daw.ui.focused_block[0]
    origin_start, origin_len = daw.ui.drag_origin_start, daw.ui.drag_origin_len
    origin_end, min_len = origin_start + origin_len, ticks_per_beat(daw.song)
    if daw.ui.mode == "move":
        daw.ui.preview_track = y_to_track(daw, y) if y_to_track(daw, y) is not None else origin_track
        daw.ui.preview_start, daw.ui.preview_len = snap_tick_to_beat(daw.song, tick - daw.ui.drag_offset_tick), origin_len
    elif daw.ui.mode == "resize_left":
        new_start = max(0, min(snap_tick_to_beat(daw.song, tick), origin_end - min_len))
        daw.ui.preview_track, daw.ui.preview_start, daw.ui.preview_len = origin_track, new_start, origin_end - new_start
    else:
        new_end = max(origin_start + min_len, snap_tick_to_beat(daw.song, tick))
        daw.ui.preview_track, daw.ui.preview_start, daw.ui.preview_len = origin_track, origin_start, new_end - origin_start
    daw.dirty = True


# ---------- drawing ----------
def draw_pattern_preview(drawlist: str, daw: DAW, block: Block, x0: float, y0: float, y1: float) -> None:
    source, ppt = resolve(daw.song, block.ref), daw.px_per_beat
    if isinstance(source, Recording):
        mid, prev, steps = (y0 + y1) / 2, None, max(8, int((block_len(daw.song, block) * px_per_tick(daw)) / 10))
        for k in range(steps + 1):
            xx, yy = x0 + (block_len(daw.song, block) * px_per_tick(daw)) * k / steps, mid + math.sin(0.35 * k) * (daw.track_height * 0.12)
            if prev: dpg.draw_line(prev, (xx, yy), color=(32, 32, 32, 255), thickness=2, parent=drawlist)
            prev = (xx, yy)
        return
    if block.ref.kind == BlockKind.PATTERN:
        hit_w = max(2.0, ppt * 0.12)
        for note in source.notes:
            nx = x0 + note.start_tick * px_per_tick(daw)
            dpg.draw_rectangle((nx, y1 - 12), (nx + hit_w, y1 - 6), fill=(30, 30, 30, 255), color=(30, 30, 30, 255), parent=drawlist)
        return
    lo, hi = min(n.pitch for n in source.notes), max(n.pitch for n in source.notes)
    span = max(1, hi - lo)
    for note in source.notes:
        ny0 = y1 - 10 - ((note.pitch - lo) / span) * max(12, y1 - y0 - 18)
        nx0 = x0 + note.start_tick * px_per_tick(daw)
        nx1 = max(nx0 + max(3.0, ppt * 0.15), nx0 + note.length_ticks * px_per_tick(daw))
        dpg.draw_rectangle((nx0, ny0), (nx1, ny0 + 6), fill=(30, 30, 30, 255), color=(30, 30, 30, 255), parent=drawlist)

def draw_header(daw: DAW, w: int, h: int) -> None:
    tpb, bar = ticks_per_beat(daw.song), ticks_per_bar(daw.song)
    for beat in range(daw.timeline_beats + 1):
        tick, x = beat * tpb, LEFT_PAD + beat * daw.px_per_beat
        is_bar = tick % bar == 0
        dpg.draw_line((x, HEADER_H), (x, h - TOP_PAD), color=GRID_MAJOR if is_bar else GRID_MINOR, thickness=2 if is_bar else 1, parent=DRAWLIST)
        if beat < daw.timeline_beats:
            if is_bar: dpg.draw_text((x + 1, 1), f"|{beat // max(1, daw.song.meter.numerator) + 1}", color=TEXT, size=15, parent=DRAWLIST)
            dpg.draw_text((x + 1, 16), str(beat), color=TEXT_SOFT, size=12, parent=DRAWLIST)

def draw_block(drawlist: str, daw: DAW, i: int, j: int, block: Block, y: float) -> None:
    view_track, start_tick, length_ticks = block_state(daw, i, j, block)
    if view_track != i: return
    x0, y0 = LEFT_PAD + start_tick * px_per_tick(daw), y + 6
    x1, y1 = x0 + length_ticks * px_per_tick(daw), y + daw.track_height - 6
    focused = daw.ui.focused_block == (i, j)
    dpg.draw_rectangle((x0, y0), (x1, y1), fill=(92, 92, 96, 255) if block.muted else FILL[block.ref.kind], color=(255, 255, 255, 255) if focused else (18, 18, 18, 255), thickness=3 if focused else 1, rounding=6, parent=drawlist)
    dpg.draw_text((x0 + 6, y0 + 6), daw.song.tracks[i].name, color=(18, 18, 18, 255), size=14, parent=drawlist)
    draw_pattern_preview(drawlist, daw, block, x0, y0 + 22, y1 - 4)

def draw_timeline(daw: DAW) -> None:
    w, h = dpg.get_item_rect_size(SCROLLER)
    if w < 20 or h < 20: return
    cw = max(int(LEFT_PAD * 2 + daw.timeline_beats * daw.px_per_beat), int(w - 16))
    ch = max(int(HEADER_H + TOP_PAD * 2 + len(daw.song.tracks) * (daw.track_height + TRACK_GAP) + 20), int(h - 16))
    dpg.configure_item(DRAWLIST, width=cw, height=ch)
    dpg.delete_item(DRAWLIST, children_only=True)
    dpg.draw_rectangle((0, 0), (cw, ch), fill=(10, 10, 12, 255), color=(10, 10, 12, 255), parent=DRAWLIST)
    draw_header(daw, cw, ch)
    for i, track in enumerate(daw.song.tracks):
        y = lane_top(i, daw)
        dpg.draw_rectangle((LEFT_PAD, y), (cw - LEFT_PAD, y + daw.track_height), fill=LANE_BG, color=LANE_BORDER, rounding=6, parent=DRAWLIST)
        for j, block in sorted(enumerate(track.blocks), key=lambda p: block_state(daw, i, p[0], p[1])[1]): draw_block(DRAWLIST, daw, i, j, block, y)

def rebuild_rows(daw: DAW) -> None:
    dpg.delete_item(ROWS, children_only=True)
    dpg.add_spacer(height=HEADER_H - 4, parent=ROWS)
    for i, track in enumerate(daw.song.tracks):
        with dpg.group(horizontal=True, parent=ROWS):
            dpg.add_checkbox(default_value=track.selected, callback=on_track_selected, user_data=(daw, i))
            dpg.add_input_text(default_value=track.name, width=150, on_enter=True, callback=on_track_name, user_data=(daw, i))
        dpg.add_spacer(height=max(0, daw.track_height - 22 + TRACK_GAP), parent=ROWS)

def redraw(daw: DAW) -> None:
    if daw.dirty:
        rebuild_rows(daw)
        draw_timeline(daw)
        daw.dirty = False


# ---------- generic callbacks ----------
def set_song_field(daw: DAW, path, value, cast=lambda x: x, clamp=lambda x: x):
    old, new = seq(daw.song, path), clamp(cast(value))
    if new != old: do(daw, [set_op(path, old, new)])

def set_daw_field(daw: DAW, name: str, value, cast=lambda x: x, clamp=lambda x: x):
    new = clamp(cast(value))
    if new != getattr(daw, name):
        setattr(daw, name, new)
        daw.dirty = True

def on_track_selected(sender, app_data, user_data):
    daw, i = user_data
    set_song_field(daw, ("tracks", i, "selected"), app_data, bool)

def on_track_name(sender, app_data, user_data):
    daw, i = user_data
    set_song_field(daw, ("tracks", i, "name"), app_data or "track")

def on_bpm(sender, app_data, daw): set_song_field(daw, ("bpm",), app_data, float, lambda x: max(1.0, x))
def on_meter_num(sender, app_data, daw): set_song_field(daw, ("meter", "numerator"), app_data, int, lambda x: max(1, x))
def on_meter_den(sender, app_data, daw): set_song_field(daw, ("meter", "denominator"), app_data, int, lambda x: max(1, x))
def on_px(sender, app_data, daw): set_daw_field(daw, "px_per_beat", app_data, float, lambda x: max(8.0, x))
def on_track_h(sender, app_data, daw): set_daw_field(daw, "track_height", app_data, int, lambda x: max(36, x))
def on_add_track(sender, app_data, daw): do(daw, [insert_op(("tracks",), len(daw.song.tracks), Track(f"track {len(daw.song.tracks)}", Instrument()))], "added track")

def on_del_track(sender, app_data, daw):
    if not daw.song.tracks: return
    i = max([j for j, t in enumerate(daw.song.tracks) if t.selected], default=len(daw.song.tracks) - 1)
    do(daw, [delete_op(("tracks",), i, daw.song.tracks[i])], "deleted track")


# ---------- actions ----------
def make_block_for_track(song: Song, start_tick: int) -> Block:
    return Block(BlockRef(BlockKind.PATTERN, ensure_blank_pattern(song)), start_tick, ticks_per_bar(song))

def add_block_at_click(daw: DAW, i: int, tick: int):
    start = (tick // ticks_per_bar(daw.song)) * ticks_per_bar(daw.song)
    j = len(track_blocks(daw.song, i))
    do(daw, [insert_op(("tracks", i, "blocks"), j, make_block_for_track(daw.song, start))], "added block")
    daw.ui.focused_track, daw.ui.focused_block = i, (i, j)

def commit_drag(daw: DAW):
    ref = focused_block_ref(daw)
    if ref is None or daw.ui.drag_origin_track is None or daw.ui.preview_start is None or daw.ui.preview_len is None or daw.ui.preview_track is None:
        reset_drag_preview(daw)
        return
    i, j, block = ref
    dest_i, ops = daw.ui.preview_track, []
    if daw.ui.mode == "move":
        if dest_i != i:
            moved = Block(block.ref, daw.ui.preview_start, daw.ui.preview_len, block.loop, block.muted)
            dest_j = len(track_blocks(daw.song, dest_i))
            ops = [delete_op(("tracks", i, "blocks"), j, block), insert_op(("tracks", dest_i, "blocks"), dest_j, moved)]
            do(daw, ops, "moved block")
            daw.ui.focused_track, daw.ui.focused_block = dest_i, (dest_i, dest_j)
        elif daw.ui.preview_start != daw.ui.drag_origin_start:
            do(daw, [set_op(("tracks", i, "blocks", j, "start_tick"), daw.ui.drag_origin_start, daw.ui.preview_start)], "moved block")
    elif daw.ui.mode in {"resize_left", "resize_right"}:
        if daw.ui.preview_start != daw.ui.drag_origin_start: ops.append(set_op(("tracks", i, "blocks", j, "start_tick"), daw.ui.drag_origin_start, daw.ui.preview_start))
        if daw.ui.preview_len != daw.ui.drag_origin_len: ops.append(set_op(("tracks", i, "blocks", j, "length_ticks"), daw.ui.drag_origin_len, daw.ui.preview_len))
        do(daw, ops, "resized block")
    reset_drag_preview(daw)
    daw.dirty = True

def on_mouse_down(sender, app_data, daw):
    if not dpg.is_item_hovered(SCROLLER): return
    pos = mouse_in_timeline()
    if pos is None: return
    x, y = pos
    if x < LEFT_PAD or y < HEADER_H: return
    tick, picked = x_to_tick(daw, x), pick_block(daw, x, y)
    if picked is not None:
        mode, i, j = picked
        begin_drag(daw, mode, i, j, tick)
    elif (i := y_to_track(daw, y)) is not None:
        add_block_at_click(daw, i, tick)

def on_mouse_drag(sender, app_data, daw):
    if not daw.ui.mouse_down or daw.ui.mode is None: return
    pos = mouse_in_timeline()
    if pos is not None: update_drag_preview(daw, x_to_tick(daw, pos[0]), pos[1])

def on_mouse_release(sender, app_data, daw): commit_drag(daw)

def on_export(sender, app_data, daw):
    write_wav("block_daw_export.wav", render_song(daw.song))
    if dpg.does_item_exist("status"): dpg.set_value("status", "wrote block_daw_export.wav")

def on_key_z(sender, app_data, daw):
    if dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl): undo(daw)

def on_key_r(sender, app_data, daw):
    if dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl): redo(daw)

def on_key_delete(sender, app_data, daw):
    if daw.ui.mouse_down: return
    ref = focused_block_ref(daw)
    if ref is None:
        daw.ui.focused_block = daw.ui.focused_track = None
    else:
        i, j, block = ref
        do(daw, [delete_op(("tracks", i, "blocks"), j, block)], "deleted block")
        daw.ui.focused_block, daw.ui.focused_track = None, i
    daw.dirty = True


# ---------- app ----------
def add_labeled(widget, label: str, **kwargs):
    dpg.add_text(label)
    widget(**kwargs)

def build_handlers(daw: DAW):
    with dpg.handler_registry():
        for key, cb in [(dpg.mvKey_Z, on_key_z), (dpg.mvKey_R, on_key_r), (dpg.mvKey_Back, on_key_delete)]:
            dpg.add_key_press_handler(key, callback=cb, user_data=daw)
        dpg.add_mouse_down_handler(callback=on_mouse_down, user_data=daw)
        dpg.add_mouse_drag_handler(callback=on_mouse_drag, user_data=daw)
        dpg.add_mouse_release_handler(callback=on_mouse_release, user_data=daw)

def build_toolbar(daw: DAW):
    with dpg.group(horizontal=True):
        add_labeled(dpg.add_input_float, "BPM", default_value=daw.song.bpm, width=90, callback=on_bpm, user_data=daw, format="%.1f")
        add_labeled(dpg.add_input_int, "Meter", default_value=daw.song.meter.numerator, width=60, callback=on_meter_num, user_data=daw)
        dpg.add_text("/")
        dpg.add_input_int(default_value=daw.song.meter.denominator, width=60, callback=on_meter_den, user_data=daw)
        add_labeled(dpg.add_input_float, "px/beat", default_value=daw.px_per_beat, width=90, callback=on_px, user_data=daw, format="%.1f")
        add_labeled(dpg.add_input_int, "h/track", default_value=daw.track_height, width=70, callback=on_track_h, user_data=daw)
        for label, cb in [("+ track", on_add_track), ("- track", on_del_track), ("Export WAV", on_export)]:
            dpg.add_button(label=label, callback=cb, user_data=daw)
        dpg.add_text("", tag="status")

def build_layout():
    with dpg.group(horizontal=True):
        with dpg.child_window(width=210, autosize_y=True, border=False):
            dpg.add_child_window(tag=ROWS, autosize_x=True, autosize_y=True, border=False)
        with dpg.child_window(tag=SCROLLER, autosize_x=True, autosize_y=True, border=False, horizontal_scrollbar=True):
            dpg.add_drawlist(tag=DRAWLIST, width=4000, height=900)

def Render(daw: DAW) -> None:
    dpg.create_context()
    build_handlers(daw)
    with dpg.window(tag=ROOT, label="block daw"):
        build_toolbar(daw)
        dpg.add_separator()
        build_layout()
    dpg.create_viewport(title="block daw", width=1600, height=920)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window(ROOT, True)
    while dpg.is_dearpygui_running():
        dpg.configure_item(ROOT, width=dpg.get_viewport_client_width(), height=dpg.get_viewport_client_height())
        redraw(daw)
        dpg.render_dearpygui_frame()
    dpg.destroy_context()


if __name__ == "__main__":
    Render(make_demo_daw())
