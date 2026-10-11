#!/usr/bin/env python3
"""Add a new YouTube channel to Shorts Pipeline. Runs in Google Cloud Shell (shell.cloud.google.com).

It does everything Google and GitHub let a program do, and walks you through the few clicks they don't:
  1. signs in to GitHub (one code to paste),
  2. creates the channel's Google Cloud project and turns on the YouTube APIs,
  3. shows you exactly what to click to set up the Google sign-in screen and make its key,
  4. gets the channel's sign-in token after you click Allow, and checks it's the right channel,
  5. saves the 3 secrets in GitHub, adds the channel to the website, and starts the "Add a channel"
     workflow, which adds it to the pipeline and writes your prefilled audit form answers.

Nothing secret is printed or saved on disk. Start it with:
  curl -fsSL https://ricardomalliah.github.io/shorts-pipeline-site/add-channel.py -o add-channel.py && python3 add-channel.py
"""
import base64, getpass, hashlib, json, os, random, re, secrets, shutil, subprocess, sys, tarfile, textwrap, time
import urllib.error, urllib.parse, urllib.request

REPO = "ricardomalliah/shorts-pipeline"
SITE_REPO = "ricardomalliah/shorts-pipeline-site"
SITE = "https://ricardomalliah.github.io/shorts-pipeline-site/"
REDIRECT = SITE + "oauth.html"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]
CONTACT = "rico.malliah@gmail.com"
GH_VERSION = "2.62.0"
B, G, R, D, X = "\033[1m", "\033[32m", "\033[31m", "\033[2m", "\033[0m"


# ---------- small helpers ----------

def say(text=""):
    for para in textwrap.dedent(text).strip("\n").split("\n"):
        print(para)


def title(n, text):
    print(f"\n{B}{'=' * 60}\n STEP {n}: {text}\n{'=' * 60}{X}")


def ok(text):
    print(f"{G}✔ {text}{X}")


def fail(text):
    print(f"{R}✘ {text}{X}")


def ask(prompt, hidden=False, check=None, hint=""):
    while True:
        value = (getpass.getpass(f"{B}{prompt}{X} ") if hidden else input(f"{B}{prompt}{X} ")).strip()
        if value and (check is None or check(value)):
            return value
        fail(hint or "That doesn't look right. Please try again.")


def wait(text="When you've done that, come back here and press Return."):
    input(f"\n{B}» {text}{X}")


def run(cmd, input=None, check=True):
    p = subprocess.run(cmd, input=input, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(p.stderr or p.stdout).strip()[-600:]}")
    return p


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def http_json(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return {"error": json.loads(body)}
        except ValueError:
            return {"error": body[:300]}


# ---------- GitHub ----------

def find_gh():
    gh = shutil.which("gh")
    if gh:
        return gh
    dest = os.path.expanduser(f"~/.local/gh-{GH_VERSION}")
    gh = os.path.join(dest, f"gh_{GH_VERSION}_linux_amd64", "bin", "gh")
    if not os.path.exists(gh):
        say(f"{D}Installing the GitHub command-line tool (one time only)...{X}")
        url = f"https://github.com/cli/cli/releases/download/v{GH_VERSION}/gh_{GH_VERSION}_linux_amd64.tar.gz"
        os.makedirs(dest, exist_ok=True)
        tgz = os.path.join(dest, "gh.tgz")
        urllib.request.urlretrieve(url, tgz)
        with tarfile.open(tgz) as t:
            t.extractall(dest)
        os.remove(tgz)
    return gh


def github_signin(gh):
    title(1, "Sign in to GitHub")
    if run([gh, "auth", "status", "-h", "github.com"], check=False).returncode == 0:
        ok("Already signed in to GitHub.")
    else:
        say(f"""
        In a moment you'll see a line like: {B}First copy your one-time code: ABCD-1234{X}
          1. Select that 8-character code with your mouse and press Cmd+C to copy it.
          2. Press Return here. If no new tab opens, open {B}https://github.com/login/device{X} yourself.
          3. Paste the code (Cmd+V), click {B}Continue{X}, then {B}Authorize github{X}.
          4. If it asks you a Yes/No question here, just press Return.
        """)
        subprocess.run([gh, "auth", "login", "-h", "github.com", "-p", "https", "-w"])
        if run([gh, "auth", "status", "-h", "github.com"], check=False).returncode != 0:
            fail("GitHub sign-in didn't finish. Run the command again to retry.")
            sys.exit(1)
        ok("Signed in to GitHub.")
    if run([gh, "api", f"repos/{REPO}", "-q", ".full_name"], check=False).returncode != 0:
        fail(f"This GitHub account can't see {REPO}. Sign in as ricardomalliah and run the command again.")
        run([gh, "auth", "logout", "-h", "github.com"], input="y\n", check=False)
        sys.exit(1)


def gh_file(gh, repo, path):
    out = json.loads(run([gh, "api", f"repos/{repo}/contents/{path}"]).stdout)
    return base64.b64decode(out["content"]).decode(), out["sha"]


# ---------- Google Cloud ----------

def gcloud(*args, check=True):
    return run(["gcloud", *args, "--quiet"], check=check)


def create_project(name, slug):
    title(3, "Create the Google Cloud project")
    say(f"{D}If a box pops up asking to authorize Cloud Shell, click Authorize.{X}")
    account = gcloud("config", "get-value", "account").stdout.strip()
    if account:
        ok(f"Using your Google account {account}")
    display = "Shorts " + re.sub(r"[^A-Za-z0-9 !-]", "", name)[:23]
    found = gcloud("projects", "list", f"--filter=name='{display}'",
                   "--format=value(projectId,projectNumber)").stdout.split()
    if found:
        pid, number = found[0], found[1]
        ok(f"Found the project you made last time: {pid}")
    else:
        base = ("shorts-" + slug)[:30].rstrip("-")
        pid = base
        for attempt in range(4):
            p = gcloud("projects", "create", pid, f"--name={display}", check=False)
            if p.returncode == 0:
                break
            if "already" not in p.stderr.lower() and "in use" not in p.stderr.lower():
                fail("Google Cloud wouldn't create the project:\n" + p.stderr.strip()[-600:])
                sys.exit(1)
            pid = f"{base[:25].rstrip('-')}-{random.randint(1000, 9999)}"
        else:
            fail("Couldn't find a free project ID. Run the command again.")
            sys.exit(1)
        number = gcloud("projects", "describe", pid, "--format=value(projectNumber)").stdout.strip()
        ok(f"Created project {pid} (number {number})")
    for attempt in range(6):
        p = gcloud("services", "enable", "youtube.googleapis.com", "youtubeanalytics.googleapis.com",
                   f"--project={pid}", check=False)
        if p.returncode == 0:
            break
        time.sleep(15)  # a brand-new project can take a few seconds before APIs can be turned on
    else:
        fail("Couldn't turn on the YouTube APIs:\n" + p.stderr.strip()[-600:])
        sys.exit(1)
    ok("Turned on YouTube Data API v3 and YouTube Analytics API")
    return pid, number, account


def consent_screen(pid, account, name):
    console = "https://console.cloud.google.com/auth"
    title(4, "Set up the Google sign-in screen (about 3 minutes of clicking)")
    say(f"""
    Google has no way for a program to do this part, so here are the exact clicks.
    Tip: in this terminal, Cmd+click a link to open it in a new tab.

    {B}4a.{X} Open: {B}{console}/overview?project={pid}{X}
          1. Check the project name at the top left says {B}Shorts {name}{X}.
             (If you see an overview page with no Get started button, this part is already done: skip to 4b.)
          2. Click {B}Get started{X}.
          3. App name: {B}Shorts Pipeline{X}
             User support email: pick {B}{account or 'your email'}{X} from the list. Click {B}Next{X}.
          4. Audience: choose {B}External{X}. Click {B}Next{X}.
          5. Contact information: type {B}{CONTACT}{X}. Click {B}Next{X}.
          6. Tick {B}I agree to the Google API Services: User Data Policy{X}. Click {B}Continue{X}, then {B}Create{X}.
    """)
    wait()
    say(f"""
    {B}4b.{X} Open: {B}{console}/branding?project={pid}{X}
          1. Scroll to {B}App domain{X} and paste these 3 links:
               Application home page:            {B}{SITE}{X}
               Application privacy policy link:  {B}{SITE}privacy.html{X}
               Application terms of service link: {B}{SITE}terms.html{X}
          2. Under {B}Authorised domains{X}, click {B}+ Add domain{X} and type {B}ricardomalliah.github.io{X}
          3. Scroll to the bottom and click {B}Save{X}.
    """)
    wait()
    say(f"""
    {B}4c.{X} Open: {B}{console}/audience?project={pid}{X}
          1. Under {B}Publishing status{X} (it says Testing), click {B}Publish app{X}.
          2. Click {B}Confirm{X}. It should now say {B}In production{X}.
    """)
    wait()


def make_client(pid):
    title(5, "Make the sign-in key")
    say(f"""
    Open: {B}https://console.cloud.google.com/auth/clients/create?project={pid}{X}
      1. Application type: {B}Web application{X}
      2. Name: {B}Shorts Pipeline web{X}
      3. Under {B}Authorised redirect URIs{X}, click {B}+ Add URI{X} and paste exactly:
           {B}{REDIRECT}{X}
      4. Click {B}Create{X}.
      5. A box shows the {B}Client ID{X} and {B}Client secret{X}. Keep it open: you'll copy them
         into this terminal now (use the copy icon next to each, then Cmd+V here).
         Google only shows the secret once, so don't close the box until both are pasted.
    """)
    cid = ask("Paste the Client ID and press Return:", check=lambda v: v.endswith(".apps.googleusercontent.com"),
              hint="The Client ID ends with .apps.googleusercontent.com. Copy it again with the copy icon.")
    say(f"{D}The secret stays hidden while you paste it. That's normal.{X}")
    csec = ask("Paste the Client secret and press Return:", hidden=True, check=lambda v: len(v) >= 20 and " " not in v,
               hint="That isn't the Client secret (it usually starts with GOCSPX-). Copy it again.")
    ok("Got the key. You can close Google's box now.")
    return cid, csec


def youtube_signin(cid, csec, name, handle):
    title(6, f"Click Allow for {name}")
    while True:
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
            "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code", "scope": " ".join(SCOPES),
            "access_type": "offline", "prompt": "consent", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": secrets.token_urlsafe(16)})
        say(f"""
        1. Cmd+click this link (it's long, that's fine):
        {B}{url}{X}
        2. Pick your Google account. If it asks you to choose a {B}channel{X}, pick {B}{name}{X} ({handle}). This matters.
        3. If you see {B}Google hasn't verified this app{X}: click {B}Advanced{X}, then {B}Go to Shorts Pipeline (unsafe){X}.
           That's expected: it's your own app.
        4. Tick every permission box, then click {B}Continue{X}.
        5. A Shorts Pipeline page opens. Click {B}Copy code{X}, come back here and paste it.
        {D}If Google shows an error page instead (for example "redirect_uri_mismatch" or "invalid_client"),
        the new key isn't active yet: wait 5 minutes, then just press Return here for a fresh link.{X}
        """)
        code = input(f"{B}Paste the code and press Return:{X} ").strip()
        if not code:
            continue
        tok = http_json("https://oauth2.googleapis.com/token", urllib.parse.urlencode({
            "client_id": cid, "client_secret": csec, "code": code, "code_verifier": verifier,
            "grant_type": "authorization_code", "redirect_uri": REDIRECT}).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"})
        if "error" in tok:
            err = tok["error"]
            err = err.get("error_description") or err.get("error") if isinstance(err, dict) else err
            fail(f"Google didn't accept that code ({err}). Let's get a fresh one.")
            if "client" in str(err).lower():
                say("If this keeps happening, the Client ID or secret may have been pasted wrong: press Ctrl+C and run the command again.")
            continue
        if not tok.get("refresh_token"):
            fail("Google didn't send a long-term token. Let's try once more.")
            continue
        if tok.get("refresh_token_expires_in"):
            fail("The app is still in Testing, so this sign-in would stop working after 7 days.")
            say(f"Do step 4c (Publish app) now, then press Return for a fresh link.")
            input()
            continue
        me = http_json("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                       headers={"Authorization": "Bearer " + tok["access_token"]})
        items = me.get("items") or []
        got = items[0]["snippet"].get("customUrl", "") if items else ""
        if got.lower() != handle.lower():
            fail(f"That sign-in is for {got or 'an account with no channel'}, not {handle}.")
            say(f"Press Return for a fresh link, and this time pick {B}{name}{X} on Google's channel list.")
            input()
            continue
        ok(f"Signed in as {items[0]['snippet']['title']} ({got})")
        return tok["refresh_token"]


# ---------- finishing up ----------

def save_and_start(gh, name, slug, handle, niche, kind, pid, number, cid, csec, refresh):
    title(7, "Save the secrets and add the channel everywhere")
    prefix = slug.upper().replace("-", "_")
    for key, value in (("CLIENT_ID", cid), ("CLIENT_SECRET", csec), ("REFRESH_TOKEN", refresh)):
        run([gh, "secret", "set", f"{prefix}_{key}", "-R", REPO], input=value)
    ok(f"Saved GitHub secrets {prefix}_CLIENT_ID, {prefix}_CLIENT_SECRET, {prefix}_REFRESH_TOKEN")

    html, sha = gh_file(gh, SITE_REPO, "index.html")
    if handle.lower() not in html.lower():
        marker = "  <!-- new channels are added above this line -->"
        safe = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = html.replace(marker, f"  <li>{safe(name)} ({safe(handle)}): {safe(niche)}</li>\n{marker}")
        run([gh, "api", "-X", "PUT", f"repos/{SITE_REPO}/contents/index.html", "-f", f"message=Add {name} to the home page",
             "-f", f"content={base64.b64encode(html.encode()).decode()}", "-f", f"sha={sha}"])
    ok("Added the channel to the website's home page")

    def latest_run():
        rows = json.loads(run([gh, "run", "list", "-R", REPO, "--workflow", "add-channel.yml", "--limit", "1",
                               "--json", "databaseId,status,conclusion,url"]).stdout or "[]")
        return rows[0] if rows else {}

    before = latest_run().get("databaseId")
    run([gh, "workflow", "run", "add-channel.yml", "-R", REPO, "-f", f"name={name}", "-f", f"handle={handle}",
         "-f", f"niche={niche}", "-f", f"kind={kind}", "-f", f"project_number={number}", "-f", f"project_id={pid}"])
    say(f"{D}Started the 'Add a channel' workflow on GitHub. Waiting for it to finish (a few minutes)...{X}")
    run_info = {}
    for _ in range(100):
        time.sleep(15)
        run_info = latest_run()
        if run_info.get("databaseId") != before and run_info.get("status") == "completed":
            break
    audit = f"https://github.com/{REPO}/blob/main/audit/{slug}.md"
    if run_info.get("conclusion") == "success":
        ok(f"{name} is now part of the pipeline")
        return audit, True
    fail(f"The workflow didn't finish cleanly. Details: {run_info.get('url', 'https://github.com/' + REPO + '/actions')}")
    return audit, False


def main():
    say(f"""
    {B}Add a new channel to Shorts Pipeline{X}
    Before you start, the YouTube channel must already exist and your phone must be verified on it
    (youtube.com/verify). Press Ctrl+C at any time to stop; running the command again picks up where it can.
    """)
    gh = find_gh()
    github_signin(gh)

    title(2, "About the channel")
    cfg = json.loads(gh_file(gh, REPO, "config/channels.json")[0])
    while True:
        name = ask("Channel name, exactly as on YouTube (for example Calm Cash):")
        slug = slugify(name)
        handle = "@" + ask("Channel handle (for example @CalmCashShorts):").lstrip("@")
        if slug in cfg and cfg[slug].get("handle", "").lower() != handle.lower():
            fail(f"There's already a channel called {cfg[slug]['channel']} with a different handle. Pick another name.")
            continue
        break
    niche = ask("In a few words, what is it about (for example budgeting for students):")
    kind = ask("Should the pipeline write and animate videos for it? Type yes, or no for a clips channel:",
               check=lambda v: v.lower() in ("yes", "y", "no", "n"), hint="Type yes or no.")
    kind = "animated" if kind.lower().startswith("y") else "clips"

    pid, number, account = create_project(name, slug)
    consent_screen(pid, account, name)
    cid, csec = make_client(pid)
    refresh = youtube_signin(cid, csec, name, handle)
    audit, done = save_and_start(gh, name, slug, handle, niche, kind, pid, number, cid, csec, refresh)
    run([gh, "auth", "logout", "-h", "github.com"], input="y\n", check=False)

    title(8, "Last step: the YouTube audit form")
    if done:
        say(f"""
        Open your prefilled answers: {B}{audit}{X}
        (sign in to GitHub if asked). It links the form and has a copy button on every answer.
        Until Google approves it, {name}'s uploads stay private; everything else already runs.
        """)
    else:
        say(f"""
        The channel's sign-in is saved, but the last GitHub step needs a look.
        Tell Claude in the project thread "the add-channel workflow failed"; it can finish from there.
        """)
    say(f"\n{G}{B}All done for {name}.{X} You can close this tab.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to carry on.")
    except RuntimeError as e:
        fail(str(e))
        say("Something went wrong. Copy the red line above into the project thread (it contains no secrets).")
        sys.exit(1)
