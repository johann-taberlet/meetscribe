"""Meeting Intelligence Pipeline — CLI entry point.

Usage:
    uv run main.py -i recording.mov
    uv run main.py -i meeting.mp3 --context "Johann est le prestataire technique Salesforce, Jason est le client courtier en assurances. Vocabulaire: Planific, CPQ, Salesforce"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    CLINotFoundError,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ProcessError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    query,
)
from pipeline_prompt import build_system_prompt, load_segment_prompt

ALLOWED_VIDEO_EXTS = {".mov", ".mp4", ".mkv", ".webm"}
ALLOWED_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".opus"}
ALLOWED_EXTS = ALLOWED_VIDEO_EXTS | ALLOWED_AUDIO_EXTS

TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Task", "TodoRead", "TodoWrite"]
SEGMENT_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "TodoRead", "TodoWrite"]
MAX_TURNS = None  # Unlimited


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Meeting Intelligence Pipeline — analyse de réunions par IA",
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="Chemin vers le fichier vidéo ou audio",
    )
    parser.add_argument(
        "-o", "--output",
        default=".",
        help="Dossier parent où créer le dossier d'analyse (défaut: répertoire courant)",
    )
    parser.add_argument(
        "--context",
        default=None,
        help="Contexte de la réunion en langage naturel (participants, rôles, vocabulaire technique, sujet...)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=MAX_TURNS,
        help="Nombre max de tours agent (défaut: illimité)",
    )
    return parser.parse_args()


def _file_creation_dt(path: Path) -> datetime:
    """Return the file's creation datetime (birthtime on macOS, mtime fallback)."""
    st = path.stat()
    ts = getattr(st, "st_birthtime", None) or st.st_mtime
    return datetime.fromtimestamp(ts)


def validate_input(input_path: Path) -> None:
    if not input_path.exists():
        sys.exit(f"Erreur: fichier introuvable — {input_path}")
    if input_path.suffix.lower() not in ALLOWED_EXTS:
        sys.exit(
            f"Erreur: extension '{input_path.suffix}' non supportée. "
            f"Extensions valides: {', '.join(sorted(ALLOWED_EXTS))}"
        )


def build_user_prompt(input_path: Path, output_dir: Path) -> str:
    """Build the initial user prompt sent to the agent."""
    media_type = "vidéo" if input_path.suffix.lower() in ALLOWED_VIDEO_EXTS else "audio"
    return (
        f"Voici le fichier {media_type} à analyser : `{input_path.resolve()}`\n\n"
        f"Le dossier de sortie pour tous les fichiers intermédiaires et le rapport final est : `{output_dir.resolve()}`\n\n"
        f"La racine du projet (pour merge_transcripts.py) est : `{Path(__file__).parent.resolve()}`\n\n"
        f"Lance le pipeline complet. Commence par l'étape 1."
    )


def print_header(input_path: Path, output_dir: Path) -> None:
    print("\n╔══════════════════════════════════════════════════════╗")
    print("║       Meeting Intelligence Pipeline                 ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"\n  Entrée  : {input_path}")
    print(f"  Sortie  : {output_dir}")
    print(f"  Début   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("─" * 56)


async def run_pipeline(args: argparse.Namespace) -> None:
    input_path = Path(args.input).expanduser().resolve()
    validate_input(input_path)

    file_dt = _file_creation_dt(input_path)
    ts = file_dt.strftime("%Y%m%d_%H%M%S")
    parent = Path(args.output).expanduser().resolve()
    output_dir = parent / f"meeting_{ts}"

    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy source file into output dir (copy, not move — preserve the original)
    dest_file = output_dir / input_path.name
    if input_path != dest_file.resolve():
        shutil.copy2(str(input_path), str(dest_file))
        input_path = dest_file.resolve()

    system_prompt = build_system_prompt(
        context=args.context,
    )

    segment_prompt = load_segment_prompt()

    user_prompt = build_user_prompt(input_path, output_dir)

    print_header(input_path, output_dir)

    env = {}
    mistral_key = os.environ.get("MISTRAL_API_KEY")
    if mistral_key:
        env["MISTRAL_API_KEY"] = mistral_key
    else:
        print("  ⚠ MISTRAL_API_KEY non définie — la transcription Voxtral échouera.")

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=TOOLS,
        permission_mode="bypassPermissions",
        cwd=str(output_dir.resolve()),
        max_turns=args.max_turns,
        max_buffer_size=10 * 1024 * 1024,  # 10 MB (default 1 MB too small for sub-agent results)
        env=env,
        agents={
            "segment-analyzer": AgentDefinition(
                description="Analyse un segment thématique de réunion : correction de transcription, analyse visuelle des frames, production du rapport de segment.",
                prompt=segment_prompt,
                tools=SEGMENT_TOOLS,
            ),
        },
    )

    try:
        turn = 0
        # Maps Task tool_use_id → short description for sub-agent tracking
        subagents: dict[str, str] = {}
        # Per-sub-agent turn counters
        subagent_turns: dict[str, int] = {}

        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, SystemMessage):
                if message.subtype == "init":
                    session_id = message.data.get("session_id", "")
                    print(f"\n  Session : {session_id}\n")

            elif isinstance(message, AssistantMessage):
                is_subagent = message.parent_tool_use_id is not None
                agent_name = ""
                if is_subagent:
                    agent_name = subagents.get(message.parent_tool_use_id, "sous-agent")

                for block in message.content:
                    if isinstance(block, TextBlock):
                        if is_subagent:
                            print(f"\n  [{agent_name}] {block.text}")
                        else:
                            print(f"\n{block.text}")
                    elif isinstance(block, ThinkingBlock):
                        preview = block.thinking[:120].replace("\n", " ")
                        if len(block.thinking) > 120:
                            preview += "…"
                        tag = f"[{agent_name}] " if is_subagent else ""
                        print(f"\n  {tag}💭 {preview}")
                    elif isinstance(block, ToolUseBlock):
                        turn += 1
                        label = _tool_label(block)
                        if is_subagent:
                            pid = message.parent_tool_use_id
                            subagent_turns[pid] = subagent_turns.get(pid, 0) + 1
                            st = subagent_turns[pid]
                            print(f"\n  [{agent_name}] [{st}] 🔧 {block.name}: {label}")
                        else:
                            # Register sub-agent when main agent launches a Task
                            if block.name == "Task":
                                desc = block.input.get("description", "?")
                                subagents[block.id] = desc
                            main_turn = turn - sum(subagent_turns.values())
                            print(f"\n  [{main_turn}] 🔧 {block.name}: {label}")
                    elif isinstance(block, ToolResultBlock):
                        if block.is_error:
                            snippet = str(block.content)[:200] if block.content else ""
                            tag = f"[{agent_name}] " if is_subagent else ""
                            print(f"  {tag}❌ Erreur: {snippet}")

            elif isinstance(message, ResultMessage):
                print("\n" + "─" * 56)
                if message.is_error:
                    print("  ❌ Pipeline terminé avec erreur")
                    if message.result:
                        print(f"     {message.result[:300]}")
                else:
                    print("  ✅ Pipeline terminé avec succès")
                    cost = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "N/A"
                    duration_min = message.duration_ms / 60_000
                    print(f"  ⏱ Durée  : {duration_min:.1f} min")
                    print(f"  💰 Coût  : {cost}")
                    print(f"  🔄 Tours : {message.num_turns}")

                report = output_dir / "REPORT.md"
                if report.exists():
                    print(f"\n  📄 Rapport : {report.resolve()}")
                print()

    except CLINotFoundError:
        sys.exit(
            "\n❌ Claude Code CLI non trouvé.\n"
            "   Le SDK claude-agent-sdk inclut le CLI, vérifiez l'installation avec: uv sync"
        )
    except ProcessError as e:
        msg = f"\n❌ Erreur du processus Claude (exit {e.exit_code})"
        if e.stderr:
            msg += f"\n   {e.stderr[:500]}"
        sys.exit(msg)
    except ClaudeSDKError as e:
        sys.exit(f"\n❌ Erreur SDK: {e}")


def _tool_label(block: ToolUseBlock) -> str:
    """Build a short human-readable label for a tool use."""
    inp = block.input
    if block.name == "Bash":
        cmd = inp.get("command", "")
        return cmd[:80] + ("…" if len(cmd) > 80 else "")
    if block.name == "Read":
        return inp.get("file_path", "")
    if block.name in ("Write", "Edit"):
        return inp.get("file_path", "")
    if block.name == "Glob":
        return inp.get("pattern", "")
    if block.name == "Task":
        desc = inp.get("description", "")
        agent = inp.get("subagent_type", "")
        return f"{agent}: {desc}"
    return str(inp)[:80]


def cli() -> None:
    load_dotenv()
    args = parse_args()
    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    cli()
