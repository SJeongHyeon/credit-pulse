"""새 clone에서 `docker compose up --build`가 뜰 때까지의 시간을 잰다(G2: 한 줄 기동, 키 없음).

왜 필요한가: README에 적은 기동 시간(109초, 11/6)은 한 번 손으로 잰 값이고 그 뒤 코드가 많이 바뀌었다.
평가자가 같은 방법으로 다시 잴 수 있게 스크립트와 원시 기록을 남긴다(11/13 리뷰 코멘트).

하는 일
  1. 원격 저장소의 지정한 브랜치를 임시 폴더에 새로 clone한다(.env 없음, API 키 없음).
  2. 그 폴더에서 `docker compose -p <이름> up --build -d`를 실행하고 `GET /health`가 200이 될 때까지 기다린다.
  3. 걸린 시간(clone, 기동 명령, health 200까지)과 loader 로그 끝부분을 JSON으로 남긴다.
  4. 컨테이너·볼륨·이 실행이 만든 이미지를 지운다(--keep을 주면 남긴다).

다른 프로젝트와 겹치지 않게 프로젝트 이름과 포트를 따로 쓴다(기본 API 18081, Postgres 55437).
필요한 것: git, docker(Compose v2). 유료 호출·API 키 없음.

사용: python -X utf8 scripts/measure_fresh_clone_boot.py [--branch main] [--output docs/evidence/g2_fresh_clone_boot.json]
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path | None = None, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and done.returncode != 0:
        raise SystemExit(f"명령 실패: {' '.join(command)}\n{done.stdout[-1500:]}\n{done.stderr[-1500:]}")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--remote", help="clone할 주소(기본: 이 저장소의 origin)")
    parser.add_argument("--project", default="proj3fresh")
    parser.add_argument("--api-port", type=int, default=18081)
    parser.add_argument("--postgres-port", type=int, default=55437)
    parser.add_argument("--timeout", type=int, default=900, help="health를 기다리는 최대 초(게이트 기준 10분 + 여유)")
    parser.add_argument("--output", default="docs/evidence/g2_fresh_clone_boot.json")
    parser.add_argument("--keep", action="store_true", help="끝난 뒤 컨테이너·이미지를 지우지 않는다")
    parser.add_argument("--no-cache", action="store_true",
                        help="이미지를 빌드 캐시 없이 만든다(이 컴퓨터에 남은 캐시의 도움을 빼, 처음 받는 컴퓨터에 가까운 조건)")
    args = parser.parse_args()

    remote = args.remote or run(["git", "remote", "get-url", "origin"], cwd=ROOT).stdout.strip()
    workdir = Path(tempfile.mkdtemp(prefix="proj3_fresh_clone_"))
    clone_dir = workdir / "repo"
    env = {**os.environ, "API_PORT": str(args.api_port), "POSTGRES_PORT": str(args.postgres_port)}
    for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "VOYAGE_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "DATABASE_URL"):
        env.pop(key, None)  # 키 없는 평가자 환경과 같게
    compose = ["docker", "compose", "-p", args.project]
    record: dict = {"what": "새 clone에서 docker compose up --build → /health 200까지의 시간(G2)", "branch": args.branch,
                    "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "machine": {"platform": platform.platform(), "docker": run(["docker", "--version"]).stdout.strip()}}
    try:
        started = time.perf_counter()
        run(["git", "clone", "--quiet", "--depth", "1", "--branch", args.branch, remote, str(clone_dir)])
        record["clone_seconds"] = round(time.perf_counter() - started, 1)
        record["commit"] = run(["git", "rev-parse", "--short", "HEAD"], cwd=clone_dir).stdout.strip()
        record["env_file_present"] = (clone_dir / ".env").exists()

        boot_started = time.perf_counter()
        record["build_cache"] = "없음(--no-cache)" if args.no_cache else "이 컴퓨터에 남은 캐시 사용"
        if args.no_cache:  # 기동 시간에 포함한다(평가자는 처음에 이미지를 만들어야 한다)
            built = run([*compose, "build", "--no-cache"], cwd=clone_dir, env=env, check=False)
            record["build_no_cache_seconds"] = round(time.perf_counter() - boot_started, 1)
            record["build_exit_code"] = built.returncode
        up = run([*compose, "up", "--build", "-d"], cwd=clone_dir, env=env, check=False)
        record["compose_up_seconds"] = round(time.perf_counter() - boot_started, 1)
        record["compose_up_exit_code"] = up.returncode
        if up.returncode != 0:
            record["compose_up_tail"] = (up.stdout + up.stderr)[-2000:]
        health_url = f"http://127.0.0.1:{args.api_port}/health"
        status = None
        while time.perf_counter() - boot_started < args.timeout:
            try:
                with urllib.request.urlopen(health_url, timeout=3) as response:
                    status = response.status
                    if status == 200:
                        break
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(2)
        record["health_status"] = status
        record["seconds_until_health_200"] = round(time.perf_counter() - boot_started, 1) if status == 200 else None
        record["within_10_minutes"] = status == 200 and record["seconds_until_health_200"] <= 600
        loader = run([*compose, "logs", "--no-color", "loader"], cwd=clone_dir, env=env, check=False).stdout
        # 데모 계정과 비밀번호가 찍힌 줄([demo] …)은 남기지 않는다. 실행마다 무작위로 만들어지는 값이지만 기록에 둘 이유가 없다.
        record["loader_log_tail"] = [line for line in loader.splitlines()
                                     if "[demo]" not in line and "@" not in line and "비밀번호" not in line and "password" not in line.lower()][-6:]
        record["services"] = run([*compose, "ps", "--format", "{{.Service}} {{.State}} {{.Health}}"], cwd=clone_dir, env=env, check=False).stdout.split("\n")[:6]
    finally:
        if not args.keep and clone_dir.exists():
            run([*compose, "down", "-v", "--rmi", "local"], cwd=clone_dir, env=env, check=False)
        shutil.rmtree(workdir, ignore_errors=True)

    output = ROOT / args.output
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: record.get(k) for k in ("commit", "clone_seconds", "compose_up_seconds", "seconds_until_health_200", "within_10_minutes", "health_status")}, ensure_ascii=False))
    print(f"→ {args.output}")
    return 0 if record.get("health_status") == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
