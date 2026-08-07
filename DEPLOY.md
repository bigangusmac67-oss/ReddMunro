# Deploying the site

`demo/` is a static site with no backend. Hosting is free on every major
platform indefinitely, because there is no server to run — Pyodide comes from a
CDN and the audit happens in the visitor's tab.

**Total running cost: the domain. ~$10–11/year.**

---

## 1. Buy the domain

[Cloudflare Registrar](https://www.cloudflare.com/application-services/solutions/low-cost-domain-names/)
sells `.com` at cost — around **$10.44/year**, and the renewal is the same
price. That last part is the reason to prefer it: the industry norm is a cheap
first year followed by a renewal two or three times higher, and you will be
renewing this every year.

Cloudflare Registrar requires the domain to use Cloudflare DNS, which is fine
and free, and is what you want anyway if hosting on Cloudflare Pages.

---

## 2. Fill in the placeholders — **do this before deploying**

Four files carry `REPLACE-WITH-YOUR-DOMAIN`, and one carries
`REPLACE-WITH-YOUR-EMAIL`:

| File | What to change |
|---|---|
| `demo/index.html` | `og:image`, `og:url`, `canonical`, `twitter:image` — **absolute URLs are required**; Slack and LinkedIn will not resolve a relative `og:image` and the card renders blank without erroring |
| `demo/robots.txt` | sitemap URL |
| `demo/sitemap.xml` | page URL |
| `demo/index.html` footer | `REPLACE-WITH-YOUR-EMAIL` — the only route anyone has to tell you anything |

CI fails the build if any placeholder survives, so this cannot be forgotten
silently. That is deliberate.

**On the email.** Use an address you are willing to have scraped. A personal
Gmail on a public page attracts spam forever. A forwarding alias on the domain
costs nothing on Cloudflare and can be turned off.

---

## 3. Deploy

```bash
cd signal-audit
python build_demo.py          # rebuild + verify before every deploy
```

### Cloudflare, via wrangler — the supported path

`wrangler.toml` deploys `demo/` as an **assets-only Worker**: no Worker script,
no code running on Cloudflare's side. That is deliberate and load-bearing. The
page tells visitors there is no server that could receive their dashboard
export; adding a `main` entry here would make that false, so it would have to
change the copy too.

**Once, to authorise the machine:**

```bash
npx wrangler login          # opens a browser; approves this device
npx wrangler whoami         # confirm the account
```

**First, confirm what the live site currently is.** It was deployed by hand
through the dashboard, and a Pages project and a Worker of the same name are
different things:

```bash
npx wrangler pages project list
```

| `reddmunro` in that list | Meaning | Do this |
|---|---|---|
| **absent** | it is already a Worker with static assets | `npx wrangler deploy` — the config here matches it |
| **present** | it is a Pages project | see the warning below |

> **If it is a Pages project, do not just run `wrangler deploy`.** That creates
> a *second*, separate Worker also called `reddmunro`, deploys to it, and
> leaves `reddmunro.com` still pointing at the Pages project — so the site
> looks unchanged and you have two deployments to reason about. Either keep
> using `npx wrangler pages deploy demo --project-name=reddmunro`, or migrate
> deliberately: deploy the Worker, move the custom domain onto it, then delete
> the Pages project.

**Every deploy after that:**

```bash
python build_demo.py        # never skip: demo/ is build output
npx wrangler deploy
```

`demo/_headers` is picked up natively by Workers static assets, exactly as it
was by Pages — including the `Content-Type: text/plain` line on
`signal_audit.py`, which is what stops a root-level `.py` being treated as a
function instead of a file.

### Vercel

```bash
cd demo && vercel --prod
```

`vercel.json` sets `framework: null` and pins `Content-Type: text/plain` on
`signal_audit.py`. **Without that, some hosts treat a root-level `.py` as a
serverless function rather than a static asset** — the single most likely way
this deploy fails.

### GitHub Pages

```bash
git subtree push --prefix signal-audit/demo origin gh-pages
```

`.nojekyll` is required and already present; without it Jekyll mangles the
directory. Note that GitHub Pages ignores `_headers`, so verify the `.py`
serves as text before trusting it.

---

### Automatic deploys from CI

`.github/workflows/demo.yml` runs the engine tests, checks `demo/` has not
drifted from source, **runs the engine inside real Pyodide on wasm32**, and
only then deploys. It has never run: there is no git repository yet.

To turn it on:

1. `git init` and push this directory — **its root must be the repo root**, so
   `wrangler.toml` and `build_demo.py` sit at the top level. The workflow says
   what to change if you commit the parent folder instead.
2. Cloudflare dashboard → **My Profile → API Tokens → Create Token → Edit
   Cloudflare Workers**. Scope it to this account only.
3. GitHub → repo **Settings → Secrets and variables → Actions**, add
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.

The token is a deploy credential for your whole Workers account. Create it and
paste it yourself; do not put it in a file, a commit, or a chat window.

## 4. Verify the deploy — five things, two minutes

1. **The page audits real data on its own.** Within ~15s of load (Pyodide is
   ~10MB on first visit, cached after) a report appears for
   `prometheus_infra.csv` with the blue "live audit" banner.
2. **Network panel is quiet after load.** Drop a CSV and confirm **no request
   is made**. This is the central claim of the product; check it on the
   deployed page, not just locally.
3. **`signal_audit.py` serves as text**, not as a function invocation or a
   download. `curl -I https://yourdomain/signal_audit.py`
4. **The social card renders.** Paste the URL into Slack. A blank card means
   `og:image` is still relative or the domain string is wrong.
5. **The lens dropdown has entries.** Empty means `domains/index.json` did not
   deploy — the manifest is generated by `build_demo.py`, so rebuild.

---

## 5. What is deliberately absent

**No analytics.** "Nothing leaves your machine" is the strongest claim this
product makes, and a tracking script would undercut it in a way the audience
would notice and enjoy pointing out. The cost is real: you will not know
whether ten people ran an audit or zero. The footer mailto is the deliberate
substitute — anyone who bothers to write is worth more than a pageview at this
stage.

If that trade stops being acceptable, use a server-log-based count or a
privacy-preserving counter, and **say so on the page**. Adding silent
instrumentation to this particular tool would be the single most damaging thing
that could be done to it.

**No signup, no pricing.** There is nothing to sign up for. Auth is a stub and
the Pro tier cannot take money, so a pricing table would be advertising
something that does not exist.

**No customer logos or testimonials.** There are no customers yet. Inventing
social proof on a page whose entire argument is "we publish what we got wrong"
would be self-defeating.
