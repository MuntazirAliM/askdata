
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent  # noqa: E402
from llm import LLM, LLMError  # noqa: E402


def show(res: agent.AgentResult, trace: bool) -> None:
    if trace:
        for step in res.steps:
            print(f"  [{step.name}] {step.detail.splitlines()[0][:110] if step.detail else ''}")
    if res.sql:
        print("\nSQL:\n" + res.sql)
    if res.rows:
        print("\n" + agent._format_rows(res.columns, res.rows, limit=15))
    print("\n" + res.answer)
    for w in res.warnings:
        print(f"\n! {w}")
    print(f"\n({res.model}, {res.attempts} attempt(s), {res.seconds}s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="*")
    parser.add_argument("--as", dest="who", default="owner", help="owner or manager:1 .. manager:5")
    parser.add_argument("--trace", action="store_true", help="print each pipeline step")
    args = parser.parse_args()
    try:
        llm = LLM()
    except LLMError as exc:
        sys.exit(str(exc))
    history: list[tuple[str, str]] = []
    questions = [" ".join(args.question)] if args.question else None
    print(f"Model: {llm.label}   Signed in as: {args.who}")
    try:
        while True:
            if questions is not None:
                if not questions:
                    break
                q = questions.pop(0)
            else:
                q = input("\nAsk: ").strip()
                if q.lower() in {"quit", "exit", ""}:
                    break
            res = agent.ask(q, args.who, llm, history)
            show(res, args.trace)
            if res.sql:
                history.append((q, res.sql))
    except LLMError as exc:
        sys.exit(f"\nModel error: {exc}")
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    main()
