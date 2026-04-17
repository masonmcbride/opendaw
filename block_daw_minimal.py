from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import math, os, random, struct, subprocess, wave, shutil, tempfile
import dearpygui.dearpygui as dpg

SR = 44100
random.seed(0)


@dataclass
class Meter:
    beats_per_bar: int = 4
    beat_unit: int = 4


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
    start_beat: float
    length_beats: float
    pitch: int
    velocity: float = 1.0


@dataclass
class Pattern:
    name: str
    length_beats: float
    notes: list[Note] = field(default_factory=list)


@dataclass
class PianoRoll:
    name: str
    length_beats: float
    notes: list[Note] = field(default_factory=list)


@dataclass
class Recording:
    name: str
    length_beats: float
    sample_path: str = ""
    sample_count: int = 0
    sample_rate: int = SR


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
    start_beat: float
    length_beats: Optional[float] = None
    loop: bool = False
    muted: bool = False


@dataclass
class Track:
    name: str
    instrument: Optional[Instrument] = None
    selected: bool = False
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Song:
    bpm: float
    meter: Meter = field(default_factory=Meter)
    patterns: dict[int, Pattern] = field(default_factory=dict)
    piano_rolls: dict[int, PianoRoll] = field(default_factory=dict)
    recordings: dict[int, Recording] = field(default_factory=dict)
    tracks: list[Track] = field(default_factory=list)

    @property
    def seconds_per_beat(self) -> float:
        return (60.0 / self.bpm) * (4.0 / self.meter.beat_unit)


@dataclass
class DAW:
    song: Song
    px_per_beat: float = 36.0
    track_height: int = 96
    dirty: bool = True
    last_viewport: tuple[int, int] = (0, 0)
    status: str = ""
    playback_proc: subprocess.Popen | None = None
    last_export_path: str = "block_daw_export.wav"


def set_status(daw: DAW, text: str) -> None:
    daw.status = text
    if dpg.does_item_exist("status_text"):
        dpg.set_value("status_text", text)


def resolve_block_source(song: Song, ref: BlockRef):
    if ref.kind == BlockKind.PATTERN:
        return song.patterns[ref.object_id]
    if ref.kind == BlockKind.PIANO_ROLL:
        return song.piano_rolls[ref.object_id]
    return song.recordings[ref.object_id]


def block_length(song: Song, block: Block) -> float:
    src_len = resolve_block_source(song, block.ref).length_beats
    return block.length_beats if block.length_beats is not None else src_len


def song_end_beat(song: Song) -> float:
    end = float(song.meter.beats_per_bar * 4)
    for track in song.tracks:
        for block in track.blocks:
            end = max(end, block.start_beat + block_length(song, block))
    return end


def meter_cell(song: Song) -> float:
    return 4.0 / max(1, song.meter.beat_unit)


def active_tracks(song: Song) -> list[Track]:
    selected = [track for track in song.tracks if track.selected]
    return selected if selected else song.tracks


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
    hold = max(0.0, note.length_beats * song.seconds_per_beat)
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


def iter_block_notes(song: Song, block: Block):
    src = resolve_block_source(song, block.ref)
    if isinstance(src, Recording):
        return
    src_len = max(1e-9, src.length_beats)
    clip_len = block_length(song, block)
    if not block.loop:
        for note in src.notes:
            if note.start_beat < clip_len:
                tail = max(0.0, clip_len - note.start_beat)
                yield Note(block.start_beat + note.start_beat, min(note.length_beats, tail), note.pitch, note.velocity)
        return
    rep = 0.0
    while rep < clip_len - 1e-9:
        for note in src.notes:
            start = rep + note.start_beat
            if start >= clip_len:
                continue
            tail = max(0.0, clip_len - start)
            yield Note(block.start_beat + start, min(note.length_beats, tail), note.pitch, note.velocity)
        rep += src_len


def render_mix(song: Song) -> list[float]:
    total_seconds = song_end_beat(song) * song.seconds_per_beat + 1.0
    mix = [0.0] * int(total_seconds * SR)
    for track in active_tracks(song):
        if track.instrument is None:
            continue
        for block in track.blocks:
            if block.muted:
                continue
            src = resolve_block_source(song, block.ref)
            if isinstance(src, Recording):
                continue
            for note in iter_block_notes(song, block):
                audio = render_note(song, track.instrument, note)
                start = int(note.start_beat * song.seconds_per_beat * SR)
                end = min(len(mix), start + len(audio))
                for j in range(end - start):
                    mix[start + j] += audio[j]
    for i, x in enumerate(mix):
        mix[i] = math.tanh(1.4 * x)
    return mix


def export_wav(daw: DAW, path: str = "block_daw_export.wav") -> str:
    write_wav(path, render_mix(daw.song))
    daw.last_export_path = path
    return path


def find_player_command(path: str) -> list[str] | None:
    candidates = [
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", path],
        ["aplay", path],
        ["paplay", path],
        ["pw-play", path],
        ["play", path],
        ["xdg-open", path],
    ]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd
    return None


def stop_playback(daw: DAW) -> None:
    proc = daw.playback_proc
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
    daw.playback_proc = None


RIGHT_PAD = 12
LANE_ROUNDING = 4
BG = (18, 18, 22, 255)
LANE = (28, 28, 34, 255)
GRID = (52, 52, 60, 255)
BAR = (84, 84, 98, 255)
TEXT = (220, 220, 230, 255)
KIND_FILL = {
    BlockKind.PATTERN: (90, 140, 220, 255),
    BlockKind.PIANO_ROLL: (100, 190, 120, 255),
    BlockKind.RECORDING: (190, 130, 80, 255),
}


def lane_height(daw: DAW) -> int:
    return max(24, daw.track_height - 16)


def draw_pattern_block(tag: str, source: Pattern, x0: float, y0: float, x1: float, y1: float, px_per_beat: float) -> None:
    dpg.draw_rectangle((x0, y0), (x1, y1), fill=KIND_FILL[BlockKind.PATTERN], color=(0, 0, 0, 0), rounding=4, parent=tag)
    steps = max(1, int(round(source.length_beats * 4)))
    for i in range(steps + 1):
        x = x0 + (x1 - x0) * i / steps
        dpg.draw_line((x, y0), (x, y1), color=(255, 255, 255, 22), thickness=1, parent=tag)
    cy = (y0 + y1) * 0.5
    for note in source.notes:
        cx = x0 + note.start_beat * px_per_beat + 3
        dpg.draw_circle((cx, cy), 4, fill=(245, 245, 250, 220), color=(0, 0, 0, 0), parent=tag)


def draw_roll_block(tag: str, source: PianoRoll, x0: float, y0: float, x1: float, y1: float, px_per_beat: float) -> None:
    dpg.draw_rectangle((x0, y0), (x1, y1), fill=KIND_FILL[BlockKind.PIANO_ROLL], color=(0, 0, 0, 0), rounding=4, parent=tag)
    if not source.notes:
        return
    lo = min(n.pitch for n in source.notes)
    hi = max(n.pitch for n in source.notes)
    span = max(1, hi - lo + 1)
    h = y1 - y0 - 8
    for note in source.notes:
        nx0 = x0 + note.start_beat * px_per_beat + 2
        nx1 = min(x1 - 2, nx0 + note.length_beats * px_per_beat - 4)
        frac = (note.pitch - lo) / span
        ny0 = y1 - 4 - frac * h - 8
        ny1 = ny0 + 8
        dpg.draw_rectangle((nx0, ny0), (nx1, ny1), fill=(20, 40, 20, 170), color=(255, 255, 255, 35), parent=tag)


def draw_recording_block(tag: str, source: Recording, x0: float, y0: float, x1: float, y1: float) -> None:
    dpg.draw_rectangle((x0, y0), (x1, y1), fill=KIND_FILL[BlockKind.RECORDING], color=(0, 0, 0, 0), rounding=4, parent=tag)
    mid = (y0 + y1) * 0.5
    n = max(12, int((x1 - x0) / 10))
    for i in range(n):
        t = i / max(1, n - 1)
        x = x0 + t * (x1 - x0)
        amp = (0.20 + 0.75 * abs(math.sin(11.0 * t))) * (y1 - y0) * 0.38
        dpg.draw_line((x, mid - amp), (x, mid + amp), color=(60, 30, 10, 160), thickness=1, parent=tag)


def draw_track_lane(drawlist_tag: str, daw: DAW, track: Track, width: int) -> None:
    song = daw.song
    row_h = daw.track_height
    l_h = lane_height(daw)
    dpg.configure_item(drawlist_tag, width=max(400, width), height=row_h)
    dpg.delete_item(drawlist_tag, children_only=True)
    dpg.draw_rectangle((0, 0), (width, row_h), fill=BG, color=BG, parent=drawlist_tag)
    lane_y = (row_h - l_h) / 2
    dpg.draw_rectangle((0, lane_y), (width - RIGHT_PAD, lane_y + l_h), fill=LANE, color=(60, 60, 72, 255), rounding=LANE_ROUNDING, parent=drawlist_tag)
    end_beat = song_end_beat(song)
    sub = meter_cell(song)
    lines = int(end_beat / sub) + 3
    for i in range(lines):
        beat = i * sub
        x = beat * daw.px_per_beat
        q = beat / song.meter.beats_per_bar
        is_bar = abs(q - round(q)) < 1e-9
        is_beat = abs(beat - round(beat)) < 1e-9
        color = BAR if is_bar else GRID if is_beat else (40, 40, 46, 255)
        thick = 2 if is_bar else 1
        dpg.draw_line((x, 0), (x, row_h), color=color, thickness=thick, parent=drawlist_tag)
    for block in track.blocks:
        src = resolve_block_source(song, block.ref)
        x0 = block.start_beat * daw.px_per_beat
        x1 = x0 + block_length(song, block) * daw.px_per_beat
        yb0 = lane_y + 4
        yb1 = lane_y + l_h - 4
        if block.ref.kind == BlockKind.PATTERN:
            draw_pattern_block(drawlist_tag, src, x0, yb0, x1, yb1, daw.px_per_beat)
        elif block.ref.kind == BlockKind.PIANO_ROLL:
            draw_roll_block(drawlist_tag, src, x0, yb0, x1, yb1, daw.px_per_beat)
        else:
            draw_recording_block(drawlist_tag, src, x0, yb0, x1, yb1)
        dpg.draw_text((x0 + 8, yb0 + 6), src.name, color=(245, 245, 248, 220), size=12, parent=drawlist_tag)


def mark_dirty(daw: DAW):
    daw.dirty = True


def on_track_checkbox(sender, app_data, user_data):
    daw, i = user_data
    daw.song.tracks[int(i)].selected = bool(app_data)
    count = len([t for t in daw.song.tracks if t.selected])
    set_status(daw, f"selected {count} track(s)" if count else "selected none -> all tracks active")
    mark_dirty(daw)


def on_bpm(sender, app_data, user_data):
    daw = user_data
    daw.song.bpm = max(10.0, float(app_data))
    mark_dirty(daw)


def on_meter_beats(sender, app_data, user_data):
    daw = user_data
    daw.song.meter.beats_per_bar = max(1, int(app_data))
    mark_dirty(daw)


def on_meter_unit(sender, app_data, user_data):
    daw = user_data
    daw.song.meter.beat_unit = max(1, int(app_data))
    mark_dirty(daw)


def on_px_per_beat(sender, app_data, user_data):
    daw = user_data
    daw.px_per_beat = max(8.0, float(app_data))
    mark_dirty(daw)


def on_track_height(sender, app_data, user_data):
    daw = user_data
    daw.track_height = max(36, int(app_data))
    rebuild_tracks_ui(daw)
    mark_dirty(daw)


def on_export(sender, app_data, user_data):
    daw = user_data
    path = export_wav(daw)
    mode = "selected tracks" if any(t.selected for t in daw.song.tracks) else "all tracks"
    set_status(daw, f"wrote {path} ({mode})")


def on_play(sender, app_data, user_data):
    daw = user_data
    stop_playback(daw)
    path = os.path.join(tempfile.gettempdir(), "block_daw_preview.wav")
    export_wav(daw, path)
    cmd = find_player_command(path)
    if cmd is None:
        set_status(daw, f"wrote preview to {path}; no audio player found")
        return
    try:
        daw.playback_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mode = "selected tracks" if any(t.selected for t in daw.song.tracks) else "all tracks"
        set_status(daw, f"playing preview ({mode})")
    except Exception as e:
        daw.playback_proc = None
        set_status(daw, f"playback failed: {e}")


def on_stop(sender, app_data, user_data):
    daw = user_data
    stop_playback(daw)
    set_status(daw, "stopped")


def rebuild_tracks_ui(daw: DAW) -> None:
    if dpg.does_item_exist("tracks_table"):
        dpg.delete_item("tracks_table")
    with dpg.table(tag="tracks_table", parent="tracks_wrap", header_row=False, resizable=False,
                   borders_innerV=False, borders_outerV=False, borders_innerH=False, borders_outerH=False,
                   policy=dpg.mvTable_SizingFixedFit):
        dpg.add_table_column(init_width_or_weight=34)
        dpg.add_table_column(init_width_or_weight=100)
        dpg.add_table_column(init_width_or_weight=1200)
        for i, track in enumerate(daw.song.tracks):
            with dpg.table_row():
                dpg.add_checkbox(
                    tag=f"track_cb_{i}",
                    default_value=track.selected,
                    callback=on_track_checkbox,
                    user_data=(daw, i),
                )
                dpg.add_text(track.name, color=TEXT)
                dpg.add_drawlist(tag=f"lane_{i}", width=1200, height=daw.track_height)
    daw.dirty = True


def redraw_if_needed(daw: DAW) -> None:
    if not daw.dirty:
        return
    total_width = int(max(900, song_end_beat(daw.song) * daw.px_per_beat + 160))
    for i, track in enumerate(daw.song.tracks):
        draw_track_lane(f"lane_{i}", daw, track, total_width)
        dpg.set_value(f"track_cb_{i}", track.selected)
    daw.dirty = False


def Render(daw: DAW) -> None:
    dpg.create_context()
    dpg.create_viewport(title="block daw minimal", width=1600, height=920, resizable=True)
    with dpg.window(tag="root", no_title_bar=True, no_move=True, no_resize=True, pos=(0, 0)):
        with dpg.group(horizontal=True):
            dpg.add_text("BPM")
            dpg.add_input_double(default_value=daw.song.bpm, width=90, min_value=10, max_value=400, min_clamped=True, max_clamped=True,
                                 callback=on_bpm, user_data=daw)
            dpg.add_text("meter")
            dpg.add_input_int(default_value=daw.song.meter.beats_per_bar, width=60, min_value=1, max_value=32, min_clamped=True, max_clamped=True,
                              callback=on_meter_beats, user_data=daw)
            dpg.add_text("/")
            dpg.add_input_int(default_value=daw.song.meter.beat_unit, width=60, min_value=1, max_value=32, min_clamped=True, max_clamped=True,
                              callback=on_meter_unit, user_data=daw)
            dpg.add_text("px/beat")
            dpg.add_input_double(default_value=daw.px_per_beat, width=90, min_value=8, max_value=200, min_clamped=True, max_clamped=True,
                                 callback=on_px_per_beat, user_data=daw)
            dpg.add_text("h/track")
            dpg.add_input_int(default_value=daw.track_height, width=90, min_value=36, max_value=320, min_clamped=True, max_clamped=True,
                              callback=on_track_height, user_data=daw)
            dpg.add_button(label="Play", callback=on_play, user_data=daw)
            dpg.add_button(label="Stop", callback=on_stop, user_data=daw)
            dpg.add_button(label="Export WAV", callback=on_export, user_data=daw)
        dpg.add_text("", tag="status_text")
        with dpg.child_window(tag="tracks_wrap", autosize_x=True, autosize_y=True, horizontal_scrollbar=True):
            pass
    rebuild_tracks_ui(daw)
    set_status(daw, "selected none -> all tracks active")
    dpg.setup_dearpygui()
    dpg.show_viewport()
    while dpg.is_dearpygui_running():
        vw, vh = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        if (vw, vh) != daw.last_viewport:
            daw.last_viewport = (vw, vh)
            dpg.configure_item("root", width=vw, height=vh)
            daw.dirty = True
        redraw_if_needed(daw)
        dpg.render_dearpygui_frame()
    stop_playback(daw)
    dpg.destroy_context()


def make_demo_daw() -> DAW:
    song = Song(bpm=142.0, meter=Meter(4, 4))
    song.patterns[0] = Pattern("kick", 4.0, [
        Note(0.0, 0.25, 36), Note(1.5, 0.25, 36), Note(2.75, 0.25, 36),
    ])
    song.patterns[1] = Pattern("snare", 4.0, [
        Note(1.0, 0.25, 38, 0.95), Note(3.0, 0.25, 38, 0.95),
    ])
    song.patterns[2] = Pattern("hat", 4.0, [
        Note(0.0, 0.25, 42, 0.65), Note(0.25, 0.25, 42, 0.35), Note(0.5, 0.25, 42, 0.35), Note(0.75, 0.25, 42, 0.35),
        Note(1.0, 0.25, 42, 0.65), Note(1.25, 0.25, 42, 0.35), Note(1.5, 0.25, 42, 0.35), Note(1.75, 0.25, 42, 0.35),
        Note(2.0, 0.25, 42, 0.65), Note(2.25, 0.25, 42, 0.35), Note(2.5, 0.25, 42, 0.35), Note(2.75, 0.25, 42, 0.35),
        Note(3.0, 0.25, 42, 0.65), Note(3.25, 0.25, 42, 0.35), Note(3.5, 0.25, 42, 0.35), Note(3.75, 0.25, 42, 0.35),
    ])
    song.piano_rolls[0] = PianoRoll("808", 4.0, [
        Note(0.0, 1.0, 36), Note(1.5, 0.5, 36), Note(2.0, 1.0, 34), Note(3.0, 1.0, 31),
    ])
    kick_inst = Instrument(waveform="sine", decay=0.10, pitch_drop=24.0, click=0.20)
    snare_inst = Instrument(waveform="square", decay=0.05, noise=0.65, click=0.08)
    hat_inst = Instrument(waveform="noise", decay=0.02, noise=0.85)
    bass_inst = Instrument(waveform="sine", decay=0.20, sustain=0.85, release=0.08, pitch_drop=7.0, click=0.02)
    song.tracks = [
        Track("kick", kick_inst, False, [Block(BlockRef(BlockKind.PATTERN, 0), 0.0), Block(BlockRef(BlockKind.PATTERN, 0), 4.0)]),
        Track("snare", snare_inst, False, [Block(BlockRef(BlockKind.PATTERN, 1), 0.0), Block(BlockRef(BlockKind.PATTERN, 1), 4.0)]),
        Track("hat", hat_inst, False, [Block(BlockRef(BlockKind.PATTERN, 2), 0.0), Block(BlockRef(BlockKind.PATTERN, 2), 4.0)]),
        Track("808", bass_inst, False, [Block(BlockRef(BlockKind.PIANO_ROLL, 0), 0.0), Block(BlockRef(BlockKind.PIANO_ROLL, 0), 4.0)]),
    ]
    return DAW(song)


if __name__ == "__main__":
    Render(make_demo_daw())
