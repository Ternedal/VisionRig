from __future__ import annotations

from .gateway import generate_gateway_token


def main() -> None:
    print(generate_gateway_token())


if __name__ == "__main__":
    main()
