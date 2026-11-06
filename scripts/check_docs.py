"""Check local Markdown links, anchors and managed PowerShell interpreter usage."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


def visible_markdown(text):
    return re.sub(r"(?ms)^```.*?^```\s*$", "", text)


def heading_ids(text):
    found, counts = set(), {}
    for heading in re.findall(r"(?m)^#{1,6}\s+(.+)$", visible_markdown(text)):
        heading = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        suffix = counts.get(heading, 0)
        counts[heading] = suffix + 1
        found.add(heading + (f"-{suffix}" if suffix else ""))
    found.update(re.findall(r'\bid=["\']([^"\']+)["\']', text))
    return found


def main():
    errors, links = [], 0
    documents = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    for path in documents:
        text = path.read_text(encoding="utf-8")
        for destination in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", visible_markdown(text)):
            destination = destination.strip().strip("<>")
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc:
                continue  # External URLs are not fetched by this offline check.
            links += 1
            target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
            if not target.exists():
                errors.append(f"{path.relative_to(ROOT)}: missing {destination}")
            elif parsed.fragment and target.suffix == ".md":
                if unquote(parsed.fragment) not in heading_ids(target.read_text(encoding="utf-8")):
                    errors.append(f"{path.relative_to(ROOT)}: missing anchor {destination}")
        for block in re.findall(r"(?ms)^```powershell\n(.*?)^```", text):
            for line in block.splitlines():
                if line.lstrip().startswith(("#", "Get-Command ")):
                    continue
                if (re.search(r"(^|\s)python(?:\.exe)?\s", line)
                        or (re.search(r"(^|\s)pip\s+install", line) and "-m pip " not in line)):
                    errors.append(f"{path.relative_to(ROOT)}: ambiguous interpreter: {line.strip()}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"DOCS_OK: {len(documents)} Markdown documents, {links} local links/anchors; no external requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
