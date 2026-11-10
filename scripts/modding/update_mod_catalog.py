#!/usr/bin/env python3
"""
Automated catalog generator and updater for OpenGOAL mod distribution.
Compliant with OpenGOAL Launcher Mod Source Schema v1:
https://github.com/open-goal/launcher/tree/main/schemas/mod-source/v1

Usage:
    python scripts/modding/update_mod_catalog.py
    python scripts/modding/update_mod_catalog.py --tag v1.0.0 --repo <owner>/jak-project
    python scripts/modding/update_mod_catalog.py --next-version
    python scripts/modding/update_mod_catalog.py --print-metadata
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from sync_common import github_repo

if hasattr(sys.stdout, "reconfigure"):
  sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
  sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_cmd(cmd: str) -> str:
  try:
    res = subprocess.run(
        cmd,
        shell=True,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
        check=False,
    )
    return res.stdout.strip()
  except Exception:
    return ""


def get_current_branch() -> str:
  branch = os.environ.get("GITHUB_REF_NAME") or run_cmd(
      "git rev-parse --abbrev-ref HEAD"
  )
  return branch if branch else "master-dev"


def mod_identity(index_path: Path, repo: str):
  """Slug and game of the mod held by a mod repository (one GitHub repository per mod).

  The repository's own index.json names exactly one mod: its key is the launcher catalog key,
  kept verbatim (e.g. `jak3-jetBoard`), and supportedGames[0] is its game. A repository with no
  catalog yet falls back to its name, `<game>-mod-<slug>` (e.g. `jak2-mod-blue-krimzon-guard`).
  Returns (None, None) for anything else, such as the mother repository's global catalog.
  """
  try:
    mods = json.loads(index_path.read_text(encoding="utf-8")).get("mods") or {}
  except (OSError, ValueError):
    mods = {}
  if len(mods) == 1:
    slug, entry = next(iter(mods.items()))
    games = entry.get("supportedGames") or []
    return slug, (games[0] if games else None)
  m = re.match(r"^(jak[123])-(?:mod-)?(.+)$", repo.split("/")[-1])
  if m:
    return m.group(2), m.group(1)
  return None, None


def sanitize_source_name(name: str) -> str:
  """Sanitize a catalog sourceName for safe use as a directory name on Windows and Linux.

  OpenGOAL Launcher uses sourceName directly as a filesystem directory name when installing mods
  (.../features/<game>/mods/<sourceName>/<modName>/). On Windows, characters like ':', '/', '\\',
  '*', '?', '"', '<', '>', '|' are illegal and trigger 'os error 123' (ERROR_INVALID_NAME).
  """
  if not name:
    return "OpenGOAL Mod Source"

  # Replace colons and slashes with hyphens, strip other filesystem-illegal characters
  cleaned = re.sub(r"\s*[:/\\]+\s*", " - ", name)
  cleaned = re.sub(r'[*?"<>|]', "", cleaned)
  # Collapse multi-hyphens and multi-spaces
  cleaned = re.sub(r"\s+-\s+-\s+", " - ", cleaned)
  cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")

  if not cleaned:
    return "OpenGOAL Mod Source"

  if cleaned.lower().endswith(" source"):
    return cleaned
  return f"{cleaned} Source"


def extract_metadata_from_readme(readme_path: Path):
  """Extract overview description from mod's root README.md without AI mentions."""
  description = None

  if not readme_path.exists():
    return description

  try:
    with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
      content = f.read()

    # Extract overview section if present
    desc_match = re.search(
        r"##\s+(?:📖\s+)?(?:Overview|Présentation du Mod)\s*\n+([\s\S]*?)(?=\n\s*(?:- \*\*Target Game|##|---|\Z))",
        content,
        re.MULTILINE,
    )
    if desc_match:
      raw_desc = desc_match.group(1).strip()
      cleaned_lines = []
      for line in raw_desc.splitlines():
        line_s = line.strip()
        if not line_s:
          continue
        if line_s.startswith("-") or line_s.startswith("*") or line_s.startswith("<") or line_s.startswith("!["):
          continue
        cleaned_lines.append(line_s)
      if cleaned_lines:
        candidate = " ".join(cleaned_lines)
        # Strip AI disclosure or badges from description
        candidate = re.sub(r"\s*\(AI-assisted[^)]*\)", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*\(AI--assisted[^)]*\)", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"AI--assisted-Modding-[^.\s]+\.svg", "", candidate)
        description = candidate.strip()
  except Exception as err:
    print(f"Warning: Failed to parse README.md: {err}", file=sys.stderr)

  return description


def generate_release_notes(
    output_path: Path,
    branch: str,
    tag: str,
    repo: str,
    checksums_path: Path = None,
    display_name: str = None,
    description: str = None,
    cover_url: str = None,
    changelog_path: Path = None,
):
  """Generates formatted markdown release notes for GitHub Releases. changelog_path: an optional
  Markdown file whose text is added under a Changelog heading."""
  readme_desc = extract_metadata_from_readme(REPO_ROOT / "README.md")
  branch_match = re.match(r"^jak([123])/([^/]+)/(.+)$", branch)
  if branch_match:
    variable_part = branch_match.group(3)
  else:
    parts = branch.split("/")
    variable_part = parts[-1] if len(parts) > 1 else branch

  disp_name = display_name or variable_part
  desc = (
      description
      or readme_desc
      or f"Mod OpenGOAL avec modifications de jeu."
  )

  checksums_content = ""
  if checksums_path and checksums_path.exists():
    try:
      with open(checksums_path, "r", encoding="utf-8", errors="replace") as f:
        checksums_content = f.read().strip()
    except Exception:
      checksums_content = ""

  cover_path = REPO_ROOT / "docs" / "img" / "mod" / "mod_cover.png"
  resolved_cover = cover_url
  if not resolved_cover and (cover_path.is_file() or branch.startswith(("jak1/", "jak2/", "jak3/"))):
    resolved_cover = f"https://raw.githubusercontent.com/{repo}/{branch}/docs/img/mod/mod_cover.png"

  notes_parts = [
      f"## 🎮 {disp_name} — {tag}",
      "",
  ]

  if resolved_cover:
    notes_parts.extend([
        f'<p align="center">',
        f'  <img src="{resolved_cover}" alt="{disp_name}" width="500">',
        f'</p>',
        "",
    ])

  notes_parts.extend([
      desc,
      "",
      "### 📦 Téléchargements Directs",
      f"- **Windows :** [`windows-{tag}.zip`](https://github.com/{repo}/releases/download/{tag}/windows-{tag}.zip)",
      f"- **Linux :** [`linux-{tag}.zip`](https://github.com/{repo}/releases/download/{tag}/linux-{tag}.zip)",
      "",
      "### 📋 Intégration OpenGOAL Launcher (Custom Mod Source)",
      "Ajoutez ce mod directement dans l'OpenGOAL Launcher via l'URL du catalogue :",
      "```text",
      f"https://raw.githubusercontent.com/{repo}/{branch}/index.json",
      "```",
  ])

  if changelog_path:
    notes_parts.extend(["", "### Changelog", "", changelog_path.read_text(encoding="utf-8").strip()])

  if checksums_content:
    notes_parts.extend([
        "",
        "### 🔒 Checksums de Sécurité (SHA-256)",
        "```text",
        checksums_content,
        "```",
    ])

  output_path.parent.mkdir(parents=True, exist_ok=True)
  with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n".join(notes_parts) + "\n")

  print(f"[OK] Generated release notes to {output_path}")


def get_next_version(index_path: Path, mod_slug: str = "") -> str:
  """
  Calculates the next version string formatted as [nom-du-mod]-[version]
  (e.g. 'jak3-jetBoard-v1.0.0', 'jak3-jetBoard-v1.0.1').
  1. Reads index.json for latest version.
  2. Increments patch number if previous version was released with checksums.
  3. Checks git tags to ensure tag uniqueness across repository.
  """
  candidate_major = 1
  candidate_minor = 0
  candidate_patch = 0

  if index_path.exists():
    try:
      with open(index_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        mods = data.get("mods", {})
        for mod_key, mod_data in mods.items():
          if mod_key.startswith("temp-sync-"):
            continue
          versions = mod_data.get("versions", [])
          if versions:
            latest_v_str = versions[0].get("version", "")
            win_ck = versions[0].get("checksums", {}).get("windows", "")
            m = re.search(r"(\d+)\.(\d+)\.(\d+)", latest_v_str)
            if m:
              candidate_major = int(m.group(1))
              candidate_minor = int(m.group(2))
              if win_ck:
                candidate_patch = int(m.group(3)) + 1
              else:
                candidate_patch = int(m.group(3))
            break
    except Exception:
      pass

  prefix = f"{mod_slug}-" if mod_slug else ""
  candidate_tag = f"{prefix}v{candidate_major}.{candidate_minor}.{candidate_patch}"

  # Verify against existing git tags
  existing_tags_raw = run_cmd("git tag -l")
  existing_tags = set(t.strip() for t in existing_tags_raw.splitlines() if t.strip())

  while candidate_tag in existing_tags:
    candidate_patch += 1
    candidate_tag = f"{prefix}v{candidate_major}.{candidate_minor}.{candidate_patch}"

  return candidate_tag


def refresh_metadata_only(index_path, mod_id, display_name, description, supported_games, cover_url,
                          website_url=None):
  """
  Update only a mod's display metadata (name, description, supported games, cover) in
  an EXISTING index.json entry — never touches `versions[]`, never invents a tag.

  A routine branch sync is not a release: it must never fabricate a draft version with
  an empty checksum and a download URL pointing at a GitHub Release nothing built. Only
  a real `--tag ... --win-sha ... --lin-sha ...` call (release.yml) may touch versions[].
  For the same reason, this never CREATES a mod's first catalog entry either — that
  first entry is what marks a mod as actually released; a mod with zero real releases
  should not show up in the public launcher catalog at all.
  """
  if not index_path.exists():
    print(f"No {index_path} yet — nothing to refresh (a real release creates it).")
    return

  try:
    with open(index_path, "r", encoding="utf-8") as f:
      catalog = json.load(f)
  except Exception as err:
    print(f"Warning: could not read {index_path}, skipping metadata refresh: {err}")
    return

  mod_entry = catalog.get("mods", {}).get(mod_id)
  if mod_entry is None:
    print(f"'{mod_id}' has no entry in {index_path} yet — nothing to refresh (a real release creates it).")
    return

  before = json.dumps(mod_entry, sort_keys=True)

  # A sync passes no display name: keep the released one instead of the branch or repository slug.
  if display_name:
    mod_entry["displayName"] = display_name
  mod_entry["description"] = description
  mod_entry["supportedGames"] = supported_games
  if cover_url:
    mod_entry["coverArtUrl"] = cover_url
    mod_entry["thumbnailArtUrl"] = cover_url
  # A mod repository's page is the repository itself, which follows it through a rename.
  if website_url:
    mod_entry["websiteUrl"] = website_url

  if json.dumps(mod_entry, sort_keys=True) == before:
    print(f"[OK] '{mod_id}' metadata unchanged — {index_path} left as-is.")
    return

  catalog["sourceName"] = sanitize_source_name(mod_entry.get("displayName") or mod_id)
  catalog["lastUpdated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

  with open(index_path, "w", encoding="utf-8") as f:
    json.dump(catalog, f, indent=2, ensure_ascii=False)
    f.write("\n")

  print(f"[OK] Refreshed '{mod_id}' metadata (name/description/games/cover) in {index_path} — versions[] untouched.")


def scan_and_register_texture_packs(
    catalog: dict,
    release_tag: str,
    repo: str,
    branch: str,
    detected_game: str,
    default_author: str,
    default_cover_url: str,
):
  """Scan docs/modding/current_mod/texture_packs/ and release-files/ for texture pack zips and register in catalog."""
  import zipfile

  candidate_dirs = [
      REPO_ROOT / "docs" / "modding" / "current_mod" / "texture_packs",
      REPO_ROOT / "release-files",
  ]
  found_zips = {}
  for c_dir in candidate_dirs:
    if c_dir.exists():
      for zf_path in c_dir.glob("*.zip"):
        if zf_path.name.startswith("windows-") or zf_path.name.startswith("linux-"):
          continue
        found_zips[zf_path.name] = zf_path

  if not found_zips:
    return

  if "texturePacks" not in catalog or not isinstance(catalog["texturePacks"], dict):
    catalog["texturePacks"] = {}

  for zip_name, zf_path in sorted(found_zips.items()):
    meta = {}
    try:
      with zipfile.ZipFile(zf_path, "r") as z:
        if "metadata.json" in z.namelist():
          meta = json.loads(z.read("metadata.json").decode("utf-8"))
    except Exception as e:
      print(f"[-] Could not read metadata.json inside {zip_name}: {e}")

    tp_name = meta.get("name") or zip_name.replace(".zip", "")
    tp_ver_match = re.search(r"(\d+\.\d+\.\d+)", zip_name)
    tp_version = meta.get("version") or (tp_ver_match.group(1) if tp_ver_match else "1.0.0")
    tp_author = meta.get("author") or meta.get("authors") or default_author
    tp_authors = [tp_author] if isinstance(tp_author, str) else [str(tp_author)]
    tp_desc = meta.get("description") or f"Texture pack for {tp_name} ({detected_game})."
    tp_tags = meta.get("tags") or [detected_game, "retexture"]
    tp_supported = meta.get("supportedGames") or [detected_game]

    # Generate slug from zip name or metadata
    base_slug = zf_path.stem.lower()
    base_slug = re.sub(r"-v\d+.*$", "", base_slug)
    base_slug = re.sub(r"[^a-z0-9_-]+", "-", base_slug).strip("-")
    if not base_slug.endswith("-textures"):
      base_slug = f"{base_slug}-textures"

    download_url = f"https://github.com/{repo}/releases/download/{release_tag}/{zip_name}"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if base_slug not in catalog["texturePacks"]:
      catalog["texturePacks"][base_slug] = {
          "displayName": tp_name,
          "description": tp_desc,
          "authors": tp_authors,
          "tags": tp_tags,
          "supportedGames": tp_supported,
          "websiteUrl": f"https://github.com/{repo}/tree/{branch}",
          "coverArtUrl": default_cover_url or "",
          "thumbnailArtUrl": default_cover_url or "",
          "versions": [],
      }

    entry = catalog["texturePacks"][base_slug]
    entry["displayName"] = tp_name
    entry["description"] = tp_desc
    if default_cover_url:
      entry["coverArtUrl"] = default_cover_url
      entry["thumbnailArtUrl"] = default_cover_url

    new_v = {
        "version": tp_version,
        "publishedDate": now_iso,
        "supportedGames": tp_supported,
        "assets": {
            "windows": download_url,
            "linux": download_url,
            detected_game: download_url,
        },
    }
    entry["versions"] = [v for v in entry.get("versions", []) if v.get("version") != tp_version]
    entry["versions"].insert(0, new_v)
    print(f"[+] Registered texture pack '{base_slug}' (v{tp_version}) in catalog index.json")


def main():
  parser = argparse.ArgumentParser(
      description="Update or create an OpenGOAL mod-source index.json catalog."
  )
  parser.add_argument(
      "--index-file",
      default=os.environ.get("INDEX_FILE", "index.json"),
      help="Path to index.json output file",
  )
  parser.add_argument(
      "--tag",
      default=os.environ.get("RELEASE_TAG") or os.environ.get("TAG", ""),
      help="Release tag (e.g. jak3-jetBoard-v1.0.0 or v1.0.0)",
  )
  parser.add_argument(
      "--repo",
      default=os.environ.get("GITHUB_REPOSITORY") or os.environ.get("REPO"),
      help="GitHub repository owner/repo (default: where the branch publishes, read from its git remote)",
  )
  parser.add_argument(
      "--mod-id",
      default=os.environ.get("MOD_ID"),
      help="Mod unique slug identifier",
  )
  parser.add_argument(
      "--display-name",
      nargs="?",
      const="",
      default=os.environ.get("DISPLAY_NAME", ""),
      help="User-friendly display name (defaults to variable branch part)",
  )
  parser.add_argument(
      "--description",
      nargs="?",
      const="",
      default=os.environ.get("DESCRIPTION", ""),
      help="Mod description (defaults to Overview in README.md)",
  )
  parser.add_argument(
      "--authors",
      default=os.environ.get("AUTHORS"),
      help="Comma-separated authors list",
  )
  parser.add_argument(
      "--supported-games",
      default=os.environ.get("SUPPORTED_GAMES"),
      help="Comma-separated games: jak1, jak2, jak3",
  )
  parser.add_argument(
      "--cover-url",
      nargs="?",
      const="",
      default=os.environ.get("COVER_URL", ""),
      help="Direct URL for cover image (defaults to docs/img/mod/mod_cover.png on GitHub)",
  )
  parser.add_argument(
      "--win-sha",
      default=os.environ.get("WIN_SHA", ""),
      help="Windows archive SHA-256",
  )
  parser.add_argument(
      "--lin-sha",
      default=os.environ.get("LIN_SHA", ""),
      help="Linux archive SHA-256",
  )
  parser.add_argument(
      "--branch",
      default=os.environ.get("GITHUB_REF_NAME"),
      help="Target branch name (e.g. jak2/features/jak3-jetBoard)",
  )
  parser.add_argument(
      "--metadata-only",
      action="store_true",
      help="Refresh displayName/description/supportedGames/cover only. Never touches "
           "versions[], never guesses a tag/version. Used by the branch-sync scripts, "
           "since a routine sync is not a release and must not fabricate a draft "
           "version with empty checksums and a download URL nothing ever built.",
  )
  parser.add_argument(
      "--next-version",
      action="store_true",
      help="Print calculated next version tag and exit",
  )
  parser.add_argument(
      "--print-metadata",
      action="store_true",
      help="Print release metadata JSON (tag, display_name, description) and exit",
  )
  parser.add_argument(
      "--generate-release-notes",
      help="Output file path to generate markdown release notes and exit",
  )
  parser.add_argument(
      "--changelog-file",
      default="",
      help="Markdown changelog added to the release notes (with --generate-release-notes)",
  )
  parser.add_argument(
      "--checksums-file",
      default="",
      help="Path to SHA256SUMS.txt file to include in release notes",
  )
  parser.add_argument(
      "--export-github-output",
      action="store_true",
      help="Export release_tag and display_name directly to GITHUB_OUTPUT environment file",
  )
  args = parser.parse_args()

  branch = args.branch or get_current_branch()
  if not args.repo:
    # The repository the branch publishes to: mods/<name> tracks its mod repository.
    remote = run_cmd(f"git config --get branch.{branch}.remote") or "origin"
    found = github_repo(remote, REPO_ROOT)
    if not found:
      raise SystemExit(f"Cannot tell which GitHub repository {branch} publishes to: pass --repo <owner>/<name>.")
    args.repo = "/".join(found)
  index_path = Path(args.index_file)
  if not index_path.is_absolute():
    index_path = REPO_ROOT / index_path

  readme_desc = extract_metadata_from_readme(REPO_ROOT / "README.md")

  # Determine target game and variable part of branch
  # Formats: jak[123]/[category]/[variable_part...]
  detected_game = "jak2"
  repo_slug = None
  branch_match = re.match(r"^jak([123])/([^/]+)/(.+)$", branch)
  if branch_match:
    game_num = branch_match.group(1)
    detected_game = f"jak{game_num}"
    variable_part = branch_match.group(3)
    detected_slug = variable_part.replace("/", "-").replace("_", "-")
  else:
    # A mod repository: its branch (main) names nothing, its own catalog does.
    repo_slug, repo_game = mod_identity(index_path, args.repo)
    if repo_slug:
      variable_part = detected_slug = repo_slug
      detected_game = repo_game or detected_game
    else:
      parts = branch.split("/")
      variable_part = parts[-1] if len(parts) > 1 else branch
      detected_slug = branch.replace("/", "-").replace("_", "-")
  website_url = (f"https://github.com/{args.repo}" if repo_slug
                 else f"https://github.com/{args.repo}/tree/{branch}")

  # Mod metadata resolution: the arguments, else the mod's catalog entry (written by its last
  # release), else the README
  try:
    released = json.loads(index_path.read_text(encoding="utf-8")).get("mods", {}).get(detected_slug) or {}
  except (OSError, ValueError):
    released = {}
  disp_arg = args.display_name.strip() if args.display_name else ""
  display_name = disp_arg or released.get("displayName") or variable_part

  desc_arg = args.description.strip() if args.description else ""
  description = (
      desc_arg
      or released.get("description")
      or readme_desc
      or f"Mod OpenGOAL {detected_game.upper()} avec modifications C++ et LISP."
  )

  # Cover image resolution: docs/img/mod/mod_cover.png or explicit --cover-url
  cover_path = REPO_ROOT / "docs" / "img" / "mod" / "mod_cover.png"
  resolved_cover_url = args.cover_url
  if not resolved_cover_url and (cover_path.is_file() or branch.startswith(("jak1/", "jak2/", "jak3/"))):
    # A mod repository publishes from main, whatever the local branch is called (mods/<name>).
    cover_ref = "main" if repo_slug else branch
    resolved_cover_url = f"https://raw.githubusercontent.com/{args.repo}/{cover_ref}/docs/img/mod/mod_cover.png"

  if args.metadata_only:
    refresh_metadata_only(
        index_path=index_path,
        mod_id=args.mod_id or detected_slug,
        display_name=disp_arg or None,
        description=description,
        supported_games=(
            [g.strip() for g in args.supported_games.split(",") if g.strip()]
            if args.supported_games else [detected_game]
        ),
        cover_url=resolved_cover_url,
        website_url=website_url if repo_slug else None,
    )
    return

  # Determine tag formatted as [nom-du-mod]-[version]
  tag_slug = variable_part.replace("/", "-")
  tag = args.tag.strip() if args.tag else ""
  if not tag or tag == "auto":
    tag = get_next_version(index_path, mod_slug=tag_slug)
  else:
    if not tag.startswith(f"{tag_slug}-"):
      v_match = re.search(r"v?\d+\.\d+\.\d+", tag)
      ver_str = v_match.group(0) if v_match else tag
      if not ver_str.startswith("v"):
        ver_str = f"v{ver_str}"
      tag = f"{tag_slug}-{ver_str}"

  if args.next_version:
    print(tag)
    return

  if args.export_github_output:
    out_file = os.environ.get("GITHUB_OUTPUT")
    if out_file:
      with open(out_file, "a", encoding="utf-8") as f:
        f.write(f"release_tag={tag}\n")
        f.write(f"display_name={display_name}\n")
        f.write("description<<EOF\n")
        f.write(f"{description}\n")
        f.write("EOF\n")
        if resolved_cover_url:
          f.write(f"cover_url={resolved_cover_url}\n")
    print(f"[OK] Exported metadata to GITHUB_OUTPUT: release_tag={tag}, display_name={display_name}")
    return

  if args.print_metadata:
    meta = {
        "tag": tag,
        "display_name": display_name,
        "description": description,
        "game": detected_game,
        "branch": branch,
        "cover_url": resolved_cover_url,
    }
    print(json.dumps(meta, ensure_ascii=False))
    return

  if args.generate_release_notes:
    output_path = Path(args.generate_release_notes)
    if not output_path.is_absolute():
      output_path = REPO_ROOT / output_path

    checksums_path = Path(args.checksums_file) if args.checksums_file else None
    if checksums_path and not checksums_path.is_absolute():
      checksums_path = REPO_ROOT / checksums_path

    changelog_path = Path(args.changelog_file) if args.changelog_file else None
    if changelog_path and not changelog_path.is_absolute():
      changelog_path = REPO_ROOT / changelog_path
    if changelog_path and not changelog_path.is_file():
      raise SystemExit(f"Changelog not found: {changelog_path}")

    generate_release_notes(
        output_path=output_path,
        branch=branch,
        tag=tag,
        repo=args.repo,
        checksums_path=checksums_path,
        display_name=display_name,
        description=description,
        cover_url=resolved_cover_url,
        changelog_path=changelog_path,
    )
    return

  mod_id = args.mod_id or detected_slug
  supported_games = (
      [g.strip() for g in args.supported_games.split(",") if g.strip()]
      if args.supported_games
      else [detected_game]
  )
  authors = (
      [a.strip() for a in args.authors.split(",") if a.strip()]
      if args.authors
      else [args.repo.split("/")[0]]
  )

  ver_match = re.search(r"(\d+\.\d+\.\d+)", tag)
  clean_version = ver_match.group(1) if ver_match else tag.lstrip("v")
  now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

  catalog = {
      "schemaVersion": "1.0.0",
      "sourceName": sanitize_source_name(display_name),
      "lastUpdated": now_iso,
      "mods": {},
      "texturePacks": {},
  }

  if index_path.exists():
    try:
      with open(index_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
        if isinstance(loaded, dict) and "mods" in loaded:
          catalog = loaded
          if isinstance(catalog["mods"], dict):
            catalog["mods"] = {k: v for k, v in catalog["mods"].items() if not k.startswith("temp-sync-")}
    except Exception as err:
      print(
          f"Warning: Could not read existing index.json, creating a fresh one: {err}"
      )

  catalog["sourceName"] = sanitize_source_name(display_name)
  catalog["lastUpdated"] = now_iso

  # Base download URLs from GitHub Releases
  base_url = f"https://github.com/{args.repo}/releases/download/{tag}"
  win_url = f"{base_url}/windows-{tag}.zip"
  lin_url = f"{base_url}/linux-{tag}.zip"

  if mod_id not in catalog["mods"]:
    catalog["mods"][mod_id] = {
        "displayName": display_name,
        "description": description,
        "authors": authors,
        "tags": ["gameplay", "custom-engine"],
        "supportedGames": supported_games,
        "websiteUrl": website_url,
        "versions": [],
    }

  mod_entry = catalog["mods"][mod_id]
  mod_entry["websiteUrl"] = website_url
  mod_entry["displayName"] = display_name
  mod_entry["description"] = description
  mod_entry["supportedGames"] = supported_games

  if resolved_cover_url:
    mod_entry["coverArtUrl"] = resolved_cover_url
    mod_entry["thumbnailArtUrl"] = resolved_cover_url

  new_version = {
      "version": clean_version,
      "publishedDate": now_iso,
      "supportedGames": supported_games,
      "assets": {
          "windows": win_url,
          "linux": lin_url,
      },
      "checksums": {
          "windows": args.win_sha,
          "linux": args.lin_sha,
      },
  }

  # Remove previous identical version entry, and drop unreleased drafts without checksums
  mod_entry["versions"] = [
      v for v in mod_entry.get("versions", [])
      if v.get("version") != clean_version
      and (v.get("checksums", {}).get("windows") or v.get("checksums", {}).get("linux"))
  ]
  mod_entry["versions"].insert(0, new_version)

  # Automatically detect and register texture packs in current_mod/texture_packs/
  scan_and_register_texture_packs(
      catalog=catalog,
      release_tag=tag,
      repo=args.repo,
      branch=branch,
      detected_game=detected_game,
      default_author=authors[0] if authors else args.repo.split("/")[0],
      default_cover_url=resolved_cover_url,
  )

  index_path.parent.mkdir(parents=True, exist_ok=True)
  with open(index_path, "w", encoding="utf-8") as f:
    json.dump(catalog, f, indent=2, ensure_ascii=False)
    f.write("\n")

  print(f"[OK] Successfully wrote catalog to {index_path}")
  print(f"     Display Name: {display_name}")
  print(f"     Description : {description[:80]}...")
  print(f"     Version     : {clean_version} ({tag})")


if __name__ == "__main__":
  main()
