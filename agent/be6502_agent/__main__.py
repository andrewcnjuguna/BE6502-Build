"""Terminal chat, for trying the agent without Telegram: python -m be6502_agent"""

import sys

from .agent import BE6502Agent
from .telegram_bot import status_report


def confirm(description: str) -> bool:
    print(f"\n>>> APPROVAL REQUIRED: {description}")
    return input(">>> Run this? [y/N] ").strip().lower() in ("y", "yes")


def main():
    agent = BE6502Agent(confirm=confirm)
    print(f"BE6502 agent ({agent.model_name})\n{status_report()}\n'quit' to exit.\n")
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user.lower() in ("quit", "exit"):
            break
        if user:
            try:
                agent.send(user)
            except KeyboardInterrupt:
                print("\n[turn interrupted - conversation kept]")
            except Exception as exc:
                print(f"\n[error: {exc}]", file=sys.stderr)
            print()


if __name__ == "__main__":
    main()
