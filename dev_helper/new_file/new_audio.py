#!/usr/bin/env python3

# === SETTINGS START ===
PATH = None
NUMBER = 1
VOLUME = 60
QUANTIZE = True
FORMAT = 'opus'
DURATION = 2.0  # Duration per note/file in seconds
SAMPLE_RATE = 48000  # Standard rate for Opus/High-quality audio
# === SETTINGS END ===

import sys
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

from path_args import resolve_paths, choose_output_file

import argparse
import math
import os
import re
import subprocess
import wave


# Pentatonic scale step ratios relative to a base root note (A4 = 440Hz)
# Root, Major 2nd, Major 3rd, Perfect 5th, Major 6th, Octave
PENTATONIC_STEPS = [1.0, 1.12246, 1.25992, 1.49831, 1.68179, 2.00000]


def get_frequency(index, quantize=True):
    """Calculate frequency. If quantize is True, steps along the pentatonic scale."""
    base_freq = 220.0  # Starting root note (A3)
    if not quantize:
        # Linear frequency stepping
        return base_freq + (index * 50.0)
    
    # Calculate scale degree and octave shift
    scale_len = len(PENTATONIC_STEPS) - 1
    octave = index // scale_len
    step = index % scale_len
    
    freq = base_freq * (2 ** octave) * PENTATONIC_STEPS[step]
    return freq


def generate_sine_wave(frequency, duration, volume_pct, sample_rate=48000):
    """Generates a raw 16-bit PCM sine wave with a soft fade-out to prevent clicks."""
    num_samples = int(sample_rate * duration)
    # Convert 0-100% volume into max 16-bit amplitude (32767)
    amplitude = int((volume_pct / 100.0) * 32767)
    
    audio_bytes = bytearray()
    fade_len = int(sample_rate * 0.05)  # 50ms fade-out
    
    for i in range(num_samples):
        # Calculate raw sine value
        t = float(i) / sample_rate
        value = math.sin(2.0 * math.pi * frequency * t)
        
        # Apply linear envelope fade-out at the end
        if i > (num_samples - fade_len):
            scale = float(num_samples - i) / fade_len
            value *= scale
            
        sample = int(value * amplitude)
        # Clamp bounds
        sample = max(-32768, min(32767, sample))
        
        # Pack as signed 16-bit little-endian integer
        audio_bytes.extend(sample.to_bytes(2, byteorder='little', signed=True))
        
    return bytes(audio_bytes)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Create numbered audio scale files with auto-increment"
    )
    parser.add_argument("path", nargs="?", default=PATH,
                        help="Output filename or folder")
    parser.add_argument("-n", "--number", type=int, default=NUMBER,
                        help=f"Number of audio tracks (default: {NUMBER})")
    parser.add_argument("-v", "--volume", type=int, default=VOLUME,
                        help=f"Volume percentage 0-100 (default: {VOLUME})")
    parser.add_argument("--quantize", type=str, default=str(QUANTIZE),
                        help=f"Quantize notes to pentatonic scale: true/false (default: {QUANTIZE})")
    parser.add_argument("--format", default=FORMAT,
                        help=f"Format override (default: {FORMAT})")
    parser.add_argument("-d", "--duration", type=float, default=DURATION,
                        help=f"Duration of each file in seconds (default: {DURATION})")
                        
    return parser.parse_args(argv)


def save_audio(out_path, raw_pcm, fmt, sample_rate=48000):
    """Saves raw PCM data. Encodes to Opus if requested via ffmpeg/opusenc."""
    fmt = fmt.lower()
    temp_wav = out_path if fmt == "wav" else out_path + ".tmp.wav"
    
    # Always write to a standard PCM WAV first
    with wave.open(temp_wav, 'wb') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(raw_pcm)
        
    if fmt == "wav":
        return

    # Handle Opus conversion via external tools (ffmpeg or opusenc)
    success = False
    for tool in [["ffmpeg", "-y", "-i", temp_wav, "-c:a", "libopus", out_path],
                 ["opusenc", temp_wav, out_path]]:
        try:
            subprocess.run(tool, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            success = True
            break
        except (subprocess.SubprocessError, FileNotFoundError):
            continue
            
    # Clean up temporary WAV
    if os.path.exists(temp_wav):
        os.remove(temp_wav)
        
    if not success:
        # Fallback action: keep the WAV format instead of failing out completely
        fallback_path = os.path.splitext(out_path)[0] + ".wav"
        with wave.open(fallback_path, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(raw_pcm)
        print(f"Warning: Codec engine missing. Exported WAV fallback instead: {fallback_path}", file=sys.stderr)


def main(argv=None):
    global FORMAT, VOLUME, QUANTIZE, DURATION, NUMBER
    args = parse_args(argv)

    FORMAT = args.format.lower()
    VOLUME = max(0, min(100, args.volume))
    QUANTIZE = args.quantize.lower() in ("true", "1", "yes")
    NUMBER = args.number
    DURATION = args.duration

    resolved = resolve_paths(
        args,
        arg_names=("path",),
        create="auto",
    )
    dir_path = resolved.first

    if dir_path.is_dir():
        base_name = str(NUMBER)
    else:
        base_name = dir_path.stem or str(NUMBER)

    m = re.match(r'^(.*?)(\d+)$', base_name)
    prefix = m.group(1) if m else base_name
    counter = int(m.group(2)) if m else 1

    for i in range(NUMBER):
        idx = counter + i
        stem = f"{prefix}{idx}"
        ext = f".{FORMAT}"

        candidate_path = dir_path / f"{stem}{ext}"

        final_path = choose_output_file(
            location=candidate_path,
            default_stem=stem,
            default_suffix=ext,
        )

        freq = get_frequency(i, quantize=QUANTIZE)
        raw_audio = generate_sine_wave(freq, DURATION, VOLUME, SAMPLE_RATE)
        save_audio(str(final_path), raw_audio, FORMAT, SAMPLE_RATE)

        print(f"Created: {final_path} ({freq:.2f} Hz)")


if __name__ == "__main__":
    main()
