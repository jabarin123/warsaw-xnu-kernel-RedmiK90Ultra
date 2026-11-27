#!/usr/bin/env python3
"""Build a warsaw (REDMI K90 Ultra, 1280x2772) RAM-only fastboot-boot package
from upstream iola1999/hackios-redmi-k90-annibale v0.2.3 (2026-11-27).

Hard boundaries respected:
  - RAM-only fastboot boot (rootdev=md0, NO root_apfs=ufs_darwin, NO ufs_write=1).
  - Never touches existing system26.apfs or root26.apfs.
  - Keeps all Apple IPSW files and local vendor firmware strictly on local disk.

Outputs:
  - F:/warsaw-ios26/tree/userland/root26-v023-warsaw.apfs
  - F:/warsaw-ios26/tree/userland/system26-v023-warsaw.apfs
  - C:/Users/Administrator/WorkBuddy/2026-11-06-17-23-24/hackios/hackios-xnu-kernel/out/payload-v023-warsaw.bin
"""

import hashlib
import json
import os
import plistlib
import shutil
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT_WB = Path("C:/Users/Administrator/WorkBuddy/2026-11-06-17-23-24")
RELEASE = ROOT_WB / "build-out/upstream-v0.2.3/extracted/hackios-k90-v0.2.3"
WARSAW_BASE_PAYLOAD = ROOT_WB / "hackios/hackios-xnu-kernel/out/payload-v99.bin"
OUT_PAYLOAD = ROOT_WB / "hackios/hackios-xnu-kernel/out/payload-v023-warsaw.bin"

TREE = Path("F:/warsaw-ios26/tree")
IOS26 = TREE / "ios26"
ROOTFS = IOS26 / "rootfs"
CRYPTEX = IOS26 / "cryptex"
CRYPTEX_APP = IOS26 / "cryptex-app"
CACHE_DIR = IOS26 / "23G90__iPhone18,3"
IPSW_OS_DMG = Path("F:/warsaw-ios26/ipsw/os.dmg")
SEVEN_ZIP = Path("F:/warsaw-ios26/tools/7z25/7z.exe")
USERLAND = TREE / "userland"

OUT_ROOT_APFS = USERLAND / "root26-v023-warsaw.apfs"
OUT_SYS_APFS = USERLAND / "system26-v023-warsaw.apfs"
WORK_ROOT = USERLAND / "root-v023-warsaw"
WORK_ROOT_MANIFEST = USERLAND / "root-v023-warsaw.manifest"
WORK_SYS = USERLAND / "sysroot-v023-warsaw"
WORK_SYS_MANIFEST = USERLAND / "system-v023-warsaw.manifest"

PAYLOAD_CAP = 0x03700000  # 57,671,680 B (strict BootShim / mkxnuimg cap)

WARSAW_CMDLINE = (
    "debug=0x8 rootdev=md0 launchd_unsecure_cache=1 panic_on_crash_disabled=1 "
    "cpus=7 bootcpu=7 small_cores_after=0 "
    "adreno8=23 adreno8_pause=0 adreno8_fine=0 adreno8_watch=0 "
    "adreno8_keep=1 adreno8_return=0 adreno8_speed=14 adreno8_secure_preflight=4 "
    "touchscreen=2 buttons=1 battery=1 gpu_log=0 gpu_coherent=1 metal=1 cpufreq=1 "
    "fb_threads=4 fb_flip=2 fb_beat=0 fb_hz=120 fb_ca_hz=120 "
    "touch_lat=0 touch_poll_us=4000 dl_max=1 "
    "pcie=10 pcie_power=1 pcie_sleep_clock=board pcie_bdf=bd_p12x.elf pcie_fw=2 "
    "pcie_smps_hpm=1 pcie_bt_en=1 pcie_aop_pdc=1 pcie_rddm=1 "
    "wifi=1 wifi_lx=2 wifi_smmu=2 wifi_keep=1 wifi_scan=1 wifi_mlo=1 "
    "wifi_mac=02:4b:39:30:00:01 wifi_station=1 applog=0 autounlock=1 "
    "fbwidth=1280 fbheight=2772 fbpitch=460"
)

# Lean RAM-boot daemon whitelist: loading all 545 flash-boot daemons alongside a
# 5.5 GiB RAM-resident system26.apfs exhausts wired kernel memory (IOBufferMemoryDescriptor
# returns NULL -> AnnibaleGPU no memory freeze). These ~47 Apple daemons + 4 annibale
# daemons cover SpringBoard, Liquid Glass, PosterBoard, Widgets, Wi-Fi, Safari, Maps,
# Photos, Files, Keyboard, Audio/Video, and TrollStore while saving ~3 GiB of RAM.
RAM_BOOT_APPLE_DAEMONS = {
    "com.apple.PosterBoard.plist",
    "com.apple.SpringBoard.plist",
    "com.apple.TextInput.kbd.plist",
    "com.apple.UserEventAgent-System.plist",
    "com.apple.appprotectiond.plist",
    "com.apple.assetsd.plist",
    "com.apple.audiomxd.plist",
    "com.apple.backboardd.plist",
    "com.apple.cfprefsd.xpc.daemon.plist",
    "com.apple.cfprefsd.xpc.daemon.system.plist",
    "com.apple.chronod.plist",
    "com.apple.configd.plist",
    "com.apple.containermanagerd.plist",
    "com.apple.containermanagerd.system.plist",
    "com.apple.diagnosticd.plist",
    "com.apple.distnoted.xpc.daemon.plist",
    "com.apple.FileProvider.plist",
    "com.apple.fontservicesd.plist",
    "com.apple.fseventsd.plist",
    "com.apple.geod.plist",
    "com.apple.iconservices.iconservicesagent.plist",
    "com.apple.locationd.plist",
    "com.apple.logd.plist",
    "com.apple.logd_helper.plist",
    "com.apple.lsd.plist",
    "com.apple.mDNSResponder.plist",
    "com.apple.mDNSResponderHelper.plist",
    "com.apple.mobile.installd.plist",
    "com.apple.mobile.usermanagerd.plist",
    "com.apple.mobileactivationd.plist",
    "com.apple.mobilegestalt.xpc.plist",
    "com.apple.notifyd.plist",
    "com.apple.nsurlsessiond.plist",
    "com.apple.pasteboard.pasted.plist",
    "com.apple.pluginkit.pkd.plist",
    "com.apple.powerd.plist",
    "com.apple.runningboardd.plist",
    "com.apple.securityd.plist",
    "com.apple.sleepd.plist",
    "com.apple.splashboardd.plist",
    "com.apple.syslogd.plist",
    "com.apple.systemstatusd.plist",
    "com.apple.tccd.plist",
    "com.apple.timed.plist",
    "com.apple.trustd.plist",
    "com.apple.videocodecd.plist",
    "com.apple.wifid.plist",
}

MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}

sys.path.insert(0, str(RELEASE / "tools/release"))
import localfiles  # noqa: E402


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def remove_tree_fast(path: Path):
    if not path.exists():
        return
    # Rename aside or remove cleanly (no safe-delete env set here; Python shutil.rmtree works)
    shutil.rmtree(path)


def prog_exists_in_ram_sys(prog: str, ann_root: Path) -> bool:
    if not prog or not prog.startswith("/"):
        return False
    if prog.startswith("/annibale/"):
        sub = prog[len("/annibale/"):]
        return (ann_root / sub).exists() or sub == "bin/bluetoothd"
    if prog.startswith("/System/Cryptexes/OS/"):
        return (CRYPTEX / prog[len("/System/Cryptexes/OS/"):]).exists()
    if prog.startswith("/System/Cryptexes/App/"):
        return (CRYPTEX_APP / prog[len("/System/Cryptexes/App/"):]).exists()
    return (ROOTFS / prog.lstrip("/")).exists()


def read_ios_file(ref: str) -> bytes:
    vol, rel = ref.split(":", 1)
    if vol == "os":
        p = ROOTFS / rel
        if p.is_file():
            return p.read_bytes()
    raise FileNotFoundError(f"iOS file not found locally: {ref}")


def build_var_skeleton(work_dir: Path, recipe: dict) -> bytes:
    """Generate etc/var.skel matching v0.2.3 exactly using full-system.py + os.dmg."""
    slt = work_dir / "os-var.slt"
    if not slt.is_file():
        print("  listing private/var/* from os.dmg via 7-Zip...", flush=True)
        with open(slt, "wb") as out:
            subprocess.run(
                [str(SEVEN_ZIP), "l", "-slt", str(IPSW_OS_DMG), "private/var/*"],
                stdout=out, stderr=subprocess.DEVNULL, check=True,
            )
    slt_unix = work_dir / "os-var-unix.slt"
    if not slt_unix.is_file():
        raw_slt = slt.read_text(encoding="utf-8", errors="replace")
        fixed = "\n".join(
            ("Path = " + l[7:].replace("\\", "/")) if l.startswith("Path = ") else l
            for l in raw_slt.splitlines()
        ) + "\n"
        slt_unix.write_text(fixed, encoding="utf-8", newline="\n")
    full_sys_py = RELEASE / "hackios-xnu-userland/ios26/full-system.py"
    res = subprocess.run(
        [sys.executable, str(full_sys_py), "skeleton", str(slt_unix)],
        capture_output=True, text=True, check=True,
    )
    skel = localfiles.apply_skeleton(res.stdout, recipe["edits"])
    data = skel.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    assert digest == recipe["sha256"], f"var.skel sha mismatch: {digest} != {recipe['sha256']}"
    print(f"  var.skel generated: {len(data)} B, sha256={digest[:16]} (EXACT MATCH)", flush=True)
    return data


def extract_v023_stage0(dst: Path):
    """Extract v0.2.3 stage0 (sbin/launchd, 34912 B) from flashboot.apfs."""
    tmp = dst.parent / "_stage0_extract"
    remove_tree_fast(tmp)
    tmp.mkdir(parents=True)
    fb = RELEASE / "payload/flashboot.apfs"
    subprocess.run(
        [str(SEVEN_ZIP), "x", "-y", "-bso0", "-bsp0", str(fb), f"-o{tmp}", "sbin/launchd"],
        check=True,
    )
    extracted = tmp / "sbin/launchd"
    assert extracted.is_file() and extracted.stat().st_size > 10000, "stage0 extraction failed"
    shutil.copyfile(extracted, dst)
    remove_tree_fast(tmp)
    print(f"  extracted v0.2.3 stage0: {dst.stat().st_size} B, sha256={sha256_file(dst)[:16]}", flush=True)


def build_root_image(stage_dir: Path, manifest_path: Path, out_apfs: Path):
    print("\n=== [1/3] Building root26-v023-warsaw.apfs (/dev/md1 -> /annibale) ===", flush=True)
    remove_tree_fast(stage_dir)
    shutil.copytree(RELEASE / "root", stage_dir)

    lf = plistlib.loads((RELEASE / "local-files.plist").read_bytes())["files"]
    manifest_json = json.loads((RELEASE / "manifest.json").read_text(encoding="utf-8"))
    mode_by_path = {rel: mode for rel, kind, mode, target in manifest_json["root"]["entries"]}

    # 1. Copy launchd.ios into /annibale/sbin/launchd.ios (avoids md0 pid 1 execve namei issue)
    shutil.copyfile(ROOTFS / "sbin/launchd", stage_dir / "sbin/launchd.ios")
    # Update etc/rc to exec /annibale/sbin/launchd.ios (LF-only)
    rc_text = (
        "# Hand process 1 over to the real iOS launchd from /annibale (md1).\n"
        "# Carrying launchd.ios on md1 avoids the RAM-disk md0 pid-1 execve namei failure.\n"
        "exec /annibale/sbin/launchd.ios\n"
    )
    (stage_dir / "etc/rc").write_bytes(rc_text.encode("utf-8"))

    # 2. Apply local-files.plist recipes (bluetoothd, CMCaptureResources, xpc static files, var.skel, LaunchDaemons)
    jobs_bytes = {}
    cache_recipe = None
    kept_daemons = 0
    skipped_daemons = []

    # First include the 4 com.annibale.*.plist already in stage_dir/LaunchDaemons
    for p in sorted((stage_dir / "LaunchDaemons").glob("*.plist")):
        jobs_bytes[p.name] = p.read_bytes()

    for r in lf:
        rel = r["path"]
        kind = r["kind"]
        dst = stage_dir / rel
        if kind == "firmware":
            continue  # handled separately for warsaw below
        elif kind == "cache":
            cache_recipe = r
            continue
        elif kind == "skeleton":
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = build_var_skeleton(stage_dir.parent, r)
            dst.write_bytes(data)
            continue
        elif rel.startswith("LaunchDaemons/"):
            fname = Path(rel).name
            if fname not in RAM_BOOT_APPLE_DAEMONS:
                skipped_daemons.append((rel, "trimmed-for-ram-boot"))
                continue
            src_bytes = read_ios_file(r["from"])
            pl = localfiles.apply_edits(plistlib.loads(src_bytes), r["edits"])
            prog = pl.get("Program") or (pl.get("ProgramArguments") or [None])[0]
            if not prog_exists_in_ram_sys(prog, stage_dir):
                skipped_daemons.append((rel, prog))
                continue
            data = localfiles.dump_plist(pl)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            jobs_bytes[fname] = data
            kept_daemons += 1
        elif kind == "copy":
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = read_ios_file(r["from"])
            assert hashlib.sha256(data).hexdigest() == r["sha256"], f"sha mismatch on {rel}"
            dst.write_bytes(data)
        elif kind == "plist":
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = localfiles.dump_plist(localfiles.apply_edits(plistlib.loads(read_ios_file(r["from"])), r["edits"]))
            assert hashlib.sha256(data).hexdigest() == r["sha256"], f"sha mismatch on {rel}"
            dst.write_bytes(data)

    print(f"  LaunchDaemons: kept {kept_daemons} Apple + {len(jobs_bytes) - kept_daemons} annibale = {len(jobs_bytes)} total; skipped {len(skipped_daemons)} absent-binary daemons", flush=True)

    # 3. Build xpc/launchd.plist cache using localfiles.cache_base()
    assert cache_recipe is not None
    apple_cache = read_ios_file(cache_recipe["from"])
    cache_dict = localfiles.cache_base(apple_cache, jobs_bytes)
    # Apply any cache edits whose target key still exists
    applied_cache_edits = 0
    for ed in cache_recipe.get("edits", []):
        try:
            localfiles.apply_edits(cache_dict, [ed])
            applied_cache_edits += 1
        except (KeyError, IndexError):
            pass
    (stage_dir / "xpc").mkdir(parents=True, exist_ok=True)
    (stage_dir / "xpc/launchd.plist").write_bytes(localfiles.dump_plist(cache_dict))
    print(f"  xpc/launchd.plist built: {len(cache_dict['LaunchDaemons'])} jobs ({applied_cache_edits} cache edits applied)", flush=True)

    # 4. Stage warsaw local firmware into fw/
    fw_dst = stage_dir / "fw"
    fw_dst.mkdir(parents=True, exist_ok=True)
    fw_copied = []
    # GPU zap (gen80000_aqe/gmu/sqe are already in v0.2.3 root/fw; keep warsaw zap)
    zap = TREE / "fw-gpu/gen80000_zap.mbn"
    assert zap.is_file(), f"Missing {zap}"
    shutil.copyfile(zap, fw_dst / "gen80000_zap.mbn")
    fw_copied.append("gen80000_zap.mbn")
    # Wi-Fi PCIe firmware (WCN7850 + warsaw bd_p12x.elf)
    wifi_dir = TREE / "fw-wifi-pcie"
    for fn in ("amss20.bin", "regdb.bin", "phy_ucode20.elf", "bd_p12x.elf", "qdss_trace_config_v2.cfg"):
        src = wifi_dir / fn
        if src.is_file():
            shutil.copyfile(src, fw_dst / fn)
            fw_copied.append(fn)
    if (wifi_dir / "bd_p12x.elf").is_file():
        shutil.copyfile(wifi_dir / "bd_p12x.elf", fw_dst / "bdwlan.elf")
        fw_copied.append("bdwlan.elf")
    print(f"  staged warsaw fw/: {', '.join(fw_copied)}", flush=True)

    # Silence launchd's own verbose stderr spam on RAM boot
    (stage_dir / "etc/quiet").write_bytes(b"")

    # 5. Normalize all text config files to LF-only and verify zero CR bytes
    for rel in ("etc/rc", "etc/rc.jobs", "etc/inject.conf", "etc/var.skel", "etc/weights.list", "etc/motd"):
        p = stage_dir / rel
        if p.is_file():
            b = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            assert b"\r" not in b, f"CR survived in {rel}"
            p.write_bytes(b)

    # 6. Write root.manifest with exact POSIX modes + symlinks (Windows/NTFS ignores chmod)
    rows = []
    for dirpath, dirnames, filenames in os.walk(stage_dir):
        dirnames.sort()
        filenames.sort()
        for name in dirnames + filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(stage_dir).as_posix()
            if full.is_dir():
                m = mode_by_path.get(rel, 0o755)
                rows.append((rel, f"{0o040000 | m:o}\t0\t0\t{rel}\t"))
            elif full.is_file():
                with open(full, "rb") as f:
                    macho = f.read(4) in MACHO_MAGICS
                if rel in mode_by_path:
                    m = mode_by_path[rel]
                else:
                    top = rel.split("/")[0]
                    executable = top in ("bin", "sbin", "lib") or rel in ("etc/rc", "etc/rc.jobs") or macho
                    m = 0o755 if executable else 0o644
                if macho:
                    m |= 0o111
                rows.append((rel, f"{0o100000 | m:o}\t0\t0\t{rel}\t"))
    # Add the symlink omitted by Windows tar extraction
    rows.append(("hal/AnnibaleAudio.driver", "120777\t0\t0\thal/AnnibaleAudio.driver\t/annibale/lib/AnnibaleAudio.driver"))
    rows.sort(key=lambda x: x[0])

    with open(manifest_path, "w", newline="\n", encoding="utf-8") as f:
        for _, row in rows:
            f.write(row + "\n")

    mode_map = {row.split("\t")[3]: row.split("\t")[0] for _, row in rows}
    for req in ("sbin/init", "sbin/launchd.ios", "bin/input26-service", "bin/bluetoothd"):
        assert mode_map.get(req) == "100755", f"manifest mode error for {req}: {mode_map.get(req)}"
    assert mode_map.get("etc/rc") in ("100644", "100755"), f"manifest mode error for etc/rc: {mode_map.get('etc/rc')}"
    print(f"  wrote {manifest_path.name}: {len(rows)} entries (verified exec bits + symlink)", flush=True)

    # 7. Pack root26-v023-warsaw.apfs using upstream v0.2.3 mkapfs.py
    mkapfs = RELEASE / "hackios-xnu-userland/tools/apfs/mkapfs.py"
    res = subprocess.run(
        [sys.executable, str(mkapfs), "-z", "--plain", "fw", "-o", str(out_apfs),
         f"annibale={stage_dir},manifest={manifest_path}"],
        capture_output=True, text=True, check=True,
    )
    print("  mkapfs:", res.stdout.strip().splitlines()[-1], flush=True)
    print(f"  root26-v023-warsaw.apfs: {out_apfs.stat().st_size:,} B, sha256={sha256_file(out_apfs)[:16]}", flush=True)


def hardlink_tree(src_dir: Path, dst_dir: Path, skip_rel=set()):
    """Fast NTFS hardlink mirror of a directory tree."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(src_dir):
        rel_dir = Path(dirpath).relative_to(src_dir).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        # Prune skipped directories
        keep_dirs = []
        for d in dirnames:
            sub = f"{rel_dir}/{d}" if rel_dir else d
            if sub in skip_rel:
                continue
            keep_dirs.append(d)
            (dst_dir / sub).mkdir(exist_ok=True)
        dirnames[:] = keep_dirs
        for fn in filenames:
            sub = f"{rel_dir}/{fn}" if rel_dir else fn
            if sub in skip_rel:
                continue
            s = Path(dirpath) / fn
            d = dst_dir / sub
            if s.is_symlink():
                # Skip host symlink; manifest provides POSIX symlink target
                continue
            if d.exists():
                d.unlink()
            os.link(s, d)


def build_system_image(root_stage: Path, sys_stage: Path, sys_manifest: Path, out_sys_apfs: Path):
    print("\n=== [2/3] Building system26-v023-warsaw.apfs (/dev/md0 -> /) ===", flush=True)
    t0 = time.time()
    # Keep existing system26.apfs completely untouched!
    if sys_stage.exists():
        stale = USERLAND / f"sysroot-v023.stale.{os.getpid()}"
        sys_stage.rename(stale)
        shutil.rmtree(stale, ignore_errors=True)

    # Extract v0.2.3 stage0
    stage0_bin = USERLAND / "stage0-v023"
    extract_v023_stage0(stage0_bin)

    # Load base system.manifest (preserving exact POSIX modes and symlinks from IPSW!)
    base_manifest_path = IOS26 / "system.manifest"
    manifest_map = {}  # rel -> line
    for raw in base_manifest_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = raw.split("\t")
        if len(parts) >= 4 and parts[3]:
            manifest_map[parts[3]] = raw

    print(f"  hardlinking ios26/rootfs -> {sys_stage.name}...", flush=True)
    skip_rootfs = {
        "System/Library/LaunchDaemons",
        "System/Library/NanoLaunchDaemons",
        "System/Library/LaunchAngels",
        "System/Library/xpc/launchd.plist",
        "usr/libexec/tzinit",
        "private/var",
    }
    hardlink_tree(ROOTFS, sys_stage, skip_rel=skip_rootfs)

    # Staged system apps -> /Applications
    staged_apps = ROOTFS / "private/var/staged_system_apps"
    if staged_apps.is_dir():
        for app in staged_apps.iterdir():
            if app.is_dir():
                hardlink_tree(app, sys_stage / "Applications" / app.name)

    # Replace sbin/launchd with v0.2.3 stage0 (original saved as sbin/launchd.ios)
    orig_ld = sys_stage / "sbin/launchd"
    if orig_ld.exists():
        orig_ld.rename(sys_stage / "sbin/launchd.ios")
    shutil.copyfile(stage0_bin, sys_stage / "sbin/launchd")

    # Put our v0.2.3 xpc/launchd.plist directly into System/Library/xpc/ (v25 fix)
    (sys_stage / "System/Library/xpc").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root_stage / "xpc/launchd.plist", sys_stage / "System/Library/xpc/launchd.plist")

    # Mountpoints & empty directories
    for d in (
        "System/Library/LaunchDaemons",
        "private/var",
        "annibale",
        "redwood",
        "sbin",
        "System/Volumes/iOS",
        "System/Library/Audio/Plug-Ins/HAL",
        "System/Library/Extensions",
        "System/Cryptexes",
    ):
        (sys_stage / d).mkdir(parents=True, exist_ok=True)

    # Cryptexes (OS + App) and dyld shared cache
    print("  hardlinking cryptex + cryptex-app + dyld_shared_cache_arm64e...", flush=True)
    if CRYPTEX.is_dir():
        hardlink_tree(CRYPTEX, sys_stage / "System/Cryptexes/OS")
    if CRYPTEX_APP.is_dir():
        hardlink_tree(CRYPTEX_APP, sys_stage / "System/Cryptexes/App")
    dyld_dir = sys_stage / "System/Cryptexes/OS/System/Library/Caches/com.apple.dyld"
    dyld_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(CACHE_DIR.glob("dyld_shared_cache_arm64*")):
        if f.name.endswith(".symbols"):
            continue
        dst = dyld_dir / f.name
        if dst.exists():
            dst.unlink()
        os.link(f, dst)

    # Build complete manifest covering EVERY entry in sys_stage + our symlinks!
    # This eliminates the Windows 0666 mode bug up-front (no in-place APFS patching needed).
    print("  generating complete POSIX manifest for system26-v023-warsaw...", flush=True)
    # Remove skipped paths from base manifest
    for k in list(manifest_map.keys()):
        if any(k == s or k.startswith(s + "/") for s in skip_rootfs):
            del manifest_map[k]
    # Add/override added paths
    extra_entries = {
        "private/var": "40755\t0\t0\tprivate/var\t",
        "annibale": "40755\t0\t0\tannibale\t",
        "redwood": "40755\t0\t0\tredwood\t",
        "System/Volumes": "40755\t0\t0\tSystem/Volumes\t",
        "System/Volumes/iOS": "40755\t0\t0\tSystem/Volumes/iOS\t",
        "System/Library/LaunchDaemons": "40755\t0\t0\tSystem/Library/LaunchDaemons\t",
        "System/Library/xpc/launchd.plist": "100644\t0\t0\tSystem/Library/xpc/launchd.plist\t",
        "sbin/launchd": "100755\t0\t0\tsbin/launchd\t",
        "sbin/launchd.ios": "100755\t0\t0\tsbin/launchd.ios\t",
        "System/Library/Extensions": "40755\t0\t0\tSystem/Library/Extensions\t",
        "System/Library/Extensions/AnnibaleMetal.bundle": "120777\t0\t0\tSystem/Library/Extensions/AnnibaleMetal.bundle\t/annibale/lib/AnnibaleMetal.bundle",
        "System/Library/Audio/Plug-Ins": "40755\t0\t0\tSystem/Library/Audio/Plug-Ins\t",
        "System/Library/Audio/Plug-Ins/HAL": "40755\t0\t0\tSystem/Library/Audio/Plug-Ins/HAL\t",
        "System/Library/Audio/Plug-Ins/HAL/AnnibaleAudio.driver": "120777\t0\t0\tSystem/Library/Audio/Plug-Ins/HAL/AnnibaleAudio.driver\t/annibale/lib/AnnibaleAudio.driver",
        "System/Cryptexes": "40755\t0\t0\tSystem/Cryptexes\t",
    }
    manifest_map.update(extra_entries)

    # Fill in POSIX modes for any file/dir under sys_stage missing from base manifest
    # (Cryptexes, staged_system_apps moved to Applications/, etc.)
    added_auto = 0
    for dirpath, dirnames, filenames in os.walk(sys_stage):
        rel_dir = Path(dirpath).relative_to(sys_stage).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        for d in dirnames:
            sub = f"{rel_dir}/{d}" if rel_dir else d
            if sub not in manifest_map:
                manifest_map[sub] = f"40755\t0\t0\t{sub}\t"
                added_auto += 1
        for fn in filenames:
            sub = f"{rel_dir}/{fn}" if rel_dir else fn
            if sub not in manifest_map:
                full = Path(dirpath) / fn
                with open(full, "rb") as f:
                    macho = f.read(4) in MACHO_MAGICS
                mode = "100755" if macho else "100644"
                manifest_map[sub] = f"{mode}\t0\t0\t{sub}\t"
                added_auto += 1

    with open(sys_manifest, "w", newline="\n", encoding="utf-8") as f:
        for k in sorted(manifest_map.keys()):
            f.write(manifest_map[k] + "\n")
    print(f"  manifest entries: {len(manifest_map)} ({added_auto} auto-typed for Cryptexes/apps)", flush=True)

    # Pack system26-v023-warsaw.apfs using upstream v0.2.3 mkapfs.py
    mkapfs = RELEASE / "hackios-xnu-userland/tools/apfs/mkapfs.py"
    print(f"  packing {out_sys_apfs.name} (this takes ~2-3 min)...", flush=True)
    res = subprocess.run(
        [sys.executable, str(mkapfs), "-z", "-o", str(out_sys_apfs),
         f"System={sys_stage},manifest={sys_manifest}"],
        capture_output=True, text=True, check=True,
    )
    print("  mkapfs:", res.stdout.strip().splitlines()[-1], flush=True)
    print(f"  system26-v023-warsaw.apfs: {out_sys_apfs.stat().st_size:,} B in {time.time() - t0:.1f}s", flush=True)


def build_payload(root_apfs: Path, out_payload: Path):
    print("\n=== [3/3] Assembling payload-v023-warsaw.bin ===", flush=True)
    base = WARSAW_BASE_PAYLOAD.read_bytes()
    assert struct.unpack_from("<4s", base, 0)[0] == b"XNUP", "base payload is not XNUP"

    # Extract warsaw loader.efi, identity, usb from our proven warsaw payload
    items = []
    off = 8
    while off + 24 <= len(base):
        name = base[off:off + 16].split(b"\0")[0].decode("ascii", "replace")
        size, res = struct.unpack_from("<II", base, off + 16)
        if not name or size > len(base) - off - 24:
            break
        items.append([name, res, base[off + 24:off + 24 + size]])
        off += 24 + ((size + 15) // 16) * 16

    v023_kernel = (RELEASE / "payload/kernel.bin").read_bytes()
    ram_bytes = root_apfs.read_bytes()
    cmd_bytes = (WARSAW_CMDLINE + "\0").encode("ascii")

    # Sanity assertions
    assert b"_adreno8_return_quiesce" in v023_kernel, "kernel lacks _adreno8_return_quiesce"
    assert b"fbwidth=1280" in cmd_bytes and b"fbheight=2772" in cmd_bytes and b"fbpitch=460" in cmd_bytes
    assert b"pcie_bdf=bd_p12x.elf" in cmd_bytes
    assert b"bootcpu=7" in cmd_bytes and b"cpus=7" in cmd_bytes
    assert b"root_apfs=" not in cmd_bytes and b"ufs_write=" not in cmd_bytes, "MUST be RAM-only!"

    out = bytearray(b"XNUP" + b"\0" * 4)
    new_items = []
    for name, res, data in items:
        if name == "kernel":
            data = v023_kernel
        elif name == "ramdisk":
            data = ram_bytes
        elif name == "cmdline":
            data = cmd_bytes
        elif name == "identity":
            data = b"v57\0"
        new_items.append((name, len(data)))
        nb = name.encode("ascii")[:15].ljust(16, b"\0")
        out += nb + struct.pack("<II", len(data), res) + data
        out += b"\0" * ((-len(data)) % 16)
    out += base[off:]
    struct.pack_into("<I", out, 4, len(out) - 8)

    assert len(out) <= PAYLOAD_CAP, f"payload {len(out)} exceeds PAYLOAD_CAP {PAYLOAD_CAP}"
    out_payload.write_bytes(out)
    print("  items:", new_items)
    print(f"  wrote {out_payload} ({len(out):,} B <= cap {PAYLOAD_CAP:,} B)")
    print(f"  payload sha256: {hashlib.sha256(out).hexdigest()}")
    print(f"  kernel  sha256: {hashlib.sha256(v023_kernel).hexdigest()}")
    print(f"  ramdisk sha256: {hashlib.sha256(ram_bytes).hexdigest()}")


def main():
    root_only = "--root-only" in sys.argv[1:]
    build_root_image(WORK_ROOT, WORK_ROOT_MANIFEST, OUT_ROOT_APFS)
    if not root_only:
        build_system_image(WORK_ROOT, WORK_SYS, WORK_SYS_MANIFEST, OUT_SYS_APFS)
    build_payload(OUT_ROOT_APFS, OUT_PAYLOAD)
    print("\nDONE! Ready for fastboot boot (RAM-only, zero flash).")


if __name__ == "__main__":
    main()
