"""The `rrl` command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from contextlib import ExitStack, closing
from dataclasses import asdict
from pathlib import Path
from typing import Any, BinaryIO

from PIL import Image

from rrl import __version__, db
from rrl.adapters import registry
from rrl.adapters.xbox_halo2.inject import DiscInjector, NotInjectable, inject_texture
from rrl.api import Inspector, SelectionError
from rrl.core.config import Config, ConfigError, ensure_workspace, load_config
from rrl.core.extraction import ExtractionError, _safe_relpath, run_extract
from rrl.core.hashing import sha256_file
from rrl.core.logging import setup_logging
from rrl.core.sources import SourceError, add_source, get_source, record_identity
from rrl.emulators.frame_log import format_reports, summarize
from rrl.emulators.xemu_textures import write_pack_texture
from rrl.processors.upscale import upscale_export
from rrl.processors.video import remaster
from rrl.store.provenance import operation

EXTERNAL_TOOLS = {
    "vgmstream-cli": "game audio decoding",
    "xdvdfs": "Xbox disc image repacking",
    "blender": "3D model processing",
}


def _tool(config: Config, name: str, override: str | None = None) -> Path:
    env = f"RRL_{name.upper()}"
    setting = override or os.environ.get(env)
    if not setting and config.config_file.exists():
        with config.config_file.open("rb") as f:
            tools = tomllib.load(f).get("tools", {})
        if not isinstance(tools, dict):
            raise ValueError(f"{config.config_file}: [tools] must be a table")
        setting = tools.get(name)
        if setting is not None and not isinstance(setting, str):
            raise ValueError(f"{config.config_file}: tools.{name} must be a path string")
    hint = f"set --{name} PATH, {env} or [tools] {name} in rrl.toml"
    if not setting and name == "ffmpeg":
        setting = shutil.which(name)
        if not setting:
            raise ValueError(f"ffmpeg missing on PATH: {hint}")
    if not setting:
        raise ValueError(f"realesrgan-ncnn-vulkan is not configured: {hint}")
    path = Path(setting).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"{name} is missing or not executable: {path}; {hint}")
    return path


def cmd_doctor(config: Config, conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    problems = 0

    def report(ok: bool, label: str, detail: str, required: bool = False) -> None:
        nonlocal problems
        mark = "ok  " if ok else ("FAIL" if required else "--  ")
        problems += 0 if ok or not required else 1
        print(f"[{mark}] {label:<16} {detail}")

    v = sys.version_info
    report(v >= (3, 12), "python", f"{v.major}.{v.minor}.{v.micro}", required=True)
    report(True, "rrl", __version__)
    report(True, "workspace", str(config.workspace))
    schema = conn.execute("PRAGMA user_version").fetchone()[0]
    report(True, "database", f"schema v{schema}, sqlite {sqlite3.sqlite_version}")

    for tool, purpose in EXTERNAL_TOOLS.items():
        found = shutil.which(tool)
        report(found is not None, tool, found or f"not found (optional: {purpose})")

    for tool in ("ffmpeg", "upscaler"):
        try:
            backend = _tool(config, tool)
        except (OSError, ValueError) as exc:
            report(False, tool, f"optional: {exc}")
        else:
            report(True, tool, f"{backend} (optional)")

    if config.xemu is None:
        report(False, "xemu", f"not configured; set [emulators] xemu in {config.config_file}")
    else:
        report(config.xemu.exists(), "xemu", str(config.xemu), required=True)

    adapters, errors = registry.load_adapters()
    for adapter in adapters:
        caps = ", ".join(sorted(adapter.capabilities))
        report(True, "adapter", f"{adapter.id} {adapter.version} [{caps}]")
    for name, error in errors.items():
        report(False, "adapter", f"{name}: {error}", required=True)
    return 1 if problems else 0


def cmd_source_add(config: Config, conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    source_id, source = add_source(conn, Path(args.path))
    print(f"source {source_id}: {source.path.name}  sha256={source.sha256}  {source.size:,} bytes")
    return 0


def cmd_source_list(config: Config, conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    rows = conn.execute("SELECT * FROM source ORDER BY id").fetchall()
    if not rows:
        print("no sources registered; use `rrl source add <image>`")
    for r in rows:
        ident = " · ".join(x for x in (r["platform"], r["title"], r["title_id"], r["region"]) if x)
        print(f"{r['id']:>4}  {Path(r['path']).name}  {ident or '(not identified)'}")
    return 0


def cmd_identify(config: Config, conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    source_id, source = get_source(conn, args.source)
    adapters, _ = registry.load_adapters()
    with operation(conn, "identify", source_id=source_id):
        matches = registry.identify(source, adapters)
        if matches:
            record_identity(conn, source_id, matches[0])
    if args.json:
        print(json.dumps([asdict(m) for m in matches], indent=2))
    elif not matches:
        print(f"{source.path.name}: not recognised by any installed adapter")
    else:
        best = matches[0]
        ident = " · ".join(x for x in (best.platform, best.title, best.title_id, best.region) if x)
        print(
            f"{source.path.name}: {ident}  (adapter {best.adapter_id}, "
            f"confidence {best.confidence:.0%})"
        )
    return 0 if matches else 1


def cmd_inspect(config: Config, conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    source_id, source = get_source(conn, args.source)
    adapters, _ = registry.load_adapters()
    matches = registry.identify(source, adapters)
    if not matches:
        print(f"{source.path.name}: not recognised by any installed adapter", file=sys.stderr)
        return 1
    adapter = registry.by_id(adapters, matches[0].adapter_id)
    if not ("inspect" in adapter.capabilities and isinstance(adapter, Inspector)):
        print(f"adapter {adapter.id} does not support inspect", file=sys.stderr)
        return 1
    with operation(
        conn, "inspect", source_id=source_id, adapter_id=adapter.id, adapter_version=adapter.version
    ):
        report: dict[str, Any] = adapter.inspect(source)
    print(json.dumps(report, indent=2, default=str))
    return 0


def cmd_extract(config: Config, conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    source_id, source = get_source(conn, args.source)
    adapters, _ = registry.load_adapters()
    matches = registry.identify(source, adapters)
    if not matches:
        print(f"{source.path.name}: not recognised by any installed adapter", file=sys.stderr)
        return 1
    adapter = registry.by_id(adapters, matches[0].adapter_id)
    export = Path(args.export).expanduser().resolve() if args.export else None
    summary = run_extract(
        conn, config.store_path, source_id, source, adapter, args.only or None, export
    )
    print(
        f"operation {summary.operation_id}: stored {summary.stored} assets, "
        f"skipped {len(summary.skipped)}"
    )
    if summary.skipped:
        reasons: dict[str, int] = {}
        for _, reason in summary.skipped:
            reasons[reason] = reasons.get(reason, 0) + 1
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  skipped {n:>5}  {reason}")
    if export:
        print(f"exported to {export}")
    return 0


def cmd_upscale(config: Config, conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    # The registered source is only a catalogue key. Its disc can be offline.
    if args.source.isdigit():
        row = conn.execute("SELECT id FROM source WHERE id = ?", (args.source,)).fetchone()
    else:
        path = Path(args.source).expanduser().resolve()
        row = conn.execute(
            "SELECT id FROM source WHERE path = ? ORDER BY id DESC", (str(path),)
        ).fetchone()
    if row is None:
        raise SourceError(f"no registered source for {args.source}")
    source_id = row["id"]
    export, out = (Path(p).expanduser().resolve() for p in (args.export, args.out))
    params = {
        "export": str(export),
        "out": str(out),
        "model": args.model,
        "scale": args.scale,
        "max_side": args.max_side,
        "only": args.only,
        "packable_only": args.packable_only,
    }
    try:
        with operation(conn, "upscale", source_id=source_id, params=params) as op_id:
            backend = _tool(config, "upscaler", args.upscaler)
            with conn:
                conn.execute(
                    "UPDATE operation SET tools_json = ? WHERE id = ?",
                    (json.dumps({"upscaler": str(backend)}), op_id),
                )
            upscale_export(
                conn,
                config.store_path,
                source_id,
                op_id,
                export=export,
                out=out,
                backend=backend,
                model=args.model,
                scale=args.scale,
                max_side=args.max_side,
                only=tuple(args.only),
                packable_only=args.packable_only,
            )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_video_remaster(config: Config, conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    source, out = (Path(p).expanduser().resolve() for p in (args.input, args.out))
    try:
        digest = sha256_file(source)
        row = conn.execute("SELECT id FROM source WHERE sha256 = ?", (digest,)).fetchone()
        params = {
            "input": str(source),
            "sha256": digest,
            "out": str(out),
            "model": args.model,
            "scale": args.scale,
            "deinterlace": not args.no_deinterlace,
            "keep_frames": args.keep_frames,
        }
        with operation(conn, "video-remaster", source_id=row["id"] if row else None, params=params):
            count = remaster(
                source,
                out,
                digest=digest,
                backend=_tool(config, "upscaler", args.upscaler),
                ffmpeg=_tool(config, "ffmpeg", args.ffmpeg),
                model=args.model,
                scale=args.scale,
                deinterlace=not args.no_deinterlace,
                keep_frames=args.keep_frames,
            )
        print(f"Wrote {count} frames to {out}")
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _copy_disc(source: Path, target: Path) -> None:
    if sys.platform == "darwin":
        try:
            subprocess.run(["cp", "-c", source, target], check=True, stderr=subprocess.PIPE)
        except (FileNotFoundError, subprocess.CalledProcessError):
            target.unlink(missing_ok=True)
        else:
            target.chmod(0o600)  # The source may be read-only.
            return
    shutil.copyfile(source, target)


def _inject_export(
    conn: sqlite3.Connection,
    source_id: int,
    images: Path,
    disc: BinaryIO,
    only: tuple[str, ...],
    resize: bool,
) -> dict[str, Any]:
    # As in pack build, repeated extractions use the newest artifact for each logical path.
    textures = dict(
        conn.execute(
            "SELECT logical_path, metadata_json FROM artifact WHERE source_id = ?"
            " AND kind = 'extracted' AND format = 'image/png' ORDER BY id",
            (source_id,),
        )
    )
    injector = DiscInjector(disc)
    maps: dict[str, dict[str, int]] = {}
    skipped: dict[str, str] = {}
    for path, metadata in textures.items():
        if only and not path.startswith(only):
            continue
        rel = _safe_relpath(path)
        png = images / rel
        if not png.resolve().is_relative_to(images):
            raise ValueError(f"{path} resolves outside {images}")
        if not png.is_file():
            continue
        map_name = rel.parts[0]
        counts = maps.setdefault(map_name, {"injected": 0, "skipped": 0})
        meta = json.loads(metadata)
        tag, index = meta.get("tag_name"), meta.get("bitmap_index")
        try:
            if not isinstance(tag, str) or not isinstance(index, int):
                raise NotInjectable("missing bitmap tag or index in extraction metadata")
            with Image.open(png, formats=["PNG"]) as image:
                injector.write(map_name, tag, index, image, resize=resize)
        except NotInjectable as exc:
            skipped[path] = str(exc)
            counts["skipped"] += 1
        except (ValueError, OSError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
        else:
            counts["injected"] += 1
    if not maps:
        raise ValueError(f"no PNGs match the selected extracted textures of source {source_id}")
    return {"maps": maps, "skipped": skipped, "signatures": injector.finish()}


def cmd_inject(config: Config, conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    source_id, source = get_source(conn, args.source)
    out = Path(args.out).expanduser().absolute()
    params: dict[str, Any] = {"out": str(out)}
    if args.images is not None:
        images = Path(args.images).expanduser().resolve()
        params.update({"from": str(images), "only": args.only, "resize": args.resize})
    else:
        image = Path(args.image).expanduser().absolute()
        params.update(map=args.map, tag=args.tag, index=args.index, image=str(image))
    try:
        with operation(conn, "inject", source_id=source_id, params=params):
            if out.resolve() == source.path.resolve():
                raise ValueError(f"output equals the source: {out}")
            if os.path.lexists(out):
                raise FileExistsError(f"output already exists: {out}")
            if args.images is not None and not images.is_dir():
                raise ValueError(f"edited export directory does not exist: {images}")
            with ExitStack() as stack:
                png = (
                    stack.enter_context(Image.open(image, formats=["PNG"]))
                    if args.image is not None
                    else None
                )
                staging = stack.enter_context(tempfile.TemporaryDirectory(dir=out.parent))
                copy = Path(staging) / "disc"
                _copy_disc(source.path, copy)
                with copy.open("r+b") as disc:
                    if png is None:
                        report = _inject_export(
                            conn, source_id, images, disc, tuple(args.only), args.resize
                        )
                    else:
                        report = asdict(inject_texture(disc, args.map, args.tag, args.index, png))
                # Publish the validated copy atomically, even if another writer chose this name.
                os.link(copy, out)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


def cmd_pack_build(config: Config, conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Turn edited copies of `rrl extract --export` PNGs into an xemu texture pack (ADR-0009)."""
    source_id, _ = get_source(conn, args.source)
    images = Path(args.images).expanduser().resolve()
    pack = Path(args.pack).expanduser().resolve()
    textures = {
        path: json.loads(meta)
        for path, meta in conn.execute(
            "SELECT logical_path, metadata_json FROM artifact WHERE source_id = ?"
            " AND kind = 'extracted' AND metadata_json LIKE '%\"xemu_hashes\"%' ORDER BY id",
            (source_id,),
        )
    }
    if not textures:
        print(
            "no extracted textures with xemu hashes: run `rrl extract` with the packs extra",
            file=sys.stderr,
        )
        return 1
    pack.mkdir(parents=True, exist_ok=True)
    built, missing, no_alpha, bad = 0, 0, 0, []
    params = {"images": str(images), "pack": str(pack)}
    # A texture shared by maps may have one edited copy; same top-level hash = same pixels.
    by_top: dict[str, Path] = {}
    for path, meta in textures.items():
        if (png := images / _safe_relpath(path)).is_file():
            by_top.setdefault(meta["xemu_hashes"][0], png)
    written: set[int] = set()
    with operation(conn, "pack_build", source_id=source_id, params=params):
        for path, meta in textures.items():
            png = images / _safe_relpath(path)
            if not png.is_file():
                png = by_top.get(meta["xemu_hashes"][0], png)
            keys = [int(k, 16) for k in meta["xemu_hashes"]]
            if written.issuperset(keys):
                continue  # another copy of this texture already covered every key
            if not png.is_file():
                missing += 1  # not edited: the game keeps its own texture
                continue
            written.update(keys)
            try:
                with Image.open(png) as im:
                    no_alpha += "A" not in im.getbands()
                    write_pack_texture(pack, keys, im, meta["width"], meta["height"])
            except (OSError, ValueError) as exc:
                bad.append(f"{path}: {exc}")
                continue
            built += 1
    print(f"packed {built} textures into {pack}; {missing} had no image in {images}")
    if no_alpha:
        print(f"  {no_alpha} images had no alpha channel: packed as fully opaque")
    for line in bad:
        print(f"  skipped {line}")
    return 0


def cmd_perf_report(config: Config, conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    logs = [args.log, args.compare] if args.compare else [args.log]
    params = {"logs": logs, "window_s": args.window}
    try:
        with operation(conn, "perf-report", params=params):
            reports = [summarize(Path(log).expanduser(), args.window) for log in logs]
            if args.json:
                print(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "window_s": args.window,
                            "reports": [asdict(report) for report in reports],
                        }
                    )
                )
            else:
                print(format_reports(reports))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rrl", description="Renegade Remaster Lab")
    p.add_argument("--version", action="version", version=f"rrl {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="show debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check the environment").set_defaults(func=cmd_doctor)

    src = sub.add_parser("source", help="manage original game images")
    src_sub = src.add_subparsers(dest="source_command", required=True)
    add = src_sub.add_parser("add", help="hash and register an image (read-only)")
    add.add_argument("path")
    add.set_defaults(func=cmd_source_add)
    src_sub.add_parser("list", help="list registered images").set_defaults(func=cmd_source_list)

    ident = sub.add_parser("identify", help="identify an image (path or source id)")
    ident.add_argument("source")
    ident.add_argument("--json", action="store_true")
    ident.set_defaults(func=cmd_identify)

    insp = sub.add_parser("inspect", help="describe an image without extracting")
    insp.add_argument("source")
    insp.set_defaults(func=cmd_inspect)

    ext = sub.add_parser("extract", help="extract assets into the store (original untouched)")
    ext.add_argument("source")
    ext.add_argument(
        "--only",
        nargs="+",
        metavar="NAME",
        help="adapter-defined subset, e.g. maps (Halo 2 also: textures, models, levels; export them"
        " to the same --export folder so texture links resolve)",
    )
    ext.add_argument("--export", metavar="DIR", help="also copy extracted files to this folder")
    ext.set_defaults(func=cmd_extract)

    up = sub.add_parser("upscale", help="enhance PNG textures from an extraction export")
    up.add_argument("source", help="registered source ID or path")
    up.add_argument("--export", required=True, metavar="DIR", help="extracted PNG tree")
    up.add_argument("--out", required=True, metavar="DIR", help="mirrored enhanced PNG tree")
    up.add_argument("--scale", type=int, choices=[2, 4], default=4)
    up.add_argument("--model", default="realesrgan-x4plus", help="native 4x model name")
    up.add_argument("--only", nargs="+", metavar="PREFIX", default=[], help="logical path prefixes")
    up.add_argument(
        "--packable-only", action="store_true", help="require extracted xemu pack hashes"
    )
    up.add_argument(
        "--max-side", type=int, default=4096, help="use 2x if 4x exceeds this side length"
    )
    up.add_argument("--upscaler", metavar="PATH", help="local realesrgan-ncnn-vulkan executable")
    up.set_defaults(func=cmd_upscale)

    video = sub.add_parser("video", help="process pre-rendered video")
    video_sub = video.add_subparsers(dest="video_command", required=True)
    movie = video_sub.add_parser("remaster", help="upscale frames and encode an HEVC MP4")
    movie.add_argument("input", metavar="INPUT")
    movie.add_argument("--out", required=True, metavar="OUT.mp4")
    movie.add_argument("--model", default="realesrgan-x4plus")
    movie.add_argument("--scale", type=int, choices=[2, 4], default=4)
    movie.add_argument("--no-deinterlace", action="store_true")
    movie.add_argument("--keep-frames", action="store_true")
    movie.add_argument("--upscaler", metavar="PATH")
    movie.add_argument("--ffmpeg", metavar="PATH")
    movie.set_defaults(func=cmd_video_remaster)

    inject = sub.add_parser("inject", help="replace textures in a new disc copy")
    inject.add_argument("source", help="image path or registered source id")
    inject.add_argument("--map", help="map name, e.g. mainmenu")
    inject.add_argument("--tag", help="bitmap tag path")
    inject.add_argument("--index", type=int, help="bitmap index")
    mode = inject.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from", dest="images", metavar="DIR", help="edited PNG export tree")
    mode.add_argument("--image", metavar="PNG", help="replacement PNG")
    inject.add_argument(
        "--only", nargs="+", metavar="PREFIX", default=[], help="export path prefixes"
    )
    inject.add_argument(
        "--resize", action="store_true", help="resize batch PNGs to native dimensions"
    )
    inject.add_argument("--out", required=True, help="new disc image path")
    inject.set_defaults(func=cmd_inject)

    perf = sub.add_parser("perf", help="analyze guest frame timing")
    perf_sub = perf.add_subparsers(dest="perf_command", required=True)
    report = perf_sub.add_parser("report", help="summarize an xemu frame log")
    report.add_argument("log", metavar="LOG")
    report.add_argument(
        "--window",
        type=int,
        default=10,
        metavar="SECONDS",
        help="window duration (default 10 seconds)",
    )
    report.add_argument("--json", action="store_true", help="emit the versioned JSON report")
    report.add_argument(
        "--compare", metavar="OTHER.log", help="compare a second capture side by side"
    )
    report.set_defaults(func=cmd_perf_report)

    pk = sub.add_parser("pack", help="xemu texture packs")
    pk_sub = pk.add_subparsers(dest="pack_command", required=True)
    build = pk_sub.add_parser("build", help="pack edited PNGs, laid out like an extract export")
    build.add_argument("source")
    build.add_argument("images", help="folder of edited PNGs (same relative paths as the export)")
    build.add_argument("pack", help="output folder, for XEMU_TEXTURE_PACK_DIR")
    build.set_defaults(func=cmd_pack_build)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "inject":
        target = (args.map, args.tag, args.index)
        if args.images is not None and any(value is not None for value in target):
            parser.error("--map, --tag and --index belong to the single-image form of inject")
        if args.image is not None:
            if None in target:
                parser.error("--image requires --map, --tag and --index")
            if args.only or args.resize:
                parser.error("--only and --resize require --from")
    try:
        config = load_config()
        ensure_workspace(config)
        with (
            setup_logging(config.log_path, args.verbose),
            closing(db.connect(config.db_path)) as conn,
        ):
            result: int = args.func(config, conn, args)
            return result
    except (
        ConfigError,
        SourceError,
        ExtractionError,
        SelectionError,
        registry.AdapterError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
