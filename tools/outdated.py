#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "rich>=13.0.0",
# ]
# ///
"""Check and update exact container tool pins in mise.toml and .env."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm

console = Console()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check exact mise and image tool pins.")
    parser.add_argument("--update", action="store_true", help="Refresh exact tool pins to their latest releases.")
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    config_path = repo_root / "mise.toml"
    secrets_path = repo_root / ".secrets"
    if not env_path.exists() or not config_path.exists():
        console.print("[red]Both .env and mise.toml are required.[/red]")
        return 1
    try:
        update_mise_pins(config_path, update=args.update)
        update_android_pins(repo_root, update=args.update)
        update_image_pins(env_path, update=args.update)
        env_values = read_env(env_path)
        secrets = read_env(secrets_path) if secrets_path.exists() else {}
        latest_gopls = latest_go_module_version("golang.org/x/tools/gopls")
        latest_debian = latest_debian_slim_tag(env_values["DEBIAN_TAG"], secrets=secrets)
        handle_latest_versions(
            env_path=env_path,
            current_debian=env_values["DEBIAN_TAG"], latest_debian=latest_debian,
            current_gopls=env_values["GOPLS_VERSION"], latest_gopls=latest_gopls,
            update=args.update,
        )
    except (RuntimeError, urllib.error.URLError, subprocess.CalledProcessError) as error:
        console.print(f"[red]Unable to check for updates:[/red] {error}")
        return 1
    return 0


def exact_version(value: str) -> bool:
    """Accept numbered releases, including Java vendor/build suffixes."""
    return re.fullmatch(r"(?:temurin-)?[0-9]+\.[0-9]+(?:\.[0-9]+)*(?:[+.-][0-9A-Za-z.]+)?", value) is not None


def write_mise_version(path: Path, key: str, version: str) -> None:
    """Replace a version while retaining backend options and formatting."""
    if not exact_version(version):
        raise RuntimeError(f"Not an exact release for {key}: {version!r}")
    escaped = re.escape(json.dumps(key))
    pattern = rf'^(\s*{escaped}\s*=\s*(?:\{{\s*version\s*=\s*)?)"[^"\n]+"'
    content, count = re.subn(pattern, lambda m: m[1] + json.dumps(version), path.read_text(), flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"Expected one pin for {key} in {path}")
    path.write_text(content)


def update_mise_pins(path: Path, *, update: bool) -> None:
    """Resolve latest versions without installing tools or creating a lockfile."""
    config = tomllib.loads(path.read_text())
    subprocess.run(["mise", "trust", str(path)], cwd=path.parent, check=True)

    def resolve(key: str) -> tuple[str, str]:
        request = "java@temurin-21" if key == "java" else key
        result = subprocess.check_output(["mise", "latest", request], cwd=path.parent, text=True).strip()
        if not exact_version(result):
            raise RuntimeError(f"Invalid release returned for {key}: {result!r}")
        if key == "java" and not result.startswith("temurin-21."):
            raise RuntimeError(f"Refusing to upgrade Java beyond 21: {result}")
        return key, result

    # Resolve independently; write the pin file only from the main thread.
    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(pool.map(resolve, config["tools"]))
    for key, latest in versions:
        pin = config["tools"][key]
        current = pin["version"] if isinstance(pin, dict) else pin
        if not exact_version(current):
            raise RuntimeError(f"Expected an exact pin for {key}: {current}")
        # A publisher may mark an older maintenance release as its latest.
        newer = tuple(map(int, re.findall(r"\d+", latest))) > tuple(map(int, re.findall(r"\d+", current)))
        if latest != current and not newer:
            console.print(f"{key}: retaining {current}; publisher latest is older ({latest})")
            continue
        console.print(f"{key}: {current} -> {latest}")
        if update and newer:
            write_mise_version(path, key, latest)


def latest_github_release(repository: str) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/releases/latest",
        headers={"User-Agent": "sakura-dev-tools"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["tag_name"].removeprefix("v")


def update_image_pins(env_path: Path, *, update: bool) -> None:
    """Resolve tools installed by dedicated image stages from their upstreams."""
    with urllib.request.urlopen("https://crates.io/api/v1/crates/cargo-nextest", timeout=30) as response:
        nextest = json.load(response)["crate"]["max_stable_version"]
    # Git has numbered tags but does not publish GitHub release objects.
    tags = subprocess.check_output(
        ["git", "ls-remote", "--tags", "--refs", "https://github.com/git/git.git"], text=True,
    )
    git_versions = re.findall(r"refs/tags/v([0-9]+\.[0-9]+\.[0-9]+)$", tags, re.MULTILINE)
    if not git_versions:
        raise RuntimeError("No stable Git tags found")
    git = max(git_versions, key=lambda version: tuple(map(int, version.split("."))))
    with urllib.request.urlopen("https://static.rust-lang.org/dist/channel-rust-nightly.toml", timeout=30) as response:
        nightly = "nightly-" + tomllib.loads(response.read().decode())["date"]
    values = {"MISE_VERSION": latest_github_release("jdx/mise"),
              "NEXTEST_VERSION": nextest, "GIT_VERSION": git,
              "RUST_NIGHTLY_VERSION": nightly}
    current = read_env(env_path)
    for key, value in values.items():
        console.print(f"{key}: {current.get(key, '<unset>')} -> {value}")
        if update and current.get(key) != value:
            write_env_value(env_path, key, value)


def update_android_pins(repo_root: Path, *, update: bool) -> None:
    """Resolve stable Android packages from Google repository metadata."""
    with urllib.request.urlopen(
        "https://dl.google.com/android/repository/repository2-1.xml", timeout=30,
    ) as response:
        root = ET.parse(response).getroot()
    packages = {}
    for package in root.findall("remotePackage"):
        channel = package.find("channelRef")
        if channel is not None and channel.get("ref") != "channel-0":
            continue
        revision = package.find("revision")
        if revision is None or revision.find("preview") is not None:
            continue
        version = tuple(int(revision.findtext(part, "0")) for part in ("major", "minor", "micro"))
        path = package.get("path", "")
        if path not in packages or version > packages[path][0]:
            packages[path] = (version, package)

    def latest(prefix: str) -> str:
        paths = [path for path in packages if path.startswith(prefix)
                 and re.fullmatch(r"[0-9.]+", path.removeprefix(prefix))]
        if not paths:
            raise RuntimeError(f"No stable Android package for {prefix}")
        return max(paths, key=lambda path: tuple(int(n) for n in path.removeprefix(prefix).split(".")))

    pins = {"packages": [latest(prefix) for prefix in
                        ("platforms;android-", "build-tools;", "ndk;")]}
    for name, path in (("command_line_tools", latest("cmdline-tools;")),
                       ("platform_tools", "platform-tools"), ("emulator", "emulator")):
        version, package = packages[path]
        archives = [a.find("complete") for a in package.findall("archives/archive")
                    if a.findtext("host-os") == "linux"]
        if len(archives) != 1 or archives[0] is None:
            raise RuntimeError(f"Expected one Linux archive for {path}")
        archive = archives[0]
        pins[name] = {"version": ".".join(map(str, version)),
                      "url": "https://dl.google.com/android/repository/" + archive.findtext("url"),
                      "sha1": archive.findtext("checksum")}
    api = pins["packages"][0].split("android-", 1)[1]
    pins["system_image"] = f"system-images;android-{api};google_apis;x86_64"
    # Confirm that this API has a stable emulator image before updating pins.
    with urllib.request.urlopen(
        "https://dl.google.com/android/repository/sys-img/google_apis/sys-img2-1.xml", timeout=30,
    ) as response:
        images = ET.parse(response).getroot()
    if not any(p.get("path") == pins["system_image"] and
               p.find("revision/preview") is None for p in images.findall("remotePackage")):
        raise RuntimeError(f"No stable system image for Android API {api}")
    values = {"ANDROID_PACKAGES": ",".join(pins["packages"]),
              "ANDROID_SYSTEM_IMAGE": pins["system_image"]}
    for name in ("command_line_tools", "platform_tools", "emulator"):
        for key, value in pins[name].items():
            values[f"ANDROID_{name.upper()}_{key.upper()}"] = value
    env_path = repo_root / ".env"
    current = read_env(env_path)
    for key, value in values.items():
        if current.get(key) != value:
            console.print(f"[yellow]{key}:[/yellow] {current.get(key, '<unset>')} -> {value}")
            if update:
                write_env_value(env_path, key, value)


def read_env(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE assignments from .env."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def write_env_value(path: Path, key: str, value: str) -> None:
    """Set or append one simple KEY=VALUE assignment in .env."""
    lines = path.read_text().splitlines()
    output: list[str] = []
    found = False

    for line in lines:
        if line.startswith(f"{key}="):
            output.append(f"{key}={value}")
            found = True
        else:
            output.append(line)

    if not found:
        output.append(f"{key}={value}")

    path.write_text("\n".join(output) + "\n")


def latest_go_module_version(module: str) -> str:
    """Resolve the latest tagged version of a Go module."""
    url = f"https://proxy.golang.org/{module}/@latest"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.load(response)

    version = payload.get("Version", "")
    if not version.startswith("v"):
        raise RuntimeError(f"invalid version returned for {module!r}: {version!r}")
    return version.removeprefix("v")


@dataclass(frozen=True, order=True)
class DebianSlimTag:
    """A numbered Debian slim tag, without any digest suffix."""

    major: int
    minor: int
    tag: str


def latest_debian_slim_tag(
    current_tag: str,
    *,
    secrets: dict[str, str],
) -> DebianSlimTag:
    """Return the latest numbered Debian slim tag from the container registry."""
    current = parse_debian_slim_tag(strip_digest(current_tag))
    if current is None:
        msg = f"DEBIAN_TAG must be a numbered Debian slim tag, got {current_tag!r}"
        raise RuntimeError(msg)

    url: str | None = (
        "https://hub.docker.com/v2/namespaces/library/repositories/debian/tags"
        "?page_size=100&name=-slim"
    )
    best: DebianSlimTag | None = None
    authorization = docker_hub_authorization(secrets)

    while url:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.5",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                **(
                    {"Authorization": authorization}
                    if authorization is not None
                    else {}
                ),
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64; rv:142.0) "
                    "Gecko/20100101 Firefox/142.0"
                ),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 403:
                if authorization is None:
                    raise RuntimeError(
                        "Docker Hub denied the unauthenticated tag request (HTTP 403). "
                        "Copy .secrets.template to .secrets, set a Docker Hub username "
                        "and read-only PAT, then run outdated-update again.",
                    ) from error
                raise RuntimeError(
                    "Docker Hub denied the authenticated tag request (HTTP 403). "
                    "Check that DOCKERHUB_USERNAME and DOCKERHUB_PAT in .secrets "
                    "are valid and that the PAT has read access.",
                ) from error
            raise RuntimeError(
                f"Docker Hub returned HTTP {error.code} while listing Debian tags "
                f"({url}).",
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"could not reach Docker Hub while listing Debian tags ({url}): "
                f"{error.reason}",
            ) from error

        for result in payload.get("results", []):
            candidate = parse_debian_slim_tag(result.get("name", ""))
            if candidate is None or candidate.major < current.major:
                continue
            if best is None or candidate > best:
                best = candidate

        url = payload.get("next")

    if best is None:
        msg = f"no numbered Debian slim tags found at or after {current.tag!r}"
        raise RuntimeError(msg)
    return best


def docker_hub_authorization(secrets: dict[str, str]) -> str | None:
    """Exchange an optional Docker Hub PAT for an API bearer token."""
    username = secrets.get("DOCKERHUB_USERNAME", "").strip()
    pat = secrets.get("DOCKERHUB_PAT", "").strip()
    if not username and not pat:
        return None
    if not username or not pat:
        raise RuntimeError(
            "Docker Hub credentials are incomplete in .secrets. Set both "
            "DOCKERHUB_USERNAME and DOCKERHUB_PAT, or remove both entries.",
        )

    request = urllib.request.Request(
        "https://hub.docker.com/v2/auth/token",
        data=json.dumps({"identifier": username, "secret": pat}).encode(),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            token = json.load(response).get("access_token")
    except (urllib.error.HTTPError, urllib.error.URLError) as error:
        detail = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else str(error.reason)
        raise RuntimeError(
            f"Docker Hub authentication failed ({detail}). Check the credentials in .secrets.",
        ) from error
    if not token:
        raise RuntimeError("Docker Hub authentication returned no access token.")
    return f"Bearer {token}"


def strip_digest(tag: str) -> str:
    """Return a container tag without an optional @sha256 digest suffix."""
    return tag.split("@", 1)[0]


def parse_debian_slim_tag(tag: str) -> DebianSlimTag | None:
    """Parse numbered Debian tags such as 13.5-slim or 14-slim."""
    match = re.fullmatch(r"(?P<major>\d+)(?:\.(?P<minor>\d+))?-slim", tag)
    if not match:
        return None
    return DebianSlimTag(
        major=int(match.group("major")),
        minor=int(match.group("minor") or 0),
        tag=tag,
    )


def debian_manifest_digest(tag: str) -> str:
    """Resolve the container registry digest for a Debian tag."""
    token_url = (
        "https://auth.docker.io/token"
        "?service=registry.docker.io&scope=repository:library/debian:pull"
    )
    with urllib.request.urlopen(token_url, timeout=30) as response:
        token = json.load(response)["token"]

    manifest_url = f"https://registry-1.docker.io/v2/library/debian/manifests/{tag}"
    request = urllib.request.Request(
        manifest_url,
        method="HEAD",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": ", ".join(
                [
                    "application/vnd.oci.image.index.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.docker.distribution.manifest.v2+json",
                ],
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        digest = response.headers.get("Docker-Content-Digest")

    if not digest:
        msg = f"could not resolve digest for debian:{tag}"
        raise RuntimeError(msg)
    return digest


def handle_latest_versions(
    *,
    env_path: Path,
    current_debian: str,
    latest_debian: DebianSlimTag,
    current_gopls: str,
    latest_gopls: str,
    update: bool,
) -> None:
    """Report and optionally update the latest Debian and gopls versions."""
    latest_tag = latest_debian.tag

    with console.status(f"resolving debian:{latest_tag} digest ...", spinner="dots"):
        latest_pinned = f"{latest_tag}@{debian_manifest_digest(latest_tag)}"

    console.print(f"Latest Debian: {current_debian}, {latest_pinned}")
    console.print(f"Latest Gopls: {current_gopls}, {latest_gopls}")

    debian_changed = current_debian != latest_pinned
    gopls_changed = current_gopls != latest_gopls
    if not debian_changed and not gopls_changed:
        console.print("[green]All latest versions are selected.[/green]")
        return

    if not update:
        return

    if not Confirm.ask("Update to Latest", default=False):
        return

    if debian_changed:
        write_env_value(env_path, "DEBIAN_TAG", latest_pinned)
        console.print(
            f"[green]updated DEBIAN_TAG:[/green] {current_debian} -> {latest_pinned}"
        )
    if gopls_changed:
        write_env_value(env_path, "GOPLS_VERSION", latest_gopls)
        console.print(
            f"[green]updated GOPLS_VERSION:[/green] {current_gopls} -> {latest_gopls}"
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
