"""One-time per-project runtime setup. No documents or model credentials are sent."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    p = argparse.ArgumentParser(description='首次创建项目私有Python环境；仅下载固定依赖')
    p.add_argument('--project-dir', required=True, type=Path)
    p.add_argument('--wheelhouse', type=Path, help='可选离线wheel目录，须匹配当前系统与Python')
    args = p.parse_args()
    if sys.version_info < (3,11):
        p.exit(2, '需要Python 3.11或以上版本。\n')
    root = args.project_dir.resolve(); root.mkdir(parents=True, exist_ok=True)
    env = root / '.takeoff-venv'
    python = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    req = Path(__file__).with_name('requirements.txt')
    expected = dict(line.split('==',1) for line in req.read_text().splitlines() if line and not line.startswith('#'))
    check = 'import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in '+repr(list(expected))+'}))'
    if python.is_file():
        done = subprocess.run([str(python),'-c',check],capture_output=True,text=True)
        if done.returncode==0 and json.loads(done.stdout)==expected:
            print(json.dumps(dict(python=str(python),reused=True),ensure_ascii=False)); return
    try:
        venv.EnvBuilder(with_pip=True).create(env)
        cmd = [str(python),'-m','pip','install','--disable-pip-version-check','--only-binary=:all:',
               '--retries','0','--timeout','45','-r',str(req)]
        if args.wheelhouse:
            cmd += ['--no-index','--find-links',str(args.wheelhouse.resolve())]
        subprocess.run(cmd, check=True, timeout=240)
        print(json.dumps(dict(python=str(python),reused=False),ensure_ascii=False))
    except (OSError, subprocess.SubprocessError) as exc:
        p.exit(2, f'环境准备失败：{exc}\n请修复网络/项目写权限或提供匹配的离线wheel包；不要循环重装，也不要开始整套识图。\n')


if __name__ == '__main__':
    main()
