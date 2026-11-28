"""Install the adapter in a disposable Harness profile and call the Windows gateway."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def smoke(cli: Path, install_dir: Path, adapter_archive: Path, pnpm: Path | None) -> None:
    with tempfile.TemporaryDirectory(prefix="multisim-dsh-") as temporary:
        root = Path(temporary)
        home = root / "home"
        result_file = root / "result.json"
        env = {**os.environ, "DSH_HOME": str(home), "MULTISIM_DSH_PROBE_RESULT": str(result_file)}
        if pnpm:
            env["PATH"] = f"{pnpm}{os.pathsep}{env['PATH']}"
        command = [shutil.which("node") or "node", str(cli)]
        plugin = command + ["plugin", "--profile", "sdk"]

        subprocess.run([*plugin, "add", str(adapter_archive)], env=env, check=True, timeout=120)
        patch = home / "profiles" / "sdk" / "cordis.patch.yml"
        probe = Path(__file__).with_name("dsh_compat_probe.mjs").resolve().as_uri()
        patch.write_text(
            "- id: multisim-mcp\n  config:\n"
            f"    installDir: {json.dumps(str(install_dir), ensure_ascii=False)}\n"
            f"- insert:\n    - id: multisim-dsh-probe\n      name: {json.dumps(probe)}\n",
            encoding="utf-8",
        )
        log_file = root / "harness.log"
        with log_file.open("wb") as log:
            with subprocess.Popen([*command, "--profile", "sdk"], env=env, stdin=subprocess.PIPE,
                                  stdout=log, stderr=log) as harness:
                deadline = time.monotonic() + 150
                try:
                    while not result_file.exists() and harness.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.2)
                finally:
                    harness.stdin.close()
                    try:
                        harness.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        harness.kill()
                        harness.wait()

        result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else {}
        if not result.get("ok"):
            print(log_file.read_text(encoding="utf-8", errors="replace"))
            raise RuntimeError(result.get("error", "Harness exited or timed out before the probe finished"))

        patch.write_text("[]\n", encoding="utf-8")
        subprocess.run([*plugin, "remove", "dsh-multisim-mcp"], env=env, check=True, timeout=60)
        discovered = subprocess.check_output([*command, "--profile", "sdk", "--dump-config"],
                                             env=env, text=True, encoding="utf-8", timeout=30)
        if "name: dsh-multisim-mcp" in discovered:
            raise RuntimeError("Harness still listed the adapter after uninstall")
        print("Harness installed the adapter, called gateway_status, unloaded its tools, and removed it.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True, type=Path)
    parser.add_argument("--install-dir", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--pnpm-bin", type=Path)
    args = parser.parse_args()
    smoke(args.cli.resolve(), args.install_dir.resolve(), args.archive.resolve(), args.pnpm_bin)
