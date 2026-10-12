# Getting a recording

The SDK can only repeat what was recorded. A good recording is short, deliberate and marked.

## Recording with the skill

```
uv run --with pydoll-python python scripts/capture_record.py --url https://site.example/ --out captures/
```

A visible Chrome opens on a clean profile, so the sign-in is recorded from its first request.
Run the command in the background and tell the user, in their words:

1. Type a scenario name in the small "REC" panel at the bottom right and press **Mark** before
   each thing the SDK has to do: `login`, `list inbox`, `open message`.
2. Do that one thing. Wait until the page has settled.
3. Repeat for every scenario. Do each thing the plain way, once. For a list, load a second
   page as well: one page cannot show how paging works.
4. If you can afford it, make one deliberate mistake under its own mark (a wrong password):
   a refusal that was recorded becomes an error class, one that was not stays unknown.
5. Press **Finish** or close the browser.

The panel lies over the page. A click on "REC" folds it when it covers something.

What the user should know before starting:

- The recording directory holds their password, cookies and tokens. It stays on their
  machine, ignored by git; you read only a redacted copy.
- A one-time code is recorded like any typed value and is redacted the same way.
- Background noise is fine: analytics and static files are left out by the inventory.

Options:

| Option | When |
|---|---|
| `--profile DIR` | the session must already exist (a sign-in that cannot be repeated); the sign-in itself is then not recorded |
| `--proxy URL` | the site is reachable only through a proxy |
| `--chrome PATH` | Chrome is not found |
| `--no-overlay` | the site must not see an extra element; marks are typed in the terminal instead, which needs a foreground run |
| `--no-actions` | nothing may be injected at all; the recording has traffic only, no actions and no marks |
| `--screenshots` | a picture at every mark, for the report |

After each action the recorder also notes what the page then showed: its title, message
elements and the few lines of text that appeared. That is how a refusal gets the site's own
name instead of a bare code. The page's whole text stays in the browser; a rendered list
(many new lines) is counted, never quoted.

What the recorder adds to the browser, so you can answer when asked: the Network, Page and
Runtime domains of the debugger, a listener in an isolated world of each page that the page's
own scripts cannot reach, and the overlay element. The browser itself is the user's installed
Chrome driven over CDP, with no WebDriver.

What a recording does not contain: the order of HTTP/2 headers and the TLS fingerprint. The
transport reproduces them with `impersonate`; the replay cannot prove them. Recording works in
Chromium browsers only.

## A recording made elsewhere

- **HAR** from the browser's developer tools: enable "Preserve log", then "Save all as HAR
  with content". Without content the responses are empty and nothing can be replayed.
- **Burp Suite**: select the items, "Save items", and tick **Base64-encode requests and
  responses**. Without it Burp writes a dot for every byte above 127 and for binary bodies:
  Cyrillic text in pages and JSON is gone, and a response case that matches on such text
  cannot be proven. `capture_normalize.py` detects this and says which exchanges were hit.
- A recording made through a proxy from a script, not from a browser, shows what that script
  sends. It proves the SDK against the script, not against the site.

Such recordings carry no actions and no marks: the whole capture is one scenario, and
scenario boundaries and operation names are agreed with the user in the plan.

## When the recording is not enough

Ask for another recording rather than guessing:

- an operation has one sample and its parameters matter (which are constant, which vary);
- a list was loaded once;
- the user needs a refusal the recording does not have;
- the inventory says a value comes from `unknown` and changes between samples.

Several recordings are normalised together: pass them all to `capture_normalize.py`.
