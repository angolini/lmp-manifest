---
name: update-lmp-milestone
description: Update the lmp-manifest to a new LTS Yocto milestone. Use when bumping openembedded-core, bitbake, and/or meta-openembedded revisions in lmp-base.xml to a Yocto release (e.g. yocto-5.0.x on scarthgap). The skill knows its three upstream repos, detects from git whether a newer LTS milestone has been published, and reports what is currently merged vs. available before updating. Everything is resolved live from git — there is no input file. Creates one signed-off commit per layer following the repo's "Relevant changes" convention.
---

# Update lmp-manifest to a new LTS Yocto milestone

Bumps layer revisions in `lmp-base.xml` to a new Yocto milestone and creates one
git commit per updated layer, matching the existing commit convention in this repo.
The skill resolves everything live from git: it knows its three upstream repos
(see the table below), detects whether a newer milestone has been published, and
reports what is currently merged vs. available before doing any work.

## Scope

This skill covers exactly three layers, all in `lmp-base.xml`:

- `openembedded-core`
- `bitbake`
- `meta-openembedded`

Other repos in `lmp-base.xml` (e.g. `meta-yocto`, `meta-lmp`) are out of scope —
this skill never touches them.

## Known repositories (no input file)

There is **no input file** — the skill knows its three upstream repos and resolves
every revision live from git, acting on what is really published today. The per-repo
metadata is fixed:

| Layer              | Upstream URL                                          | Branch     | Milestone tags |
|--------------------|-------------------------------------------------------|------------|----------------|
| `openembedded-core`| `https://git.openembedded.org/openembedded-core`      | `scarthgap`| `yocto-5.0.*`  |
| `bitbake`          | `https://git.openembedded.org/bitbake`                | `2.8`      | `yocto-5.0.*`  |
| `meta-openembedded`| `https://github.com/openembedded/meta-openembedded`   | `scarthgap`| none (tip)     |

`openembedded-core` and `bitbake` are **tagged** — the target is a published Yocto
milestone tag matching the pattern above. `meta-openembedded` is **different** — it
has no milestone tags; its target is the **current tip of its branch today**.

This table is tied to the current LTS line (scarthgap / Yocto 5.0). When the manifest
moves to a new LTS, update the branch names and the tag pattern here.

## Step 0: Detect milestone status (run this first)

Before touching anything, work out — live from git — what is currently merged in
`lmp-base.xml` versus what is published upstream, and report it. This answers
"is there a newer milestone we haven't merged yet?" without making any change.

For each **tagged repo** (`openembedded-core`, `bitbake`):

1. List all published tags matching the pattern and version-sort them; the last line
   is the **latest published milestone**:

   ```bash
   git ls-remote --tags <UPSTREAM_URL> 'refs/tags/yocto-5.0.*' \
     | sed 's,.*refs/tags/,,; s,\^{},,' | sort -uV
   ```

2. Identify the **currently merged milestone** from OLD (the `revision="..."` for that
   `<project>` in `lmp-base.xml`). A blobless single-branch clone plus `git describe`
   maps the merged SHA to its milestone even when it sits between tags:

   ```bash
   TMP=$(mktemp -d)
   git clone --filter=blob:none --single-branch --branch <BRANCH> <UPSTREAM_URL> "$TMP"
   git -C "$TMP" fetch --tags --quiet
   git -C "$TMP" describe --tags <OLD>   # e.g. yocto-5.0.17  or  yocto-5.0.17-12-gabc1234
   ```

   An exact tag (`yocto-5.0.17`) means OLD is exactly that milestone. A `-<n>-g<hash>`
   suffix means OLD sits N commits past that milestone.

3. The **unmerged milestones** are the version-sorted tags strictly greater than the
   currently-merged one, up to the latest. An empty set means the layer is already at
   the latest milestone.

For **meta-openembedded** (no tags — it tracks the branch tip), report how far behind
OLD is from the current tip instead of a milestone name:

```bash
git -C "$TMP" rev-parse HEAD                # current tip (candidate NEW)
git -C "$TMP" rev-list --count <OLD>..HEAD  # commits behind the tip
```

Clean up each temp dir (`rm -rf "$TMP"`).

Then print a status summary and **stop to ask the user** whether to proceed. Example:

```
Milestone status:
- openembedded-core: merged yocto-5.0.17, latest yocto-5.0.18  → 1 milestone behind (yocto-5.0.18)
- bitbake:           merged yocto-5.0.17, latest yocto-5.0.18  → 1 milestone behind (yocto-5.0.18)
- meta-openembedded: 34 commits behind branch tip (no tags)
```

State plainly whether the latest milestone is already merged ("up to date — nothing to
do") or whether there are unmerged milestones. If there is something to update, ask the
user whether to proceed and — when more than one milestone is behind — which target tag
to bump to (default to the latest). Only after this confirmation do you run the
Procedure below. **Make no commits or file edits during detection.**

## Procedure

Do the layers **one at a time** — update the XML, then commit, then move to the next.
A clean working tree before starting is expected. The targets were already confirmed
with the user in Step 0 (this skill auto-commits).

For each layer:

### 1. Determine OLD and NEW revisions

- **OLD** = the current `revision="..."` for that `<project>` in `lmp-base.xml`.
  - `bitbake` → `<project name="bitbake" ...>`
  - `openembedded-core` → `<project name="openembedded-core" ...>`
  - `meta-openembedded` → `<project name="meta-openembedded" ...>`
- **NEW** (always resolved live from git):
  - `openembedded-core`, `bitbake` → the commit of the target milestone tag chosen in
    Step 0 (the latest, or the user-selected one); resolve its commit in step 2.
  - `meta-openembedded` → the **tip of the branch** today (resolve it in step 2).

If OLD == NEW, skip the layer (nothing to update).

### 2. Get the layer git log (for the commit body)

The layers are not checked out in this repo, so clone the upstream branch (use the
URL/branch from the "Known repositories" table) into a temp dir. A blobless
single-branch clone is fast and gives the full commit graph:

```bash
TMP=$(mktemp -d)
git clone --filter=blob:none --single-branch --branch <BRANCH> <UPSTREAM_URL> "$TMP"
```

For `openembedded-core` and `bitbake`, confirm the target tag exists and resolve NEW
from git:

```bash
# List published tags matching the pattern, e.g. yocto-5.0.*
git ls-remote --tags <UPSTREAM_URL> 'refs/tags/yocto-5.0.*'
# Resolve the chosen tag to its commit (peel annotated tags with ^{})
NEW=$(git ls-remote <UPSTREAM_URL> 'refs/tags/<TARGET_TAG>^{}' | awk '{print $1}')
# fall back to the non-peeled ref if the tag is lightweight
[ -n "$NEW" ] || NEW=$(git ls-remote <UPSTREAM_URL> 'refs/tags/<TARGET_TAG>' | awk '{print $1}')
```

If the expected tag is not present upstream, stop and report it to the user rather
than guessing.

For `meta-openembedded`, resolve NEW from the freshly cloned tip:

```bash
NEW=$(git -C "$TMP" rev-parse HEAD)
```

Generate the "Relevant changes" list — the **full** log of `OLD..NEW`, newest first,
one line per commit as `- <short-hash> <subject>`:

```bash
git -C "$TMP" log --format="- %h %s" <OLD>..<NEW>
```

This output is copied verbatim into the commit body — do not curate or trim it.

Clean up the temp dir when done (`rm -rf "$TMP"`).

### 3. Update the revision in lmp-base.xml

Replace only the `revision="<OLD>"` with `revision="<NEW>"` on that layer's
`<project>` line. Leave every other line untouched.

### 4. Commit (one commit per layer, signed off)

Commit message **subject** lines (note bitbake omits the word "layer"):

| Layer              | Subject                                        |
|--------------------|------------------------------------------------|
| `openembedded-core`| `lmp-base: update openembedded-core layer`     |
| `bitbake`          | `lmp-base: update bitbake`                      |
| `meta-openembedded`| `lmp-base: update meta-openembedded layer`     |

Body format:

```
<subject>

Relevant changes:
<the - <short-hash> <subject> lines from step 2>

Signed-off-by: <name> <email>
```

**`meta-openembedded` is different** — its body carries an extra heading paragraph
explaining that meta-openembedded's release cycle is not synced with Yocto, the
date the tip was taken, and a link to the NEW commit. Do **not** invent this
wording: read the previous meta-openembedded commits in this repo to copy the
current pattern verbatim, then fill in today's date and the NEW commit URL.

```bash
git log --grep="update meta-openembedded" -i --format=%B -n 5
```

The pattern in recent history looks like this (subject is still the table value
above; the heading goes between subject and "Relevant changes:"):

```
lmp-base: update meta-openembedded layer

meta-openembedded release cycle is not synchronized with yocto project.
This patch update meta-openembedded to the latest <BRANCH> commit up to
<YYYY-MM-DD>
https://github.com/openembedded/meta-openembedded/commit/<NEW>

Relevant changes:
<the - <short-hash> <subject> lines from step 2>

Signed-off-by: <name> <email>
```

Use the current date (the day you resolved the tip) for `<YYYY-MM-DD>`. If the
previous commits' wording has drifted from the above, follow the **most recent**
commit's wording — it is the source of truth for the pattern.

Use `git commit -s` so the `Signed-off-by` is taken from the local git config
(do not hardcode a name). Stage only `lmp-base.xml`. Use a HEREDOC for the message:

```bash
git add lmp-base.xml
git commit -s -m "$(cat <<'EOF'
lmp-base: update openembedded-core layer

Relevant changes:
- <hash> <subject>
- <hash> <subject>
EOF
)"
```

(`-s` appends the `Signed-off-by:` line; do not also write it in the HEREDOC.)

Repeat steps 1–4 for each of the three layers.

### 5. Print the NEW commit links (after all layers are done)

Once every updated layer is committed, print a final summary listing the web link
to each NEW revision used. Keep a note of the NEW hash for every layer you actually
updated (skip layers where OLD == NEW), then derive the link from that layer's own
upstream URL:

- cgit hosts (`git.openembedded.org`) → `<UPSTREAM_URL>/commit/?id=<NEW>`
  - `openembedded-core` → `https://git.openembedded.org/openembedded-core/commit/?id=<NEW>`
  - `bitbake` → `https://git.openembedded.org/bitbake/commit/?id=<NEW>`
- GitHub hosts → `<UPSTREAM_URL>/commit/<NEW>`
  - `meta-openembedded` → `https://github.com/openembedded/meta-openembedded/commit/<NEW>`

Print one line per updated layer, e.g.:

```
NEW commit links:
- openembedded-core: https://git.openembedded.org/openembedded-core/commit/?id=<NEW>
- bitbake:           https://git.openembedded.org/bitbake/commit/?id=<NEW>
- meta-openembedded: https://github.com/openembedded/meta-openembedded/commit/<NEW>
```

## Example commit (from this repo's history)

```
lmp-base: update bitbake

Relevant changes:
- 112bddd8f data: Add exception details if build_dependencies catches one
- a70c33679 bitbake-setup: share sstate by default between builds
- ...

Signed-off-by: Jose Quaresma <jose.quaresma@foundries.io>
```

The top entry of the list is always the NEW revision itself; the list ends just
before the OLD revision (`OLD..NEW` semantics).

## Notes

- The short-hash length comes from git's default abbreviation per repo (`%h`);
  it varies between repos — that is expected, don't normalize it.
- Do not push. Leave the commits local for the user to review and push.
