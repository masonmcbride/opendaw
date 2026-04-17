from __future__ import annotations

from dataclasses import dataclass, field
import math
import wave
import struct
import random

import dearpygui.dearpygui as dpg

SR = 44100
random.seed(0)


@dataclass
class Instrument:
    waveform: str = "sine"   # sine, square, saw, noise
    attack: float = 0.0
    decay: float = 0.1
    sustain: float = 0.0
    release: float = 0.0
    pitch_drop: float = 0.0
    noise: float = 0.0
    click: float = 0.0


@dataclass
class Note:
    beat: float
    length: float
    pitch: int
    velocity: float = 1.0


@dataclass
class Pattern:
    name: str
    length_beats: int
    notes: list[Note] = field(default_factory=list)

    def add(self, beat: float, length: float, pitch: int, velocity: float = 1.0) -> None:
        self.notes.append(Note(beat, length, pitch, velocity))


@dataclass
class Clip:
    pattern_id: int
    start_beat: int
    length_beats: int | None = None
    loop: bool = False


@dataclass
class Track:
    name: str
    instrument: Instrument
    clips: list[Clip] = field(default_factory=list)
    selected: bool = False


@dataclass
class Song:
    bpm: float
    patterns: dict[int, Pattern] = field(default_factory=dict)
    tracks: list[Track] = field(default_factory=list)

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.bpm


@dataclass
class DAW:
    song: Song
    selected_clip: tuple[int, int] | None = None  # (track_index, clip_index)
    view_beats: int = 32
    lane_height: int = 40
    beat_px: int = 28
    dragging_new_clip: tuple[int, int] | None = None  # (track_index, start_beat)
    dragging_clip: tuple[int, int, int] | None = None  # (track_index, clip_index, mouse_beat_offset)


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
    hold = max(0.0, note.length * song.seconds_per_beat)
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


def clip_span(song: Song, clip: Clip) -> int:
    pat = song.patterns[clip.pattern_id]
    return clip.length_beats if clip.length_beats is not None else pat.length_beats


def render(song: Song) -> list[float]:
    last_beat = 0.0
    for track in song.tracks:
        for clip in track.clips:
            last_beat = max(last_beat, clip.start_beat + clip_span(song, clip))
    total_seconds = max(1.0, last_beat * song.seconds_per_beat)
    n_samples = int(total_seconds * SR) + 1
    mix = [0.0] * n_samples

    for track in song.tracks:
        for clip in track.clips:
            pattern = song.patterns[clip.pattern_id]
            span = clip_span(song, clip)
            if clip.loop:
                loops = max(1, math.ceil(span / max(1, pattern.length_beats)))
                offsets = [k * pattern.length_beats for k in range(loops)]
            else:
                offsets = [0]

            for offset in offsets:
                for note in pattern.notes:
                    local_beat = offset + note.beat
                    if local_beat >= span:
                        continue
                    placed = Note(
                        beat=clip.start_beat + local_beat,
                        length=note.length,
                        pitch=note.pitch,
                        velocity=note.velocity,
                    )
                    audio = render_note(song, track.instrument, placed)
                    start = int(placed.beat * song.seconds_per_beat * SR)
                    end = min(n_samples, start + len(audio))
                    for j in range(end - start):
                        mix[start + j] += audio[j]

    for i, x in enumerate(mix):
        mix[i] = math.tanh(1.2 * x)
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


def default_pattern_id(song: Song) -> int:
    return next(iter(song.patterns))


def clip_at(song: Song, track: Track, beat: int) -> int | None:
    for i, clip in enumerate(track.clips):
        start = clip.start_beat
        end = start + clip_span(song, clip)
        if start <= beat < end:
            return i
    return None


def copy_clip(track: Track, clip_index: int, delta_beats: int, song: Song) -> None:
    clip = track.clips[clip_index]
    track.clips.append(
        Clip(
            pattern_id=clip.pattern_id,
            start_beat=max(0, clip.start_beat + delta_beats),
            length_beats=clip.length_beats,
            loop=clip.loop,
        )
    )
    track.clips.sort(key=lambda c: c.start_beat)


def make_demo_song() -> Song:
    song = Song(bpm=142)

    kick_pat = Pattern("kick pat", 4)
    for beat in [0, 1.5, 2.75]:
        kick_pat.add(beat, 0.25, 36)

    snare_pat = Pattern("snare pat", 4)
    for beat in [1.0, 3.0]:
        snare_pat.add(beat, 0.25, 38, 0.95)

    hat_pat = Pattern("hat pat", 4)
    for i in range(8):
        hat_pat.add(i * 0.5, 0.1, 42, 0.35 if i % 2 else 0.65)

    bass_pat = Pattern("808 pat", 4)
    for beat, length, pitch in [
        (0.0, 1.0, 36),
        (1.5, 0.5, 36),
        (2.0, 1.0, 34),
        (3.0, 1.0, 31),
    ]:
        bass_pat.add(beat, length, pitch)

    song.patterns = {
        0: kick_pat,
        1: snare_pat,
        2: hat_pat,
        3: bass_pat,
    }

    kick = Track("kick", Instrument(waveform="sine", decay=0.10, pitch_drop=24.0, click=0.20))
    kick.clips = [Clip(0, 0), Clip(0, 4), Clip(0, 8), Clip(0, 12)]

    snare = Track("snare", Instrument(waveform="square", decay=0.05, noise=0.65, click=0.08))
    snare.clips = [Clip(1, 0), Clip(1, 8)]

    hat = Track("hat", Instrument(waveform="noise", decay=0.02, noise=0.85))
    hat.clips = [Clip(2, 0, length_beats=16, loop=True)]

    bass = Track("808", Instrument(waveform="sine", decay=0.20, sustain=0.85, release=0.08, pitch_drop=7.0, click=0.02))
    bass.clips = [Clip(3, 0), Clip(3, 8)]

    song.tracks = [kick, snare, hat, bass]
    return song


def track_row_y(i: int, lane_height: int) -> int:
    return 40 + i * lane_height


def lane_left() -> int:
    return 170


def beat_to_x(daw: DAW, beat: int | float) -> int:
    return lane_left() + int(beat * daw.beat_px)


def mouse_to_track_and_beat(daw: DAW, pos: tuple[float, float]) -> tuple[int | None, int | None]:
    x, y = pos
    ti = int((y - 40) // daw.lane_height)
    if not (0 <= ti < len(daw.song.tracks)):
        return None, None
    beat = int((x - lane_left()) // daw.beat_px)
    if beat < 0:
        return ti, None
    return ti, beat


def draw_arrangement(daw: DAW) -> None:
    dpg.delete_item("arrange_draw", children_only=True)
    draw = "arrange_draw"
    width = lane_left() + daw.view_beats * daw.beat_px + 20
    height = 40 + len(daw.song.tracks) * daw.lane_height + 10

    dpg.draw_rectangle((0, 0), (width, height), color=(40, 40, 40, 255), fill=(20, 20, 20, 255), parent=draw)

    for beat in range(daw.view_beats + 1):
        x = beat_to_x(daw, beat)
        col = (90, 90, 90, 255) if beat % 4 == 0 else (55, 55, 55, 255)
        dpg.draw_line((x, 0), (x, height), color=col, parent=draw)
        if beat < daw.view_beats:
            dpg.draw_text((x + 4, 12), str(beat), color=(180, 180, 180, 255), size=12, parent=draw)

    for i, track in enumerate(daw.song.tracks):
        y0 = track_row_y(i, daw.lane_height)
        y1 = y0 + daw.lane_height
        dpg.draw_rectangle((0, y0), (width, y1), color=(35, 35, 35, 255), fill=(28, 28, 28, 255), parent=draw)

        cb0 = (12, y0 + 10)
        cb1 = (28, y0 + 26)
        dpg.draw_rectangle(cb0, cb1, color=(180, 180, 180, 255), fill=(18, 18, 18, 255), parent=draw)
        if track.selected:
            dpg.draw_line((14, y0 + 18), (19, y0 + 23), color=(220, 220, 220, 255), thickness=2, parent=draw)
            dpg.draw_line((19, y0 + 23), (26, y0 + 12), color=(220, 220, 220, 255), thickness=2, parent=draw)

        dpg.draw_text((38, y0 + 11), track.name, color=(230, 230, 230, 255), size=14, parent=draw)

        for ci, clip in enumerate(track.clips):
            x0 = beat_to_x(daw, clip.start_beat)
            x1 = beat_to_x(daw, clip.start_beat + clip_span(daw.song, clip))
            selected = daw.selected_clip == (i, ci)
            fill = (93, 140, 255, 255) if selected else (85, 110, 160, 255)
            border = (200, 220, 255, 255) if selected else (150, 170, 210, 255)
            dpg.draw_rectangle((x0 + 1, y0 + 5), (x1 - 1, y1 - 5), color=border, fill=fill, rounding=3, parent=draw)
            pat = daw.song.patterns[clip.pattern_id]
            label = pat.name + (" loop" if clip.loop else "")
            dpg.draw_text((x0 + 6, y0 + 11), label, color=(240, 240, 240, 255), size=12, parent=draw)

    if daw.dragging_new_clip is not None:
        track_i, start_beat = daw.dragging_new_clip
        pos = dpg.get_mouse_pos(local=False)
        _, beat = mouse_to_track_and_beat(daw, pos)
        if beat is not None:
            left = min(start_beat, beat)
            right = max(start_beat + 1, beat + 1)
            y0 = track_row_y(track_i, daw.lane_height)
            dpg.draw_rectangle(
                (beat_to_x(daw, left), y0 + 8),
                (beat_to_x(daw, right), y0 + daw.lane_height - 8),
                color=(180, 180, 180, 255),
                fill=(180, 180, 180, 40),
                parent=draw,
            )


def refresh_info(daw: DAW) -> None:
    txt = "selected clip: none"
    if daw.selected_clip is not None:
        ti, ci = daw.selected_clip
        clip = daw.song.tracks[ti].clips[ci]
        pat = daw.song.patterns[clip.pattern_id]
        txt = f"selected clip: track={daw.song.tracks[ti].name} pattern={pat.name} start={clip.start_beat} span={clip_span(daw.song, clip)}"
    dpg.set_value("info_text", txt)


def on_mouse_down(sender, app_data, user_data) -> None:
    daw: DAW = user_data
    pos = dpg.get_mouse_pos(local=False)
    ti, beat = mouse_to_track_and_beat(daw, pos)
    if ti is None:
        return
    track = daw.song.tracks[ti]
    y0 = track_row_y(ti, daw.lane_height)
    x, y = pos

    if 12 <= x <= 28 and y0 + 10 <= y <= y0 + 26:
        track.selected = not track.selected
        draw_arrangement(daw)
        return

    if beat is None:
        return

    ci = clip_at(daw.song, track, beat)
    if ci is not None:
        daw.selected_clip = (ti, ci)
        clip = track.clips[ci]
        daw.dragging_clip = (ti, ci, beat - clip.start_beat)
    else:
        daw.selected_clip = None
        daw.dragging_new_clip = (ti, beat)

    refresh_info(daw)
    draw_arrangement(daw)


def on_mouse_release(sender, app_data, user_data) -> None:
    daw: DAW = user_data
    pos = dpg.get_mouse_pos(local=False)

    if daw.dragging_new_clip is not None:
        ti, start_beat = daw.dragging_new_clip
        ti2, beat = mouse_to_track_and_beat(daw, pos)
        if ti2 == ti and beat is not None:
            left = min(start_beat, beat)
            right = max(start_beat + 1, beat + 1)
            daw.song.tracks[ti].clips.append(
                Clip(pattern_id=default_pattern_id(daw.song), start_beat=left, length_beats=right - left, loop=False)
            )
            daw.song.tracks[ti].clips.sort(key=lambda c: c.start_beat)
        daw.dragging_new_clip = None

    daw.dragging_clip = None
    refresh_info(daw)
    draw_arrangement(daw)


def on_mouse_move(sender, app_data, user_data) -> None:
    daw: DAW = user_data
    if daw.dragging_clip is None:
        if daw.dragging_new_clip is not None:
            draw_arrangement(daw)
        return

    ti, ci, offset = daw.dragging_clip
    pos = dpg.get_mouse_pos(local=False)
    ti2, beat = mouse_to_track_and_beat(daw, pos)
    if ti2 != ti or beat is None:
        return
    clip = daw.song.tracks[ti].clips[ci]
    clip.start_beat = max(0, beat - offset)
    daw.song.tracks[ti].clips.sort(key=lambda c: c.start_beat)

    try:
        new_ci = daw.song.tracks[ti].clips.index(clip)
    except ValueError:
        new_ci = ci
    daw.selected_clip = (ti, new_ci)
    daw.dragging_clip = (ti, new_ci, offset)
    refresh_info(daw)
    draw_arrangement(daw)


def add_track(daw: DAW) -> None:
    daw.song.tracks.append(Track(f"track {len(daw.song.tracks)}", Instrument()))
    draw_arrangement(daw)


def duplicate_selected_clip(daw: DAW) -> None:
    if daw.selected_clip is None:
        return
    ti, ci = daw.selected_clip
    track = daw.song.tracks[ti]
    delta = clip_span(daw.song, track.clips[ci])
    copy_clip(track, ci, delta, daw.song)
    draw_arrangement(daw)


def toggle_loop_selected_clip(daw: DAW) -> None:
    if daw.selected_clip is None:
        return
    ti, ci = daw.selected_clip
    clip = daw.song.tracks[ti].clips[ci]
    clip.loop = not clip.loop
    if clip.loop and clip.length_beats is None:
        clip.length_beats = clip_span(daw.song, clip) * 2
    draw_arrangement(daw)
    refresh_info(daw)


def render_wav(daw: DAW) -> None:
    audio = render(daw.song)
    write_wav("minimal_arranger.wav", audio)
    dpg.set_value("info_text", "wrote minimal_arranger.wav")


def Render(daw: DAW) -> None:
    dpg.create_context()
    dpg.create_viewport(title="Minimal Arranger", width=1100, height=320)

    with dpg.window(tag="main", label="Minimal Arranger", width=1080, height=280):
        with dpg.group(horizontal=True):
            dpg.add_text("BPM")
            dpg.add_input_float(default_value=daw.song.bpm, width=80,
                                callback=lambda s, a, u: setattr(u.song, "bpm", max(1.0, float(a))),
                                user_data=daw)
            dpg.add_button(label="Add Track", callback=lambda: add_track(daw))
            dpg.add_button(label="Duplicate Clip", callback=lambda: duplicate_selected_clip(daw))
            dpg.add_button(label="Toggle Loop", callback=lambda: toggle_loop_selected_clip(daw))
            dpg.add_button(label="Render WAV", callback=lambda: render_wav(daw))
        dpg.add_text("drag empty lane to make clip, drag clip to move it, click checkbox to mark track")
        dpg.add_text("", tag="info_text")
        with dpg.drawlist(width=1080, height=220, tag="arrange_draw"):
            pass

    with dpg.handler_registry():
        dpg.add_mouse_down_handler(callback=on_mouse_down, user_data=daw)
        dpg.add_mouse_release_handler(callback=on_mouse_release, user_data=daw)
        dpg.add_mouse_move_handler(callback=on_mouse_move, user_data=daw)

    refresh_info(daw)
    draw_arrangement(daw)

    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()


if __name__ == "__main__":
    song = make_demo_song()
    daw = DAW(song)
    Render(daw)
