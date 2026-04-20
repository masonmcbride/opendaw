from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math, random, struct, wave
import dearpygui.dearpygui as dpg

SR = 44100
TPQ = 960
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


# ---------- time ----------
def ticks_per_beat(song: Song) -> int:
    return TPQ * 4 // song.meter.denominator


def ticks_per_bar(song: Song) -> int:
    return song.meter.numerator * ticks_per_beat(song)


def seconds_per_tick(song: Song) -> float:
    return (60.0 / song.bpm) / TPQ


def ticks_to_seconds(song: Song, ticks: int) -> float:
    return ticks * seconds_per_tick(song)


def px_per_tick(daw: DAW) -> float:
    return daw.px_per_beat / ticks_per_beat(daw.song)


# ---------- tiny history ----------
def access(root, key):
    return getattr(root, key) if isinstance(key, str) else root[key]


def parent_key(root, path):
    cur = root
    for key in path[:-1]:
        cur = access(cur, key)
    return cur, path[-1]


def seq(root, path):
    cur = root
    for key in path:
        cur = access(cur, key)
    return cur


def apply_ops(song: Song, ops: list[tuple], undo: bool = False) -> None:
    for op in (reversed(ops) if undo else ops):
        kind = op[0]
        if kind == "set":
            _, path, old, new = op
            parent, key = parent_key(song, path)
            value = old if undo else new
            setattr(parent, key, value) if isinstance(key, str) else parent.__setitem__(key, value)
        elif kind == "insert":
            _, path, index, value = op
            (seq(song, path).pop(index) if undo else seq(song, path).insert(index, value))
        elif kind == "delete":
            _, path, index, value = op
            (seq(song, path).insert(index, value) if undo else seq(song, path).pop(index))


def do(daw: DAW, ops: list[tuple], msg: str = "") -> None:
    if not ops:
        return
    apply_ops(daw.song, ops)
    daw.undo.append(ops)
    daw.redo.clear()
    daw.dirty = True
    if msg and dpg.does_item_exist("status"):
        dpg.set_value("status", msg)


def undo(daw: DAW) -> None:
    if daw.undo:
        ops = daw.undo.pop()
        apply_ops(daw.song, ops, undo=True)
        daw.redo.append(ops)
        daw.dirty = True


def redo(daw: DAW) -> None:
    if daw.redo:
        ops = daw.redo.pop()
        apply_ops(daw.song, ops)
        daw.undo.append(ops)
        daw.dirty = True


# ---------- content ----------
def resolve(song: Song, ref: BlockRef):
    return song.patterns[ref.object_id] if ref.kind == BlockKind.PATTERN else song.piano_rolls[ref.object_id] if ref.kind == BlockKind.PIANO_ROLL else song.recordings[ref.object_id]


def block_len(song: Song, block: Block) -> int:
    return block.length_ticks if block.length_ticks is not None else resolve(song, block.ref).length_ticks


def selected_tracks(song: Song) -> list[Track]:
    picked = [t for t in song.tracks if t.selected]
    return picked or song.tracks


def next_pattern_id(song: Song) -> int:
    return max(song.patterns.keys() | {0}) + 1


def step_ticks(song: Song) -> int:
    return ticks_per_beat(song) // 4


def default_pattern(song: Song) -> int:
    return 0 if 0 in song.patterns else next(iter(song.patterns))


# ---------- demo ----------
def make_demo_daw() -> DAW:
    song = Song()
    s = step_ticks(song)
    song.patterns[0] = Pattern("kick", 16 * s, [Note(0 * s, 1 * s, 36), Note(6 * s, 1 * s, 36), Note(11 * s, 1 * s, 36)])
    song.patterns[1] = Pattern("snare", 16 * s, [Note(4 * s, 1 * s, 38, 0.95), Note(12 * s, 1 * s, 38, 0.95)])
    song.patterns[2] = Pattern("hat", 16 * s, [Note(i * s, 1 * s, 42, 0.35 if i % 4 else 0.65) for i in range(16)])
    song.piano_rolls[0] = PianoRoll("808", 16 * s, [Note(0 * s, 4 * s, 36), Note(6 * s, 2 * s, 36), Note(8 * s, 4 * s, 34), Note(12 * s, 4 * s, 31)])
    kick = Instrument("sine", decay=0.10, pitch_drop=24.0, click=0.20)
    snare = Instrument("square", decay=0.05, noise=0.65, click=0.08)
    hat = Instrument("noise", decay=0.02, noise=0.85)
    bass = Instrument("sine", decay=0.20, sustain=0.85, release=0.08, pitch_drop=7.0, click=0.02)
    song.tracks = [
        Track("kick", kick, True, [Block(BlockRef(BlockKind.PATTERN, 0), 0, 16 * s), Block(BlockRef(BlockKind.PATTERN, 0), 16 * s, 16 * s)]),
        Track("snare", snare, True, [Block(BlockRef(BlockKind.PATTERN, 1), 0, 16 * s), Block(BlockRef(BlockKind.PATTERN, 1), 16 * s, 16 * s)]),
        Track("hat", hat, True, [Block(BlockRef(BlockKind.PATTERN, 2), 0, 16 * s), Block(BlockRef(BlockKind.PATTERN, 2), 16 * s, 16 * s)]),
        Track("808", bass, True, [Block(BlockRef(BlockKind.PIANO_ROLL, 0), 0, 16 * s), Block(BlockRef(BlockKind.PIANO_ROLL, 0), 16 * s, 16 * s)]),
    ]
    return DAW(song)


# ---------- audio ----------
def midi_to_hz(m: int) -> float:
    return 440.0 * (2.0 ** ((m - 69) / 12.0))


def osc_sample(waveform: str, phase: float) -> float:
    x = phase - math.floor(phase)
    if waveform == "sine": return math.sin(2 * math.pi * x)
    if waveform == "square": return 1.0 if x < 0.5 else -1.0
    if waveform == "saw": return 2.0 * x - 1.0
    if waveform == "noise": return random.uniform(-1.0, 1.0)
    return math.sin(2 * math.pi * x)


def adsr(inst: Instrument, t: float, hold: float) -> float:
    a, d, s, r = inst.attack, inst.decay, inst.sustain, inst.release
    if a > 0 and t < a: return t / a
    t2 = t - a
    if d > 0 and t2 < d: return 1.0 + (s - 1.0) * (t2 / d)
    if t < hold: return s
    if r <= 0: return 0.0
    rel_t = t - hold
    return s * (1.0 - rel_t / r) if rel_t < r else 0.0


def render_note(song: Song, inst: Instrument, note: Note) -> list[float]:
    hold = max(0.0, ticks_to_seconds(song, note.length_ticks))
    total = hold + inst.release
    n = max(1, int(total * SR))
    out, base_hz, phase = [0.0] * n, midi_to_hz(note.pitch), 0.0
    for i in range(n):
        t = i / SR
        env = adsr(inst, t, hold)
        if env <= 0.0:
            continue
        if inst.pitch_drop > 0.0:
            frac = min(1.0, t / max(min(0.08, total), 1e-9))
            hz = base_hz * (2.0 ** ((inst.pitch_drop * (1.0 - frac)) / 12.0))
        else:
            hz = base_hz
        phase += hz / SR
        tonal = osc_sample(inst.waveform, phase)
        noise = random.uniform(-1.0, 1.0) * inst.noise
        click = (1.0 - min(1.0, t / 0.004)) * inst.click if t < 0.004 else 0.0
        out[i] = (0.9 * tonal + noise + click) * env * note.velocity
    return out


def iter_notes(song: Song, block: Block):
    source = resolve(song, block.ref)
    if isinstance(source, Recording):
        return
    clip_len, src_len = block_len(song, block), max(1, source.length_ticks)
    if block.loop and clip_len > src_len:
        k = 0
        while k * src_len < clip_len:
            off = k * src_len
            for note in source.notes:
                if off + note.start_tick < clip_len:
                    yield off + note.start_tick, note
            k += 1
    else:
        for note in source.notes:
            if note.start_tick < clip_len:
                yield note.start_tick, note


def song_end_tick(song: Song) -> int:
    end = ticks_per_bar(song) * 2
    for track in selected_tracks(song):
        for block in track.blocks:
            end = max(end, block.start_tick + block_len(song, block))
    return end


def render_song(song: Song) -> list[float]:
    mix = [0.0] * max(1, int((ticks_to_seconds(song, song_end_tick(song)) + 0.25) * SR))
    for track in selected_tracks(song):
        for block in track.blocks:
            if block.muted:
                continue
            for rel, note in iter_notes(song, block):
                start = int(ticks_to_seconds(song, block.start_tick + rel) * SR)
                audio = render_note(song, track.instrument, note)
                for j in range(min(len(audio), len(mix) - start)):
                    mix[start + j] += audio[j]
    for i, x in enumerate(mix):
        mix[i] = math.tanh(1.4 * x)
    return mix


def write_wav(path: str, samples: list[float]) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        frames = bytearray(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
        wf.writeframes(b"".join(frames))


# ---------- ui ----------
ROOT, ROWS, SCROLLER, DRAWLIST = "root", "rows", "scroll", "draw"
LEFT_PAD, TOP_PAD, HEADER_H, TRACK_GAP = 12, 12, 34, 10
LANE_BG, LANE_BORDER = (22, 22, 24, 255), (70, 70, 76, 255)
GRID_MINOR, GRID_MAJOR = (45, 45, 48, 255), (214, 178, 42, 255)
TEXT, TEXT_SOFT = (238, 214, 96, 255), (220, 220, 220, 255)
FILL = {BlockKind.PATTERN: (184, 146, 28, 255), BlockKind.PIANO_ROLL: (214, 178, 42, 255), BlockKind.RECORDING: (140, 114, 30, 255)}
EDGE_PX = 8


def lane_top(i: int, daw: DAW) -> float:
    return HEADER_H + TOP_PAD + i * (daw.track_height + TRACK_GAP)


def block_rect(daw: DAW, track_i: int, block_i: int, block: Block) -> tuple[float, float, float, float]:
    view_track = display_track_index(daw, track_i, block_i)
    start_tick, length_ticks = display_start_len(daw, track_i, block_i, block)
    y = lane_top(view_track, daw)
    x0 = LEFT_PAD + start_tick * px_per_tick(daw)
    x1 = x0 + length_ticks * px_per_tick(daw)
    return x0, y + 6, x1, y + daw.track_height - 6


def y_to_track(daw: DAW, y: float) -> int | None:
    for i in range(len(daw.song.tracks)):
        y0 = lane_top(i, daw)
        y1 = y0 + daw.track_height
        if y0 <= y <= y1:
            return i
    return None


def x_to_tick(daw: DAW, x: float) -> int:
    return max(0, int(round((x - LEFT_PAD) / px_per_tick(daw))))


def snap_tick_to_beat(song: Song, tick: int) -> int:
    tpb = ticks_per_beat(song)
    return max(0, round(tick / tpb) * tpb)


def mouse_in_timeline() -> tuple[float, float] | None:
    try:
        mx, my = dpg.get_mouse_pos(local=False)
        try:
            state = dpg.get_item_state(SCROLLER)
        except Exception:
            state = None
        if state and "rect_min" in state:
            wx, wy = state["rect_min"]
        elif state and "pos" in state:
            wx, wy = state["pos"]
        else:
            wx, wy = dpg.get_item_pos(SCROLLER)
        return mx - wx + dpg.get_x_scroll(SCROLLER), my - wy + dpg.get_y_scroll(SCROLLER)
    except Exception:
        return None

def preview_block_tuple(daw: DAW) -> tuple[int, int] | None:
    return daw.ui.focused_block if daw.ui.mouse_down and daw.ui.mode in {"move", "resize_left", "resize_right"} else None


def display_track_index(daw: DAW, track_i: int, block_i: int) -> int:
    active = preview_block_tuple(daw)
    if active == (track_i, block_i) and daw.ui.preview_track is not None:
        return daw.ui.preview_track
    return track_i


def display_start_len(daw: DAW, track_i: int, block_i: int, block: Block) -> tuple[int, int]:
    active = preview_block_tuple(daw)
    if active == (track_i, block_i) and daw.ui.preview_start is not None and daw.ui.preview_len is not None:
        return daw.ui.preview_start, daw.ui.preview_len
    return block.start_tick, block_len(daw.song, block)


def hit_block(daw: DAW, x: float, y: float):
    for i, track in enumerate(daw.song.tracks):
        for j in range(len(track.blocks) - 1, -1, -1):
            x0, y0, x1, y1 = block_rect(daw, i, j, track.blocks[j])
            if x0 <= x <= x1 and y0 <= y <= y1:
                return i, j
    return None


def hit_block_edge(daw: DAW, x: float, y: float):
    hit = hit_block(daw, x, y)
    if hit is None:
        return None
    i, j = hit
    x0, _, x1, _ = block_rect(daw, i, j, daw.song.tracks[i].blocks[j])
    if abs(x - x0) <= EDGE_PX:
        return "resize_left", i, j
    if abs(x - x1) <= EDGE_PX:
        return "resize_right", i, j
    return None


def draw_pattern_preview(drawlist: str, daw: DAW, block: Block, x0: float, y0: float, y1: float) -> None:
    source = resolve(daw.song, block.ref)
    ppt = daw.px_per_beat
    if isinstance(source, Recording):
        mid, prev, steps = (y0 + y1) / 2, None, max(8, int((block_len(daw.song, block) * px_per_tick(daw)) / 10))
        for k in range(steps + 1):
            xx = x0 + (block_len(daw.song, block) * px_per_tick(daw)) * k / steps
            yy = mid + math.sin(0.35 * k) * (daw.track_height * 0.12)
            if prev: dpg.draw_line(prev, (xx, yy), color=(32, 32, 32, 255), thickness=2, parent=drawlist)
            prev = (xx, yy)
        return
    if block.ref.kind == BlockKind.PATTERN:
        hit_w = max(2.0, ppt * 0.12)
        for note in source.notes:
            nx = x0 + note.start_tick * px_per_tick(daw)
            dpg.draw_rectangle((nx, y1 - 12), (nx + hit_w, y1 - 6), fill=(30, 30, 30, 255), color=(30, 30, 30, 255), parent=drawlist)
    else:
        lo, hi = min(n.pitch for n in source.notes), max(n.pitch for n in source.notes)
        span = max(1, hi - lo)
        for note in source.notes:
            frac = (note.pitch - lo) / span
            ny0 = y1 - 10 - frac * max(12, y1 - y0 - 18)
            nx0 = x0 + note.start_tick * px_per_tick(daw)
            nx1 = max(nx0 + max(3.0, ppt * 0.15), nx0 + note.length_ticks * px_per_tick(daw))
            dpg.draw_rectangle((nx0, ny0), (nx1, ny0 + 6), fill=(30, 30, 30, 255), color=(30, 30, 30, 255), parent=drawlist)


def draw_header(daw: DAW, w: int, h: int) -> None:
    tpb, bar = ticks_per_beat(daw.song), ticks_per_bar(daw.song)
    total_ticks = daw.timeline_beats * tpb
    for beat in range(daw.timeline_beats + 1):
        tick = beat * tpb
        x = LEFT_PAD + tick * px_per_tick(daw)
        is_bar = (tick % bar) == 0
        dpg.draw_line((x, HEADER_H), (x, h - TOP_PAD), color=GRID_MAJOR if is_bar else GRID_MINOR, thickness=2 if is_bar else 1, parent=DRAWLIST)
        if beat < daw.timeline_beats:
            if is_bar: dpg.draw_text((x + 1, 1), f"|{beat // max(1, daw.song.meter.numerator) + 1}", color=TEXT, size=15, parent=DRAWLIST)
            dpg.draw_text((x + 1, 16), str(beat), color=TEXT_SOFT, size=12, parent=DRAWLIST)


def draw_timeline(daw: DAW) -> None:
    w, h = dpg.get_item_rect_size(SCROLLER)
    if w < 20 or h < 20:
        return
    cw = max(int(LEFT_PAD * 2 + daw.timeline_beats * daw.px_per_beat), int(w - 16))
    ch = max(int(HEADER_H + TOP_PAD * 2 + len(daw.song.tracks) * (daw.track_height + TRACK_GAP) + 20), int(h - 16))
    dpg.configure_item(DRAWLIST, width=cw, height=ch)
    dpg.delete_item(DRAWLIST, children_only=True)
    dpg.draw_rectangle((0, 0), (cw, ch), fill=(10, 10, 12, 255), color=(10, 10, 12, 255), parent=DRAWLIST)
    draw_header(daw, cw, ch)
    for i, track in enumerate(daw.song.tracks):
        y = HEADER_H + TOP_PAD + i * (daw.track_height + TRACK_GAP)
        dpg.draw_rectangle((LEFT_PAD, y), (cw - LEFT_PAD, y + daw.track_height), fill=LANE_BG, color=LANE_BORDER, rounding=6, parent=DRAWLIST)
        for j, block in sorted(enumerate(track.blocks), key=lambda p: display_start_len(daw, i, p[0], p[1])[0]):
            view_track = display_track_index(daw, i, j)
            if view_track != i:
                continue
            start_tick, length_ticks = display_start_len(daw, i, j, block)
            x0 = LEFT_PAD + start_tick * px_per_tick(daw)
            x1 = x0 + length_ticks * px_per_tick(daw)
            y0, y1 = y + 6, y + daw.track_height - 6
            focused = daw.ui.focused_block == (i, j)
            dpg.draw_rectangle(
                (x0, y0),
                (x1, y1),
                fill=(92, 92, 96, 255) if block.muted else FILL[block.ref.kind],
                color=(255, 255, 255, 255) if focused else (18, 18, 18, 255),
                thickness=3 if focused else 1,
                rounding=6,
                parent=DRAWLIST,
            )
            dpg.draw_text((x0 + 6, y0 + 6), track.name, color=(18, 18, 18, 255), size=14, parent=DRAWLIST)
            draw_pattern_preview(DRAWLIST, daw, block, x0, y0 + 22, y1 - 4)


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


# ---------- actions ----------
def on_track_selected(sender, app_data, user_data):
    daw, i = user_data
    do(daw, [("set", ("tracks", i, "selected"), daw.song.tracks[i].selected, bool(app_data))])


def on_track_name(sender, app_data, user_data):
    daw, i = user_data
    new = app_data or "track"
    old = daw.song.tracks[i].name
    if new != old:
        do(daw, [("set", ("tracks", i, "name"), old, new)])


def on_bpm(sender, app_data, user_data):
    daw = user_data
    new = max(1.0, float(app_data))
    if new != daw.song.bpm:
        do(daw, [("set", ("bpm",), daw.song.bpm, new)])


def on_meter_num(sender, app_data, user_data):
    daw = user_data
    new = max(1, int(app_data))
    if new != daw.song.meter.numerator:
        do(daw, [("set", ("meter", "numerator"), daw.song.meter.numerator, new)])


def on_meter_den(sender, app_data, user_data):
    daw = user_data
    new = max(1, int(app_data))
    if new != daw.song.meter.denominator:
        do(daw, [("set", ("meter", "denominator"), daw.song.meter.denominator, new)])


def on_px(sender, app_data, user_data):
    daw = user_data
    new = max(8.0, float(app_data))
    if new != daw.px_per_beat:
        daw.px_per_beat = new
        daw.dirty = True


def on_track_h(sender, app_data, user_data):
    daw = user_data
    new = max(36, int(app_data))
    if new != daw.track_height:
        daw.track_height = new
        daw.dirty = True


def on_add_track(sender, app_data, user_data):
    daw = user_data
    tid = len(daw.song.tracks)
    track = Track(f"track {tid}", Instrument())
    do(daw, [("insert", ("tracks",), len(daw.song.tracks), track)], "added track")


def on_del_track(sender, app_data, user_data):
    daw = user_data
    if not daw.song.tracks:
        return
    i = max([j for j, t in enumerate(daw.song.tracks) if t.selected], default=len(daw.song.tracks) - 1)
    do(daw, [("delete", ("tracks",), i, daw.song.tracks[i])], "deleted track")


def make_block_for_track(song: Song, track: Track, start_tick: int) -> Block:
    ref = BlockRef(BlockKind.PIANO_ROLL, 0) if "808" in track.name.lower() else BlockRef(BlockKind.PATTERN, default_pattern(song))
    return Block(ref, start_tick, ticks_per_bar(song))


def focus_block(daw: DAW, track_i: int, block_i: int):
    daw.ui.focused_track = track_i
    daw.ui.focused_block = (track_i, block_i)
    block = daw.song.tracks[track_i].blocks[block_i]
    print("focused block", {
        "track": track_i,
        "block": block_i,
        "kind": block.ref.kind,
        "object_id": block.ref.object_id,
        "start_tick": block.start_tick,
        "length_ticks": block_len(daw.song, block),
    })
    daw.dirty = True


def add_block_at_click(daw: DAW, track_i: int, tick: int):
    track = daw.song.tracks[track_i]
    start = (tick // ticks_per_bar(daw.song)) * ticks_per_bar(daw.song)
    block = make_block_for_track(daw.song, track, start)
    j = len(track.blocks)
    do(daw, [("insert", ("tracks", track_i, "blocks"), j, block)], "added block")
    daw.ui.focused_track = track_i
    daw.ui.focused_block = (track_i, j)


def reset_drag_preview(daw: DAW):
    daw.ui.mode = None
    daw.ui.mouse_down = False
    daw.ui.drag_origin_track = None
    daw.ui.drag_offset_tick = 0
    daw.ui.preview_track = None
    daw.ui.preview_start = None
    daw.ui.preview_len = None


def begin_drag(daw: DAW, mode: str, track_i: int, block_i: int, tick: int):
    focus_block(daw, track_i, block_i)
    block = daw.song.tracks[track_i].blocks[block_i]
    daw.ui.mode = mode
    daw.ui.mouse_down = True
    daw.ui.drag_origin_tick = tick
    daw.ui.drag_origin_track = track_i
    daw.ui.drag_origin_start = block.start_tick
    daw.ui.drag_origin_len = block_len(daw.song, block)
    daw.ui.drag_offset_tick = tick - block.start_tick
    daw.ui.preview_track = track_i
    daw.ui.preview_start = block.start_tick
    daw.ui.preview_len = block_len(daw.song, block)


def update_drag_preview(daw: DAW, tick: int, y: float):
    if daw.ui.focused_block is None or daw.ui.mode is None:
        return
    tpb = ticks_per_beat(daw.song)
    min_len = tpb
    origin_track = daw.ui.drag_origin_track if daw.ui.drag_origin_track is not None else daw.ui.focused_block[0]
    origin_start = daw.ui.drag_origin_start
    origin_len = daw.ui.drag_origin_len
    origin_end = origin_start + origin_len
    if daw.ui.mode == "move":
        preview_track = y_to_track(daw, y)
        if preview_track is None:
            preview_track = origin_track
        preview_start = snap_tick_to_beat(daw.song, tick - daw.ui.drag_offset_tick)
        daw.ui.preview_track = preview_track
        daw.ui.preview_start = preview_start
        daw.ui.preview_len = origin_len
    elif daw.ui.mode == "resize_left":
        new_start = max(0, min(snap_tick_to_beat(daw.song, tick), origin_end - min_len))
        daw.ui.preview_track = origin_track
        daw.ui.preview_start = new_start
        daw.ui.preview_len = origin_end - new_start
    elif daw.ui.mode == "resize_right":
        new_end = max(origin_start + min_len, snap_tick_to_beat(daw.song, tick))
        daw.ui.preview_track = origin_track
        daw.ui.preview_start = origin_start
        daw.ui.preview_len = new_end - origin_start
    daw.dirty = True


def commit_drag(daw: DAW):
    active = daw.ui.focused_block
    if active is None or daw.ui.drag_origin_track is None or daw.ui.preview_start is None or daw.ui.preview_len is None or daw.ui.preview_track is None:
        reset_drag_preview(daw)
        return
    origin_track_i, origin_block_i = active
    dest_track_i = daw.ui.preview_track
    src_track = daw.song.tracks[origin_track_i]
    block = src_track.blocks[origin_block_i]
    ops = []
    if daw.ui.mode == "move":
        moved_tracks = dest_track_i != origin_track_i
        moved_start = daw.ui.preview_start != daw.ui.drag_origin_start
        if moved_tracks:
            moved_block = Block(block.ref, daw.ui.preview_start, daw.ui.preview_len, block.loop, block.muted)
            dest_index = len(daw.song.tracks[dest_track_i].blocks)
            ops = [
                ("delete", ("tracks", origin_track_i, "blocks"), origin_block_i, block),
                ("insert", ("tracks", dest_track_i, "blocks"), dest_index, moved_block),
            ]
            if ops:
                do(daw, ops, "moved block")
                daw.ui.focused_track = dest_track_i
                daw.ui.focused_block = (dest_track_i, dest_index)
        elif moved_start:
            ops = [
                ("set", ("tracks", origin_track_i, "blocks", origin_block_i, "start_tick"), daw.ui.drag_origin_start, daw.ui.preview_start),
            ]
            do(daw, ops, "moved block")
    elif daw.ui.mode in {"resize_left", "resize_right"}:
        if daw.ui.preview_start != daw.ui.drag_origin_start:
            ops.append(("set", ("tracks", origin_track_i, "blocks", origin_block_i, "start_tick"), daw.ui.drag_origin_start, daw.ui.preview_start))
        if daw.ui.preview_len != daw.ui.drag_origin_len:
            ops.append(("set", ("tracks", origin_track_i, "blocks", origin_block_i, "length_ticks"), daw.ui.drag_origin_len, daw.ui.preview_len))
        if ops:
            do(daw, ops, "resized block")
    reset_drag_preview(daw)
    daw.dirty = True


def on_mouse_down(sender, app_data, user_data):
    daw = user_data
    if not dpg.is_item_hovered(SCROLLER):
        return
    pos = mouse_in_timeline()
    if pos is None:
        return
    x, y = pos
    if x < LEFT_PAD or y < HEADER_H:
        return
    tick = x_to_tick(daw, x)
    edge = hit_block_edge(daw, x, y)
    if edge is not None:
        mode, track_i, block_i = edge
        begin_drag(daw, mode, track_i, block_i, tick)
        return
    hit = hit_block(daw, x, y)
    if hit is not None:
        track_i, block_i = hit
        begin_drag(daw, "move", track_i, block_i, tick)
        return
    track_i = y_to_track(daw, y)
    if track_i is not None:
        add_block_at_click(daw, track_i, tick)


def on_mouse_drag(sender, app_data, user_data):
    daw = user_data
    if not daw.ui.mouse_down or daw.ui.mode is None:
        return
    pos = mouse_in_timeline()
    if pos is None:
        return
    x, y = pos
    update_drag_preview(daw, x_to_tick(daw, x), y)


def on_mouse_release(sender, app_data, user_data):
    commit_drag(user_data)


def on_export(sender, app_data, user_data):
    daw = user_data
    write_wav("block_daw_export.wav", render_song(daw.song))
    if dpg.does_item_exist("status"):
        dpg.set_value("status", "wrote block_daw_export.wav")


def on_key_z(sender, app_data, user_data):
    daw = user_data
    if dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl):
        undo(daw)


def on_key_r(sender, app_data, user_data):
    daw = user_data
    if dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl):
        redo(daw)


# ---------- app ----------
def Render(daw: DAW) -> None:
    dpg.create_context()
    with dpg.handler_registry():
        dpg.add_key_press_handler(dpg.mvKey_Z, callback=on_key_z, user_data=daw)
        dpg.add_key_press_handler(dpg.mvKey_R, callback=on_key_r, user_data=daw)
        dpg.add_mouse_down_handler(callback=on_mouse_down, user_data=daw)
        dpg.add_mouse_drag_handler(callback=on_mouse_drag, user_data=daw)
        dpg.add_mouse_release_handler(callback=on_mouse_release, user_data=daw)
    with dpg.window(tag=ROOT, label="block daw"):
        with dpg.group(horizontal=True):
            dpg.add_text("BPM")
            dpg.add_input_float(default_value=daw.song.bpm, width=90, callback=on_bpm, user_data=daw, format="%.1f")
            dpg.add_text("Meter")
            dpg.add_input_int(default_value=daw.song.meter.numerator, width=60, callback=on_meter_num, user_data=daw)
            dpg.add_text("/")
            dpg.add_input_int(default_value=daw.song.meter.denominator, width=60, callback=on_meter_den, user_data=daw)
            dpg.add_text("px/beat")
            dpg.add_input_float(default_value=daw.px_per_beat, width=90, callback=on_px, user_data=daw, format="%.1f")
            dpg.add_text("h/track")
            dpg.add_input_int(default_value=daw.track_height, width=70, callback=on_track_h, user_data=daw)
            dpg.add_button(label="+ track", callback=on_add_track, user_data=daw)
            dpg.add_button(label="- track", callback=on_del_track, user_data=daw)
            dpg.add_button(label="Export WAV", callback=on_export, user_data=daw)
            dpg.add_text("", tag="status")
        dpg.add_separator()
        with dpg.group(horizontal=True):
            with dpg.child_window(width=210, autosize_y=True, border=False):
                dpg.add_child_window(tag=ROWS, autosize_x=True, autosize_y=True, border=False)
            with dpg.child_window(tag=SCROLLER, autosize_x=True, autosize_y=True, border=False, horizontal_scrollbar=True):
                dpg.add_drawlist(tag=DRAWLIST, width=4000, height=900)
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
