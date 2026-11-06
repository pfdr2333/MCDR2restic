# -*- coding: utf-8 -*-
"""同步 restic release 资产到自建 CF R2 镜像并维护 WAF 白名单。

用法：python tools/sync_restic_mirror.py [版本...]（缺省 = restic_release.yml 固化版本 + restic 最新稳定版）

流程（每次同步，幂等，只增不删）：
  下载资产（github.com）→ SHA256 校验 → 对象已在镜像则跳过上传
  → R2 S3 SigV4 上传对象 → WAF 白名单自适应追加 → 端到端回读校验

凭证全部经环境变量流转，脚本不落任何字面量、不回显：
  CF_ACCOUNT_ID / CF_ZONE_ID / CF_WAF_TOKEN / CF_R2_ACCESS_KEY_ID / CF_R2_SECRET_ACCESS_KEY
可选：GITHUB_TOKEN（提升 GitHub API 限流配额）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = REPO_ROOT / "mcdr2restic" / "defaults" / "restic_release.yml"

CF_API_BASE = "https://api.cloudflare.com/client/v4"
R2_S3_ENDPOINT_TEMPLATE = "https://{account_id}.r2.cloudflarestorage.com"
DEFAULT_BUCKET = "mcdr2restic-dl"
DEFAULT_RULESET_ID = "880be8b2ffd54844b9d6896d299c52d8"
WAF_PHASE_ENTRYPOINT = (
    CF_API_BASE + "/zones/{zone_id}/rulesets/phases/http_request_firewall_custom/entrypoint"
)
WAF_RULESET_TEMPLATE = CF_API_BASE + "/zones/{zone_id}/rulesets/{ruleset_id}"

GITHUB_RELEASE_API = "https://api.github.com/repos/restic/restic/releases/latest"
GITHUB_DOWNLOAD_BASE = "https://github.com/restic/restic/releases/download"

PLATFORM_ASSET_SUFFIXES = ("linux_amd64.bz2", "windows_amd64.zip")
SUMS_ASSET_NAME = "SHA256SUMS"
CONTENT_TYPES = {
    SUMS_ASSET_NAME: "text/plain",
    ".bz2": "application/x-bzip2",
    ".zip": "application/zip",
}

PATH_TOKEN_PATTERN = re.compile(r"/v\d+\.\d+\.\d+/[A-Za-z0-9._\-]+")
DIGEST_PATTERN = re.compile(r"^([0-9a-f]{64})\s+\*?(.+)$", re.MULTILINE)
VERSION_PATTERN = re.compile(r"^version:\s*(\S+)\s*$", re.MULTILINE)
E2E_ATTEMPTS = 6
E2E_RETRY_SECONDS = 10


def log(message: str):
    print(message, flush=True)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit("missing required environment variable: {}".format(name))
    return value


def github_request(url: str) -> urllib.request.Request:
    headers = {"User-Agent": "MCDR2Restic-mirror-sync"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    return urllib.request.Request(url, headers=headers)


DEFAULT_REQUEST_UA = "MCDR2Restic/mcdr2restic"


def http_get_text(url: str, headers: dict | None = None) -> str:
    return http_get_bytes(url, headers).decode("utf-8", "replace")


def http_get_bytes(url: str, headers: dict | None = None) -> bytes:
    # 镜像站开着 Cloudflare 浏览器完整性检查，裸 Python-urllib 签名会被 1010 拒绝
    all_headers = {"User-Agent": DEFAULT_REQUEST_UA}
    if headers:
        all_headers.update(headers)
    request = urllib.request.Request(url, headers=all_headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def resolve_versions(manifest_path: Path) -> list:
    """固化版本优先，追加 restic 最新稳定版，去重保序。"""

    versions = []
    manifest_text = manifest_path.read_text(encoding="utf-8")
    pinned = VERSION_PATTERN.search(manifest_text)
    if pinned:
        versions.append(pinned.group(1))
    # 认证走 github_request：Actions 共享出口 IP 的匿名限额 60/h 极易撞满
    with urllib.request.urlopen(github_request(GITHUB_RELEASE_API), timeout=120) as response:
        release = json.loads(response.read().decode("utf-8"))
    latest = str(release.get("tag_name") or "").strip()
    if latest and latest not in versions:
        versions.append(latest)
    if not versions:
        raise SystemExit("no restic version resolved (manifest empty and GitHub API unavailable)")
    return versions


def asset_names_for(version: str) -> list:
    bare = version.lstrip("v")
    return ["restic_{}_{}".format(bare, suffix) for suffix in PLATFORM_ASSET_SUFFIXES] + [
        SUMS_ASSET_NAME
    ]


def mirror_url(version: str, name: str) -> str:
    base = os.environ.get("MIRROR_BASE_URL", "https://dl.m2r.pfdr.cc").rstrip("/")
    return "{}/{}/{}".format(base, version, name)


def download_github_assets(version: str, target_dir: Path) -> dict:
    """从 github.com 下载 SHA256SUMS 与平台资产，返回 {name: 本地路径}。"""

    base = "{}/{}".format(GITHUB_DOWNLOAD_BASE, version)
    paths = {}
    for name in asset_names_for(version):
        destination = target_dir / name
        log("downloading {}: {}".format(name, base + "/" + name))
        with urllib.request.urlopen(github_request(base + "/" + name), timeout=300) as response, open(
            destination, "wb"
        ) as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
        paths[name] = destination
    return paths


def parse_sums(text: str) -> dict:
    return {name: digest for digest, name in DIGEST_PATTERN.findall(text)}


def verify_downloaded_assets(paths: dict, manifest_path: Path, version: str):
    sums = parse_sums((paths[SUMS_ASSET_NAME]).read_text(encoding="utf-8", errors="replace"))
    for name, local_path in paths.items():
        if name == SUMS_ASSET_NAME:
            continue
        digest = hashlib.sha256(local_path.read_bytes()).hexdigest()
        expected = sums.get(name, "")
        if digest != expected:
            raise SystemExit(
                "sha256 mismatch for {}: expected {} got {}".format(name, expected, digest)
            )
        log("verified {}: {}".format(name, digest))
    # 交叉核对固化清单：清单哈希与官方 SHA256SUMS 不一致说明清单需要更新
    manifest_text = manifest_path.read_text(encoding="utf-8")
    for name, digest in sums.items():
        if name in manifest_text and digest not in manifest_text:
            log(
                "WARNING: manifest sha256 differs from official SHA256SUMS for {}".format(
                    name
                )
            )


def mirror_already_synced(version: str, github_sums_text: str) -> bool:
    url = mirror_url(version, SUMS_ASSET_NAME)
    try:
        return http_get_text(url) == github_sums_text
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return False
        # 其他状态（限流/5xx/网络异常）按未同步处理：重传同内容幂等安全，
        # 正确性最终由端到端哈希回读兜底
        log("sync check for {} inconclusive (http {}); will re-upload".format(version, exc.code))
        return False
    except OSError as exc:
        log("sync check for {} failed ({}); will re-upload".format(version, exc))
        return False


def upload_object(credentials: dict, version: str, name: str, local_path: Path):
    account_id = credentials["account_id"]
    bucket = credentials["bucket"]
    access_key = credentials["access_key"]
    secret_key = credentials["secret_key"]
    content_type = CONTENT_TYPES.get(name) or CONTENT_TYPES.get(
        Path(name).suffix, "application/octet-stream"
    )
    url = "{}/{}/{}/{}".format(
        R2_S3_ENDPOINT_TEMPLATE.format(account_id=account_id), bucket, version, name
    )
    result = subprocess.run(
        [
            "curl",
            "-sS",
            "-X",
            "PUT",
            "--aws-sigv4",
            "aws:amz:auto:s3",
            "--user",
            "{}:{}".format(access_key, secret_key),
            "-H",
            "x-amz-content-sha256: UNSIGNED-PAYLOAD",
            "-H",
            "Content-Type: {}".format(content_type),
            "--data-binary",
            "@{}".format(local_path),
            "-w",
            "\n%{http_code}",
            url,
        ],
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "").rsplit("\n", 1)
    status = output[-1].strip() if output else ""
    body = output[0] if len(output) == 2 else ""
    if status != "200":
        raise SystemExit(
            "upload failed for {}/{}: http {} {}".format(
                version, name, status, (result.stderr or body)[:500]
            )
        )
    log("uploaded {}: {}".format(version, name))


def fetch_waf_rules(zone_id: str, waf_token: str) -> dict:
    url = WAF_PHASE_ENTRYPOINT.format(zone_id=zone_id)
    request = urllib.request.Request(
        url, headers={"Authorization": "Bearer {}".format(waf_token)}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("success"):
        raise SystemExit("fetch WAF entrypoint failed: {}".format(payload.get("errors")))
    return payload["result"]


def put_waf_rules(zone_id: str, waf_token: str, ruleset_id: str, rules: list):
    url = WAF_RULESET_TEMPLATE.format(zone_id=zone_id, ruleset_id=ruleset_id)
    request = urllib.request.Request(
        url,
        data=json.dumps({"rules": rules}).encode("utf-8"),
        headers={
            "Authorization": "Bearer {}".format(waf_token),
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("success"):
        raise SystemExit("update WAF ruleset failed: {}".format(payload.get("errors")))


def append_waf_paths(rules: list, new_paths: list) -> tuple:
    """按既有规则结构自适应追加路径；返回 (新规则数组, 实际追加数)。

    兼容两种白名单形态：单条规则内 `uri.path in {...}` 枚举多路径，或
    每路径一条规则。既无法识别又无既有镜像路径时直接报错，不做猜测。
    """

    existing = set()
    for rule in rules:
        existing.update(PATH_TOKEN_PATTERN.findall(str(rule.get("expression", ""))))
    todo = [path for path in new_paths if path not in existing]
    if not todo:
        return rules, 0

    rules_with_paths = [
        rule for rule in rules if PATH_TOKEN_PATTERN.findall(str(rule.get("expression", "")))
    ]
    if not rules_with_paths:
        raise SystemExit(
            "no existing mirror path rule found in WAF ruleset; refusing to guess structure"
        )

    combined = next(
        (
            rule
            for rule in rules_with_paths
            if len(PATH_TOKEN_PATTERN.findall(str(rule.get("expression", "")))) >= 2
        ),
        None,
    )
    if combined is not None:
        expression = str(combined.get("expression", ""))
        for path in todo:
            expression, count = re.subn(
                r"(http\.request\.uri\.path\s+in\s*\{)([^{}]*)(\})",
                # 默认参固化循环变量：subn 虽然当次迭代即调用，但显式绑定
                # 才能通过 B023 的晚绑定检查
                lambda match, path=path: '{}{} "{}" }}'.format(
                    match.group(1), match.group(2).rstrip(), path
                ),
                expression,
                count=1,
            )
            if count != 1:
                raise SystemExit(
                    "failed to append path into combined rule expression: {}".format(
                        combined.get("expression")
                    )
                )
        combined["expression"] = expression
        return rules, len(todo)

    template = rules_with_paths[0]
    template_paths = PATH_TOKEN_PATTERN.findall(str(template.get("expression", "")))
    appended = 0
    for path in todo:
        clone = {key: value for key, value in template.items() if key != "id"}
        clone["expression"] = str(template["expression"]).replace(template_paths[0], path)
        if clone.get("description") and template_paths[0] in str(clone["description"]):
            clone["description"] = str(clone["description"]).replace(template_paths[0], path)
        rules.append(clone)
        appended += 1
    return rules, appended


def e2e_verify(version: str, paths: dict):
    for attempt in range(1, E2E_ATTEMPTS + 1):
        pending = []
        for name, local_path in paths.items():
            expected = hashlib.sha256(local_path.read_bytes()).hexdigest()
            try:
                remote = http_get_bytes(mirror_url(version, name))
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                pending.append("{} ({})".format(name, exc))
                continue
            if hashlib.sha256(remote).hexdigest() == expected:
                log("e2e ok: {}/{}".format(version, name))
            else:
                pending.append("{} (digest mismatch)".format(name))
        if not pending:
            return
        log(
            "e2e attempt {}/{} failed for: {}; retrying".format(
                attempt, E2E_ATTEMPTS, ", ".join(pending)
            )
        )
        if attempt < E2E_ATTEMPTS:
            time.sleep(E2E_RETRY_SECONDS)
    raise SystemExit("e2e verification failed for {}/{}".format(version, pending))


def sync_version(version: str, manifest_path: Path, credentials: dict):
    log("== syncing restic {} ==".format(version))
    names = asset_names_for(version)
    with tempfile.TemporaryDirectory(prefix="restic-mirror-") as temp_dir:
        target_dir = Path(temp_dir)
        paths = download_github_assets(version, target_dir)
        verify_downloaded_assets(paths, manifest_path, version)

        synced = mirror_already_synced(version, paths[SUMS_ASSET_NAME].read_text(encoding="utf-8", errors="replace"))
        if synced:
            log("mirror already has {}: skipping upload".format(version))
        else:
            # SUMS 必须一并上传：它同时是 mirror_already_synced 的幂等标记，
            # 不上传则跳过检查永远 404，每次同步都会白做一遍下载+上传
            for name in names:
                upload_object(credentials, version, name, paths[name])

        waf_paths = ["/{}/{}".format(version, name) for name in names]
        ruleset = fetch_waf_rules(credentials["zone_id"], credentials["waf_token"])
        rules, appended = append_waf_paths(ruleset.get("rules", []), waf_paths)
        if appended:
            # 顺序铁律：对象已上传后再更新白名单，避免新版本窗口内源站 404 回源
            put_waf_rules(
                credentials["zone_id"], credentials["waf_token"], credentials["ruleset_id"], rules
            )
            log("appended {} WAF whitelist paths for {}".format(appended, version))
        else:
            log("WAF whitelist already contains all paths for {}".format(version))

        e2e_verify(version, paths)


def main(argv: list) -> int:
    manifest_path = Path(os.environ.get("MANIFEST_PATH", DEFAULT_MANIFEST_PATH))
    credentials = {
        "account_id": require_env("CF_ACCOUNT_ID"),
        "zone_id": require_env("CF_ZONE_ID"),
        "waf_token": require_env("CF_WAF_TOKEN"),
        "access_key": require_env("CF_R2_ACCESS_KEY_ID"),
        "secret_key": require_env("CF_R2_SECRET_ACCESS_KEY"),
        "bucket": os.environ.get("CF_BUCKET", DEFAULT_BUCKET),
        "ruleset_id": os.environ.get("CF_RULESET_ID", DEFAULT_RULESET_ID),
    }
    versions = argv or resolve_versions(manifest_path)

    failed = []
    for version in versions:
        try:
            sync_version(version, manifest_path, credentials)
        except (SystemExit, Exception) as exc:
            # 单版本失败只记录不中断，其余版本继续同步
            failed.append(version)
            log("ERROR syncing {}: {}: {}".format(version, type(exc).__name__, exc))
    if failed:
        log("sync failed for: {}".format(", ".join(failed)))
        return 1
    log("mirror sync completed for: {}".format(", ".join(versions)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
