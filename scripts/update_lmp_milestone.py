#!/usr/bin/env python3
"""Bump Yocto LTS milestone revisions in lmp-base.xml.

Ports the update-lmp-milestone skill into an unattended script: it updates the
three upstream layers (openembedded-core, bitbake, meta-openembedded) in
lmp-base.xml to the latest published Yocto LTS milestone and creates one
signed-off commit per updated layer.

The active LTS line (scarthgap / Yocto 5.0 today) is derived from the manifest's
own openembedded-core revision, so the same script follows whatever release the
branch it runs on targets, now and for future LTS lines.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timezone, datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO_ROOT, "lmp-base.xml")

# Stable per-layer facts. These upstream URLs and the tagged/untagged nature do
# not change between LTS lines, so keeping them here is not "hardcoding the
# release" -- the release-specific bits (version, codename branch) are derived.
OECORE = "openembedded-core"
BITBAKE = "bitbake"
META_OE = "meta-openembedded"

LAYERS = {
    OECORE: {
        "url": "https://git.openembedded.org/openembedded-core",
        "tagged": True,
        "subject": "lmp-base: update openembedded-core layer up to {ms}",
    },
    BITBAKE: {
        "url": "https://git.openembedded.org/bitbake",
        "tagged": True,
        "subject": "lmp-base: update bitbake up to {ms}",
    },
    META_OE: {
        "url": "https://github.com/openembedded/meta-openembedded",
        "tagged": False,
        "subject": "lmp-base: update meta-openembedded layer",
    },
}

# Order layers are processed/committed in.
ORDER = [OECORE, BITBAKE, META_OE]


def run(cmd, cwd=None, check=True):
    """Run a command and return stdout (stripped). Raises on failure if check."""
    result = subprocess.run(
        cmd, cwd=cwd, check=check, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def read_revisions():
    """Return {project_name: revision} for the three tracked layers."""
    with open(MANIFEST, encoding="utf-8") as f:
        text = f.read()
    revs = {}
    for name in LAYERS:
        m = re.search(
            r'<project\b[^>]*\bname="%s"[^>]*\brevision="([0-9a-fA-F]+)"'
            % re.escape(name),
            text,
        )
        if not m:
            # Attribute order may vary; try revision before name too.
            m = re.search(
                r'<project\b[^>]*\brevision="([0-9a-fA-F]+)"[^>]*\bname="%s"'
                % re.escape(name),
                text,
            )
        if not m:
            sys.exit("error: could not find revision for project '%s' in %s"
                     % (name, MANIFEST))
        revs[name] = m.group(1)
    return revs


def set_revision(name, old, new):
    """Replace only the revision of the given project in lmp-base.xml."""
    with open(MANIFEST, encoding="utf-8") as f:
        text = f.read()
    # Match the single <project ...> element for this name and swap its
    # revision attribute, leaving every other line untouched.
    pattern = re.compile(
        r'(<project\b[^>]*\bname="%s"[^>]*?)\brevision="%s"'
        % (re.escape(name), re.escape(old))
    )
    new_text, n = pattern.subn(r'\1revision="%s"' % new, text)
    if n == 0:
        # Try the other attribute order.
        pattern = re.compile(
            r'\brevision="%s"((?:[^>]*?)\bname="%s")' % (re.escape(old), re.escape(name))
        )
        new_text, n = pattern.subn(r'revision="%s"\1' % new, text)
    if n != 1:
        sys.exit("error: expected exactly one revision match for '%s' (got %d)"
                 % (name, n))
    with open(MANIFEST, "w", encoding="utf-8") as f:
        f.write(new_text)


def version_key(tag):
    """Sort key for 'yocto-X.Y.Z' tags by numeric components."""
    nums = re.findall(r"\d+", tag)
    return tuple(int(n) for n in nums)


def list_milestone_tags(url, pattern):
    """Return version-sorted list of unique tag names matching pattern."""
    out = run(["git", "ls-remote", "--tags", url,
               "refs/tags/%s" % pattern], check=False)
    tags = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        ref = line.split("\t", 1)[-1]
        ref = ref.replace("refs/tags/", "").replace("^{}", "")
        tags.add(ref)
    return sorted(tags, key=version_key)


def resolve_tag_commit(url, tag):
    """Resolve a tag to its (peeled) commit SHA via ls-remote."""
    out = run(["git", "ls-remote", url,
               "refs/tags/%s^{}" % tag], check=False)
    if out:
        return out.split("\t", 1)[0].strip()
    out = run(["git", "ls-remote", url, "refs/tags/%s" % tag], check=False)
    if out:
        return out.split("\t", 1)[0].strip()
    sys.exit("error: could not resolve tag '%s' on %s" % (tag, url))


def clone_blobless(url, tmp, branch=None, all_refs=False):
    """Blobless clone into tmp. With all_refs, fetch all heads + tags."""
    cmd = ["git", "clone", "--filter=blob:none", "--quiet"]
    if branch and not all_refs:
        cmd += ["--single-branch", "--branch", branch]
    elif not all_refs:
        cmd += ["--single-branch"]
    cmd += [url, tmp]
    run(cmd)
    if all_refs:
        run(["git", "-C", tmp, "fetch", "--tags", "--quiet"])


def derive_lts_line(oecore_old):
    """Clone oe-core, derive (version 'X.Y', codename branch, merged describe)."""
    tmp = tempfile.mkdtemp(prefix="lmp-oecore-")
    try:
        clone_blobless(LAYERS[OECORE]["url"], tmp, all_refs=True)
        described = run(["git", "-C", tmp, "describe", "--tags", oecore_old],
                        check=False)
        m = re.match(r"yocto-(\d+)\.(\d+)\.(\d+)", described or "")
        if not m:
            sys.exit("error: oe-core revision %s does not describe to a "
                     "yocto-X.Y.Z tag (got %r)" % (oecore_old, described))
        version = "%s.%s" % (m.group(1), m.group(2))

        branches = run(["git", "-C", tmp, "branch", "-r", "--contains",
                        oecore_old], check=False)
        codename = None
        for line in branches.splitlines():
            name = line.strip().split("/", 1)[-1]
            if name in ("master", "main", "HEAD") or "->" in line:
                continue
            codename = name
            break
        if not codename:
            sys.exit("error: could not derive codename branch containing %s"
                     % oecore_old)
        return version, codename, described
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def log_lines(url, old, new, branch=None):
    """Return the 'Relevant changes' lines for old..new from a temp clone."""
    tmp = tempfile.mkdtemp(prefix="lmp-log-")
    try:
        clone_blobless(url, tmp, all_refs=True)
        out = run(["git", "-C", tmp, "log", "--format=- %h %s",
                   "%s..%s" % (old, new)])
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def commit_link(name, new):
    url = LAYERS[name]["url"]
    if "git.openembedded.org" in url:
        return "%s/commit/?id=%s" % (url, new)
    return "%s/commit/%s" % (url, new)


def build_message(name, new, milestone, codename, changes):
    """Build the full commit message body (sign-off added by git -s)."""
    subject = LAYERS[name]["subject"].format(ms=milestone)
    parts = [subject, ""]
    if name == META_OE:
        today = datetime.now(timezone.utc).date().isoformat()
        parts += [
            "meta-openembedded release cycle is not synchronized with yocto project.",
            "This patch update meta-openembedded to the latest %s commit up to" % codename,
            today,
            "",
            "https://github.com/openembedded/meta-openembedded/commit/%s" % new,
            "",
        ]
    parts += ["Relevant changes:", changes]
    return "\n".join(parts) + "\n"


def git_commit(message):
    run(["git", "-C", REPO_ROOT, "add", "lmp-base.xml"])
    subprocess.run(["git", "-C", REPO_ROOT, "commit", "-s", "-F", "-"],
                   input=message, text=True, check=True)


def emit_output(changed, title):
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        return
    with open(out_path, "a", encoding="utf-8") as f:
        f.write("changed=%s\n" % ("true" if changed else "false"))
        if title:
            f.write("title=%s\n" % title)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="detect and report only; make no edits or commits")
    args = ap.parse_args()

    old = read_revisions()
    version, codename, described = derive_lts_line(old[OECORE])
    tag_pattern = "yocto-%s.*" % version

    print("LTS line: Yocto %s (branch '%s')" % (version, codename))
    print("oe-core merged at: %s\n" % described)

    plan = {}      # name -> (old, new, milestone)
    statuses = []  # human-readable lines

    for name in ORDER:
        layer = LAYERS[name]
        o = old[name]
        if layer["tagged"]:
            tags = list_milestone_tags(layer["url"], tag_pattern)
            if not tags:
                sys.exit("error: no %s tags found on %s" % (tag_pattern, layer["url"]))
            latest = tags[-1]
            new = resolve_tag_commit(layer["url"], latest)
            milestone = latest[len("yocto-"):]
            if o == new:
                statuses.append("- %-18s up to date at %s" % (name + ":", latest))
            else:
                plan[name] = (o, new, milestone)
                statuses.append("- %-18s -> %s" % (name + ":", latest))
        else:
            tmp = tempfile.mkdtemp(prefix="lmp-tip-")
            try:
                clone_blobless(layer["url"], tmp, branch=codename)
                new = run(["git", "-C", tmp, "rev-parse", "HEAD"])
                behind = run(["git", "-C", tmp, "rev-list", "--count",
                              "%s..HEAD" % o], check=False)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            if o == new:
                statuses.append("- %-18s up to date at branch tip" % (name + ":"))
            else:
                plan[name] = (o, new, None)
                statuses.append("- %-18s %s commits behind tip" % (name + ":", behind))

    print("Milestone status:")
    print("\n".join(statuses))
    print()

    if not plan:
        print("Nothing to update -- all layers at the latest milestone/tip.")
        emit_output(False, "")
        return

    if args.dry_run:
        print("[dry-run] %d layer(s) would be updated; no changes made."
              % len(plan))
        emit_output(True, "")
        return

    new_links = []
    milestones = []
    for name in ORDER:
        if name not in plan:
            continue
        o, new, milestone = plan[name]
        changes = log_lines(LAYERS[name]["url"], o, new, branch=codename)
        message = build_message(name, new, milestone, codename, changes)
        set_revision(name, o, new)
        git_commit(message)
        new_links.append("- %-18s %s" % (name + ":", commit_link(name, new)))
        if milestone:
            milestones.append(milestone)

    print("\nNEW commit links:")
    print("\n".join(new_links))

    # PR title + body for the workflow.
    if milestones:
        title = "lmp-base: bump %s to Yocto %s" % (codename, max(milestones, key=version_key))
    else:
        title = "lmp-base: bump %s layer revisions" % codename
    body_lines = [
        "Automated Yocto LTS milestone bump for the **%s** line (Yocto %s)." % (codename, version),
        "",
        "## Layers updated",
        "",
    ] + statuses + ["", "## New revisions", ""] + new_links + [
        "",
        "Generated by `scripts/update_lmp_milestone.py`.",
    ]
    with open(os.path.join(REPO_ROOT, "pr-body.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(body_lines) + "\n")

    emit_output(True, title)


if __name__ == "__main__":
    main()
