"""Complete exact OpenSpec checkboxes only after their acceptance has been checked."""
import json
from pathlib import Path
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
root = Path(__file__).resolve().parents[1]
command = ["cmd", "/c", "openspec", "instructions", "apply", "--change", "add-evidence-grounded-assessment", "--json"]
for label in sys.argv[1:]:
    before = json.loads(subprocess.check_output(command, cwd=root).decode("utf-8"))
    task = next(row for row in before["tasks"] if row["description"].startswith(label+" "))
    if task["done"]:
        continue
    path = Path(task["sourcePath"])
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    position = task["line"]-1
    assert lines[position].strip() == "- [ ] "+task["description"], "Task drift; refresh before editing"
    lines[position] = lines[position].replace("- [ ]", "- [x]", 1)
    path.write_text("".join(lines), encoding="utf-8")
    after = json.loads(subprocess.check_output(command, cwd=root).decode("utf-8"))
    assert next(row for row in after["tasks"] if row["id"] == task["id"])["done"]
    assert after["progress"]["complete"] == before["progress"]["complete"]+1
    print(f"{label}: {after['progress']['complete']}/{after['progress']['total']}")
