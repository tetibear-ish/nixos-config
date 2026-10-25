"""Small client for a ComfyUI server's HTTP API (standard library only).

Library use:

    import comfy
    c = comfy.Comfy()                       # $COMFY_URL or http://127.0.0.1:8188
    files = c.txt2img("a lighthouse at dusk", width=768, height=768)
    wf = comfy.load_workflow("upscale.json")  # API-format export
    files = c.run(wf, prompt="...", set={"12.scale_by": 2})

CLI use: `comfy --help`. Emacs drives the CLI with `--json`.

Workflows must be in ComfyUI's *API* format: in the web UI, Workflow ->
Export (API). The regular "Save" format (with "nodes"/"links") can't be
queued directly.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterable

DEFAULT_URL = "http://127.0.0.1:8188"
OUTPUT_DIR = Path(os.environ.get("COMFY_OUTPUT_DIR", "~/comfy/outputs")).expanduser()
WORKFLOW_DIR = Path(os.environ.get("COMFY_WORKFLOW_DIR", "~/comfy/workflows")).expanduser()

# Inputs that hold a sampler seed; randomized on every run unless pinned, since
# ComfyUI caches identical graphs and would otherwise return nothing new.
SEED_INPUTS = ("seed", "noise_seed")

Workflow = dict[str, dict[str, Any]]


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, url: str | None = None, timeout: float = 30):
        self.url = (url or os.environ.get("COMFY_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex

    # -- HTTP --------------------------------------------------------------

    def _request(self, method: str, path: str, body: Any = None,
                 headers: dict[str, str] | None = None) -> bytes:
        data = None
        headers = dict(headers or {})
        if body is not None and not isinstance(body, bytes):
            data = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        elif body is not None:
            data = body
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise ComfyError(f"{method} {path} -> HTTP {e.code}: {_error_summary(detail)}") from None
        except urllib.error.URLError as e:
            raise ComfyError(f"can't reach ComfyUI at {self.url} ({e.reason}); is the service running?") from None

    def get(self, path: str) -> Any:
        return json.loads(self._request("GET", path))

    def post(self, path: str, body: Any = None) -> Any:
        raw = self._request("POST", path, body if body is not None else {})
        return json.loads(raw) if raw.strip() else None

    # -- Server state ------------------------------------------------------

    def is_up(self) -> bool:
        try:
            self.get("/system_stats")
            return True
        except ComfyError:
            return False

    def wait_until_up(self, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        while not self.is_up():
            if time.monotonic() > deadline:
                raise ComfyError(f"ComfyUI at {self.url} didn't come up within {timeout:.0f}s")
            time.sleep(1)

    def system_stats(self) -> dict:
        return self.get("/system_stats")

    def queue(self) -> dict:
        return self.get("/queue")

    def models(self, folder: str | None = None) -> list[str]:
        """Model folders (no FOLDER), or the files in FOLDER, e.g. "checkpoints"."""
        if folder is None:
            return self.get("/models")
        return self.get(f"/models/{urllib.parse.quote(folder)}")

    def interrupt(self) -> None:
        self.post("/interrupt")

    def clear_queue(self) -> None:
        self.post("/queue", {"clear": True})

    def free(self, unload_models: bool = True) -> None:
        """Unload models and free (V)RAM without stopping the server."""
        self.post("/free", {"unload_models": unload_models, "free_memory": True})

    def upload_image(self, path: str | os.PathLike, overwrite: bool = True) -> str:
        """Upload an image into the server's input/ dir; returns its name for LoadImage."""
        path = Path(path)
        boundary = uuid.uuid4().hex
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\n'
            f'{"true" if overwrite else "false"}\r\n'.encode(),
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'.encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        raw = self._request("POST", "/upload/image", b"".join(parts),
                            {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        info = json.loads(raw)
        return f"{info['subfolder']}/{info['name']}" if info.get("subfolder") else info["name"]

    # -- Running workflows -------------------------------------------------

    def submit(self, workflow: Workflow) -> str:
        """Queue WORKFLOW (API format) and return its prompt id."""
        resp = self.post("/prompt", {"prompt": workflow, "client_id": self.client_id})
        return resp["prompt_id"]

    def wait(self, prompt_id: str, timeout: float | None = None,
             on_status: Any = None) -> dict:
        """Block until PROMPT_ID finishes; return its history entry."""
        deadline = None if timeout is None else time.monotonic() + timeout
        last = None
        while True:
            hist = self.get(f"/history/{prompt_id}")
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyError(_execution_error(status))
                if status.get("completed", True):
                    return entry
            state = self._queue_state(prompt_id)
            if on_status and state != last:
                on_status(state)
                last = state
            if deadline and time.monotonic() > deadline:
                raise ComfyError(f"prompt {prompt_id} still {state} after {timeout:.0f}s")
            time.sleep(0.5)

    def _queue_state(self, prompt_id: str) -> str:
        q = self.queue()
        if any(item[1] == prompt_id for item in q.get("queue_running", [])):
            return "running"
        pending = [item[1] for item in q.get("queue_pending", [])]
        if prompt_id in pending:
            return f"queued ({pending.index(prompt_id) + 1} ahead)" if pending.index(prompt_id) else "queued"
        return "finishing"

    def outputs(self, entry: dict) -> list[dict]:
        """File records (filename/subfolder/type) from a history entry's outputs."""
        files = []
        for node_output in entry.get("outputs", {}).values():
            for key in ("images", "gifs", "videos", "audio", "files"):
                for item in node_output.get(key, []):
                    if isinstance(item, dict) and "filename" in item and item.get("type") == "output":
                        files.append(item)
        return files

    def download(self, item: dict, out_dir: str | os.PathLike = OUTPUT_DIR) -> Path:
        out_dir = Path(out_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        query = urllib.parse.urlencode({k: item.get(k, "") for k in ("filename", "subfolder", "type")})
        dest = out_dir / item["filename"]
        dest.write_bytes(self._request("GET", f"/view?{query}"))
        return dest

    def run(self, workflow: Workflow, *, prompt: str | None = None,
            negative: str | None = None, seed: int | None = None,
            set: dict[str, Any] | None = None, out_dir: str | os.PathLike = OUTPUT_DIR,
            timeout: float | None = None, on_status: Any = None) -> list[Path]:
        """Run WORKFLOW to completion and download its outputs into OUT_DIR.

        PROMPT/NEGATIVE replace the text of the prompt nodes feeding the
        sampler; SEED pins every sampler seed (random otherwise); SET maps
        "node_id.input" to a value for anything else.
        """
        wf = apply_inputs(workflow, prompt=prompt, negative=negative, seed=seed, set=set)
        prompt_id = self.submit(wf)
        if on_status:
            on_status(f"queued {prompt_id}")
        entry = self.wait(prompt_id, timeout=timeout, on_status=on_status)
        return [self.download(item, out_dir) for item in self.outputs(entry)]

    def txt2img(self, prompt: str, negative: str = "", *, checkpoint: str | None = None,
                width: int = 1024, height: int = 1024, steps: int = 25, cfg: float = 7.0,
                sampler: str = "euler", scheduler: str = "normal", seed: int | None = None,
                batch: int = 1, **run_kwargs: Any) -> list[Path]:
        """Plain checkpoint text-to-image. Defaults to the first checkpoint found."""
        if checkpoint is None:
            ckpts = self.models("checkpoints")
            if not ckpts:
                raise ComfyError("no checkpoints in models/checkpoints/ on the server; "
                                 "add a .safetensors there (e.g. an SDXL or SD1.5 model)")
            checkpoint = ckpts[0]
        wf = txt2img_workflow(prompt, negative, checkpoint=checkpoint, width=width, height=height,
                              steps=steps, cfg=cfg, sampler=sampler, scheduler=scheduler,
                              seed=_seed(seed), batch=batch)
        return self.run(wf, seed=seed, **run_kwargs)


# -- Workflow helpers -----------------------------------------------------

def load_workflow(path: str | os.PathLike) -> Workflow:
    """Load an API-format workflow. A bare name is looked up in WORKFLOW_DIR."""
    p = Path(path).expanduser()
    if not p.exists() and not p.is_absolute():
        for candidate in (WORKFLOW_DIR / p, WORKFLOW_DIR / f"{p}.json"):
            if candidate.exists():
                p = candidate
                break
    data = json.loads(p.read_text())
    if "nodes" in data and "links" in data:
        raise ComfyError(f"{p} is a UI-format workflow; re-export it with Workflow -> Export (API)")
    if "prompt" in data and isinstance(data["prompt"], dict):
        data = data["prompt"]
    return data


def list_workflows(directory: str | os.PathLike = WORKFLOW_DIR) -> list[Path]:
    d = Path(directory).expanduser()
    return sorted(d.glob("*.json")) if d.is_dir() else []


def apply_inputs(workflow: Workflow, *, prompt: str | None = None, negative: str | None = None,
                 seed: int | None = None, set: dict[str, Any] | None = None) -> Workflow:
    """Return a copy of WORKFLOW with prompts, seeds and explicit inputs applied."""
    wf = json.loads(json.dumps(workflow))
    if prompt is not None or negative is not None:
        pos, neg = _prompt_nodes(wf)
        if prompt is not None:
            if not pos:
                raise ComfyError("couldn't find a positive prompt node; use --set NODE.text=...")
            wf[pos]["inputs"]["text"] = prompt
        if negative is not None:
            if not neg:
                raise ComfyError("couldn't find a negative prompt node; use --set NODE.text=...")
            wf[neg]["inputs"]["text"] = negative
    for node in wf.values():
        for key in SEED_INPUTS:
            if key in node.get("inputs", {}) and not isinstance(node["inputs"][key], list):
                node["inputs"][key] = _seed(seed)
    for target, value in (set or {}).items():
        node_id, _, name = target.partition(".")
        if node_id not in wf or not name:
            raise ComfyError(f"bad --set target {target!r}; expected NODE_ID.input (nodes: {', '.join(wf)})")
        wf[node_id]["inputs"][name] = value
    return wf


def _prompt_nodes(wf: Workflow) -> tuple[str | None, str | None]:
    """Find the text-encode nodes wired to a sampler's positive/negative inputs."""
    def text_source(ref: Any, role: str, depth: int = 0) -> str | None:
        # Follow links upstream (through conditioning combiners, ControlNet
        # apply, WanVaceToVideo, ...) until reaching a node with a string
        # "text" input. Nodes taking both conditionings are followed through
        # the input named after ROLE, so "negative" doesn't land on positive.
        if not isinstance(ref, list) or depth > 8:
            return None
        node = wf.get(str(ref[0]))
        if node is None:
            return None
        inputs = node.get("inputs", {})
        if isinstance(inputs.get("text"), str):
            return str(ref[0])
        other = "negative" if role == "positive" else "positive"
        if role in inputs:
            candidates = [inputs[role]]
        else:
            candidates = [v for k, v in inputs.items() if k != other]
        for value in candidates:
            found = text_source(value, role, depth + 1)
            if found:
                return found
        return None

    for node in wf.values():
        inputs = node.get("inputs", {})
        if "positive" in inputs:
            return (text_source(inputs.get("positive"), "positive"),
                    text_source(inputs.get("negative"), "negative"))
    texts = [k for k, n in wf.items() if isinstance(n.get("inputs", {}).get("text"), str)]
    return (texts[0] if texts else None), (texts[1] if len(texts) > 1 else None)


def txt2img_workflow(prompt: str, negative: str, *, checkpoint: str, width: int, height: int,
                     steps: int, cfg: float, sampler: str, scheduler: str, seed: int,
                     batch: int = 1) -> Workflow:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": batch}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
            "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler,
            "scheduler": scheduler, "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "comfy"}},
    }


def _seed(seed: int | None) -> int:
    return seed if seed is not None else random.randrange(2**63)


def _error_summary(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()[:500]
    err = data.get("error", {})
    msg = err.get("message", "") if isinstance(err, dict) else str(err)
    details = []
    for node_id, node_err in (data.get("node_errors") or {}).items():
        for e in node_err.get("errors", []):
            details.append(f"node {node_id} ({node_err.get('class_type')}): "
                           f"{e.get('message')} {e.get('details', '')}".strip())
    return "; ".join([msg] + details) if details else (msg or body[:500])


def _execution_error(status: dict) -> str:
    for kind, data in status.get("messages", []):
        if kind == "execution_interrupted":
            return "interrupted"
        if kind == "execution_error":
            return (f"node {data.get('node_id')} ({data.get('node_type')}) failed: "
                    f"{data.get('exception_type')}: {data.get('exception_message', '').strip()}")
    return "workflow failed"


# -- CLI --------------------------------------------------------------------

def _parse_set(pairs: Iterable[str]) -> dict[str, Any]:
    out = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise ComfyError(f"--set expects NODE.input=VALUE, got {pair!r}")
        try:
            out[key] = json.loads(raw)
        except ValueError:
            out[key] = raw
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="comfy", description="Talk to a ComfyUI server.")
    ap.add_argument("--url", help=f"server URL (default $COMFY_URL or {DEFAULT_URL})")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="server, device and queue info")
    p = sub.add_parser("models", help="list model folders, or the models in one")
    p.add_argument("folder", nargs="?")
    sub.add_parser("workflows", help=f"list API-format workflows in {WORKFLOW_DIR}")
    sub.add_parser("queue", help="show running and pending prompts")
    sub.add_parser("interrupt", help="stop the running prompt")
    sub.add_parser("clear", help="drop all pending prompts")
    sub.add_parser("free", help="unload models and free VRAM")
    p = sub.add_parser("upload", help="upload an image to the server's input/ dir")
    p.add_argument("image")
    p = sub.add_parser("wait-up", help="block until the server answers")
    p.add_argument("--timeout", type=float, default=120)

    def add_run_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--seed", type=int, help="pin the seed (random by default)")
        p.add_argument("--out", default=str(OUTPUT_DIR), help="download outputs here (default %(default)s)")
        p.add_argument("--timeout", type=float, help="give up after this many seconds")

    p = sub.add_parser("gen", help="text-to-image with a checkpoint model")
    p.add_argument("prompt")
    p.add_argument("-n", "--negative", default="")
    p.add_argument("-c", "--checkpoint", help="checkpoint file (default: first in models/checkpoints)")
    p.add_argument("-W", "--width", type=int, default=1024)
    p.add_argument("-H", "--height", type=int, default=1024)
    p.add_argument("--steps", type=int, default=25)
    p.add_argument("--cfg", type=float, default=7.0)
    p.add_argument("--sampler", default="euler")
    p.add_argument("--scheduler", default="normal")
    p.add_argument("-b", "--batch", type=int, default=1)
    add_run_opts(p)

    p = sub.add_parser("run", help="run an API-format workflow file")
    p.add_argument("workflow", help=f"path, or a name in {WORKFLOW_DIR}")
    p.add_argument("-p", "--prompt", help="replace the positive prompt text")
    p.add_argument("-n", "--negative", help="replace the negative prompt text")
    p.add_argument("--set", action="append", default=[], metavar="NODE.INPUT=VALUE",
                   help="override any node input (VALUE parsed as JSON if possible)")
    add_run_opts(p)

    args = ap.parse_args(argv)
    c = Comfy(args.url)

    def emit(data: Any, text: str) -> None:
        print(json.dumps(data) if args.json else text)

    def status(msg: str) -> None:
        print(f"comfy: {msg}", file=sys.stderr, flush=True)

    try:
        if args.cmd == "status":
            stats, q = c.system_stats(), c.queue()
            devs = stats.get("devices", [])
            data = {"url": c.url, "up": True, "system": stats.get("system", {}), "devices": devs,
                    "running": len(q.get("queue_running", [])), "pending": len(q.get("queue_pending", []))}
            lines = [f"ComfyUI {stats['system'].get('comfyui_version', '?')} at {c.url}"]
            for d in devs:
                lines.append(f"  {d['name']}: {(d['vram_total'] - d['vram_free']) / 2**30:.1f} / "
                             f"{d['vram_total'] / 2**30:.1f} GiB used")
            lines.append(f"  queue: {data['running']} running, {data['pending']} pending")
            emit(data, "\n".join(lines))
        elif args.cmd == "models":
            items = c.models(args.folder)
            emit(items, "\n".join(items))
        elif args.cmd == "workflows":
            items = [str(p) for p in list_workflows()]
            emit(items, "\n".join(items))
        elif args.cmd == "queue":
            q = c.queue()
            data = {"running": [i[1] for i in q.get("queue_running", [])],
                    "pending": [i[1] for i in q.get("queue_pending", [])]}
            emit(data, "\n".join([f"running {p}" for p in data["running"]] +
                                 [f"pending {p}" for p in data["pending"]]) or "queue empty")
        elif args.cmd == "interrupt":
            c.interrupt(); emit({"ok": True}, "interrupted")
        elif args.cmd == "clear":
            c.clear_queue(); emit({"ok": True}, "queue cleared")
        elif args.cmd == "free":
            c.free(); emit({"ok": True}, "models unloaded")
        elif args.cmd == "upload":
            name = c.upload_image(args.image); emit({"name": name}, name)
        elif args.cmd == "wait-up":
            c.wait_until_up(args.timeout); emit({"up": True}, "up")
        elif args.cmd in ("gen", "run"):
            if args.cmd == "gen":
                files = c.txt2img(args.prompt, args.negative, checkpoint=args.checkpoint,
                                  width=args.width, height=args.height, steps=args.steps,
                                  cfg=args.cfg, sampler=args.sampler, scheduler=args.scheduler,
                                  seed=args.seed, batch=args.batch, out_dir=args.out,
                                  timeout=args.timeout, on_status=status)
            else:
                files = c.run(load_workflow(args.workflow), prompt=args.prompt,
                              negative=args.negative, seed=args.seed, set=_parse_set(args.set),
                              out_dir=args.out, timeout=args.timeout, on_status=status)
            emit({"files": [str(f) for f in files]}, "\n".join(str(f) for f in files))
    except (ComfyError, OSError, ValueError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"comfy: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
