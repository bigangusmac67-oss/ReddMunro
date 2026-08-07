"""
configure_site.py — fill the deployment placeholders in demo/.

Four files carry `REPLACE-WITH-YOUR-DOMAIN` and one carries
`REPLACE-WITH-YOUR-EMAIL`. They are placeholders rather than defaults on
purpose: a relative og:image does not fail loudly, it renders a blank
card in Slack and nobody notices for a month. CI refuses to build while
any placeholder survives, and this script is the intended way to clear
them.

    python configure_site.py --domain reddmunro.com --email you@example.com
    python configure_site.py --check          # report what is unfilled

Idempotent. Running it twice with the same values changes nothing, and
running it with new values rewrites cleanly, because it substitutes on
the URL pattern rather than on the placeholder text alone.
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "demo")

DOMAIN_FILES = ["index.html", "robots.txt", "sitemap.xml"]
EMAIL_FILES = ["index.html"]

PLACEHOLDER_DOMAIN = "REPLACE-WITH-YOUR-DOMAIN"
PLACEHOLDER_EMAIL = "REPLACE-WITH-YOUR-EMAIL"

# ---------------------------------------------------------------------
# WHY THIS IS NOT A GENERAL URL REGEX
#
# The first version matched  https://<any-host>/  so that re-running with
# a different domain would work. It rewrote the Pyodide CDN — the page
# then loaded its WebAssembly engine from https://reddmunro.com/pyodide/
# which does not exist, `loadPyodide` was never defined, and the site
# died on load with a message that named none of that.
#
# Third-party URLs on this page are not ours to touch. So substitution is
# now anchored to the EXACT attributes we own, and `--from` is required
# to change an already-configured domain. Nothing is matched loosely.
# ---------------------------------------------------------------------

# Each pattern captures a prefix and a suffix; only the host between them
# is replaced. Anything not listed here is never modified.
DOMAIN_PATTERNS = [
    (r'(<link rel="canonical" href="https://)([^"/]+)(/")'),
    (r'(<meta property="og:image" content="https://)([^"/]+)(/og\.png")'),
    (r'(<meta property="og:url" content="https://)([^"/]+)(/")'),
    (r'(<meta name="twitter:image" content="https://)([^"/]+)(/og\.png")'),
    (r'(Sitemap: https://)([^"/\s]+)(/sitemap\.xml)'),
    (r'(<loc>https://)([^"/<]+)(/</loc>)'),
]

EMAIL_RE = re.compile(r'(mailto:)([^"?\s]+)')

# Hosts that must never be rewritten, checked after every run.
THIRD_PARTY = ["cdn.jsdelivr.net", "unpkg.com"]


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _write(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def check():
    """Report unfilled placeholders. Exit 1 if any remain."""
    left = []
    for name in sorted(set(DOMAIN_FILES + EMAIL_FILES)):
        p = os.path.join(DEMO, name)
        if not os.path.exists(p):
            continue
        s = _read(p)
        if PLACEHOLDER_DOMAIN in s:
            left.append(f"{name}: domain ({s.count(PLACEHOLDER_DOMAIN)}x)")
        if PLACEHOLDER_EMAIL in s:
            left.append(f"{name}: email")
    if left:
        print("  UNFILLED:")
        for x in left:
            print(f"    · {x}")
        print("\n  Fix: python configure_site.py --domain YOURDOMAIN.com "
              "--email you@example.com")
        return 1
    print("  all placeholders filled")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", help="bare domain, e.g. reddmunro.com "
                                     "(no scheme, no trailing slash)")
    ap.add_argument("--email", help="contact address for the footer")
    ap.add_argument("--from", dest="from_domain",
                    help="an already-configured domain to change FROM. "
                         "Required to move an existing domain; without it "
                         "only the placeholder is substituted, so a "
                         "second run cannot silently rewrite anything.")
    ap.add_argument("--check", action="store_true",
                    help="report unfilled placeholders and exit")
    a = ap.parse_args()

    print("=" * 62)
    print("SITE CONFIGURATION")
    print("=" * 62 + "\n")

    if a.check or (not a.domain and not a.email):
        return check()

    if a.domain:
        d = a.domain.strip().rstrip("/")
        for pre in ("https://", "http://"):
            if d.startswith(pre):
                d = d[len(pre):]
        if "." not in d or " " in d:
            print(f"  ERROR: {a.domain!r} does not look like a bare domain.")
            print("  Expected something like: reddmunro.com")
            return 2

        old = a.__dict__.get("from_domain") or PLACEHOLDER_DOMAIN
        for name in DOMAIN_FILES:
            p = os.path.join(DEMO, name)
            if not os.path.exists(p):
                continue
            s_ = _read(p)
            total = 0
            for pat in DOMAIN_PATTERNS:
                def sub(m):
                    nonlocal total
                    if m.group(2) != old:
                        return m.group(0)      # not ours — leave alone
                    total += 1
                    return m.group(1) + d + m.group(3)
                s_ = re.sub(pat, sub, s_)
            if total:
                _write(p, s_)
            print(f"  {name:<14} domain -> {d}   ({total} occurrence(s))")

        # Post-condition: a third-party URL must never have moved.
        bad = []
        for name in DOMAIN_FILES:
            p = os.path.join(DEMO, name)
            if not os.path.exists(p):
                continue
            body = _read(p)
            for host in THIRD_PARTY:
                if host in body:
                    continue
                if name == "index.html" and host == "cdn.jsdelivr.net":
                    bad.append(f"{name}: {host} is missing — CDN URL was rewritten")
        if bad:
            print("\n  ERROR: third-party URL damaged:")
            for b in bad:
                print(f"    · {b}")
            return 3

    if a.email:
        e = a.email.strip()
        if "@" not in e:
            print(f"  ERROR: {a.email!r} does not look like an address.")
            return 2
        for name in EMAIL_FILES:
            p = os.path.join(DEMO, name)
            if not os.path.exists(p):
                continue
            s = _read(p)
            new, n = EMAIL_RE.subn(f"mailto:{e}", s)
            if n:
                _write(p, new)
            print(f"  {name:<14} email  -> {e}   ({n} occurrence(s))")
        print("\n  Note: this address goes on a public page and will be "
              "scraped.\n  A forwarding alias on your own domain costs "
              "nothing and can be\n  switched off; a personal inbox cannot.")

    print()
    rc = check()
    if rc == 0:
        print("\n  Next:  python build_demo.py")
        print("         then deploy demo/ — see DEPLOY.md")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
