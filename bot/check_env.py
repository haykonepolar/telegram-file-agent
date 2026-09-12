import pathlib
p = pathlib.Path(r"C:\private-ai\.env")
text = p.read_text(encoding="utf-8-sig")
for line in text.splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, val = line.split("=", 1)
        print(f"key={key.strip()!r} token_len={len(val.strip())} starts_with_digit={val.strip()[:1].isdigit()}")
