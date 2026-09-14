"""Create local settings without passing credentials through cmd.exe expansion."""
from getpass import getpass
from pathlib import Path


def main() -> None:
    target = Path(__file__).resolve().parent / ".env"
    if target.exists():
        print(".env already exists; keeping your current settings.")
        return
    token = getpass("Telegram BOT_TOKEN (hidden): ").strip()
    key = getpass("OpenAI API key (hidden): ").strip()
    if not token or not key:
        raise SystemExit("BOT_TOKEN and OPENAI_API_KEY are required.")
    base_url = input("OpenAI base URL (Enter for default): ").strip()
    model = input("OpenAI model (Enter for gpt-6-astra): ").strip() or "gpt-6-astra"
    entries = {"BOT_TOKEN": token, "OPENAI_API_KEY": key, "OPENAI_BASE_URL": base_url,
               "OPENAI_MODEL": model, "DATABASE_PATH": "data/bot.db", "TIMEZONE": "Asia/Yekaterinburg"}
    def quote(value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("Settings must be single-line values.")
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    with target.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(f"{key}={quote(value)}" for key, value in entries.items()) + "\n")
    print(".env created locally. Run run_local.bat after installing dependencies.")


if __name__ == "__main__":
    main()
