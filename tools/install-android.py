#!/usr/bin/env python3
"""Install the pinned Android packages and prepare phone and tablet AVDs."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request


def main() -> None:
    pins = dict(line.split("=", 1) for line in
                Path(__file__).with_name(".env").read_text().splitlines()
                if line and not line.startswith("#"))
    packages = pins["ANDROID_PACKAGES"].split(",")
    sdk = Path(os.environ["ANDROID_HOME"])
    with tempfile.TemporaryDirectory() as temporary:
        for name in ("command_line_tools", "platform_tools", "emulator"):
            prefix = f"ANDROID_{name.upper()}"
            url = pins[f"{prefix}_URL"]
            archive = Path(temporary) / f"{name}.zip"
            urllib.request.urlretrieve(url, archive)
            with archive.open("rb") as source:
                actual = hashlib.file_digest(source, "sha1").hexdigest()
            if actual != pins[f"{prefix}_SHA1"]:
                raise RuntimeError(f"Checksum mismatch for {url}")
            # The system unzip preserves executable permissions in SDK archives.
            destination = Path(temporary) / "cli" if name == "command_line_tools" else sdk
            subprocess.run(["unzip", "-q", str(archive), "-d", str(destination)], check=True)
            if name == "command_line_tools":
                (sdk / "cmdline-tools").mkdir(exist_ok=True)
                shutil.move(str(destination / "cmdline-tools"), sdk / "cmdline-tools/latest")
            archive.unlink()

    manager = str(sdk / "cmdline-tools/latest/bin/sdkmanager")
    subprocess.run([manager, f"--sdk_root={sdk}", "--licenses"],
                   input="y\n" * 100, text=True, check=True)
    subprocess.run([manager, f"--sdk_root={sdk}", "--channel=0",
                    *packages, pins["ANDROID_SYSTEM_IMAGE"]], check=True)
    ndk = next(package.split(";", 1)[1] for package in packages if package.startswith("ndk;"))
    Path(os.environ["ANDROID_NDK_HOME"]).symlink_to(sdk / "ndk" / ndk)

    avdmanager = str(sdk / "cmdline-tools/latest/bin/avdmanager")
    for name, device in (("android-phone", "pixel_7"), ("android-tablet", "pixel_tablet")):
        subprocess.run([avdmanager, "create", "avd", "--force", "--name", name,
                        "--package", pins["ANDROID_SYSTEM_IMAGE"], "--device", device],
                       input="no\n", text=True, check=True)
        config = Path(os.environ["ANDROID_AVD_HOME"]) / f"{name}.avd/config.ini"
        with config.open("a") as output:
            output.write("\nhw.gpu.enabled=yes\nhw.gpu.mode=swiftshader_indirect\nhw.keyboard=yes\n")
    shutil.rmtree(Path(os.environ["ANDROID_USER_HOME"]) / "cache", ignore_errors=True)


if __name__ == "__main__":
    main()
