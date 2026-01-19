from pathlib import Path

from app.scanner import extract_datamatrix


def main() -> None:
    for path in sorted(Path("test").glob("*.png")):
        data = extract_datamatrix(path.read_bytes())
        if data:
            print(f"{path}: {data}")
        else:
            print(f"{path}: no DataMatrix found")


if __name__ == "__main__":
    main()

