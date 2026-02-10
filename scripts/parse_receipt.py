import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.receipt_parser import ReceiptParser, render_items  # noqa: E402


def main() -> None:
    parser = ReceiptParser()
    data = parser.parse_pdf(os.path.join(ROOT, "data", "reciept2.pdf"))
    print(f"Дата: {data.date}")
    print("Позиции:")
    print(render_items(data.items))


if __name__ == "__main__":
    main()

