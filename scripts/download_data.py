#!/usr/bin/env python3
"""Download the official UCI archive locally; raw data is git-ignored."""
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile
import io

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
URL = "https://archive.ics.uci.edu/static/public/296/diabetes+130-us+hospitals+for+years+1999-2008.zip"


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    print("Downloading official UCI dataset...")
    with urlopen(URL, timeout=90) as response:
        payload = response.read()
    with ZipFile(io.BytesIO(payload)) as archive:
        allowed = {"diabetic_data.csv", "IDS_mapping.csv"}
        for member in archive.namelist():
            name = Path(member).name
            if name in allowed:
                (RAW / name).write_bytes(archive.read(member))
    missing = [name for name in ("diabetic_data.csv", "IDS_mapping.csv") if not (RAW / name).exists()]
    if missing:
        raise RuntimeError(f"UCI archive did not contain expected files: {missing}")
    print(f"Saved source files to {RAW}; they are excluded from Git by .gitignore.")

if __name__ == "__main__":
    main()
