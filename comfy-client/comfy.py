"""Small client for a ComfyUI server's HTTP API (standard library only).

Library use:

    import comfy
    c = comfy.Comfy()                       # $COMFY_URL or http://127.0.0.1:8188
    files = c.txt2img("a lighthouse at dusk", width=768, height=768)
    wf = comfy.load_workflow("upscale.json")  # API-format export
    files = c.run(wf, prompt="...", set={"12.scale_by": 2})

    comfy.fetch_models("some-workflow.json")  # download its missing models
    comfy.browse("templates", query="qwen")  # shared workflows: templates/hf/civitai
    comfy.get_workflow(url)                   # into the web UI's workflow library
    comfy.discover_models(tag="landscape", types=["lora"])  # Civitai models
    comfy.install_model(version_id)           # into models/<its folder>/
    comfy.design_workflow("checkpoints/x.safetensors", "portraits")  # via Claude

CLI use: `comfy --help`. Emacs drives the CLI with `--json`.

Workflows must be in ComfyUI's *API* format: in the web UI, Workflow ->
Export (API). The regular "Save" format (with "nodes"/"links") can't be
queued directly.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import io
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_URL = "http://127.0.0.1:8188"
OUTPUT_DIR = Path(os.environ.get("COMFY_OUTPUT_DIR", "~/comfy/outputs")).expanduser()
WORKFLOW_DIR = Path(os.environ.get("COMFY_WORKFLOW_DIR", "~/comfy/workflows")).expanduser()
# The server's models/ dir; `fetch` downloads into it, so it must run on the
# server's machine (or see that dir some other way).
MODELS_DIR = Path(os.environ.get("COMFY_MODELS_DIR", "/var/lib/comfyui/models")).expanduser()
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "comfy"
# Hugging Face orgs whose repos are searched for models a workflow names
# without a download URL. Comfy-Org repackages most official models.
HF_AUTHORS = os.environ.get("COMFY_HF_AUTHORS", "Comfy-Org").split(",")

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
        path = Path(path).expanduser()
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
            set: dict[str, Any] | None = None, images: list[str] | None = None,
            out_dir: str | os.PathLike = OUTPUT_DIR,
            timeout: float | None = None, on_status: Any = None) -> list[Path]:
        """Run WORKFLOW to completion and download its outputs into OUT_DIR.

        PROMPT/NEGATIVE replace the text of the prompt nodes feeding the
        sampler; SEED pins every sampler seed (random otherwise); SET maps
        "node_id.input" to a value for anything else. IMAGES are files to
        upload into the workflow's LoadImage/LoadVideo nodes: "path" fills
        them in order, "node_id=path" a specific one.
        """
        set = dict(set or {})
        slots = file_inputs(workflow)
        for i, spec in enumerate(images or []):
            node_id, sep, path = spec.partition("=")
            if not sep or node_id not in workflow:
                node_id, path = (slots[i][0] if i < len(slots) else None), spec
            if node_id is None:
                raise ComfyError(f"no LoadImage/LoadVideo node left for {spec!r}")
            key = FILE_INPUT_NODES[workflow[node_id]["class_type"]]
            set[f"{node_id}.{key}"] = self.upload_image(path)
            if on_status:
                on_status(f"uploaded {Path(path).name} into node {node_id}")
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

def read_workflow(source: str | os.PathLike) -> list[dict]:
    """Raw workflow document(s) from SOURCE: a path, a name in WORKFLOW_DIR,
    an http(s) URL, or a PNG saved by ComfyUI (which embeds both the UI
    "workflow" and the API "prompt"; both are returned)."""
    source = str(source)
    if source.startswith(("http://", "https://")):
        url = source
        if "github.com/" in url and "/blob/" in url:
            url = url.replace("github.com/", "raw.githubusercontent.com/", 1).replace("/blob/", "/", 1)
        with urllib.request.urlopen(_download_request(url), timeout=60) as resp:
            raw = resp.read()
        label = source
    else:
        p = Path(source).expanduser()
        if not p.exists() and not p.is_absolute():
            for candidate in (WORKFLOW_DIR / p, WORKFLOW_DIR / f"{p}.json"):
                if candidate.exists():
                    p = candidate
                    break
        if not p.exists():
            raise ComfyError(f"no workflow {source!r} (looked in {WORKFLOW_DIR} too)")
        raw = p.read_bytes()
        label = str(p)
    if raw.startswith(b"\x89PNG"):
        chunks = _png_text(raw)
        docs = [json.loads(chunks[k]) for k in ("prompt", "workflow") if k in chunks]
        if not docs:
            raise ComfyError(f"{label} has no embedded ComfyUI workflow")
        return docs
    try:
        return [json.loads(raw)]
    except ValueError:
        raise ComfyError(f"{label} isn't JSON") from None


def load_workflow(source: str | os.PathLike) -> Workflow:
    """Load an API-format workflow (see `read_workflow' for SOURCE)."""
    for data in read_workflow(source):
        if "prompt" in data and isinstance(data["prompt"], dict):
            data = data["prompt"]
        if not _is_ui_format(data):
            return data
    raise ComfyError(f"{source} is a UI-format workflow; re-export it with Workflow -> Export (API)")


def _is_ui_format(data: dict) -> bool:
    return "nodes" in data and "links" in data


def _png_text(raw: bytes) -> dict[str, str]:
    """tEXt/iTXt/zTXt chunks of a PNG, which is where ComfyUI saves workflows."""
    out, pos = {}, 8
    while pos + 8 <= len(raw):
        length = int.from_bytes(raw[pos:pos + 4], "big")
        kind = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        try:
            if kind == b"tEXt":
                key, _, value = body.partition(b"\0")
                out[key.decode("latin-1")] = value.decode("utf-8", "replace")
            elif kind == b"zTXt":
                key, _, rest = body.partition(b"\0")
                out[key.decode("latin-1")] = zlib.decompress(rest[1:]).decode("utf-8", "replace")
            elif kind == b"iTXt":
                key, _, rest = body.partition(b"\0")
                compressed, rest = rest[0], rest[2:]
                _lang, _, rest = rest.partition(b"\0")
                _tkey, _, text = rest.partition(b"\0")
                out[key.decode("latin-1")] = (zlib.decompress(text) if compressed else text).decode("utf-8", "replace")
            elif kind == b"IEND":
                break
        except (zlib.error, IndexError):
            continue
    return out


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


# -- Model fetching -------------------------------------------------------

MODEL_EXTENSIONS = (".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx")

# Folders under models/ (used to recognize a folder inside a download path).
MODEL_FOLDERS = (
    "checkpoints", "diffusion_models", "unet", "text_encoders", "clip", "vae", "loras",
    "controlnet", "clip_vision", "upscale_models", "latent_upscale_models", "style_models",
    "embeddings", "hypernetworks", "photomaker", "gligen", "vae_approx", "model_patches",
    "audio_encoders", "diffusers",
)

# Which folder a loader node reads, for references that carry only a filename.
CLASS_FOLDERS = {
    "CheckpointLoaderSimple": "checkpoints", "CheckpointLoader": "checkpoints",
    "ImageOnlyCheckpointLoader": "checkpoints", "unCLIPCheckpointLoader": "checkpoints",
    "UNETLoader": "diffusion_models", "UnetLoaderGGUF": "unet",
    "CLIPLoader": "text_encoders", "DualCLIPLoader": "text_encoders",
    "TripleCLIPLoader": "text_encoders", "QuadrupleCLIPLoader": "text_encoders",
    "CLIPLoaderGGUF": "text_encoders", "DualCLIPLoaderGGUF": "text_encoders",
    "VAELoader": "vae", "LoraLoader": "loras", "LoraLoaderModelOnly": "loras",
    "ControlNetLoader": "controlnet", "DiffControlNetLoader": "controlnet",
    "CLIPVisionLoader": "clip_vision", "UpscaleModelLoader": "upscale_models",
    "LatentUpscaleModelLoader": "latent_upscale_models", "StyleModelLoader": "style_models",
    "HypernetworkLoader": "hypernetworks", "PhotoMakerLoader": "photomaker",
    "GLIGENLoader": "gligen", "ModelPatchLoader": "model_patches",
    "AudioEncoderLoader": "audio_encoders",
}
# Inputs that take a file the user supplies at run time (uploaded into the
# server's input/ dir), so they're filled per run rather than validated.
FILE_INPUT_NODES = {"LoadImage": "image", "LoadImageMask": "image", "LoadVideo": "file"}


def file_inputs(workflow: Workflow) -> list[tuple[str, str, str]]:
    """(node_id, input_name, class_type) for each node that loads a user file."""
    return [(node_id, FILE_INPUT_NODES[n["class_type"]], n["class_type"])
            for node_id, n in workflow.items()
            if isinstance(n, dict) and n.get("class_type") in FILE_INPUT_NODES]


# Fallback by input name, for API-format nodes not in CLASS_FOLDERS.
INPUT_FOLDERS = {
    "ckpt_name": "checkpoints", "unet_name": "diffusion_models", "vae_name": "vae",
    "lora_name": "loras", "control_net_name": "controlnet", "clip_name": "text_encoders",
    "style_model_name": "style_models", "gligen_name": "gligen",
}


@dataclass
class ModelRef:
    name: str                      # as the workflow names it; may include a subfolder
    directory: str | None = None   # folder under models/
    url: str | None = None
    sha256: str | None = None
    size: int | None = None
    source: str = "workflow"       # where the URL came from
    status: str = ""               # present / missing / unresolved / downloaded / failed
    path: str | None = None
    error: str | None = None


def model_refs(docs: list[dict]) -> list[ModelRef]:
    """Every model file DOCS (UI- or API-format workflows) reference."""
    refs: dict[str, ModelRef] = {}

    def add(name: Any, directory: str | None = None, url: str | None = None,
            sha256: str | None = None) -> None:
        if not isinstance(name, str) or not name.lower().endswith(MODEL_EXTENSIONS):
            return
        name = name.replace("\\", "/")
        ref = refs.setdefault(name, ModelRef(name))
        ref.directory = ref.directory or directory
        ref.url = ref.url or url
        ref.sha256 = ref.sha256 or sha256

    def add_listed(models: Any) -> None:
        # The frontend's model metadata: [{name, url, directory, hash?, hash_type?}]
        for m in models if isinstance(models, list) else []:
            if isinstance(m, dict):
                sha = m.get("hash") if str(m.get("hash_type", "")).lower() == "sha256" else None
                add(m.get("name"), m.get("directory"), m.get("url"), sha)

    def add_ui_nodes(nodes: Any) -> None:
        for node in nodes if isinstance(nodes, list) else []:
            add_listed((node.get("properties") or {}).get("models"))
            folder = CLASS_FOLDERS.get(node.get("type"))
            values = node.get("widgets_values")
            for value in (values.values() if isinstance(values, dict) else values or []):
                add(value, folder)

    for data in docs:
        if "prompt" in data and isinstance(data["prompt"], dict):
            data = data["prompt"]
        if _is_ui_format(data):
            add_listed(data.get("models"))
            add_ui_nodes(data.get("nodes"))
            for sub in (data.get("definitions") or {}).get("subgraphs") or []:
                add_ui_nodes(sub.get("nodes"))
        else:
            for node in data.values():
                if not isinstance(node, dict):
                    continue
                for key, value in (node.get("inputs") or {}).items():
                    add(value, CLASS_FOLDERS.get(node.get("class_type"))
                        or INPUT_FOLDERS.get(key.rstrip("0123456789")))
    return list(refs.values())


def _folder_in_path(path: str) -> str | None:
    parts = urllib.parse.unquote(path).split("/")
    return next((part for part in reversed(parts[:-1]) if part in MODEL_FOLDERS), None)


def hf_index(refresh: bool = False) -> dict[str, list[list[str]]]:
    """basename -> [[repo, path in repo], ...] for every model file in
    HF_AUTHORS' repos."""
    return _hf_listing(refresh)["files"]


def _hf_listing(refresh: bool = False) -> dict:
    """Model files and example workflow JSONs in HF_AUTHORS' repos, from one
    API call per author. Cached for a day in CACHE_DIR."""
    cache = CACHE_DIR / "hf-index.json"
    if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < 86400:
        cached = json.loads(cache.read_text())
        if cached.get("authors") == HF_AUTHORS and "workflows" in cached:
            return cached
    files: dict[str, list[list[str]]] = {}
    workflows: list[list[str]] = []
    for author in HF_AUTHORS:
        url = ("https://huggingface.co/api/models?" +
               urllib.parse.urlencode({"author": author, "limit": 1000, "full": "true"}))
        with urllib.request.urlopen(_download_request(url), timeout=60) as resp:
            for repo in json.load(resp):
                for sibling in repo.get("siblings", []):
                    path = sibling["rfilename"]
                    if path.lower().endswith(MODEL_EXTENSIONS):
                        files.setdefault(path.rsplit("/", 1)[-1], []).append([repo["id"], path])
                    elif path.lower().endswith(".json") and "workflow" in path.lower():
                        workflows.append([repo["id"], path])
    listing = {"authors": HF_AUTHORS, "files": files, "workflows": workflows}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(listing))
    return listing


def _hf_token() -> str | None:
    token = os.environ.get("HF_TOKEN")
    if not token:
        path = Path(os.environ.get("HF_HOME", "~/.cache/huggingface")).expanduser() / "token"
        token = path.read_text().strip() if path.exists() else None
    return token or None


def _civitai_token() -> str | None:
    """$CIVITAI_TOKEN, else the contents of ~/.config/comfy/civitai-token."""
    path = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "comfy" / "civitai-token"
    return os.environ.get("CIVITAI_TOKEN") or (path.read_text().strip() if path.exists() else None) or None


def _download_request(url: str, headers: dict[str, str] | None = None,
                      method: str = "GET") -> urllib.request.Request:
    """A request with the right credentials for URL's host (HF_TOKEN /
    ~/.cache/huggingface/token, CIVITAI_TOKEN), never forwarded on redirects."""
    host = urllib.parse.urlparse(url).hostname or ""
    if host.endswith("civitai.com") and (token := _civitai_token()):
        url += ("&" if "?" in url else "?") + "token=" + token
    req = urllib.request.Request(url, method=method,
                                 headers={"User-Agent": "comfy-client/0.1", **(headers or {})})
    if host == "huggingface.co" and (token := _hf_token()):
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    return req


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _probe(ref: ModelRef) -> None:
    """Fill REF's size, and its sha256 when the host publishes one (Hugging
    Face does for LFS files, as X-Linked-Etag on the redirect to its CDN).
    Same-host redirects (renamed repos) are followed and REF.url updated to
    the final one, so credentials still apply when downloading."""
    opener = urllib.request.build_opener(_NoRedirect)
    url = ref.url
    for _hop in range(5):
        try:
            resp = opener.open(_download_request(url, method="HEAD"), timeout=30)
            headers, location = resp.headers, None
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                hint = " (gated or private: needs an HF or Civitai token)" if e.code in (401, 403) else ""
                raise ComfyError(f"{url} -> HTTP {e.code}{hint}") from None
            headers, location = e.headers, urllib.parse.urljoin(url, e.headers.get("Location", ""))
        if headers.get("X-Linked-Size") or not location:
            break
        if urllib.parse.urlparse(location).hostname != urllib.parse.urlparse(url).hostname:
            headers = None  # off to a CDN without telling us the size; leave it unknown
            break
        url = location
    ref.url = url
    if headers is None:
        return
    size = headers.get("X-Linked-Size") or (None if location else headers.get("Content-Length"))
    ref.size = int(size) if size and size.isdigit() else ref.size
    etag = (headers.get("X-Linked-Etag") or "").strip('"')
    if not ref.sha256 and len(etag) == 64:
        ref.sha256 = etag


def plan_models(source: str | os.PathLike, models_dir: str | os.PathLike = MODELS_DIR,
                refresh_index: bool = False, probe: bool = True) -> list[ModelRef]:
    """What SOURCE's workflow needs: each model as present (with its path),
    missing (with a URL and folder to fetch it into) or unresolved."""
    models_dir = Path(models_dir).expanduser()
    if not models_dir.is_dir():
        raise ComfyError(f"models dir {models_dir} doesn't exist; set COMFY_MODELS_DIR or "
                         f"--models-dir (fetching must run where the server's models live)")
    refs = model_refs(read_workflow(source))
    local: dict[str, list[Path]] = {}
    for root, _dirs, files in os.walk(models_dir, followlinks=True):
        for f in files:
            local.setdefault(f, []).append(Path(root) / f)
    index = None
    for ref in refs:
        basename = ref.name.rsplit("/", 1)[-1]
        found = [p for p in local.get(basename, []) if p.as_posix().endswith("/" + ref.name)]
        if found:
            exact = models_dir / (ref.directory or "") / ref.name
            ref.status, ref.path = "present", str(exact if exact in found else found[0])
            continue
        if not ref.url:
            if index is None:
                try:
                    index = hf_index(refresh_index)
                except (OSError, ValueError) as e:
                    index = {}
                    ref.error = f"couldn't load the Hugging Face index: {e}"
            candidates = index.get(basename, [])
            # Prefer a copy filed under the folder we expect, then any.
            candidates = sorted(candidates, key=lambda c: _folder_in_path(c[1]) != ref.directory)
            if candidates:
                repo, path = candidates[0]
                ref.url = f"https://huggingface.co/{repo}/resolve/main/{urllib.parse.quote(path)}"
                ref.source = f"huggingface:{repo}"
                ref.directory = ref.directory or _folder_in_path(path)
        if ref.url and not ref.directory:
            ref.directory = _folder_in_path(urllib.parse.urlparse(ref.url).path)
        if not (ref.url and ref.directory):
            ref.status = "unresolved"
            ref.error = ref.error or ("no download URL known" if not ref.url else
                                      "can't tell which models/ folder it belongs in")
            continue
        ref.status = "missing"
        ref.path = str(models_dir / ref.directory / ref.name)
        if probe:
            try:
                _probe(ref)
            except (ComfyError, OSError) as e:
                ref.error = str(e)
    return refs


def download_model(ref: ModelRef, on_status: Any = None) -> Path:
    """Download REF.url to REF.path, resuming a .part file, checking sha256."""
    dest = Path(ref.path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    digest = hashlib.sha256() if ref.sha256 else None
    if have and digest:
        with open(part, "rb") as f:
            while chunk := f.read(1 << 22):
                digest.update(chunk)
    if not (ref.size and have == ref.size):
        req = _download_request(ref.url, {"Range": f"bytes={have}-"} if have else None)
        try:
            resp = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            hint = " (gated or private: needs an HF or Civitai token)" if e.code in (401, 403) else ""
            raise ComfyError(f"{ref.url} -> HTTP {e.code}{hint}") from None
        with resp:
            if have and resp.status != 206:  # server ignored the Range; start over
                have, digest = 0, (hashlib.sha256() if ref.sha256 else None)
            length = resp.headers.get("Content-Length")
            total = have + int(length) if length and length.isdigit() else ref.size
            done, started, last = have, time.monotonic(), 0.0
            with open(part, "ab" if have else "wb") as f:
                while chunk := resp.read(1 << 20):
                    f.write(chunk)
                    if digest:
                        digest.update(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if on_status and now - last >= 1:
                        last = now
                        rate = (done - have) / max(now - started, 1e-6)
                        pct = f"{100 * done / total:.0f}% " if total else ""
                        on_status(f"downloading {ref.name}: {pct}({_human(done)}"
                                  f"{' / ' + _human(total) if total else ''}, {_human(rate)}/s)")
    if digest and digest.hexdigest() != ref.sha256.lower():
        part.unlink()
        raise ComfyError(f"{ref.name}: sha256 mismatch, download discarded")
    part.chmod(0o644)
    part.rename(dest)
    return dest


def fetch_models(source: str | os.PathLike, models_dir: str | os.PathLike = MODELS_DIR,
                 dry_run: bool = False, refresh_index: bool = False,
                 on_status: Any = None) -> list[ModelRef]:
    """Download every model SOURCE's workflow needs that isn't in MODELS_DIR
    yet, into the folder its loader reads. Returns the plan with statuses."""
    refs = plan_models(source, models_dir, refresh_index)
    if dry_run:
        return refs
    for ref in refs:
        if ref.status != "missing":
            continue
        try:
            download_model(ref, on_status)
            ref.status, ref.error = "downloaded", None
            if on_status:
                on_status(f"saved {ref.path}")
        except (ComfyError, OSError) as e:
            ref.status, ref.error = "failed", str(e)
            if on_status:
                on_status(f"{ref.name} failed: {e}")
    return refs


def _human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} B"
        n /= 1024
    return f"{n:.1f} GiB"


# -- Browsing shared workflows ----------------------------------------------

TEMPLATES_RAW = "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/"
# The web UI's own workflow library (its Workflows sidebar); `get' saves here.
WEBUI_WORKFLOW_DIR = Path(os.environ.get(
    "COMFY_WEBUI_WORKFLOW_DIR", "/var/lib/comfyui/user/default/workflows")).expanduser()
BROWSE_SOURCES = ("templates", "hf", "civitai")


def _cached_json(url: str, name: str, refresh: bool = False, max_age: float = 86400) -> Any:
    path = CACHE_DIR / name
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < max_age:
        return json.loads(path.read_text())
    with urllib.request.urlopen(_download_request(url), timeout=60) as resp:
        data = json.load(resp)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def _matches(entry: dict, query: str | None) -> bool:
    if not query:
        return True
    hay = " ".join(str(entry.get(k) or "") for k in ("title", "description", "category")) + " " + \
        " ".join(entry.get("tags") or []) + " " + " ".join(entry.get("models") or [])
    return all(word in hay.lower() for word in query.lower().split())


def _version_tuple(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def browse(source: str = "templates", query: str | None = None, include_api: bool = False,
           refresh: bool = False, cursor: str | None = None, limit: int = 50,
           server: "Comfy | None" = None) -> dict:
    """Shared workflows from SOURCE ("templates": ComfyUI's official templates;
    "hf": example workflows in HF_AUTHORS' repos; "civitai": Civitai's workflow
    uploads). Returns {"entries": [...], "next": cursor or None}. Each entry
    has title, description, tags, models, size, thumbnail, workflow (the URL
    `get_workflow' takes) and page (a URL for people)."""
    entries: list[dict] = []
    next_cursor = None
    if source == "templates":
        for category in _cached_json(TEMPLATES_RAW + "index.json", "templates-index.json", refresh):
            for t in category.get("templates", []):
                if not include_api and not t.get("openSource", True):
                    continue
                name = t["name"]
                entries.append({
                    "id": name, "source": source, "title": t.get("title") or name,
                    "description": t.get("description", ""),
                    "category": category.get("title"), "tags": t.get("tags", []),
                    "models": t.get("models", []), "size": t.get("size") or None,
                    "date": t.get("date"), "usage": t.get("usage"),
                    "open_source": t.get("openSource", True),
                    "min_version": t.get("minComfyUIVersion"),
                    "thumbnail": f"{TEMPLATES_RAW}{name}-1.{t.get('mediaSubtype', 'webp')}",
                    "workflow": f"{TEMPLATES_RAW}{name}.json",
                    "page": t.get("tutorialUrl") or
                    f"https://github.com/Comfy-Org/workflow_templates/blob/main/templates/{name}.json",
                })
        entries = [e for e in entries if _matches(e, query)]
    elif source == "hf":
        for repo, path in _hf_listing(refresh)["workflows"]:
            stem = path.rsplit("/", 1)[-1][:-5]
            entries.append({
                "id": f"{repo.split('/')[-1]}-{stem}", "source": source,
                "title": stem.replace("_", " ").replace("-", " "),
                "description": f"Example workflow from {repo}", "category": repo.split("/")[-1],
                "tags": [], "models": [], "open_source": True,
                "workflow": f"https://huggingface.co/{repo}/resolve/main/{urllib.parse.quote(path)}",
                "page": f"https://huggingface.co/{repo}/blob/main/{urllib.parse.quote(path)}",
            })
        entries = [e for e in entries if _matches(e, query)]
    elif source == "civitai":
        params = {"types": "Workflows", "limit": limit, "nsfw": "false",
                  "sort": "Most Downloaded"}
        if query:
            params["query"] = query
        if cursor:
            params["cursor"] = cursor
        url = "https://civitai.com/api/v1/models?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(_download_request(url), timeout=60) as resp:
            data = json.load(resp)
        for item in data.get("items", []):
            versions = item.get("modelVersions") or []
            if not versions or not versions[0].get("files"):
                continue
            version = versions[0]
            files = version["files"]
            file = next((f for f in files if f.get("primary")), files[0])
            image = next((i["url"] for i in version.get("images", [])
                          if (i.get("nsfwLevel") or 1) <= 1 and i.get("type", "image") == "image"), None)
            description = html.unescape(re.sub(r"<[^>]+>", " ", item.get("description") or ""))
            entries.append({
                "id": f"civitai-{item['id']}", "source": source, "title": item.get("name", "?"),
                "description": re.sub(r"\s+", " ", description).strip()[:600],
                "category": (item.get("creator") or {}).get("username"),
                "tags": item.get("tags", []), "models": [version.get("baseModel")] if version.get("baseModel") else [],
                "date": (version.get("publishedAt") or "")[:10],
                "usage": (item.get("stats") or {}).get("downloadCount"), "open_source": True,
                "thumbnail": image.replace("/original=true/", "/width=320/") if image else None,
                "workflow": file["downloadUrl"],
                "page": f"https://civitai.com/models/{item['id']}",
            })
        next_cursor = (data.get("metadata") or {}).get("nextCursor")
    else:
        raise ComfyError(f"unknown source {source!r}; one of {', '.join(BROWSE_SOURCES)}")

    # Flag templates newer than the server, when it's reachable.
    try:
        version = (server or Comfy(timeout=3)).system_stats()["system"].get("comfyui_version")
    except (ComfyError, KeyError, OSError):
        version = None
    for e in entries:
        e["too_new"] = bool(version and e.get("min_version")
                            and _version_tuple(e["min_version"]) > _version_tuple(version))
    return {"entries": entries, "next": next_cursor, "server_version": version}


def fetch_thumbnails(entries: list[dict]) -> None:
    """Download each entry's thumbnail into CACHE_DIR (once) and set
    "thumbnail_path". Failures just leave it unset."""
    thumbs = CACHE_DIR / "thumbnails"
    thumbs.mkdir(parents=True, exist_ok=True)

    def one(entry: dict) -> None:
        url = entry.get("thumbnail")
        if not url:
            return
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if suffix not in (".webp", ".png", ".jpg", ".jpeg", ".gif"):
            return  # audio templates "preview" as .mp3
        dest = thumbs / (hashlib.sha1(url.encode()).hexdigest() + suffix)
        if not dest.exists():
            try:
                with urllib.request.urlopen(_download_request(url), timeout=30) as resp:
                    tmp = dest.with_suffix(".part")
                    tmp.write_bytes(resp.read())
                    tmp.rename(dest)
            except (OSError, urllib.error.URLError):
                return
        entry["thumbnail_path"] = str(dest)

    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        list(pool.map(one, entries))


def _slug(name: str) -> str:
    return re.sub(r"[^\w.-]+", "-", name).strip("-.")[:80] or "workflow"


def get_workflow(url: str, name: str | None = None,
                 dest_dir: str | os.PathLike = WEBUI_WORKFLOW_DIR) -> list[Path]:
    """Download a shared workflow (JSON, a PNG with one embedded, or a zip of
    either, as Civitai serves them) into DEST_DIR, which defaults to the web
    UI's workflow library. Returns the saved .json paths."""
    dest_dir = Path(dest_dir).expanduser()
    try:
        with urllib.request.urlopen(_download_request(url), timeout=120) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        hint = " (needs login: put a Civitai API key in ~/.config/comfy/civitai-token, or an HF token in ~/.cache/huggingface/token)" if e.code in (401, 403) else ""
        raise ComfyError(f"{url} -> HTTP {e.code}{hint}") from None
    name = _slug(name or Path(urllib.parse.urlparse(url).path).stem)

    def workflow_of(data: bytes) -> dict | None:
        if data.startswith(b"\x89PNG"):
            chunks = _png_text(data)
            text = chunks.get("workflow") or chunks.get("prompt")
            return json.loads(text) if text else None
        try:
            doc = json.loads(data)
        except ValueError:
            return None
        return doc if isinstance(doc, dict) and (_is_ui_format(doc) or "prompt" in doc or
                                                 all(isinstance(v, dict) and "class_type" in v
                                                     for v in doc.values())) else None

    found: list[tuple[str, dict]] = []
    if raw.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for member in zf.namelist():
                base = member.rsplit("/", 1)[-1]
                if base.lower().endswith((".json", ".png")) and not base.startswith("."):
                    doc = workflow_of(zf.read(member))
                    if doc:
                        found.append((_slug(base.rsplit(".", 1)[0]), doc))
        target = dest_dir / name if len(found) > 1 else dest_dir
    else:
        doc = workflow_of(raw)
        if doc:
            found.append((name, doc))
        target = dest_dir
    if not found:
        raise ComfyError(f"no ComfyUI workflow found in {url}")
    target.mkdir(parents=True, exist_ok=True)
    saved = []
    for stem, doc in found:
        path = target / f"{stem if len(found) > 1 else name}.json"
        path.write_text(json.dumps(doc, indent=2))
        saved.append(path)
    return saved


# -- Discovering models (Civitai) ---------------------------------------------

CIVITAI_API = "https://civitai.com/api/v1/"
# Civitai model type -> folder under models/ it's installed into. Only these
# types are browsable, since ComfyUI's core loaders read these folders.
CIVITAI_TYPE_FOLDERS = {
    "Checkpoint": "checkpoints", "LORA": "loras", "LoCon": "loras", "DoRA": "loras",
    "TextualInversion": "embeddings", "Hypernetwork": "hypernetworks", "VAE": "vae",
    "Controlnet": "controlnet", "Upscaler": "upscale_models",
}
# Friendlier names for --type.
MODEL_TYPE_NAMES = {
    "checkpoint": "Checkpoint", "lora": "LORA", "locon": "LoCon", "dora": "DoRA",
    "embedding": "TextualInversion", "hypernetwork": "Hypernetwork", "vae": "VAE",
    "controlnet": "Controlnet", "upscaler": "Upscaler",
}
SAVED_MODELS = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "comfy" / "saved-models.json"


def _civitai_get(path: str, params: dict | None = None) -> Any:
    url = CIVITAI_API + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    try:
        with urllib.request.urlopen(_download_request(url), timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise ComfyError(f"Civitai {path} -> HTTP {e.code}") from None


def _primary_file(version: dict) -> dict | None:
    files = [f for f in version.get("files") or [] if f.get("type") in (None, "Model")] or \
        version.get("files") or []
    return next((f for f in files if f.get("primary")), files[0] if files else None)


def _model_entry(model: dict, version: dict, models_dir: Path, saved: set[int]) -> dict | None:
    """A browse entry (same shape as `browse' ones, kind "model") for one
    Civitai model version, or None if ComfyUI can't load its type."""
    folder = CIVITAI_TYPE_FOLDERS.get(model.get("type"))
    file = _primary_file(version)
    if not folder or not file:
        return None
    image = next((i["url"] for i in version.get("images") or []
                  if (i.get("nsfwLevel") or 1) <= 1 and i.get("type", "image") == "image"), None)
    description = html.unescape(re.sub(r"<[^>]+>", " ", model.get("description") or ""))
    trained = [w.strip() for w in version.get("trainedWords") or [] if w.strip()]
    type_label = {v: k for k, v in MODEL_TYPE_NAMES.items()}.get(model["type"], model["type"])
    return {
        "kind": "model", "id": f"civitai-model-{model['id']}", "source": "models",
        "model_id": model["id"], "version_id": version["id"],
        "title": f"{model.get('name', '?')} ({version.get('name', '')})",
        "description": re.sub(r"\s+", " ", description).strip()[:500],
        "category": type_label, "tags": model.get("tags") or [],
        "models": [version["baseModel"]] if version.get("baseModel") else [],
        "size": int((file.get("sizeKB") or 0) * 1024) or None,
        "date": (version.get("publishedAt") or version.get("createdAt") or "")[:10],
        "usage": (model.get("stats") or {}).get("downloadCount"), "open_source": True,
        "trained_words": trained,
        "thumbnail": image.replace("/original=true/", "/width=320/") if image else None,
        "page": f"https://civitai.com/models/{model['id']}?modelVersionId={version['id']}",
        "file": file["name"], "folder": folder,
        "installed": (models_dir / folder / file["name"]).exists(),
        "saved": version["id"] in saved,
    }


def _saved_models() -> list[dict]:
    return json.loads(SAVED_MODELS.read_text()) if SAVED_MODELS.exists() else []


def _write_saved_models(entries: list[dict]) -> None:
    SAVED_MODELS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SAVED_MODELS.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2))
    tmp.replace(SAVED_MODELS)


def discover_models(query: str | None = None, tag: str | None = None,
                    types: list[str] | None = None, base: str | None = None,
                    sort: str = "Most Downloaded", period: str | None = None,
                    cursor: str | None = None, limit: int = 40,
                    models_dir: str | os.PathLike = MODELS_DIR) -> dict:
    """Search Civitai for models ComfyUI can load (checkpoints, LoRAs, VAEs,
    ...). TYPES are names from MODEL_TYPE_NAMES; BASE is a Civitai base model
    like "SDXL 1.0" or "Flux.1 D". Returns {"entries": [...], "next": cursor}."""
    wanted = [MODEL_TYPE_NAMES.get(t.lower(), t) for t in types] if types else \
        list(CIVITAI_TYPE_FOLDERS)
    params: dict[str, Any] = {"types": wanted, "sort": sort, "limit": limit, "nsfw": "false"}
    for key, value in (("query", query), ("tag", tag), ("baseModels", base),
                       ("period", period), ("cursor", cursor)):
        if value:
            params[key] = value
    data = _civitai_get("models", params)
    saved = {e["version_id"] for e in _saved_models()}
    entries = []
    for model in data.get("items", []):
        versions = model.get("modelVersions") or []
        if versions and (entry := _model_entry(model, versions[0], Path(models_dir), saved)):
            entries.append(entry)
    return {"entries": entries, "next": (data.get("metadata") or {}).get("nextCursor")}


def model_tags(query: str | None = None, limit: int = 200) -> list[str]:
    """Popular Civitai tags (matching QUERY), most used first."""
    params: dict[str, Any] = {"limit": limit}
    if query:
        params["query"] = query
    return [t["name"] for t in _civitai_get("tags", params).get("items", [])]


def _version_entry(version_id: int, models_dir: str | os.PathLike = MODELS_DIR) -> dict:
    version = _civitai_get(f"model-versions/{version_id}")
    model = _civitai_get(f"models/{version['modelId']}")
    entry = _model_entry(model, version, Path(models_dir),
                         {e["version_id"] for e in _saved_models()})
    if entry is None:
        raise ComfyError(f"{model.get('name')} is a {model.get('type')}, which ComfyUI's core loaders don't use")
    return entry


def saved_models(models_dir: str | os.PathLike = MODELS_DIR) -> list[dict]:
    """The saved list, newest first, with "installed" refreshed."""
    entries = _saved_models()
    for e in entries:
        e["saved"] = True
        e["installed"] = (Path(models_dir) / e["folder"] / e["file"]).exists()
    return entries


def save_model(version_id: int) -> dict:
    """Add a Civitai model version to the saved list (no-op if present)."""
    entries = _saved_models()
    if not any(e["version_id"] == version_id for e in entries):
        entry = _version_entry(version_id)
        entry["saved"] = True
        entries.insert(0, entry)
        _write_saved_models(entries)
    return {"saved": True, "version_id": version_id}


def unsave_model(version_id: int) -> dict:
    _write_saved_models([e for e in _saved_models() if e["version_id"] != version_id])
    return {"saved": False, "version_id": version_id}


def install_model(version_id: int, models_dir: str | os.PathLike = MODELS_DIR,
                  on_status: Any = None) -> ModelRef:
    """Download a Civitai model version into the models/ folder for its type,
    checking Civitai's sha256. Skips it if the file is already there."""
    models_dir = Path(models_dir).expanduser()
    if not models_dir.is_dir():
        raise ComfyError(f"models dir {models_dir} doesn't exist; set COMFY_MODELS_DIR or --models-dir")
    version = _civitai_get(f"model-versions/{version_id}")
    model_type = (version.get("model") or {}).get("type")
    folder = CIVITAI_TYPE_FOLDERS.get(model_type)
    file = _primary_file(version)
    if not folder or not file:
        raise ComfyError(f"can't install a {model_type} model (no folder for it, or no file)")
    ref = ModelRef(name=file["name"], directory=folder, url=file["downloadUrl"],
                   sha256=(file.get("hashes") or {}).get("SHA256"),
                   size=int((file.get("sizeKB") or 0) * 1024) or None, source="civitai",
                   path=str(models_dir / folder / file["name"]))
    if Path(ref.path).exists():
        ref.status = "present"
        return ref
    # Civitai's sizeKB is rounded, so don't use it to decide a .part is complete.
    ref.size = None
    download_model(ref, on_status)
    ref.status = "downloaded"
    return ref


# -- AI workflow design --------------------------------------------------------

# The design step asks Claude through the Claude Code CLI (`claude -p`), so it
# uses that login; no separate API key. COMFY_CLAUDE_MODEL picks a model
# (e.g. "sonnet"); unset uses the CLI's default.
CLAUDE_COMMAND = os.environ.get("COMFY_CLAUDE_COMMAND", "claude")
CLAUDE_MODEL = os.environ.get("COMFY_CLAUDE_MODEL")
# Node categories left out of the catalog the model sees: paid API nodes and
# areas unrelated to image/video generation, to keep the prompt small.
CATALOG_SKIP = ("partner", "3d", "audio", "comfy cloud", "experimental", "api node")


def installed_models(models_dir: str | os.PathLike = MODELS_DIR) -> list[dict]:
    """Model files under MODELS_DIR: folder, name (relative to it), size."""
    models_dir = Path(models_dir)
    out = []
    for root, _dirs, files in os.walk(models_dir, followlinks=True):
        for f in files:
            path = Path(root) / f
            if not f.lower().endswith(MODEL_EXTENSIONS):
                continue
            rel = path.relative_to(models_dir)
            out.append({"folder": rel.parts[0], "name": "/".join(rel.parts[1:]),
                        "size": path.stat().st_size, "path": str(path)})
    return sorted(out, key=lambda m: (m["folder"], m["name"].lower()))


def file_sha256(path: str | os.PathLike) -> str:
    """sha256 of PATH, cached by path/size/mtime (model files are big)."""
    path = Path(path)
    st = path.stat()
    key = f"{path}:{st.st_size}:{st.st_mtime_ns}"
    cache = CACHE_DIR / "hashes.json"
    hashes = json.loads(cache.read_text()) if cache.exists() else {}
    if key not in hashes:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 22):
                digest.update(chunk)
        hashes[key] = digest.hexdigest()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(hashes))
    return hashes[key]


def identify_model(path: str | os.PathLike) -> dict:
    """What's known about a local model file: Civitai's record (by sha256),
    else the Hugging Face repo that has a file of that name."""
    path = Path(path)
    info: dict[str, Any] = {"file": path.name, "folder": path.parent.name}
    cache = CACHE_DIR / "model-info.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    try:
        sha = file_sha256(path)
        if sha in known:
            return {**known[sha], "file": path.name, "folder": path.parent.name}
        version = _civitai_get(f"model-versions/by-hash/{sha}")
        model = version.get("model") or {}
        desc = html.unescape(re.sub(r"<[^>]+>", " ", (version.get("description") or "") + " " +
                                    (model.get("description") or "")))
        info.update({
            "source": "civitai", "name": model.get("name"), "type": model.get("type"),
            "base_model": version.get("baseModel"), "version": version.get("name"),
            "trigger_words": version.get("trainedWords") or [],
            "notes": re.sub(r"\s+", " ", desc).strip()[:1500],
            "page": f"https://civitai.com/models/{version.get('modelId')}?modelVersionId={version.get('id')}",
        })
        known[sha] = info
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(known))
        return info
    except (ComfyError, OSError, KeyError):
        pass
    try:
        hits = hf_index().get(path.name, [])
    except (OSError, ValueError):
        hits = []
    if hits:
        info.update({"source": "huggingface", "repos": sorted({h[0] for h in hits})})
    return info


def _input_spec(spec: list) -> tuple[Any, dict]:
    kind = spec[0]
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if kind == "COMBO":  # newer schema: options live in the dict
        kind = opts.get("options", [])
    return kind, opts


def node_catalog(object_info: dict) -> str:
    """One compact line per usable node: Name(input: TYPE=default, combo: [a|b], ...) -> OUTPUTS."""
    lines = []
    for name, node in sorted(object_info.items()):
        category = (node.get("category") or "").lower()
        if node.get("api_node") or category.startswith(CATALOG_SKIP) or node.get("deprecated"):
            continue
        parts = []
        for section in ("required", "optional"):
            for key, spec in (node.get("input", {}).get(section) or {}).items():
                kind, opts = _input_spec(spec)
                if isinstance(kind, list):
                    shown = "|".join(map(str, kind[:40])) + ("|…" if len(kind) > 40 else "")
                    text = f"[{shown}]"
                else:
                    text = str(kind)
                    if kind in ("INT", "FLOAT", "BOOLEAN") and "default" in opts:
                        text += f"={opts['default']}"
                parts.append(f"{key}{'?' if section == 'optional' else ''}: {text}")
        outputs = ", ".join(f"{o}" for o in node.get("output", []))
        lines.append(f"{name}({', '.join(parts)}) -> {outputs or '(output node)'}"
                     + (" [OUTPUT]" if node.get("output_node") else ""))
    return "\n".join(lines)


def validate_workflow(workflow: Any, object_info: dict) -> tuple[list[str], list[dict]]:
    """Check an API-format workflow against the server's node definitions
    without running it. Returns (errors, missing_models); a model file a
    loader can't find is reported as missing rather than as an error."""
    errors: list[str] = []
    missing: list[dict] = []
    if not isinstance(workflow, dict) or not workflow:
        return ["workflow must be a non-empty object of node_id -> {class_type, inputs}"], []
    has_output = False
    for node_id, node in workflow.items():
        where = f"node {node_id}"
        if not isinstance(node, dict) or "class_type" not in node:
            errors.append(f"{where}: needs class_type and inputs")
            continue
        ct = node["class_type"]
        where = f"node {node_id} ({ct})"
        if ct not in object_info:
            errors.append(f"{where}: unknown node type (not installed on this server)")
            continue
        info = object_info[ct]
        has_output = has_output or bool(info.get("output_node"))
        spec_in = info.get("input", {})
        required = spec_in.get("required") or {}
        optional = spec_in.get("optional") or {}
        inputs = node.get("inputs") or {}
        for key in required:
            if key not in inputs:
                errors.append(f"{where}: missing required input {key!r}")
        for key, value in inputs.items():
            spec = required.get(key) or optional.get(key)
            if spec is None:
                if key not in (spec_in.get("hidden") or {}):
                    errors.append(f"{where}: no input named {key!r} (inputs: {', '.join(list(required) + list(optional))})")
                continue
            kind, opts = _input_spec(spec)
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                src_id = str(value[0])
                src = workflow.get(src_id)
                if not isinstance(src, dict) or src.get("class_type") not in object_info:
                    errors.append(f"{where}: input {key!r} links to missing/invalid node {src_id}")
                    continue
                outputs = object_info[src["class_type"]].get("output", [])
                if value[1] >= len(outputs):
                    errors.append(f"{where}: input {key!r} uses output {value[1]} of node {src_id}, "
                                  f"which has only {len(outputs)} ({', '.join(map(str, outputs))})")
                    continue
                out_type = outputs[value[1]]
                if isinstance(kind, str) and "*" not in (kind, out_type) and \
                        out_type not in kind.split(",") and kind != "COMBO":
                    errors.append(f"{where}: input {key!r} expects {kind} but node {src_id} "
                                  f"output {value[1]} is {out_type}")
                continue
            if isinstance(kind, list):
                if FILE_INPUT_NODES.get(ct) == key or opts.get("image_upload") or opts.get("video_upload"):
                    if not isinstance(value, str):
                        errors.append(f"{where}: {key} must be a file name string")
                    continue  # supplied per run (`comfy run --image')
                if value not in kind:
                    if isinstance(value, str) and value.lower().endswith(MODEL_EXTENSIONS):
                        missing.append({"name": value, "folder": CLASS_FOLDERS.get(ct)
                                        or INPUT_FOLDERS.get(key.rstrip("0123456789")), "node": node_id})
                    else:
                        errors.append(f"{where}: {key}={value!r} isn't one of: "
                                      + ", ".join(map(str, kind[:25])) + (" …" if len(kind) > 25 else ""))
            elif kind == "INT" or kind == "FLOAT":
                ok = isinstance(value, (int, float)) and not isinstance(value, bool) and \
                    (kind == "FLOAT" or float(value).is_integer())
                if not ok:
                    errors.append(f"{where}: {key} must be {kind}, got {value!r}")
                elif ("min" in opts and value < opts["min"]) or ("max" in opts and value > opts["max"]):
                    errors.append(f"{where}: {key}={value} outside [{opts.get('min')}, {opts.get('max')}]")
            elif kind == "BOOLEAN" and not isinstance(value, bool):
                errors.append(f"{where}: {key} must be true/false, got {value!r}")
            elif kind == "STRING" and not isinstance(value, str):
                errors.append(f"{where}: {key} must be a string, got {value!r}")
    if not has_output:
        errors.append("no output node (e.g. SaveImage) so nothing would run")
    return errors, missing


def find_installed(model: str, installed: list[dict] | None = None,
                   models_dir: str | os.PathLike = MODELS_DIR) -> dict:
    """The installed model MODEL names: "folder/name", or just the file name."""
    for m in installed if installed is not None else installed_models(models_dir):
        if model in (f"{m['folder']}/{m['name']}", m["name"], m["name"].rsplit("/", 1)[-1]):
            return m
    raise ComfyError(f"no installed model {model!r} under {models_dir}")


# Goals offered per kind of model, most likely first (the first is the default).
IMAGE_GOALS = ["text to image", "image to image", "inpainting", "text to image with a hires-fix upscale"]
VIDEO_TERMS = r"\b(wan|hunyuan ?video|ltx|ltxv|mochi|cogvideo|svd|animatediff|video)"


def suggest_goals(model: str, models_dir: str | os.PathLike = MODELS_DIR) -> dict:
    """Goals that suit an installed MODEL, from rules over its folder, file
    name and Civitai record (type, base model, name). Returns {"goals",
    "default", "basis"}; any other goal can still be typed."""
    target = find_installed(model, models_dir=models_dir)
    info = identify_model(target["path"])
    folder = target["folder"]
    # One lowercase haystack, with separators turned into spaces so \b works
    # on names like "wan2.1_i2v_480p".
    text = re.sub(r"[_\-./]+", " ", " ".join(
        str(x) for x in (target["name"], info.get("name"), info.get("base_model"), info.get("type"))
        if x).lower())
    has = lambda pattern: re.search(pattern, text) is not None
    video = has(VIDEO_TERMS)
    audio = has(r"\b(stable audio|ace ?step|audio|music|tts)")

    if folder == "upscale_models" or info.get("type") == "Upscaler":
        goals = ["upscale an image", "upscale a generated image (txt2img + upscale)"]
    elif folder == "controlnet" or info.get("type") == "Controlnet":
        goals = ["text to image guided by a control image (pose, depth or edges)",
                 "restyle an image keeping its structure"]
    elif folder == "embeddings" or info.get("type") == "TextualInversion":
        goals = ["text to image using this embedding"]
    elif folder in ("vae", "text_encoders", "clip", "clip_vision"):
        # Companion files (by folder: Civitai sometimes lists these as
        # checkpoints): suggest what their family is used for.
        goals = ["text to video", "image to video"] if video else IMAGE_GOALS[:2]
        goals = [f"{g} (using this {folder.rstrip('s').replace('_', ' ')})" for g in goals]
    elif folder == "loras" or info.get("type") in ("LORA", "LoCon", "DoRA"):
        goals = (["text to video using this LoRA", "image to video using this LoRA"] if video
                 else ["text to image using this LoRA", "image to image using this LoRA"])
        if video and has(r"\bi2v\b"):
            goals.reverse()
    elif audio and not video:
        goals = ["text to music/audio"]
    elif video:
        if has(r"\bflf2v\b|first last"):
            goals = ["first and last frame to video", "image to video"]
        elif has(r"\bvace\b"):
            goals = ["text to video", "image to video (reference image)",
                     "video to video (restyle a clip, keeping its motion)"]
        elif has(r"\bi2v\b|image to video|\bimg2vid"):
            goals = ["image to video"]
        elif has(r"\bti2v\b"):
            goals = ["text to video", "image to video"]
        elif has(r"\bt2v\b|text to video"):
            goals = ["text to video", "text to image (single frame)"]
        else:
            goals = ["text to video", "image to video"]
    elif has(r"\binpaint"):
        goals = ["inpainting", "outpainting (extend an image)", "image to image"]
    elif has(r"\bkontext\b|\bedit\b|\bomnigen|instruct ?pix"):
        goals = ["edit an image with instructions", "image to image", "text to image"]
    else:
        goals = list(IMAGE_GOALS)

    if info.get("source") == "civitai":
        basis = f"Civitai: {info.get('type')}, {info.get('base_model')}"
    elif info.get("source") == "huggingface":
        basis = f"file name, from {', '.join(info.get('repos', [])[:2])}"
    else:
        basis = f"folder {folder}/ and file name"
    return {"goals": goals, "default": goals[0], "basis": basis}


DESIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "short name, e.g. 'SD1.5 portrait txt2img'"},
        "description": {"type": "string", "description": "what the workflow does and how to use it"},
        "positive_prompt": {"type": "string"},
        "negative_prompt": {"type": "string"},
        "notes": {"type": "string", "description": "settings chosen and why; tips; caveats"},
        "missing_models": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "folder": {"type": "string"}}, "required": ["name", "folder"]}},
        "workflow": {"type": "object", "description": "ComfyUI API-format workflow",
                     "additionalProperties": {"type": "object", "properties": {
                         "class_type": {"type": "string"}, "inputs": {"type": "object"}},
                         "required": ["class_type", "inputs"]}},
    },
    "required": ["title", "description", "positive_prompt", "negative_prompt", "notes",
                 "missing_models", "workflow"],
}

DESIGN_SYSTEM = """You design ComfyUI workflows in API format: a JSON object mapping node ids
("1", "2", ...) to {"class_type": ..., "inputs": {...}}. An input is either a literal
value or a link [source_node_id_string, output_index].

Rules:
- Use only node types from the catalog, with exactly the input names it lists (inputs
  marked ? are optional). Literal values must be valid for the input's type/choices.
- Links must connect an output of the right type (the catalog shows each node's outputs
  in order; output_index is 0-based).
- Build the workflow around the given model, using its loader node (checkpoints:
  CheckpointLoaderSimple; diffusion_models: UNETLoader plus the right text encoder via
  CLIPLoader/DualCLIPLoader and VAE via VAELoader; loras: LoraLoader on top of a
  compatible base checkpoint; upscale_models: UpscaleModelLoader + ImageUpscaleWithModel;
  vae/controlnet/etc. likewise).
- Prefer models already installed. If the model needs companion files that aren't
  installed (a base checkpoint for a LoRA, text encoders, VAE), use the standard official
  file names (as Comfy-Org repackages them on Hugging Face) and list each in
  missing_models with its models/ folder.
- Use settings suited to the model's base architecture (native resolution, steps, cfg,
  sampler/scheduler, any ModelSampling node) and the author's notes. Include LoRA trigger
  words in the prompt.
- Put a short, neutral example prompt in the positive text node and a matching negative
  prompt (or empty if the model doesn't use one); return them in positive_prompt and
  negative_prompt too.
- For an input image (img2img, inpainting, upscaling, image-to-video) use LoadImage
  with image "input.png" (LoadVideo with file "input.mp4" for video); the user picks the
  real file when running it. Don't replace it with generated placeholders.
- End with an output node (SaveImage for images; for video, a node that saves video).
- Keep it as simple as the goal allows. The GPU is an AMD RX 6800 XT (16 GB, ROCm)."""


def _ask_claude(prompt: str, on_status: Any = None) -> dict:
    cmd = [CLAUDE_COMMAND, "-p", "--tools", "", "--no-session-persistence",
           "--output-format", "json", "--json-schema", json.dumps(DESIGN_SCHEMA),
           "--system-prompt", DESIGN_SYSTEM]
    if CLAUDE_MODEL:
        cmd += ["--model", CLAUDE_MODEL]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=900,
                              cwd=str(CACHE_DIR))
    except FileNotFoundError:
        raise ComfyError(f"{CLAUDE_COMMAND!r} (Claude Code CLI) not found; set COMFY_CLAUDE_COMMAND") from None
    except subprocess.TimeoutExpired:
        raise ComfyError("Claude took too long (15 min)") from None
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        raise ComfyError(f"claude failed: {(proc.stderr or proc.stdout).strip()[:500]}") from None
    if result.get("is_error") or not isinstance(result.get("structured_output"), dict):
        raise ComfyError(f"claude failed: {str(result.get('result') or result)[:500]}")
    if on_status:
        # Token counts, not total_cost_usd: that's an API-price estimate the
        # CLI reports even on a claude.ai plan, where nothing is billed per call.
        usage = result.get("usage") or {}
        tokens_in = sum(usage.get(k) or 0 for k in
                        ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        on_status(f"Claude answered ({tokens_in // 1000}k tokens in, "
                  f"{(usage.get('output_tokens') or 0) // 1000}k out)")
    return result["structured_output"]


def design_workflow(model: str, goal: str = "text to image", attempts: int = 4,
                    server: "Comfy | None" = None, models_dir: str | os.PathLike = MODELS_DIR,
                    out_dir: str | os.PathLike = WORKFLOW_DIR, on_status: Any = None) -> dict:
    """Have Claude build an API-format workflow around MODEL (a "folder/name"
    or file name under MODELS_DIR) for GOAL, validated against the server's
    nodes (retrying with the errors), and save it to OUT_DIR."""
    server = server or Comfy()
    models_dir = Path(models_dir)
    installed = installed_models(models_dir)
    target = find_installed(model, installed, models_dir)
    status = on_status or (lambda _msg: None)
    status(f"identifying {target['name']} (hashing it the first time)")
    info = identify_model(target["path"])
    status("reading the server's node definitions")
    object_info = server.get("/object_info")
    catalog = node_catalog(object_info)
    installed_text = "\n".join(f"{m['folder']}/{m['name']} ({_human(m['size'])})" for m in installed)
    base_prompt = (
        f"Goal: {goal}\n\n"
        f"Model to build around: {target['folder']}/{target['name']}\n"
        f"What's known about it:\n{json.dumps(info, indent=1, ensure_ascii=False)}\n\n"
        f"Installed models (folder/name):\n{installed_text}\n\n"
        f"Node catalog of this ComfyUI server:\n{catalog}\n")
    prompt = base_prompt
    design: dict = {}
    errors: list[str] = []
    missing: list[dict] = []
    for attempt in range(1, attempts + 1):
        status(f"asking Claude for a workflow (attempt {attempt}/{attempts})")
        design = _ask_claude(prompt, status)
        errors, missing = validate_workflow(design.get("workflow"), object_info)
        if not errors:
            break
        status(f"{len(errors)} problem(s) found; asking Claude to fix them")
        prompt = (base_prompt +
                  "\nYour previous workflow had these problems:\n- " + "\n- ".join(errors) +
                  "\n\nPrevious workflow:\n" + json.dumps(design.get("workflow"), indent=1) +
                  "\n\nReturn a corrected version.")
    if errors:
        raise ComfyError(f"no valid workflow after {attempts} attempts; last problems: " + "; ".join(errors[:5]))
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _slug(design["title"]).lower()
    path = out_dir / f"{stem}.json"
    n = 2
    while path.exists():
        path, n = out_dir / f"{stem}-{n}.json", n + 1
    path.write_text(json.dumps(design["workflow"], indent=2))
    path.with_suffix(".md").write_text(
        f"# {design['title']}\n\nModel: {target['folder']}/{target['name']}\nGoal: {goal}\n\n"
        f"{design['description']}\n\n## Prompt\n\n{design['positive_prompt']}\n\n"
        f"## Negative prompt\n\n{design['negative_prompt']}\n\n## Notes\n\n{design['notes']}\n")
    names = {m["name"] for m in missing} | {m["name"] for m in design.get("missing_models", [])}
    return {"path": str(path), "name": path.stem, "title": design["title"],
            "description": design["description"], "positive_prompt": design["positive_prompt"],
            "negative_prompt": design["negative_prompt"], "notes": design["notes"],
            "model": info, "attempts": attempt, "missing_models": sorted(names)}


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

    p = sub.add_parser("fetch", help="download the models a workflow needs into the right models/ folders")
    p.add_argument("workflow", help=f"name in {WORKFLOW_DIR}, path, URL, or a ComfyUI PNG")
    p.add_argument("--dry-run", action="store_true", help="only show what's present/missing")
    p.add_argument("--models-dir", default=str(MODELS_DIR), help="server models dir (default %(default)s)")
    p.add_argument("--refresh-index", action="store_true", help="re-read the Hugging Face file index now")

    p = sub.add_parser("browse", help="list shared workflows: official templates, HF examples, Civitai")
    p.add_argument("source", nargs="?", default="templates", choices=BROWSE_SOURCES)
    p.add_argument("-q", "--query", help="only entries matching all these words")
    p.add_argument("--api", action="store_true", help="include templates that use paid API nodes")
    p.add_argument("--refresh", action="store_true", help="re-download the index now")
    p.add_argument("--cursor", help="next page (Civitai)")
    p.add_argument("--thumbnails", action="store_true", help="download thumbnails to the cache")

    p = sub.add_parser("get", help="download a shared workflow into the web UI's workflow library")
    p.add_argument("url", help="workflow URL (a `browse` entry's workflow field), JSON, PNG or zip")
    p.add_argument("--name", help="file name to save as")
    p.add_argument("--dest", default=str(WEBUI_WORKFLOW_DIR), help="directory (default %(default)s)")

    p = sub.add_parser("discover", help="browse Civitai models ComfyUI can load")
    p.add_argument("-q", "--query")
    p.add_argument("-t", "--tag")
    p.add_argument("--type", action="append", choices=sorted(MODEL_TYPE_NAMES),
                   help="repeatable; default: all loadable types")
    p.add_argument("-b", "--base", help='base model, e.g. "SDXL 1.0", "Flux.1 D", "Illustrious"')
    p.add_argument("--sort", default="Most Downloaded",
                   choices=["Most Downloaded", "Highest Rated", "Newest"])
    p.add_argument("--period", choices=["AllTime", "Year", "Month", "Week", "Day"])
    p.add_argument("--cursor", help="next page")
    p.add_argument("--thumbnails", action="store_true", help="download thumbnails to the cache")
    p = sub.add_parser("tags", help="popular Civitai tags")
    p.add_argument("-q", "--query")
    p = sub.add_parser("install", help="download a Civitai model version into the right models/ folder")
    p.add_argument("version_id", type=int)
    p.add_argument("--models-dir", default=str(MODELS_DIR))
    p = sub.add_parser("saved", help="list saved models")
    p.add_argument("--thumbnails", action="store_true")
    p = sub.add_parser("save", help="save a Civitai model version for later")
    p.add_argument("version_id", type=int)
    p = sub.add_parser("unsave", help="remove a model version from the saved list")
    p.add_argument("version_id", type=int)

    p = sub.add_parser("installed", help="list installed model files")
    p.add_argument("--models-dir", default=str(MODELS_DIR))
    p = sub.add_parser("validate", help="check a workflow against the server's nodes without running it")
    p.add_argument("workflow")
    p = sub.add_parser("goals", help="workflow goals that suit an installed model")
    p.add_argument("model", help="installed model, as folder/name or just the file name")
    p.add_argument("--models-dir", default=str(MODELS_DIR))
    p = sub.add_parser("design", help="have Claude build a workflow around an installed model")
    p.add_argument("model", help="installed model, as folder/name or just the file name")
    p.add_argument("-g", "--goal", default="text to image",
                   help='what it should do, e.g. "portrait photos", "img2img", "upscale x4"')
    p.add_argument("--attempts", type=int, default=4, help="Claude calls allowed to fix validation errors")
    p.add_argument("--models-dir", default=str(MODELS_DIR))

    p = sub.add_parser("run", help="run an API-format workflow file")
    p.add_argument("workflow", help=f"path, or a name in {WORKFLOW_DIR}")
    p.add_argument("-p", "--prompt", help="replace the positive prompt text")
    p.add_argument("-n", "--negative", help="replace the negative prompt text")
    p.add_argument("--set", action="append", default=[], metavar="NODE.INPUT=VALUE",
                   help="override any node input (VALUE parsed as JSON if possible)")
    p.add_argument("--fetch", action="store_true", help="download missing models first (see `fetch`)")
    p.add_argument("-i", "--image", action="append", default=[], metavar="[NODE=]FILE",
                   help="upload FILE into the workflow's LoadImage/LoadVideo nodes (in order, or NODE)")
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
        elif args.cmd == "browse":
            data = browse(args.source, args.query, include_api=args.api, refresh=args.refresh,
                          cursor=args.cursor, server=c)
            if args.thumbnails:
                fetch_thumbnails(data["entries"])
            emit(data, "\n".join(
                f"{e['title']}  [{', '.join(e.get('tags') or [])}]\n    {e['workflow']}"
                for e in data["entries"]) or "nothing found")
        elif args.cmd in ("discover", "saved"):
            if args.cmd == "discover":
                data = discover_models(args.query, args.tag, args.type, args.base,
                                       args.sort, args.period, args.cursor)
            else:
                data = {"entries": saved_models(), "next": None}
            if args.thumbnails:
                fetch_thumbnails(data["entries"])
            emit(data, "\n".join(
                f"{e['title']}  [{e['category']}, {', '.join(e['models'])}]"
                f"{'  saved' if e['saved'] else ''}{'  installed' if e['installed'] else ''}"
                f"\n    version {e['version_id']}  {e['page']}"
                for e in data["entries"]) or "nothing found")
        elif args.cmd == "installed":
            items = installed_models(args.models_dir)
            emit(items, "\n".join(f"{m['folder']}/{m['name']}  ({_human(m['size'])})" for m in items))
        elif args.cmd == "validate":
            errors, missing = validate_workflow(load_workflow(args.workflow), c.get("/object_info"))
            emit({"errors": errors, "missing_models": missing},
                 "\n".join([f"error: {e}" for e in errors] +
                           [f"missing model: {m['folder']}/{m['name']} (node {m['node']})" for m in missing])
                 or "ok")
            if errors:
                return 1
        elif args.cmd == "goals":
            data = suggest_goals(args.model, args.models_dir)
            emit(data, f"({data['basis']})\n" + "\n".join(data["goals"]))
        elif args.cmd == "design":
            result = design_workflow(args.model, args.goal, args.attempts, c, args.models_dir,
                                     on_status=status)
            emit(result, f"saved {result['path']}\n\n{result['description']}\n\n"
                         f"Prompt: {result['positive_prompt']}\n\n{result['notes']}"
                 + (f"\n\nMissing models: {', '.join(result['missing_models'])}"
                    if result["missing_models"] else ""))
        elif args.cmd == "tags":
            tags = model_tags(args.query)
            emit(tags, "\n".join(tags))
        elif args.cmd == "install":
            ref = install_model(args.version_id, args.models_dir, on_status=status)
            emit(asdict(ref), f"{ref.status}: {ref.path}")
        elif args.cmd == "save":
            emit(save_model(args.version_id), "saved")
        elif args.cmd == "unsave":
            emit(unsave_model(args.version_id), "removed")
        elif args.cmd == "get":
            files = get_workflow(args.url, args.name, args.dest)
            emit({"files": [str(f) for f in files]}, "\n".join(str(f) for f in files))
        elif args.cmd == "fetch":
            refs = fetch_models(args.workflow, args.models_dir, dry_run=args.dry_run,
                                refresh_index=args.refresh_index, on_status=status)
            missing = [r for r in refs if r.status == "missing"]
            data = {"models": [asdict(r) for r in refs],
                    "missing_bytes": sum(r.size or 0 for r in missing)}
            lines = []
            for r in refs:
                where = f"{r.directory or '?'}/{r.name}"
                extra = r.error or (r.source if r.status == "missing" else r.path) or ""
                size = _human(r.size) if r.size else ""
                lines.append(f"{r.status:<11} {where:<60} {size:>10}  {extra}")
            if missing:
                lines.append(f"{len(missing)} to download, {_human(data['missing_bytes'])}"
                             + ("; run without --dry-run to fetch" if args.dry_run else ""))
            emit(data, "\n".join(lines) or "no model files referenced")
            if any(r.status in ("failed", "unresolved") for r in refs) and not args.dry_run:
                return 1
        elif args.cmd in ("gen", "run"):
            if args.cmd == "gen":
                files = c.txt2img(args.prompt, args.negative, checkpoint=args.checkpoint,
                                  width=args.width, height=args.height, steps=args.steps,
                                  cfg=args.cfg, sampler=args.sampler, scheduler=args.scheduler,
                                  seed=args.seed, batch=args.batch, out_dir=args.out,
                                  timeout=args.timeout, on_status=status)
            else:
                if args.fetch:
                    for r in fetch_models(args.workflow, on_status=status):
                        if r.status in ("failed", "unresolved"):
                            status(f"{r.name}: {r.status}: {r.error}")
                files = c.run(load_workflow(args.workflow), prompt=args.prompt,
                              negative=args.negative, seed=args.seed, set=_parse_set(args.set),
                              images=args.image,
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
