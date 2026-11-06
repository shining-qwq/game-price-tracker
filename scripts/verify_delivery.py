"""Install and test a clean project copy without using the working database."""
import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import venv


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-url", help="Optional HTTPS Python package index")
    parser.add_argument("--keep", action="store_true", help="Keep the isolated environment for inspection")
    parser.add_argument("--prepare-only", action="store_true", help="Install/import/upgrade only; do not run tests yet")
    args = parser.parse_args()
    if args.index_url:
        index = urlsplit(args.index_url)
        if index.scheme != "https" or not index.hostname or index.username or index.password:
            parser.error("Use an HTTPS index URL without embedded credentials")

    temporary_parent = Path(tempfile.gettempdir()).resolve()
    work = Path(tempfile.mkdtemp(prefix="price-tracker-delivery-", dir=temporary_parent)).resolve()
    project, environment = work / "project", work / "venv"
    print(f"ISOLATED_ROOT={work}", flush=True)
    try:
        project.mkdir()
        for path in ROOT.iterdir():
            if path.is_file() and (path.suffix in {".py", ".sql", ".md"} or path.name.startswith("requirements")):
                shutil.copy2(path, project / path.name)
        for name in ["adapters", "services", "templates", "static", "scripts", "tests", "docs"]:
            shutil.copytree(ROOT / name, project / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_test*.sqlite3", "_debug.sqlite3"))
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

        def run(*arguments):
            subprocess.run([str(python), *arguments], cwd=project, check=True)

        install = ["-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--timeout", "20", "--retries", "1",
                   "-r", "requirements.txt"]
        if args.index_url:
            install.extend(["--index-url", args.index_url])
        run(*install)
        run("-m", "pip", "check")
        run("-c", "import sys,flask,app; print('IMPORT_OK',sys.version,sys.executable)")
        # Default instance/ belongs to the newly copied project, never ROOT.
        run("-m", "flask", "--app", "app", "upgrade-db")
        run("-m", "flask", "--app", "app", "upgrade-db")
        if not args.prepare_only:
            run("-m", "unittest", "discover", "-s", "tests", "-q")
            run("-m", "compileall", "-q", "app.py", "admin_sync.py", "security.py", "migrations.py",
                "price_chart.py", "adapters", "services", "scripts", "tests")
            for path in project.rglob("*.py"):
                if "instance" not in path.parts and "__pycache__" not in path.parts:
                    ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 10))
            print("PYTHON_310_GRAMMAR_OK", flush=True)
        print(json.dumps({"success": True, "tests_run": not args.prepare_only,
                          "python": str(python), "project": str(project), "kept": args.keep}), flush=True)
    finally:
        if not args.keep:
            # Only the newly generated temp root is removed; source data is not copied.
            if work.parent != temporary_parent or not work.name.startswith("price-tracker-delivery-"):
                raise RuntimeError("Refusing to remove a path outside the generated temporary workspace")
            shutil.rmtree(work)


if __name__ == "__main__":
    main()
