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
class DAW:
    song: Song
    px_per_beat: float = 48.0
    track_height: int = 96
    timeline_beats: int = 2048
    undo: list[list[tuple]] = field(default_factory=list)
    redo: list[list[tuple]] = field(default_factory=list)
    dirty: bool = True


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
        for block in sorted(track.blocks, key=lambda b: b.start_tick):
            x0 = LEFT_PAD + block.start_tick * px_per_tick(daw)
            x1 = x0 + block_len(daw.song, block) * px_per_tick(daw)
            y0, y1 = y + 6, y + daw.track_height - 6
            dpg.draw_rectangle((x0, y0), (x1, y1), fill=(92, 92, 96, 255) if block.muted else FILL[block.ref.kind], color=(18, 18, 18, 255), rounding=6, parent=DRAWLIST)
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


def on_add_block(sender, app_data, user_data):
    daw = user_data
    if not daw.song.tracks:
        return
    i = max([j for j, t in enumerate(daw.song.tracks) if t.selected], default=0)
    tpb = ticks_per_beat(daw.song)
    track = daw.song.tracks[i]
    start = max((b.start_tick + block_len(daw.song, b) for b in track.blocks), default=0)
    pid = default_pattern(daw.song) if "808" not in track.name.lower() else 0
    ref = BlockRef(BlockKind.PATTERN, default_pattern(daw.song)) if "808" not in track.name.lower() else BlockRef(BlockKind.PIANO_ROLL, 0)
    block = Block(ref, start, 4 * tpb)
    do(daw, [("insert", ("tracks", i, "blocks"), len(track.blocks), block)], "added block")


def on_del_block(sender, app_data, user_data):
    daw = user_data
    chosen = [(i, t) for i, t in enumerate(daw.song.tracks) if t.selected and t.blocks]
    if not chosen:
        return
    i, track = chosen[-1]
    j = len(track.blocks) - 1
    do(daw, [("delete", ("tracks", i, "blocks"), j, track.blocks[j])], "deleted block")


def on_nudge(sender, app_data, user_data):
    daw, delta = user_data
    for i, track in enumerate(daw.song.tracks):
        if track.selected:
            ops = [("set", ("tracks", i, "blocks", j, "start_tick"), b.start_tick, max(0, b.start_tick + delta)) for j, b in enumerate(track.blocks)]
            do(daw, ops, "nudged blocks")
            return


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
            dpg.add_button(label="+ block", callback=on_add_block, user_data=daw)
            dpg.add_button(label="- block", callback=on_del_block, user_data=daw)
            dpg.add_button(label="< beat", callback=on_nudge, user_data=(daw, -ticks_per_beat(daw.song)))
            dpg.add_button(label="> beat", callback=on_nudge, user_data=(daw, ticks_per_beat(daw.song)))
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
