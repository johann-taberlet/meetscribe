#!/usr/bin/env python3
"""Merge Voxtral transcript chunks into a single unified transcript.

Handles any number of chunks produced by the chunking scheme in PIPELINE_PROMPT.md:
  - 20-minute chunks with 30-second overlap
  - Chunk N starts at offset (N-1) * 1200 seconds

Usage:
    python3 merge_transcripts.py <transcripts_dir> [--chunk-duration 1200] [--overlap 30]

Expects files named raw_chunk_001.json, raw_chunk_002.json, ... in <transcripts_dir>.
Produces raw_merged.json and transcript_readable.txt in the same directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path


def load_chunk(path: Path, offset: float) -> list[dict]:
    """Load a chunk JSON and shift timestamps by offset."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    segments = []
    for seg in data["segments"]:
        segments.append({
            "text": seg["text"],
            "start": round(seg["start"] + offset, 2),
            "end": round(seg["end"] + offset, 2),
            "speaker_id": seg["speaker_id"],
            "type": seg.get("type", "transcription_segment"),
        })
    return segments


def text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def build_speaker_mapping(
    earlier: list[dict], later: list[dict],
    overlap_start: float, overlap_end: float,
) -> dict[str, str]:
    """Map speaker IDs from a later chunk to the earlier chunk's IDs
    by matching text in the overlap zone."""
    earlier_overlap = [
        s for s in earlier
        if s["start"] >= overlap_start - 1 and s["end"] <= overlap_end + 1
    ]
    later_overlap = [
        s for s in later
        if s["start"] >= overlap_start - 1 and s["end"] <= overlap_end + 1
    ]
    if not earlier_overlap or not later_overlap:
        return {}

    votes: Counter[tuple[str, str]] = Counter()
    for ls in later_overlap:
        best_sim = 0.0
        best_speaker = None
        for es in earlier_overlap:
            sim = text_similarity(ls["text"], es["text"])
            time_close = abs(ls["start"] - es["start"]) < 5.0
            if sim > 0.5 and time_close and sim > best_sim:
                best_sim = sim
                best_speaker = es["speaker_id"]
        if best_speaker:
            votes[(ls["speaker_id"], best_speaker)] += 1

    mapping: dict[str, str] = {}
    later_speakers = {s["speaker_id"] for s in later}
    for later_spk in later_speakers:
        candidates = {
            es: votes[(later_spk, es)]
            for (ls, es) in votes
            if ls == later_spk and votes[(ls, es)] > 0
        }
        if candidates:
            mapping[later_spk] = max(candidates, key=candidates.get)  # type: ignore[arg-type]
    return mapping


def apply_speaker_mapping(segments: list[dict], mapping: dict[str, str]) -> list[dict]:
    for seg in segments:
        if seg["speaker_id"] in mapping:
            seg["speaker_id"] = mapping[seg["speaker_id"]]
    return segments


def filter_overlap(segments: list[dict], overlap_start: float, overlap_end: float) -> list[dict]:
    """Remove segments whose midpoint falls within the overlap zone."""
    return [
        s for s in segments
        if (s["start"] + s["end"]) / 2 < overlap_start
        or (s["start"] + s["end"]) / 2 >= overlap_end
    ]


def fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def merge(transcripts_dir: Path, chunk_duration: int = 1200, overlap: int = 30) -> Path:
    """Merge all raw_chunk_NNN.json files in transcripts_dir.

    Returns the path to raw_merged.json.
    """
    chunk_files = sorted(transcripts_dir.glob("raw_chunk_*.json"))
    if not chunk_files:
        sys.exit(f"No raw_chunk_*.json files found in {transcripts_dir}")

    n = len(chunk_files)
    print(f"Found {n} chunk(s) to merge.")

    # Single chunk — just copy with no merge needed
    if n == 1:
        segs = load_chunk(chunk_files[0], offset=0.0)
        merged = {"text": " ".join(s["text"].strip() for s in segs), "segments": segs}
        out = transcripts_dir / "raw_merged.json"
        out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Single chunk — copied as {out}")
        return out

    # Load all chunks with their offsets
    chunk_segments: list[list[dict]] = []
    for i, path in enumerate(chunk_files):
        offset = i * chunk_duration
        segs = load_chunk(path, offset)
        speakers = sorted({s["speaker_id"] for s in segs})
        print(f"  Chunk {i+1}: {len(segs)} segments, "
              f"{fmt_ts(segs[0]['start'])}–{fmt_ts(segs[-1]['end'])}, "
              f"speakers: {speakers}")
        chunk_segments.append(segs)

    # For each consecutive pair: map speakers then remove overlap duplicates
    print("\nReconciling overlaps...")
    for i in range(1, n):
        overlap_start = i * chunk_duration
        overlap_end = overlap_start + overlap

        # Speaker mapping: align later chunk's speaker IDs to earlier chunk
        mapping = build_speaker_mapping(
            chunk_segments[i - 1], chunk_segments[i],
            overlap_start, overlap_end,
        )
        if mapping:
            print(f"  Chunks {i}→{i+1} speaker mapping: {mapping}")
            chunk_segments[i] = apply_speaker_mapping(chunk_segments[i], mapping)
        else:
            print(f"  Chunks {i}→{i+1}: no speaker mapping found in overlap zone")

        # Remove overlap segments from the later chunk (keep earlier chunk's version)
        before = len(chunk_segments[i])
        chunk_segments[i] = filter_overlap(chunk_segments[i], overlap_start, overlap_end)
        removed = before - len(chunk_segments[i])
        print(f"  Removed {removed} overlap segments from chunk {i+1} "
              f"(zone {fmt_ts(overlap_start)}–{fmt_ts(overlap_end)})")

    # Merge and sort
    all_segments: list[dict] = []
    for segs in chunk_segments:
        all_segments.extend(segs)
    all_segments.sort(key=lambda s: s["start"])

    # Deduplicate near-identical consecutive segments
    deduped: list[dict] = [all_segments[0]]
    for seg in all_segments[1:]:
        prev = deduped[-1]
        if (abs(seg["start"] - prev["start"]) < 2.0
                and text_similarity(seg["text"], prev["text"]) > 0.8):
            # Keep the longer text
            if len(seg["text"]) > len(prev["text"]):
                deduped[-1] = seg
        else:
            deduped.append(seg)

    dup_count = len(all_segments) - len(deduped)
    if dup_count:
        print(f"\n  Removed {dup_count} near-duplicate segment(s)")
    all_segments = deduped

    # Save merged JSON
    full_text = " ".join(s["text"].strip() for s in all_segments)
    merged = {"text": full_text, "segments": all_segments}
    out_json = transcripts_dir / "raw_merged.json"
    out_json.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    # Save readable transcript
    out_txt = transcripts_dir / "transcript_readable.txt"
    with open(out_txt, "w", encoding="utf-8") as f:
        for seg in all_segments:
            f.write(f"[{fmt_ts(seg['start'])}] {seg['speaker_id']}: {seg['text'].strip()}\n")

    # Summary
    speakers = sorted({s["speaker_id"] for s in all_segments})
    duration = all_segments[-1]["end"] - all_segments[0]["start"]
    speaker_counts = Counter(s["speaker_id"] for s in all_segments)
    speaker_dur: Counter[str] = Counter()
    for s in all_segments:
        speaker_dur[s["speaker_id"]] += s["end"] - s["start"]

    print(f"\n{'='*50}")
    print(f"MERGE SUMMARY")
    print(f"{'='*50}")
    print(f"Total segments : {len(all_segments)}")
    print(f"Speakers       : {len(speakers)} ({', '.join(speakers)})")
    print(f"Time span      : {fmt_ts(all_segments[0]['start'])} – {fmt_ts(all_segments[-1]['end'])} "
          f"({duration:.0f}s / {duration/60:.1f}min)")
    print(f"Text length    : {len(full_text)} chars")
    for spk in speakers:
        d = speaker_dur[spk]
        print(f"  {spk}: {speaker_counts[spk]} segments, {d:.0f}s ({d/60:.1f}min)")
    print(f"{'='*50}")
    print(f"\nSaved: {out_json}")
    print(f"Saved: {out_txt}")

    return out_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge Voxtral transcript chunks")
    parser.add_argument("transcripts_dir", type=Path, help="Directory containing raw_chunk_NNN.json files")
    parser.add_argument("--chunk-duration", type=int, default=1200, help="Chunk step in seconds (default: 1200 = 20min)")
    parser.add_argument("--overlap", type=int, default=30, help="Overlap duration in seconds (default: 30)")
    args = parser.parse_args()
    merge(args.transcripts_dir, args.chunk_duration, args.overlap)


if __name__ == "__main__":
    main()
