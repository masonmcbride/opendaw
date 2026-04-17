from dataclasses import dataclass, field
import math, wave, struct, random

SR = 44100
random.seed(0)


@dataclass
class Instrument:
    waveform: str = "sine"   # sine, square, saw, noise
    attack: float = 0.0
    decay: float = 0.1
    sustain: float = 0.0
    release: float = 0.0
    pitch_drop: float = 0.0  # semitones of downward sweep early in note
    noise: float = 0.0
    click: float = 0.0


@dataclass
class Note:
    step: int
    length: int
    pitch: int
    velocity: float = 1.0


@dataclass
class Track:
    name: str
    instrument: Instrument
    notes: list[Note] = field(default_factory=list)

    def add(self, step: int, length: int, pitch: int, velocity: float = 1.0):
        self.notes.append(Note(step, length, pitch, velocity))


@dataclass
class Song:
    bpm: float
    tracks: list[Track] = field(default_factory=list)

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.bpm

    @property
    def seconds_per_step(self) -> float:
        # fixed tiny version: 16 steps per bar in 4/4
        return self.seconds_per_beat / 4.0


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
    hold = max(0.0, note.length * song.seconds_per_step)
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


def render(song: Song, bars: int = 2) -> list[float]:
    total_steps = 16 * bars
    total_seconds = total_steps * song.seconds_per_step
    n_samples = int(total_seconds * SR)
    mix = [0.0] * n_samples

    for track in song.tracks:
        for note in track.notes:
            audio = render_note(song, track.instrument, note)
            for bar in range(bars):
                start = int((note.step + 16 * bar) * song.seconds_per_step * SR)
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


# -------- tiny trap beat --------

song = Song(bpm=142)

kick = Track("kick", Instrument(
    waveform="sine",
    decay=0.10,
    pitch_drop=24.0,
    click=0.20,
))

snare = Track("snare", Instrument(
    waveform="square",
    decay=0.05,
    noise=0.65,
    click=0.08,
))

hat = Track("hat", Instrument(
    waveform="noise",
    decay=0.02,
    noise=0.85,
))

bass808 = Track("808", Instrument(
    waveform="sine",
    decay=0.20,
    sustain=0.85,
    release=0.08,
    pitch_drop=7.0,
    click=0.02,
))

for step in [0, 6, 11]:
    kick.add(step, 1, 36)

for step in [4, 12]:
    snare.add(step, 1, 38, 0.95)

for step in range(16):
    hat.add(step, 1, 42, 0.35 if step % 4 else 0.65)

for step, length, pitch in [
    (0, 4, 36),
    (6, 2, 36),
    (8, 4, 34),
    (12, 4, 31),
]:
    bass808.add(step, length, pitch)

song.tracks += [kick, snare, hat, bass808]

audio = render(song, bars=2)
write_wav("tiny_trap.wav", audio)

print("wrote tiny_trap.wav")