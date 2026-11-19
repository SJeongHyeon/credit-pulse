"""배포 주소가 main의 코드인지 확인한다(D10: 'PR 대상이 main인지, 배포 주소가 그 코드인지').

왜 필요한가: Cloud Run을 `gcloud run deploy --source .`로 올리면 그때 작업 폴더의 파일이 올라간다.
git 커밋과 연결된 표시가 없어서, 배포가 main과 같은 코드인지는 올라간 소스 묶음을 직접 견줘야 알 수 있다.

하는 일(읽기 전용)
  1. 지금 트래픽을 받는 리비전과, 그 리비전을 만든 소스 묶음(zip)의 위치를 gcloud로 읽는다.
  2. 소스 묶음을 임시 폴더에 받아 푼다.
  3. 묶음의 파일마다 견줄 커밋(기본 origin/main)의 같은 파일과 sha256을 견준다(줄 끝은 LF로 맞춘 뒤).
     `data/service/`(배포 때만 넣는 검색 산출물)는 git에 없으므로 견주지 않는다.
  4. 완전히 같은 커밋이 최근 이력에 있으면 그 커밋도 찾아 준다.

필요한 것: 이 GCP 프로젝트를 읽을 수 있는 gcloud 로그인, git. 비밀값은 읽지 않는다.

사용: python -X utf8 scripts/check_deploy_source.py [--ref origin/main] [--output docs/evidence/d10/deploy_vs_main.json]
종료 코드: 서비스 코드(backend/, Dockerfile)가 같으면 0, 다르면 1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE, REGION, PROJECT = "creditpulse-api", "asia-northeast3", "creditpulse-513108"
SKIP_PREFIX = "data/service/"
CODE_PREFIXES = ("backend/", "Dockerfile", "db/")


def run(command: list[str], binary: bool = False):
    use_shell = command[0] == "gcloud" and sys.platform == "win32"  # Windows의 gcloud는 .cmd라 셸을 거쳐야 한다
    done = subprocess.run(command, cwd=ROOT, capture_output=True, shell=use_shell)
    if done.returncode != 0:
        raise SystemExit(f"명령 실패: {' '.join(command)}\n{done.stderr.decode('utf-8', 'replace')[-800:]}")
    return done.stdout if binary else done.stdout.decode("utf-8", "replace").strip()


def normalized_sha256(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def differing_files(deployed: dict[str, str], ref: str) -> list[str]:
    tree = {}
    for line in run(["git", "ls-tree", "-r", ref]).split("\n"):
        meta, name = line.split("\t", 1)
        tree[name.strip('"')] = meta.split()[2]
    # 한글 파일명은 ls-tree가 8진수로 적으므로, 못 찾은 이름은 git show로 한 번 더 확인한다
    different = []
    for name, digest in deployed.items():
        blob = tree.get(name)
        if blob is None:
            shown = subprocess.run(["git", "show", f"{ref}:{name}"], cwd=ROOT, capture_output=True)
            if shown.returncode != 0 or normalized_sha256(shown.stdout) != digest:
                different.append(name)
            continue
        if normalized_sha256(run(["git", "cat-file", "blob", blob], binary=True)) != digest:
            different.append(name)
    return sorted(different)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--search", type=int, default=40, help="완전히 같은 커밋을 찾을 때 볼 최근 커밋 수")
    parser.add_argument("--output")
    args = parser.parse_args()

    base = ["--region", REGION, "--project", PROJECT]
    revision = run(["gcloud", "run", "services", "describe", SERVICE, *base, "--format=value(status.traffic[0].revisionName)"])
    described = json.loads(run(["gcloud", "run", "revisions", "describe", revision, *base, "--format=json"]))
    created = described["metadata"]["creationTimestamp"]
    location = json.loads(described["metadata"]["annotations"]["run.googleapis.com/build-source-location"])
    source = next(iter(location.values())).split("#")[0]

    workdir = Path(tempfile.mkdtemp(prefix="deploy_source_"))
    try:
        run(["gcloud", "storage", "cp", source, str(workdir / "source.zip"), "--project", PROJECT])
        deployed = {}
        with zipfile.ZipFile(workdir / "source.zip") as archive:
            for item in archive.infolist():
                if item.is_dir() or item.filename.startswith(SKIP_PREFIX):
                    continue
                deployed[item.filename] = normalized_sha256(archive.read(item))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    different = differing_files(deployed, args.ref)
    code_different = [name for name in different if name.startswith(CODE_PREFIXES)]
    exact = None
    for line in run(["git", "log", args.ref, "--format=%h %cI %s", f"-{args.search}"]).split("\n"):
        if not differing_files(deployed, line.split()[0]):
            exact = line
            break
    record = {
        "what": "배포 리비전의 소스 묶음과 git 커밋의 파일별 sha256 비교(줄 끝 LF 기준, data/service/ 제외)",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "service": SERVICE, "revision": revision, "revision_created_utc": created,
        "compared_ref": args.ref, "compared_commit": run(["git", "rev-parse", "--short", args.ref]),
        "deployed_files_compared": len(deployed),
        "files_different": different, "service_code_different": code_different,
        "same_service_code": not code_different,
        "exactly_matching_commit": exact,
    }
    text = json.dumps(record, ensure_ascii=False, indent=2)
    if args.output:
        (ROOT / args.output).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / args.output).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0 if not code_different else 1


if __name__ == "__main__":
    raise SystemExit(main())
