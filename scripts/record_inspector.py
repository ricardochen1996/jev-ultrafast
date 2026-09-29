"""Record the local inspector while it runs one goal, and render it at 1x.

Needs `uv run jev` on port 8766. The run itself makes paid model calls. Frames keep their original
capture times, so the video plays at the speed the run happened.

    uv run --with imageio-ffmpeg python scripts/record_inspector.py artifacts/recording \\
        --url https://www.google.com --goal "Search for browser ultrafast."
"""

import argparse
import base64
import io
import json
import subprocess
import time
from pathlib import Path

import httpx
from PIL import Image

from jev_ultrafast.browser import Browser

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("folder", type=Path, help="New folder for frames and the run state")
parser.add_argument("--url", required=True)
parser.add_argument("--goal", required=True)
parser.add_argument("--lang", default="en", choices=["en", "zh"])
parser.add_argument("--inspector", default="http://127.0.0.1:8766/")
args = parser.parse_args()
args.folder.mkdir(parents=True, exist_ok=False)

WIDTH, HEIGHT = 1440, 900
browser = Browser(args.inspector)
frames = []
try:
    browser.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
    before = browser.evaluate("document.documentElement.lang.startsWith('zh') ? 'zh' : 'en'")
    reuse = browser.evaluate("document.getElementById('reuse').checked")
    browser.evaluate(
        f"""document.querySelector('[data-lang="{args.lang}"]').click();
        document.getElementById('runs-panel').open = false;
        document.getElementById('target-url').value = {json.dumps(args.url)};
        document.getElementById('goal').value = {json.dumps(args.goal)};
        document.getElementById('goal').dispatchEvent(new Event('input'));
        // An owned tab keeps the recording off the user's own tabs; no change event, so not remembered.
        document.getElementById('reuse').checked = false; 1"""
    )
    # Published footage must not carry the account of the browser profile: mask e-mail addresses in
    # what the inspector displays. The run and the model input are untouched.
    browser.evaluate(
        r"""const mask = (root) => {
          const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
          for (let n; (n = walk.nextNode()); )
            if (/@\S+\.\w/.test(n.data)) n.data = n.data.replace(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, "•••@•••");
        };
        mask(document.body);
        new MutationObserver(() => mask(document.body)).observe(document.body, {subtree: true, childList: true,
          characterData: true}); 1"""
    )
    time.sleep(0.8)

    def grab():
        data = browser.call("Page.captureScreenshot", format="jpeg", quality=85)["data"]
        frames.append((time.perf_counter(), base64.b64decode(data)))

    for _ in range(3):
        grab()
    # A page left open at this address would take the goal as its next instruction; start fresh.
    browser.evaluate(
        "const n = document.getElementById('new-task'); (n.hidden ? document.getElementById('start') : n).click(); 1"
    )
    started = time.perf_counter()
    while time.perf_counter() - started < 120:
        grab()
        running = browser.evaluate("document.getElementById('start').classList.contains('stopping')")
        if not running and time.perf_counter() - started > 2:
            break
    end = time.perf_counter()
    while time.perf_counter() - end < 1.5:
        grab()
    state = httpx.get(args.inspector + "api/state", timeout=10).json()
    browser.evaluate(f"document.querySelector('[data-lang=\"{before}\"]').click(); 1")
    # Starting a run remembers the toggles; give the user's own choice back.
    browser.evaluate(
        f"const r = document.getElementById('reuse'); r.checked = {json.dumps(reuse)};"
        " r.dispatchEvent(new Event('change')); 1"
    )
finally:
    browser.close()

(args.folder / "state.json").write_text(
    json.dumps(
        {
            "url": args.url,
            "goal": args.goal,
            "status": state.get("status"),
            "elapsed_ms": state.get("elapsed_ms"),
            "steps": len(state.get("history") or []),
            "final_url": (state.get("page") or {}).get("url"),
            "wall_ms": round((end - started) * 1000),
        },
        indent=2,
    )
)
if state.get("status") != "done":
    raise SystemExit(f"The run ended {state.get('status')!r}; docs were left unchanged.")
images = [Image.open(io.BytesIO(data)).convert("RGB") for _, data in frames]
durations = [round((b[0] - a[0]) * 1000) for a, b in zip(frames, frames[1:])] + [1500]
for i, image in enumerate(images):
    image.save(args.folder / f"{i:05d}.jpg", quality=90)
scaled = [image.resize((1080, 675), Image.LANCZOS) for image in images]
scaled[0].save(
    ROOT / "docs/demo.gif", save_all=True, append_images=scaled[1:], duration=durations, loop=0, optimize=True
)
images[-1].save(ROOT / "docs/inspector.png")
concat = args.folder / "frames.txt"
concat.write_text(
    "".join(f"file '{i:05d}.jpg'\nduration {d / 1000:.3f}\n" for i, d in enumerate(durations))
    + f"file '{len(images) - 1:05d}.jpg'\n"
)
import imageio_ffmpeg  # noqa: E402

subprocess.run(
    [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat)]
    + ["-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-crf", "26", "-movflags", "+faststart"]
    + [str(ROOT / "docs/demo.mp4")],
    check=True,
)
print(json.dumps(json.loads((args.folder / "state.json").read_text()), ensure_ascii=False))
