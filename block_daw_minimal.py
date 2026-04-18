from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import math
import random
import struct
import wave

import dearpygui.dearpygui as dpg

SR = 44100
TICKS_PER_QUARTER = 960
random.seed(0)


# ---------------- musical time ----------------

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
    sample_path: str = ""


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
    length_ticks: Optional[int] = None
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
    bpm: float
    meter: Meter = field(default_factory=Meter)
    patterns: dict[int, Pattern] = field(default_factory=dict)
    piano_rolls: dict[int, PianoRoll] = field(default_factory=dict)
    recordings: dict[int, Recording] = field(default_factory=dict)
    tracks: list[Track] = field(default_factory=list)


@dataclass
class DAW:
    song: Song
    px_per_beat: float = 72.0
    track_height: int = 96
    timeline_total_beats: int = 2048
    dirty: bool = True


# ---------------- conversions ----------------

def ticks_per_beat(song: Song) -> int:
    return max(1, (TICKS_PER_QUARTER * 4) // max(1, song.meter.denominator))


def ticks_per_bar(song: Song) -> int:
    return max(1, song.meter.numerator * ticks_per_beat(song))


def ticks_to_seconds(song: Song, ticks: int) -> float:
    return ticks * (60.0 / song.bpm) / TICKS_PER_QUARTER


def ticks_to_beats(song: Song, ticks: int) -> float:
    return ticks / ticks_per_beat(song)


def beats_to_ticks(song: Song, beats: float) -> int:
    return int(round(beats * ticks_per_beat(song)))


def px_per_tick(daw: DAW) -> float:
    return daw.px_per_beat / ticks_per_beat(daw.song)


def step_to_ticks(step: int) -> int:
    return step * (TICKS_PER_QUARTER // 4)


def make_note_from_steps(step: int, length: int, pitch: int, velocity: float = 1.0) -> Note:
    return Note(start_tick=step_to_ticks(step), length_ticks=step_to_ticks(length), pitch=pitch, velocity=velocity)


# ---------------- model helpers ----------------

def resolve_block_source(song: Song, ref: BlockRef):
    if ref.kind == BlockKind.PATTERN:
        return song.patterns[ref.object_id]
    if ref.kind == BlockKind.PIANO_ROLL:
        return song.piano_rolls[ref.object_id]
    if ref.kind == BlockKind.RECORDING:
        return song.recordings[ref.object_id]
    raise KeyError(ref.kind)


def block_length_ticks(song: Song, block: Block) -> int:
    if block.length_ticks is not None:
        return block.length_ticks
    return resolve_block_source(song, block.ref).length_ticks


def selected_tracks(song: Song) -> list[Track]:
    chosen = [t for t in song.tracks if t.selected]
    return chosen if chosen else song.tracks


def make_pattern(name: str, step_notes: list[tuple[int, int, int, float]]) -> Pattern:
    notes = [make_note_from_steps(step, length, pitch, velocity) for step, length, pitch, velocity in step_notes]
    return Pattern(name=name, length_ticks=step_to_ticks(16), notes=notes)


def make_roll(name: str, step_notes: list[tuple[int, int, int, float]]) -> PianoRoll:
    notes = [make_note_from_steps(step, length, pitch, velocity) for step, length, pitch, velocity in step_notes]
    return PianoRoll(name=name, length_ticks=step_to_ticks(16), notes=notes)


def make_demo_daw() -> DAW:
    song = Song(bpm=142.0, meter=Meter(4, 4))

    song.patterns[0] = make_pattern("kick", [
        (0, 1, 36, 1.0),
        (6, 1, 36, 1.0),
        (11, 1, 36, 1.0),
    ])
    song.patterns[1] = make_pattern("snare", [
        (4, 1, 38, 0.95),
        (12, 1, 38, 0.95),
    ])
    song.patterns[2] = make_pattern("hat", [
        (step, 1, 42, 0.35 if step % 4 else 0.65) for step in range(16)
    ])
    song.piano_rolls[0] = make_roll("808", [
        (0, 4, 36, 1.0),
        (6, 2, 36, 1.0),
        (8, 4, 34, 1.0),
        (12, 4, 31, 1.0),
    ])

    kick_inst = Instrument(waveform="sine", decay=0.10, pitch_drop=24.0, click=0.20)
    snare_inst = Instrument(waveform="square", decay=0.05, noise=0.65, click=0.08)
    hat_inst = Instrument(waveform="noise", decay=0.02, noise=0.85)
    bass_inst = Instrument(waveform="sine", decay=0.20, sustain=0.85, release=0.08, pitch_drop=7.0, click=0.02)

    one_bar = step_to_ticks(16)
    song.tracks = [
        Track("kick", kick_inst, True, [
            Block(BlockRef(BlockKind.PATTERN, 0), 0),
            Block(BlockRef(BlockKind.PATTERN, 0), one_bar),
        ]),
        Track("snare", snare_inst, True, [
            Block(BlockRef(BlockKind.PATTERN, 1), 0),
            Block(BlockRef(BlockKind.PATTERN, 1), one_bar),
        ]),
        Track("hat", hat_inst, True, [
            Block(BlockRef(BlockKind.PATTERN, 2), 0),
            Block(BlockRef(BlockKind.PATTERN, 2), one_bar),
        ]),
        Track("808", bass_inst, True, [
            Block(BlockRef(BlockKind.PIANO_ROLL, 0), 0),
            Block(BlockRef(BlockKind.PIANO_ROLL, 0), one_bar),
        ]),
    ]
    return DAW(song=song)


# ---------------- audio ----------------

def midi_to_hz(m: int) -> float:
    return 440.0 * (2.0 ** ((m - 69) / 12.0))


def osc_sample(waveform: str, phase: float) -> float:
    x = phase - math.floor(phase)
    if waveform == "sine":
        return math.sin(2 * math.pi * x)
    if waveform == "square":
        return 1.0 if x < 0.5 else -1.0
    if waveform == "saw":
        return 2.0 * x - 1.0
    if waveform == "noise":
        return random.uniform(-1.0, 1.0)
    return math.sin(2 * math.pi * x)


def adsr(inst: Instrument, t: float, hold: float) -> float:
    a, d, s, r = inst.attack, inst.decay, inst.sustain, inst.release
    if a > 0 and t < a:
        return t / a
    t2 = t - a
    if d > 0 and t2 < d:
        return 1.0 + (s - 1.0) * (t2 / d)
    if t < hold:
        return s
    if r <= 0:
        return 0.0
    rel_t = t - hold
    if rel_t < r:
        return s * (1.0 - rel_t / r)
    return 0.0


def render_note(song: Song, inst: Instrument, note: Note) -> list[float]:
    hold = max(0.0, ticks_to_seconds(song, note.length_ticks))
    total = hold + inst.release
    n = max(1, int(total * SR))
    out = [0.0] * n
    base_hz = midi_to_hz(note.pitch)
    phase = 0.0

    for i in range(n):
        t = i / SR
        env = adsr(inst, t, hold)
        if env <= 0.0:
            continue
        if inst.pitch_drop > 0.0:
            drop_window = min(0.08, total)
            frac = min(1.0, t / max(drop_window, 1e-9))
            semis = inst.pitch_drop * (1.0 - frac)
            hz = base_hz * (2.0 ** (semis / 12.0))
        else:
            hz = base_hz
        phase += hz / SR
        tonal = osc_sample(inst.waveform, phase)
        noise = random.uniform(-1.0, 1.0) * inst.noise
        click = (1.0 - min(1.0, t / 0.004)) * inst.click if t < 0.004 else 0.0
        out[i] = (0.9 * tonal + noise + click) * env * note.velocity
    return out


def iter_block_notes(song: Song, block: Block):
    source = resolve_block_source(song, block.ref)
    if isinstance(source, Recording):
        return
    clip_len = block_length_ticks(song, block)
    src_len = max(1, source.length_ticks)
    if block.loop and clip_len > src_len:
        k = 0
        while k * src_len < clip_len:
            off = k * src_len
            for note in source.notes:
                rel = off + note.start_tick
                if rel < clip_len:
                    yield rel, note
            k += 1
    else:
        for note in source.notes:
            if note.start_tick < clip_len:
                yield note.start_tick, note


def song_end_tick(song: Song) -> int:
    end_tick = ticks_per_bar(song) * 2
    for track in selected_tracks(song):
        for block in track.blocks:
            end_tick = max(end_tick, block.start_tick + block_length_ticks(song, block))
    return end_tick


def render_song_to_samples(song: Song) -> list[float]:
    total_seconds = ticks_to_seconds(song, song_end_tick(song)) + 0.25
    n_samples = max(1, int(total_seconds * SR))
    mix = [0.0] * n_samples

    for track in selected_tracks(song):
        for block in track.blocks:
            if block.muted:
                continue
            source = resolve_block_source(song, block.ref)
            if isinstance(source, Recording):
                continue
            for rel_start_tick, note in iter_block_notes(song, block):
                start = int(ticks_to_seconds(song, block.start_tick + rel_start_tick) * SR)
                audio = render_note(song, track.instrument, note)
                end = min(n_samples, start + len(audio))
                for j in range(end - start):
                    mix[start + j] += audio[j]

    for i, x in enumerate(mix):
        mix[i] = math.tanh(1.4 * x)
    return mix


def write_wav(path: str, samples: list[float]) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        frames = bytearray()
        for s in samples:
            s = max(-1.0, min(1.0, s))
            frames += struct.pack("<h", int(s * 32767))
        wf.writeframes(frames)


# ---------------- gui ----------------
TOP_BAR = "top_bar"
ROWS = "rows_panel"
SCROLLER = "timeline_scroll"
DRAWLIST = "timeline_drawlist"
STATUS = "status_text"
BPM = "bpm_input"
METER_NUM = "meter_num_input"
METER_DEN = "meter_den_input"
PX = "px_per_beat_input"
TRACK_H = "track_height_input"

LEFT_PAD = 10
TOP_PAD = 10
HEADER_H = 34
TRACK_GAP = 10
LANE_BG = (24, 24, 27, 255)
LANE_BORDER = (62, 62, 66, 255)
GRID_MINOR = (48, 48, 52, 255)
GRID_MAJOR = (92, 92, 98, 255)
TEXT = (232, 214, 92, 255)
TEXT_SOFT = (180, 166, 88, 255)
BLOCK_FILL = {
    BlockKind.PATTERN: (184, 146, 28, 255),
    BlockKind.PIANO_ROLL: (214, 178, 42, 255),
    BlockKind.RECORDING: (140, 114, 30, 255),
}


class Runtime:
    def __init__(self) -> None:
        self.last_sig = None


RUNTIME = Runtime()


def mark_dirty(daw: DAW, msg: str = "") -> None:
    daw.dirty = True
    if msg and dpg.does_item_exist(STATUS):
        dpg.set_value(STATUS, msg)


def sync_top_bar(daw: DAW) -> None:
    dpg.set_value(BPM, daw.song.bpm)
    dpg.set_value(METER_NUM, daw.song.meter.numerator)
    dpg.set_value(METER_DEN, daw.song.meter.denominator)
    dpg.set_value(PX, daw.px_per_beat)
    dpg.set_value(TRACK_H, daw.track_height)


def rebuild_rows(daw: DAW) -> None:
    dpg.delete_item(ROWS, children_only=True)
    for i, track in enumerate(daw.song.tracks):
        with dpg.group(horizontal=True, parent=ROWS):
            dpg.add_checkbox(default_value=track.selected, callback=on_track_selected, user_data=(daw, i))
            dpg.add_text(track.name)
            dpg.add_spacer(height=max(0, daw.track_height - 24))


def draw_pattern_contents(drawlist: str, song: Song, x0: float, y0: float, y1: float, source: Pattern | PianoRoll, kind: BlockKind, pptick: float):
    if kind == BlockKind.PATTERN:
        for note in source.notes:
            nx0 = x0 + note.start_tick * pptick
            nx1 = max(nx0 + 2, nx0 + note.length_ticks * pptick)
            dpg.draw_line((nx0, y0 + 4), (nx0, y1 - 4), color=(32, 32, 32, 255), thickness=2, parent=drawlist)
            dpg.draw_rectangle((nx0, y1 - 12), (nx1, y1 - 6), fill=(32, 32, 32, 255), color=(32, 32, 32, 255), parent=drawlist)
    else:
        if not source.notes:
            return
        pitches = [n.pitch for n in source.notes]
        lo, hi = min(pitches), max(pitches)
        span = max(1, hi - lo)
        for note in source.notes:
            frac = (note.pitch - lo) / span
            ny0 = y1 - 10 - frac * max(12, (y1 - y0 - 18))
            ny1 = ny0 + 6
            nx0 = x0 + note.start_tick * pptick
            nx1 = max(nx0 + 4, nx0 + note.length_ticks * pptick)
            dpg.draw_rectangle((nx0, ny0), (nx1, ny1), fill=(32, 32, 32, 255), color=(32, 32, 32, 255), parent=drawlist)


def draw_header(drawlist: str, daw: DAW, width: int, height: int) -> None:
    tpb = ticks_per_beat(daw.song)
    bar_ticks = ticks_per_bar(daw.song)
    total_ticks = daw.timeline_total_beats * tpb
    pptick = px_per_tick(daw)

    for beat_index in range(daw.timeline_total_beats + 1):
        tick = beat_index * tpb
        x = LEFT_PAD + tick * pptick
        is_bar = (tick % bar_ticks) == 0
        dpg.draw_line((x, HEADER_H), (x, height - TOP_PAD), color=GRID_MAJOR if is_bar else GRID_MINOR, thickness=2 if is_bar else 1, parent=drawlist)
        if beat_index < daw.timeline_total_beats:
            if is_bar:
                bar_number = beat_index // max(1, daw.song.meter.numerator) + 1
                dpg.draw_text((x + 2, 2), f"|{bar_number}", color=TEXT, size=15, parent=drawlist)
            dpg.draw_text((x + 2, 16), str(beat_index), color=TEXT_SOFT, size=12, parent=drawlist)

    x_end = LEFT_PAD + total_ticks * pptick
    dpg.draw_line((x_end, HEADER_H), (x_end, height - TOP_PAD), color=GRID_MINOR, thickness=1, parent=drawlist)


def draw_timeline(daw: DAW) -> None:
    w, h = dpg.get_item_rect_size(SCROLLER)
    if w <= 10 or h <= 10:
        return

    content_width = max(int(LEFT_PAD * 2 + daw.timeline_total_beats * daw.px_per_beat), int(w - 16))
    content_height = max(int(HEADER_H + TOP_PAD * 2 + len(daw.song.tracks) * (daw.track_height + TRACK_GAP) + 20), int(h - 16))
    dpg.configure_item(DRAWLIST, width=content_width, height=content_height)
    dpg.delete_item(DRAWLIST, children_only=True)
    dpg.draw_rectangle((0, 0), (content_width, content_height), fill=(12, 12, 14, 255), color=(12, 12, 14, 255), parent=DRAWLIST)

    draw_header(DRAWLIST, daw, content_width, content_height)
    pptick = px_per_tick(daw)

    for ti, track in enumerate(daw.song.tracks):
        y = HEADER_H + TOP_PAD + ti * (daw.track_height + TRACK_GAP)
        dpg.draw_rectangle((LEFT_PAD, y), (content_width - LEFT_PAD, y + daw.track_height), fill=LANE_BG, color=LANE_BORDER, rounding=6, parent=DRAWLIST)
        for block in track.blocks:
            x0 = LEFT_PAD + block.start_tick * pptick
            x1 = x0 + block_length_ticks(daw.song, block) * pptick
            y0 = y + 6
            y1 = y + daw.track_height - 6
            fill = (92, 92, 96, 255) if block.muted else BLOCK_FILL[block.ref.kind]
            dpg.draw_rectangle((x0, y0), (x1, y1), fill=fill, color=(20, 20, 20, 255), rounding=6, parent=DRAWLIST)
            src = resolve_block_source(daw.song, block.ref)
            dpg.draw_text((x0 + 6, y0 + 6), src.name, color=(18, 18, 18, 255), size=14, parent=DRAWLIST)
            if isinstance(src, (Pattern, PianoRoll)):
                draw_pattern_contents(DRAWLIST, daw.song, x0 + 4, y0 + 24, y1 - 4, src, block.ref.kind, pptick)
            else:
                mid = (y0 + y1) / 2
                prev = None
                steps = max(8, int((x1 - x0) / 10))
                for k in range(steps + 1):
                    xx = x0 + (x1 - x0) * k / steps
                    yy = mid + math.sin(0.35 * k) * (daw.track_height * 0.12)
                    if prev is not None:
                        dpg.draw_line(prev, (xx, yy), color=(32, 32, 32, 255), thickness=2, parent=DRAWLIST)
                    prev = (xx, yy)


def redraw_if_needed(daw: DAW) -> None:
    track_sig = tuple((t.selected, len(t.blocks), tuple((b.start_tick, block_length_ticks(daw.song, b), b.ref.kind, b.ref.object_id, b.muted, b.loop) for b in t.blocks)) for t in daw.song.tracks)
    sig = (
        daw.dirty,
        daw.song.bpm,
        daw.song.meter.numerator,
        daw.song.meter.denominator,
        daw.px_per_beat,
        daw.track_height,
        daw.timeline_total_beats,
        track_sig,
    )
    size = tuple(dpg.get_item_rect_size(SCROLLER))
    cur = (sig, size)
    if cur != RUNTIME.last_sig:
        rebuild_rows(daw)
        draw_timeline(daw)
        RUNTIME.last_sig = cur
        daw.dirty = False


def on_track_selected(sender, app_data, user_data):
    daw, i = user_data
    daw.song.tracks[int(i)].selected = bool(app_data)
    mark_dirty(daw)


def on_bpm(sender, app_data, user_data):
    daw = user_data
    daw.song.bpm = max(1.0, float(app_data))
    mark_dirty(daw)


def on_meter_num(sender, app_data, user_data):
    daw = user_data
    daw.song.meter.numerator = max(1, int(app_data))
    mark_dirty(daw)


def on_meter_den(sender, app_data, user_data):
    daw = user_data
    daw.song.meter.denominator = max(1, int(app_data))
    mark_dirty(daw)


def on_px_per_beat(sender, app_data, user_data):
    daw = user_data
    daw.px_per_beat = max(8.0, min(240.0, float(app_data)))
    mark_dirty(daw)


def on_track_height(sender, app_data, user_data):
    daw = user_data
    daw.track_height = max(40, min(320, int(app_data)))
    mark_dirty(daw)


def on_export(sender, app_data, user_data):
    daw = user_data
    write_wav("block_daw_export.wav", render_song_to_samples(daw.song))
    active = [t.name for t in selected_tracks(daw.song)]
    mark_dirty(daw, f"wrote block_daw_export.wav from: {', '.join(active)}")


def Render(daw: DAW) -> None:
    dpg.create_context()
    dpg.create_viewport(title="Block DAW Minimal", width=1600, height=920, min_width=1100, min_height=700, resizable=True)

    with dpg.window(tag="root", no_title_bar=True, no_move=True, no_resize=True, no_close=True):
        with dpg.group(horizontal=True, tag=TOP_BAR):
            dpg.add_text("BPM")
            dpg.add_input_float(tag=BPM, width=90, step=1.0, callback=on_bpm, user_data=daw, format="%.1f")
            dpg.add_spacer(width=8)
            dpg.add_text("Meter")
            dpg.add_input_int(tag=METER_NUM, width=70, callback=on_meter_num, user_data=daw)
            dpg.add_text("/")
            dpg.add_input_int(tag=METER_DEN, width=70, callback=on_meter_den, user_data=daw)
            dpg.add_spacer(width=8)
            dpg.add_text("px/beat")
            dpg.add_input_float(tag=PX, width=100, step=2.0, callback=on_px_per_beat, user_data=daw, format="%.1f")
            dpg.add_spacer(width=8)
            dpg.add_text("h/track")
            dpg.add_input_int(tag=TRACK_H, width=90, callback=on_track_height, user_data=daw)
            dpg.add_spacer(width=10)
            dpg.add_button(label="Export WAV", callback=on_export, user_data=daw)
            dpg.add_spacer(width=14)
            dpg.add_text("", tag=STATUS)

        dpg.add_separator()
        with dpg.group(horizontal=True):
            with dpg.child_window(width=210, autosize_y=True, border=False):
                dpg.add_text("Tracks")
                dpg.add_separator()
                dpg.add_child_window(tag=ROWS, autosize_x=True, autosize_y=True, border=False)
            with dpg.child_window(tag=SCROLLER, autosize_x=True, autosize_y=True, border=False, horizontal_scrollbar=True):
                dpg.add_drawlist(tag=DRAWLIST, width=3000, height=800)

    sync_top_bar(daw)
    rebuild_rows(daw)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("root", True)

    while dpg.is_dearpygui_running():
        vpw = dpg.get_viewport_client_width()
        vph = dpg.get_viewport_client_height()
        dpg.configure_item("root", width=vpw, height=vph)
        redraw_if_needed(daw)
        dpg.render_dearpygui_frame()

    dpg.destroy_context()


if __name__ == "__main__":
    Render(make_demo_daw())
