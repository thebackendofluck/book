#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 27c, Migrating a Single-Jurisdiction Casino Platform to Hub & Spo.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Inline each service's app/main.py into its k8s manifest ConfigMap.

Replaces the line matching `__MAIN_PY__` in each YAML with the indented
contents of the corresponding Python source. Produces files under
k8s/rendered/ ready for `kubectl apply -f`.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
RENDERED = ROOT / "k8s" / "rendered"
RENDERED.mkdir(parents=True, exist_ok=True)

MAPPING = [
    ("k8s/global-id.yaml", "hub/global-id/app/main.py"),
    ("k8s/mailer.yaml", "hub/mailer/app/main.py"),
    ("k8s/wallet-br.yaml", "spoke-br/wallet/app/main.py"),
]


def render(yaml_rel: str, py_rel: str) -> Path:
    yaml_path = ROOT / yaml_rel
    py_path = ROOT / py_rel
    yaml_lines = yaml_path.read_text().splitlines()
    py_text = py_path.read_text()

    out_lines: list[str] = []
    for line in yaml_lines:
        if line.strip() == "__MAIN_PY__":
            # Preserve the indentation of the placeholder line.
            indent = line[: len(line) - len(line.lstrip())]
            for src_line in py_text.splitlines():
                out_lines.append(f"{indent}{src_line}" if src_line else indent.rstrip())
        else:
            out_lines.append(line)
    out = RENDERED / Path(yaml_rel).name
    out.write_text("\n".join(out_lines) + "\n")
    return out


def main() -> int:
    # Copy over the non-templated manifests too so `rendered/` is self-contained.
    passthrough = [
        "namespace-hub.yaml",
        "namespace-spoke-br.yaml",
        "hub-postgres.yaml",
        "hub-redis.yaml",
        "spoke-br-postgres.yaml",
        "networkpolicy-spoke-cannot-reach-hub-db.yaml",
    ]
    for name in passthrough:
        (RENDERED / name).write_text((ROOT / "k8s" / name).read_text())
    for y, p in MAPPING:
        out = render(y, p)
        print(f"rendered {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
